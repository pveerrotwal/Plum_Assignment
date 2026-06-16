"""Document Gatekeeper Agent — early validation before processing."""

from __future__ import annotations

from typing import Optional

from app.models.schemas import (
    ClaimSubmission,
    DocumentInput,
    DocumentType,
    TraceStatus,
    TraceStep,
)
from app.services.policy_loader import get_document_requirements, get_member


DOCUMENT_LABELS = {
    "PRESCRIPTION": "Medical Prescription",
    "HOSPITAL_BILL": "Hospital/Clinic Bill",
    "PHARMACY_BILL": "Pharmacy Bill",
    "LAB_REPORT": "Lab/Diagnostic Report",
    "DIAGNOSTIC_REPORT": "Diagnostic Report",
    "DENTAL_REPORT": "Dental Report",
    "DISCHARGE_SUMMARY": "Discharge Summary",
}


class DocumentGatekeeperResult:
    def __init__(self) -> None:
        self.blocked = False
        self.block_reason = ""
        self.member_message = ""
        self.trace_steps: list[TraceStep] = []
        self.classified_docs: list[tuple[DocumentInput, str]] = []


def _classify_document(doc: DocumentInput) -> str:
    if doc.actual_type:
        return doc.actual_type
    name = (doc.file_name or "").lower()
    if "prescription" in name or "rx" in name:
        return DocumentType.PRESCRIPTION.value
    if "pharmacy" in name:
        return DocumentType.PHARMACY_BILL.value
    if "lab" in name or "report" in name:
        return DocumentType.LAB_REPORT.value
    if "bill" in name or "invoice" in name:
        return DocumentType.HOSPITAL_BILL.value
    if "dental" in name:
        return DocumentType.DENTAL_REPORT.value
    return DocumentType.UNKNOWN.value


def verify_documents(claim: ClaimSubmission) -> DocumentGatekeeperResult:
    result = DocumentGatekeeperResult()
    category = claim.claim_category.value
    requirements = get_document_requirements(category)
    required = requirements.get("required", [])

    member = get_member(claim.member_id)
    if not member:
        result.blocked = True
        result.block_reason = "INVALID_MEMBER"
        result.member_message = f"Member ID '{claim.member_id}' was not found in the policy roster."
        result.trace_steps.append(
            TraceStep(
                step_id="gatekeeper_member",
                component="DocumentGatekeeperAgent",
                action="validate_member",
                status=TraceStatus.FAILED,
                message=result.member_message,
            )
        )
        return result

    result.trace_steps.append(
        TraceStep(
            step_id="gatekeeper_member",
            component="DocumentGatekeeperAgent",
            action="validate_member",
            status=TraceStatus.PASSED,
            details={"member_id": claim.member_id, "member_name": member["name"]},
            message=f"Member {member['name']} ({claim.member_id}) verified.",
        )
    )

    classified: list[tuple[DocumentInput, str]] = []
    for doc in claim.documents:
        doc_type = _classify_document(doc)
        classified.append((doc, doc_type))

    result.classified_docs = classified
    uploaded_types = [dt for _, dt in classified]
    type_counts: dict[str, int] = {}
    for dt in uploaded_types:
        type_counts[dt] = type_counts.get(dt, 0) + 1

    result.trace_steps.append(
        TraceStep(
            step_id="gatekeeper_classify",
            component="DocumentGatekeeperAgent",
            action="classify_documents",
            status=TraceStatus.PASSED,
            details={
                "uploaded": [
                    {"file_id": d.file_id, "file_name": d.file_name, "detected_type": dt}
                    for d, dt in classified
                ]
            },
            message=f"Classified {len(classified)} document(s).",
        )
    )

    missing: list[str] = []
    wrong_uploads: list[str] = []
    for req in required:
        if req not in uploaded_types:
            missing.append(req)

    if missing:
        duplicate_types = [dt for dt, count in type_counts.items() if count > 1]
        for dup in duplicate_types:
            if dup in required and dup not in missing:
                count = type_counts[dup]
                for req in missing:
                    label_uploaded = DOCUMENT_LABELS.get(dup, dup)
                    label_required = DOCUMENT_LABELS.get(req, req)
                    wrong_uploads.append(
                        f"You uploaded {count} {label_uploaded}(s), but a {label_required} is required for {category} claims."
                    )

        if not wrong_uploads:
            missing_labels = [DOCUMENT_LABELS.get(m, m) for m in missing]
            uploaded_labels = [DOCUMENT_LABELS.get(u, u) for u in uploaded_types]
            result.blocked = True
            result.block_reason = "MISSING_DOCUMENTS"
            result.member_message = (
                f"Your {category} claim is missing required document(s): {', '.join(missing_labels)}. "
                f"You uploaded: {', '.join(uploaded_labels)}. "
                f"Please upload the missing document(s) and resubmit."
            )
        else:
            result.blocked = True
            result.block_reason = "WRONG_DOCUMENT_TYPE"
            result.member_message = " ".join(wrong_uploads)
            if missing:
                missing_labels = [DOCUMENT_LABELS.get(m, m) for m in missing]
                result.member_message += f" Missing: {', '.join(missing_labels)}."

        result.trace_steps.append(
            TraceStep(
                step_id="gatekeeper_requirements",
                component="DocumentGatekeeperAgent",
                action="check_document_requirements",
                status=TraceStatus.FAILED,
                details={"required": required, "uploaded": uploaded_types, "missing": missing},
                message=result.member_message,
            )
        )
        return result

    result.trace_steps.append(
        TraceStep(
            step_id="gatekeeper_requirements",
            component="DocumentGatekeeperAgent",
            action="check_document_requirements",
            status=TraceStatus.PASSED,
            details={"required": required, "uploaded": uploaded_types},
            message="All required documents present.",
        )
    )

    for doc, doc_type in classified:
        if doc.quality == "UNREADABLE":
            label = DOCUMENT_LABELS.get(doc_type, doc_type)
            result.blocked = True
            result.block_reason = "UNREADABLE_DOCUMENT"
            result.member_message = (
                f"The {label} ({doc.file_name or doc.file_id}) is too blurry or unreadable to process. "
                f"Please re-upload a clear photo or scan of your {label}."
            )
            result.trace_steps.append(
                TraceStep(
                    step_id=f"gatekeeper_quality_{doc.file_id}",
                    component="DocumentGatekeeperAgent",
                    action="check_document_quality",
                    status=TraceStatus.FAILED,
                    details={"file_id": doc.file_id, "document_type": doc_type, "quality": "UNREADABLE"},
                    message=result.member_message,
                )
            )
            return result

    patient_names: list[tuple[str, str]] = []
    for doc, doc_type in classified:
        name: Optional[str] = doc.patient_name_on_doc
        if not name and doc.content:
            name = doc.content.get("patient_name")
        if name:
            patient_names.append((doc.file_id, name))

    if len(patient_names) >= 2:
        unique_names = {n for _, n in patient_names}
        if len(unique_names) > 1:
            details_parts = []
            for fid, name in patient_names:
                doc_type = next(dt for d, dt in classified if d.file_id == fid)
                label = DOCUMENT_LABELS.get(doc_type, doc_type)
                details_parts.append(f"{label} ({fid}): {name}")

            result.blocked = True
            result.block_reason = "PATIENT_MISMATCH"
            result.member_message = (
                "Your documents appear to belong to different patients: "
                + "; ".join(details_parts)
                + ". Please ensure all documents are for the same patient and resubmit."
            )
            result.trace_steps.append(
                TraceStep(
                    step_id="gatekeeper_patient_consistency",
                    component="DocumentGatekeeperAgent",
                    action="check_patient_consistency",
                    status=TraceStatus.FAILED,
                    details={"patient_names": dict(patient_names)},
                    message=result.member_message,
                )
            )
            return result

    result.trace_steps.append(
        TraceStep(
            step_id="gatekeeper_patient_consistency",
            component="DocumentGatekeeperAgent",
            action="check_patient_consistency",
            status=TraceStatus.PASSED,
            message="Patient names consistent across documents.",
        )
    )

    return result
