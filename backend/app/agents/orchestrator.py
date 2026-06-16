"""Pipeline Orchestrator — coordinates multi-agent claim processing."""

from __future__ import annotations

import time
import uuid
from typing import Optional

from app.agents.document_extractor import aggregate_extraction, extract_documents
from app.agents.document_gatekeeper import verify_documents
from app.agents.fraud_detector import detect_fraud
from app.agents.policy_evaluator import evaluate_policy
from app.models.schemas import (
    ClaimDecision,
    ClaimResponse,
    ClaimSubmission,
    DecisionType,
    TraceStatus,
    TraceStep,
)


def _compute_confidence(
    extraction_confidence: float,
    component_failures: list[str],
    policy_decision: Optional[DecisionType],
    blocked: bool,
) -> float:
    if blocked:
        return 0.95
    base = extraction_confidence
    if component_failures:
        base *= 0.65
    if policy_decision == DecisionType.REJECTED:
        base = max(base, 0.90)
    elif policy_decision == DecisionType.MANUAL_REVIEW:
        base = min(base, 0.75)
    elif policy_decision == DecisionType.PARTIAL:
        base = min(base, 0.85)
    else:
        base = max(base, 0.85)
    return round(min(max(base, 0.0), 1.0), 2)


def process_claim(claim: ClaimSubmission) -> ClaimResponse:
    start = time.time()
    claim_id = f"CLM_{uuid.uuid4().hex[:8].upper()}"
    trace: list[TraceStep] = []
    component_failures: list[str] = []

    trace.append(
        TraceStep(
            step_id="orchestrator_start",
            component="PipelineOrchestrator",
            action="start_processing",
            status=TraceStatus.PASSED,
            details={"claim_id": claim_id, "category": claim.claim_category.value},
            message=f"Processing claim {claim_id} for member {claim.member_id}.",
        )
    )

    gatekeeper = verify_documents(claim)
    trace.extend(gatekeeper.trace_steps)

    if gatekeeper.blocked:
        decision = ClaimDecision(
            claim_id=claim_id,
            decision=None,
            confidence_score=0.95,
            trace=trace,
            member_message=gatekeeper.member_message,
            blocked=True,
            block_reason=gatekeeper.block_reason,
            ops_notes=f"Claim blocked at document gatekeeper: {gatekeeper.block_reason}",
        )
        elapsed = (time.time() - start) * 1000
        return ClaimResponse(success=True, claim_id=claim_id, result=decision, processing_time_ms=elapsed)

    extraction = extract_documents(
        gatekeeper.classified_docs,
        simulate_failure=claim.simulate_component_failure,
    )
    trace.extend(extraction.trace_steps)
    if extraction.failed:
        component_failures.append(extraction.failure_reason)

    aggregated = aggregate_extraction(extraction.extracted)
    aggregated["extractions"] = extraction.extracted

    fraud = detect_fraud(claim)
    trace.extend(fraud.trace_steps)

    policy = evaluate_policy(claim, aggregated)
    trace.extend(policy.trace_steps)

    final_decision = policy.decision
    approved_amount = policy.approved_amount
    reason = policy.reason
    rejection_reasons = list(policy.rejection_reasons)
    manual_review_recommended = False

    if fraud.manual_review_required and final_decision not in (DecisionType.REJECTED, None):
        final_decision = DecisionType.MANUAL_REVIEW
        reason = (
            "Claim flagged for manual review due to fraud/risk signals. "
            + "; ".join(fraud.signals)
        )
        trace.append(
            TraceStep(
                step_id="decision_manual_review",
                component="DecisionAgent",
                action="route_manual_review",
                status=TraceStatus.WARNING,
                details={"signals": fraud.signals, "fraud_score": fraud.fraud_score},
                message=reason,
            )
        )

    if extraction.failed and final_decision == DecisionType.APPROVED:
        manual_review_recommended = True
        reason += " Manual review recommended due to incomplete document extraction."

    if claim.simulate_component_failure and final_decision == DecisionType.APPROVED:
        manual_review_recommended = True

    confidence = _compute_confidence(
        aggregated.get("min_confidence", 0.8),
        component_failures,
        final_decision,
        blocked=False,
    )
    if extraction.failed:
        confidence = min(confidence, 0.72)

    trace.append(
        TraceStep(
            step_id="decision_final",
            component="DecisionAgent",
            action="finalize_decision",
            status=TraceStatus.PASSED,
            details={
                "decision": final_decision.value if final_decision else None,
                "approved_amount": approved_amount,
                "confidence_score": confidence,
            },
            message=f"Final decision: {final_decision.value if final_decision else 'BLOCKED'}.",
        )
    )

    decision = ClaimDecision(
        claim_id=claim_id,
        decision=final_decision,
        approved_amount=approved_amount,
        reason=reason,
        confidence_score=confidence,
        rejection_reasons=rejection_reasons,
        line_item_decisions=policy.line_item_decisions,
        trace=trace,
        member_message=reason if final_decision else gatekeeper.member_message,
        ops_notes=reason,
        component_failures=component_failures,
        manual_review_recommended=manual_review_recommended,
        financial_breakdown=policy.financial_breakdown,
    )

    elapsed = (time.time() - start) * 1000
    return ClaimResponse(success=True, claim_id=claim_id, result=decision, processing_time_ms=elapsed)
