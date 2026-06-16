"""Fraud Detection Agent — flags suspicious claim patterns."""

from __future__ import annotations

from collections import Counter
from typing import Any

from app.models.schemas import ClaimSubmission, TraceStatus, TraceStep
from app.services.policy_loader import get_fraud_thresholds


class FraudDetectionResult:
    def __init__(self) -> None:
        self.fraud_score: float = 0.0
        self.signals: list[str] = []
        self.manual_review_required: bool = False
        self.trace_steps: list[TraceStep] = []


def detect_fraud(claim: ClaimSubmission) -> FraudDetectionResult:
    result = FraudDetectionResult()
    thresholds = get_fraud_thresholds()
    treatment_date = claim.treatment_date

    same_day = [h for h in claim.claims_history if h.date == treatment_date]
    same_day_count = len(same_day)
    limit = thresholds.get("same_day_claims_limit", 2)

    if same_day_count > limit:
        result.fraud_score += 0.5
        providers = [h.provider or "Unknown" for h in same_day]
        result.signals.append(
            f"Unusual same-day claim pattern: {same_day_count} prior claim(s) on {treatment_date} "
            f"from providers: {', '.join(providers)}. This is claim #{same_day_count + 1} today."
        )

    if claim.claimed_amount >= thresholds.get("high_value_claim_threshold", 25000):
        result.fraud_score += 0.2
        result.signals.append(f"High-value claim: ₹{claim.claimed_amount:,.0f}.")

    monthly = Counter(h.date[:7] for h in claim.claims_history)
    month_key = treatment_date[:7]
    monthly_count = monthly.get(month_key, 0)
    if monthly_count >= thresholds.get("monthly_claims_limit", 6):
        result.fraud_score += 0.3
        result.signals.append(f"Monthly claim limit approached: {monthly_count} claims in {month_key}.")

    fraud_threshold = thresholds.get("fraud_score_manual_review_threshold", 0.80)
    auto_manual = thresholds.get("auto_manual_review_above", 25000)

    if result.fraud_score >= fraud_threshold or claim.claimed_amount >= auto_manual:
        result.manual_review_required = True

    if same_day_count > limit:
        result.manual_review_required = True

    status = TraceStatus.WARNING if result.signals else TraceStatus.PASSED
    result.trace_steps.append(
        TraceStep(
            step_id="fraud_detection",
            component="FraudDetectionAgent",
            action="analyze_claim_patterns",
            status=status,
            details={
                "fraud_score": round(result.fraud_score, 2),
                "signals": result.signals,
                "same_day_prior_claims": same_day_count,
                "manual_review_required": result.manual_review_required,
            },
            message="; ".join(result.signals) if result.signals else "No fraud signals detected.",
        )
    )
    return result
