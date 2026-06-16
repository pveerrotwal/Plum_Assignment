"""Pydantic schemas for the claims processing pipeline."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ClaimCategory(str, Enum):
    CONSULTATION = "CONSULTATION"
    DIAGNOSTIC = "DIAGNOSTIC"
    PHARMACY = "PHARMACY"
    DENTAL = "DENTAL"
    VISION = "VISION"
    ALTERNATIVE_MEDICINE = "ALTERNATIVE_MEDICINE"


class DocumentType(str, Enum):
    PRESCRIPTION = "PRESCRIPTION"
    HOSPITAL_BILL = "HOSPITAL_BILL"
    PHARMACY_BILL = "PHARMACY_BILL"
    LAB_REPORT = "LAB_REPORT"
    DIAGNOSTIC_REPORT = "DIAGNOSTIC_REPORT"
    DENTAL_REPORT = "DENTAL_REPORT"
    DISCHARGE_SUMMARY = "DISCHARGE_SUMMARY"
    UNKNOWN = "UNKNOWN"


class DecisionType(str, Enum):
    APPROVED = "APPROVED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class TraceStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    DEGRADED = "DEGRADED"
    WARNING = "WARNING"


class DocumentInput(BaseModel):
    file_id: str
    file_name: Optional[str] = None
    actual_type: Optional[str] = None
    quality: Optional[str] = None
    patient_name_on_doc: Optional[str] = None
    content: Optional[dict[str, Any]] = None
    file_path: Optional[str] = None


class ClaimHistoryEntry(BaseModel):
    claim_id: str
    date: str
    amount: float
    provider: Optional[str] = None


class ClaimSubmission(BaseModel):
    member_id: str
    policy_id: str = "PLUM_GHI_2024"
    claim_category: ClaimCategory
    treatment_date: str
    claimed_amount: float
    documents: list[DocumentInput]
    ytd_claims_amount: float = 0
    claims_history: list[ClaimHistoryEntry] = Field(default_factory=list)
    simulate_component_failure: bool = False
    hospital_name: Optional[str] = None


class TraceStep(BaseModel):
    step_id: str
    component: str
    action: str
    status: TraceStatus
    details: dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class LineItemDecision(BaseModel):
    description: str
    amount: float
    approved: bool
    approved_amount: float = 0
    reason: str = ""


class ClaimDecision(BaseModel):
    claim_id: str
    decision: Optional[DecisionType] = None
    approved_amount: float = 0
    reason: str = ""
    confidence_score: float = 0.0
    rejection_reasons: list[str] = Field(default_factory=list)
    line_item_decisions: list[LineItemDecision] = Field(default_factory=list)
    trace: list[TraceStep] = Field(default_factory=list)
    member_message: str = ""
    ops_notes: str = ""
    component_failures: list[str] = Field(default_factory=list)
    manual_review_recommended: bool = False
    financial_breakdown: dict[str, Any] = Field(default_factory=dict)
    blocked: bool = False
    block_reason: str = ""


class ClaimResponse(BaseModel):
    success: bool
    claim_id: str
    result: ClaimDecision
    processing_time_ms: float = 0
