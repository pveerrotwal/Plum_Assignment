"""Unit and integration tests for the claims pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.document_gatekeeper import verify_documents
from app.agents.orchestrator import process_claim
from app.models.schemas import ClaimCategory, ClaimHistoryEntry, ClaimSubmission, DocumentInput
from app.services.policy_loader import (
    compute_eligibility_date,
    get_per_claim_limit,
    is_network_hospital,
    requires_pre_auth,
)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def load_test_cases() -> list[dict]:
    with open(DATA_DIR / "test_cases.json") as f:
        return json.load(f)["test_cases"]


def build_claim(tc_input: dict) -> ClaimSubmission:
    docs = [DocumentInput(**d) for d in tc_input["documents"]]
    history = [ClaimHistoryEntry(**h) for h in tc_input.get("claims_history", [])]
    return ClaimSubmission(
        member_id=tc_input["member_id"],
        policy_id=tc_input.get("policy_id", "PLUM_GHI_2024"),
        claim_category=ClaimCategory(tc_input["claim_category"]),
        treatment_date=tc_input["treatment_date"],
        claimed_amount=tc_input["claimed_amount"],
        documents=docs,
        ytd_claims_amount=tc_input.get("ytd_claims_amount", 0),
        claims_history=history,
        simulate_component_failure=tc_input.get("simulate_component_failure", False),
        hospital_name=tc_input.get("hospital_name"),
    )


class TestPolicyLoader:
    def test_per_claim_limit(self):
        assert get_per_claim_limit() == 5000

    def test_network_hospital(self):
        assert is_network_hospital("Apollo Hospitals") is True
        assert is_network_hospital("Random Clinic") is False

    def test_pre_auth_required(self):
        assert requires_pre_auth("MRI Lumbar Spine", 15000) is True
        assert requires_pre_auth("CBC", 500) is False

    def test_eligibility_date(self):
        date = compute_eligibility_date("2024-09-01", "diabetes")
        assert date == "2024-11-30"


class TestDocumentGatekeeper:
    def test_wrong_document_blocks(self):
        claim = build_claim(load_test_cases()[0]["input"])
        result = verify_documents(claim)
        assert result.blocked is True
        assert "Hospital" in result.member_message or "HOSPITAL" in result.member_message.upper()
        assert result.block_reason == "WRONG_DOCUMENT_TYPE"

    def test_unreadable_blocks(self):
        claim = build_claim(load_test_cases()[1]["input"])
        result = verify_documents(claim)
        assert result.blocked is True
        assert "unreadable" in result.member_message.lower() or "blurry" in result.member_message.lower()

    def test_patient_mismatch_blocks(self):
        claim = build_claim(load_test_cases()[2]["input"])
        result = verify_documents(claim)
        assert result.blocked is True
        assert "Rajesh Kumar" in result.member_message
        assert "Arjun Mehta" in result.member_message


@pytest.mark.parametrize("case_id", [f"TC{i:03d}" for i in range(1, 13)])
def test_eval_case(case_id: str):
    cases = {tc["case_id"]: tc for tc in load_test_cases()}
    tc = cases[case_id]
    claim = build_claim(tc["input"])
    response = process_claim(claim)
    result = response.result
    expected = tc["expected"]

    assert response.success is True

    if expected.get("decision") is None:
        assert result.blocked is True or result.decision is None
    else:
        assert result.decision is not None
        assert result.decision.value == expected["decision"]

    if "approved_amount" in expected:
        assert result.approved_amount == expected["approved_amount"]

    if "rejection_reasons" in expected:
        for reason in expected["rejection_reasons"]:
            assert reason in result.rejection_reasons

    if case_id == "TC001":
        assert "prescription" in result.member_message.lower()
        assert "hospital" in result.member_message.lower() or "bill" in result.member_message.lower()

    if case_id == "TC005":
        assert "2024-11-30" in result.reason or "2024-11-30" in result.member_message

    if case_id == "TC009":
        assert "same-day" in result.reason.lower() or "same-day" in str(result.trace).lower()

    if case_id == "TC011":
        assert len(result.component_failures) > 0
        assert result.manual_review_recommended is True
        assert result.confidence_score < 0.85

    if case_id == "TC012":
        assert result.confidence_score >= 0.90
