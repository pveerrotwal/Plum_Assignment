"""Document Extractor Agent — structured extraction from documents."""

from __future__ import annotations

import os
from typing import Any, Optional

from app.models.schemas import DocumentInput, TraceStatus, TraceStep


class ExtractionResult:
    def __init__(self) -> None:
        self.extracted: dict[str, dict[str, Any]] = {}
        self.trace_steps: list[TraceStep] = []
        self.failed = False
        self.failure_reason = ""


def _extract_from_content(doc: DocumentInput, doc_type: str) -> dict[str, Any]:
    """Use embedded content (test/eval mode) or attempt LLM extraction."""
    if doc.content:
        data = dict(doc.content)
        data["document_type"] = doc_type
        data["extraction_source"] = "structured_content"
        data["confidence"] = 0.95
        return data

    if doc.file_path and os.path.exists(doc.file_path):
        return _llm_extract(doc.file_path, doc_type)

    return {
        "document_type": doc_type,
        "extraction_source": "metadata_only",
        "confidence": 0.5,
        "patient_name": doc.patient_name_on_doc,
    }


def _llm_extract(file_path: str, doc_type: str) -> dict[str, Any]:
    """Optional OpenAI vision extraction when API key is available."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {
            "document_type": doc_type,
            "extraction_source": "no_api_key",
            "confidence": 0.4,
            "note": "OPENAI_API_KEY not set; limited extraction from filename/metadata only.",
        }

    try:
        import base64
        import httpx

        with open(file_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        ext = file_path.rsplit(".", 1)[-1].lower()
        mime = "application/pdf" if ext == "pdf" else f"image/{ext if ext in ('png', 'jpg', 'jpeg', 'webp') else 'jpeg'}"

        prompt = (
            f"Extract structured medical information from this {doc_type} document. "
            "Return JSON with: patient_name, doctor_name, doctor_registration, diagnosis, "
            "treatment, date, line_items (array of description/amount), total, hospital_name, "
            "tests_ordered, medicines, confidence (0-1), unreadable (bool)."
        )

        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                        ],
                    }
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": 1500,
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        import json

        content = resp.json()["choices"][0]["message"]["content"]
        data = json.loads(content)
        data["document_type"] = doc_type
        data["extraction_source"] = "openai_vision"
        return data
    except Exception as exc:
        return {
            "document_type": doc_type,
            "extraction_source": "llm_failed",
            "confidence": 0.3,
            "error": str(exc),
        }


def extract_documents(
    classified_docs: list[tuple[DocumentInput, str]],
    simulate_failure: bool = False,
) -> ExtractionResult:
    result = ExtractionResult()

    if simulate_failure:
        result.failed = True
        result.failure_reason = "DocumentExtractorAgent simulated failure (TC011)"
        result.trace_steps.append(
            TraceStep(
                step_id="extractor_failure",
                component="DocumentExtractorAgent",
                action="extract_all_documents",
                status=TraceStatus.SKIPPED,
                details={"simulated": True},
                message="Component failure simulated — extraction skipped, using submission metadata.",
            )
        )
        for doc, doc_type in classified_docs:
            fallback = {
                "document_type": doc_type,
                "extraction_source": "fallback_after_failure",
                "confidence": 0.45,
            }
            if doc.content:
                fallback.update(doc.content)
            result.extracted[doc.file_id] = fallback
        return result

    for doc, doc_type in classified_docs:
        try:
            data = _extract_from_content(doc, doc_type)
            result.extracted[doc.file_id] = data
            conf = data.get("confidence", 0.8)
            status = TraceStatus.PASSED if conf >= 0.6 else TraceStatus.WARNING
            result.trace_steps.append(
                TraceStep(
                    step_id=f"extract_{doc.file_id}",
                    component="DocumentExtractorAgent",
                    action="extract_document",
                    status=status,
                    details={"file_id": doc.file_id, "document_type": doc_type, "confidence": conf, "fields": list(data.keys())},
                    message=f"Extracted {len(data)} fields from {doc_type} ({doc.file_id}).",
                )
            )
        except Exception as exc:
            result.extracted[doc.file_id] = {
                "document_type": doc_type,
                "extraction_source": "error",
                "confidence": 0.2,
                "error": str(exc),
            }
            result.trace_steps.append(
                TraceStep(
                    step_id=f"extract_{doc.file_id}",
                    component="DocumentExtractorAgent",
                    action="extract_document",
                    status=TraceStatus.DEGRADED,
                    details={"error": str(exc)},
                    message=f"Partial extraction failure for {doc.file_id}: {exc}",
                )
            )

    return result


def aggregate_extraction(extracted: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Merge per-document extractions into a unified claim view."""
    merged: dict[str, Any] = {
        "patient_names": [],
        "diagnoses": [],
        "doctors": [],
        "line_items": [],
        "totals": [],
        "hospital_names": [],
        "tests_ordered": [],
        "medicines": [],
        "min_confidence": 1.0,
    }

    for _fid, data in extracted.items():
        if data.get("patient_name"):
            merged["patient_names"].append(data["patient_name"])
        if data.get("diagnosis"):
            merged["diagnoses"].append(data["diagnosis"])
        if data.get("doctor_name"):
            merged["doctors"].append(data["doctor_name"])
        if data.get("hospital_name"):
            merged["hospital_names"].append(data["hospital_name"])
        if data.get("total"):
            merged["totals"].append(float(data["total"]))
        if data.get("line_items"):
            merged["line_items"].extend(data["line_items"])
        if data.get("tests_ordered"):
            tests = data["tests_ordered"]
            if isinstance(tests, list):
                merged["tests_ordered"].extend(tests)
            else:
                merged["tests_ordered"].append(tests)
        if data.get("test_name"):
            merged["tests_ordered"].append(data["test_name"])
        if data.get("medicines"):
            meds = data["medicines"]
            if isinstance(meds, list):
                merged["medicines"].extend(meds)
        conf = data.get("confidence", 0.8)
        merged["min_confidence"] = min(merged["min_confidence"], conf)

    merged["primary_diagnosis"] = merged["diagnoses"][0] if merged["diagnoses"] else ""
    merged["primary_hospital"] = merged["hospital_names"][0] if merged["hospital_names"] else ""
    merged["bill_total"] = max(merged["totals"]) if merged["totals"] else None
    return merged
