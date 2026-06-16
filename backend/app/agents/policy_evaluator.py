"""Policy Evaluator Agent — applies rules from policy_terms.json."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from dateutil import parser as date_parser

from app.models.schemas import (
    ClaimCategory,
    ClaimSubmission,
    DecisionType,
    LineItemDecision,
    TraceStatus,
    TraceStep,
)
from app.services.policy_loader import (
    compute_eligibility_date,
    get_category_config,
    get_member,
    get_per_claim_limit,
    is_dental_covered,
    is_dental_excluded,
    is_excluded_condition,
    is_network_hospital,
    match_condition_to_waiting_period,
    requires_pre_auth,
)


class PolicyEvaluationResult:
    def __init__(self) -> None:
        self.decision: Optional[DecisionType] = None
        self.approved_amount: float = 0.0
        self.rejection_reasons: list[str] = []
        self.line_item_decisions: list[LineItemDecision] = []
        self.trace_steps: list[TraceStep] = []
        self.financial_breakdown: dict[str, Any] = {}
        self.reason: str = ""
        self.eligibility_date: Optional[str] = None
        self.manual_review_signals: list[str] = []


def _parse_date(d: str) -> datetime:
    return date_parser.parse(d)


def evaluate_policy(
    claim: ClaimSubmission,
    aggregated: dict[str, Any],
    has_pre_auth: bool = False,
) -> PolicyEvaluationResult:
    result = PolicyEvaluationResult()
    member = get_member(claim.member_id)
    category = claim.claim_category.value
    cat_config = get_category_config(category)
    diagnosis = aggregated.get("primary_diagnosis", "")
    treatment = ""
    for ext in aggregated.get("extractions", {}).values() if isinstance(aggregated.get("extractions"), dict) else []:
        treatment = ext.get("treatment", treatment)

    # 1. Exclusions (before financial limits — TC012)
    excluded, exclusion_reason = is_excluded_condition(diagnosis, treatment)
    if not excluded and claim.claim_category != ClaimCategory.DENTAL:
        for item in aggregated.get("line_items", []):
            desc = item.get("description", "")
            ex, reason = is_excluded_condition(desc, desc)
            if ex:
                excluded, exclusion_reason = ex, reason
                break

    if excluded:
        result.decision = DecisionType.REJECTED
        result.rejection_reasons.append("EXCLUDED_CONDITION")
        result.reason = f"Treatment is excluded under policy: {exclusion_reason}."
        result.trace_steps.append(
            TraceStep(
                step_id="policy_exclusion",
                component="PolicyEvaluatorAgent",
                action="check_exclusions",
                status=TraceStatus.FAILED,
                details={"diagnosis": diagnosis, "exclusion": exclusion_reason},
                message=result.reason,
            )
        )
        return result

    result.trace_steps.append(
        TraceStep(
            step_id="policy_exclusion",
            component="PolicyEvaluatorAgent",
            action="check_exclusions",
            status=TraceStatus.PASSED,
            message="No policy exclusions matched.",
        )
    )

    # 2. Waiting period
    if member:
        condition_key = match_condition_to_waiting_period(diagnosis)
        if condition_key:
            join_date = member["join_date"]
            treatment_dt = _parse_date(claim.treatment_date).date()
            join_dt = _parse_date(join_date).date()
            from app.services.policy_loader import get_waiting_period_days

            wait_days = get_waiting_period_days(condition_key) or 0
            days_since_join = (treatment_dt - join_dt).days
            eligible_date = compute_eligibility_date(join_date, condition_key)

            if days_since_join < wait_days:
                result.decision = DecisionType.REJECTED
                result.rejection_reasons.append("WAITING_PERIOD")
                result.eligibility_date = eligible_date
                result.reason = (
                    f"Claim rejected: {diagnosis} is subject to a {wait_days}-day waiting period. "
                    f"Member joined on {join_date}. You will be eligible for {condition_key.replace('_', ' ')} "
                    f"claims from {eligible_date}."
                )
                result.trace_steps.append(
                    TraceStep(
                        step_id="policy_waiting_period",
                        component="PolicyEvaluatorAgent",
                        action="check_waiting_period",
                        status=TraceStatus.FAILED,
                        details={
                            "condition": condition_key,
                            "wait_days": wait_days,
                            "days_since_join": days_since_join,
                            "eligible_from": eligible_date,
                        },
                        message=result.reason,
                    )
                )
                return result

        result.trace_steps.append(
            TraceStep(
                step_id="policy_waiting_period",
                component="PolicyEvaluatorAgent",
                action="check_waiting_period",
                status=TraceStatus.PASSED,
                message="Waiting period check passed.",
            )
        )

    # 3. Pre-authorization (before per-claim limit — TC007)
    if claim.claim_category == ClaimCategory.DIAGNOSTIC:
        tests = aggregated.get("tests_ordered", [])
        test_name = tests[0] if tests else diagnosis
        amount = claim.claimed_amount
        if requires_pre_auth(test_name, amount) and not has_pre_auth:
            result.decision = DecisionType.REJECTED
            result.rejection_reasons.append("PRE_AUTH_MISSING")
            result.reason = (
                f"Pre-authorization is required for {test_name} when amount exceeds ₹10,000. "
                f"No pre-authorization was found. Please obtain pre-auth via the Plum app or "
                f"call support, then resubmit with the pre-auth reference number."
            )
            result.trace_steps.append(
                TraceStep(
                    step_id="policy_pre_auth",
                    component="PolicyEvaluatorAgent",
                    action="check_pre_authorization",
                    status=TraceStatus.FAILED,
                    details={"test": test_name, "amount": amount},
                    message=result.reason,
                )
            )
            return result

        result.trace_steps.append(
            TraceStep(
                step_id="policy_pre_auth",
                component="PolicyEvaluatorAgent",
                action="check_pre_authorization",
                status=TraceStatus.PASSED,
                message="Pre-authorization check passed.",
            )
        )

    hospital = claim.hospital_name or aggregated.get("primary_hospital", "")
    line_items = aggregated.get("line_items", [])
    claimed = claim.claimed_amount

    # 4. Dental line-item evaluation (before per-claim limit — TC006)
    if claim.claim_category == ClaimCategory.DENTAL and line_items:
        approved_total = 0.0
        partial = False
        for item in line_items:
            desc = item.get("description", "")
            amt = float(item.get("amount", 0))
            if is_dental_covered(desc):
                result.line_item_decisions.append(
                    LineItemDecision(
                        description=desc,
                        amount=amt,
                        approved=True,
                        approved_amount=amt,
                        reason="Covered dental procedure",
                    )
                )
                approved_total += amt
            else:
                excluded_dental, exc_reason = is_dental_excluded(desc)
                if excluded_dental:
                    partial = True
                    result.line_item_decisions.append(
                        LineItemDecision(
                            description=desc,
                            amount=amt,
                            approved=False,
                            approved_amount=0,
                            reason=f"Excluded cosmetic procedure: {exc_reason}",
                        )
                    )
                else:
                    result.line_item_decisions.append(
                        LineItemDecision(
                            description=desc,
                            amount=amt,
                            approved=True,
                            approved_amount=amt,
                            reason="Dental procedure — approved pending review",
                        )
                    )
                    approved_total += amt

        result.approved_amount = approved_total
        if partial and approved_total > 0:
            result.decision = DecisionType.PARTIAL
            rejected_items = [li for li in result.line_item_decisions if not li.approved]
            result.reason = (
                f"Partial approval: ₹{approved_total:,.0f} approved. "
                f"Rejected: {', '.join(f'{li.description} (₹{li.amount:,.0f}) — {li.reason}' for li in rejected_items)}."
            )
        elif approved_total > 0:
            result.decision = DecisionType.APPROVED
            result.reason = f"Full approval: ₹{approved_total:,.0f}."
        else:
            result.decision = DecisionType.REJECTED
            result.rejection_reasons.append("EXCLUDED_CONDITION")
            result.reason = "All line items excluded under dental policy."

        result.trace_steps.append(
            TraceStep(
                step_id="policy_dental_line_items",
                component="PolicyEvaluatorAgent",
                action="evaluate_dental_line_items",
                status=TraceStatus.PASSED if result.decision != DecisionType.REJECTED else TraceStatus.FAILED,
                details={"line_items": [li.model_dump() for li in result.line_item_decisions]},
                message=result.reason,
            )
        )
        return result

    # 5. Per-claim limit (OPD categories — TC008; skipped for DENTAL/DIAGNOSTIC)
    per_claim_categories = {
        ClaimCategory.CONSULTATION,
        ClaimCategory.PHARMACY,
        ClaimCategory.VISION,
        ClaimCategory.ALTERNATIVE_MEDICINE,
    }
    if claim.claim_category in per_claim_categories:
        per_claim_limit = get_per_claim_limit()
        if claim.claimed_amount > per_claim_limit:
            result.decision = DecisionType.REJECTED
            result.rejection_reasons.append("PER_CLAIM_EXCEEDED")
            result.reason = (
                f"Claim amount ₹{claim.claimed_amount:,.0f} exceeds the per-claim limit of ₹{per_claim_limit:,.0f}. "
                f"Please split into multiple claims or contact support."
            )
            result.trace_steps.append(
                TraceStep(
                    step_id="policy_per_claim",
                    component="PolicyEvaluatorAgent",
                    action="check_per_claim_limit",
                    status=TraceStatus.FAILED,
                    details={"claimed": claim.claimed_amount, "limit": per_claim_limit},
                    message=result.reason,
                )
            )
            return result

        per_claim_limit = get_per_claim_limit()
        result.trace_steps.append(
            TraceStep(
                step_id="policy_per_claim",
                component="PolicyEvaluatorAgent",
                action="check_per_claim_limit",
                status=TraceStatus.PASSED,
                details={"claimed": claim.claimed_amount, "limit": per_claim_limit},
                message=f"Claim amount ₹{claim.claimed_amount:,.0f} within per-claim limit ₹{per_claim_limit:,.0f}.",
            )
        )

    # 6. Financial calculation (network discount → co-pay)
    network = is_network_hospital(hospital)
    network_discount_pct = cat_config.get("network_discount_percent", 0) if network else 0
    copay_pct = cat_config.get("copay_percent", 0)

    base_amount = claimed
    after_network = base_amount
    network_discount = 0.0
    if network and network_discount_pct > 0:
        network_discount = base_amount * (network_discount_pct / 100)
        after_network = base_amount - network_discount

    copay_amount = after_network * (copay_pct / 100)
    final_amount = after_network - copay_amount

    result.approved_amount = round(final_amount, 2)
    result.financial_breakdown = {
        "claimed_amount": base_amount,
        "network_hospital": network,
        "hospital_name": hospital,
        "network_discount_percent": network_discount_pct,
        "network_discount_amount": round(network_discount, 2),
        "amount_after_network_discount": round(after_network, 2),
        "copay_percent": copay_pct,
        "copay_amount": round(copay_amount, 2),
        "approved_amount": result.approved_amount,
    }

    if network:
        result.reason = (
            f"Network discount ({network_discount_pct}%) applied on ₹{base_amount:,.0f} = ₹{after_network:,.0f}. "
            f"Co-pay ({copay_pct}%) applied on ₹{after_network:,.0f} = ₹{copay_amount:,.0f} deducted. "
            f"Final: ₹{result.approved_amount:,.0f}."
        )
    elif copay_pct > 0:
        result.reason = (
            f"{copay_pct}% co-pay applied on consultation category (₹{copay_amount:,.0f} deducted). "
            f"Final approved: ₹{result.approved_amount:,.0f}."
        )
    else:
        result.reason = f"Approved amount: ₹{result.approved_amount:,.0f}."

    result.decision = DecisionType.APPROVED
    result.trace_steps.append(
        TraceStep(
            step_id="policy_financial",
            component="PolicyEvaluatorAgent",
            action="calculate_approved_amount",
            status=TraceStatus.PASSED,
            details=result.financial_breakdown,
            message=result.reason,
        )
    )
    return result
