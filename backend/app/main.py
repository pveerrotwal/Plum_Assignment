"""FastAPI application for claims processing."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.agents.orchestrator import process_claim
from app.models.schemas import (
    ClaimCategory,
    ClaimDecision,
    ClaimHistoryEntry,
    ClaimResponse,
    ClaimSubmission,
    DocumentInput,
)
from app.services.policy_loader import load_policy

UPLOAD_DIR = Path(__file__).resolve().parents[2] / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

app = FastAPI(
    title="Plum Claims Processing System",
    description="Multi-agent health insurance claims automation pipeline",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_claims_store: dict[str, ClaimDecision] = {}


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "plum-claims-pipeline"}


@app.get("/api/policy")
def get_policy() -> dict[str, Any]:
    return load_policy()


@app.get("/api/members")
def get_members() -> list[dict[str, Any]]:
    return load_policy()["members"]


@app.get("/api/document-requirements/{category}")
def document_requirements(category: str) -> dict[str, Any]:
    policy = load_policy()
    return policy["document_requirements"].get(category.upper(), {"required": [], "optional": []})


@app.post("/api/claims/submit", response_model=ClaimResponse)
def submit_claim(claim: ClaimSubmission) -> ClaimResponse:
    response = process_claim(claim)
    _claims_store[response.claim_id] = response.result
    return response


@app.post("/api/claims/submit-with-files", response_model=ClaimResponse)
async def submit_claim_with_files(
    member_id: str = Form(...),
    claim_category: str = Form(...),
    treatment_date: str = Form(...),
    claimed_amount: float = Form(...),
    ytd_claims_amount: float = Form(0),
    hospital_name: str = Form(""),
    simulate_component_failure: bool = Form(False),
    claims_history_json: str = Form("[]"),
    files: list[UploadFile] = File(...),
) -> ClaimResponse:
    documents: list[DocumentInput] = []
    for f in files:
        file_id = f"F_{uuid.uuid4().hex[:6].upper()}"
        dest = UPLOAD_DIR / f"{file_id}_{f.filename}"
        with open(dest, "wb") as out:
            shutil.copyfileobj(f.file, out)
        documents.append(
            DocumentInput(
                file_id=file_id,
                file_name=f.filename,
                file_path=str(dest),
            )
        )

    try:
        history = json.loads(claims_history_json)
        claims_history = [ClaimHistoryEntry(**h) for h in history]
    except (json.JSONDecodeError, TypeError):
        claims_history = []

    claim = ClaimSubmission(
        member_id=member_id,
        claim_category=ClaimCategory(claim_category.upper()),
        treatment_date=treatment_date,
        claimed_amount=claimed_amount,
        ytd_claims_amount=ytd_claims_amount,
        hospital_name=hospital_name or None,
        simulate_component_failure=simulate_component_failure,
        claims_history=claims_history,
        documents=documents,
    )
    response = process_claim(claim)
    _claims_store[response.claim_id] = response.result
    return response


@app.get("/api/claims")
def list_claims() -> list[dict[str, Any]]:
    return [
        {
            "claim_id": cid,
            "decision": d.decision.value if d.decision else None,
            "approved_amount": d.approved_amount,
            "confidence_score": d.confidence_score,
            "blocked": d.blocked,
            "block_reason": d.block_reason,
        }
        for cid, d in _claims_store.items()
    ]


@app.get("/api/claims/{claim_id}", response_model=ClaimDecision)
def get_claim(claim_id: str) -> ClaimDecision:
    if claim_id not in _claims_store:
        raise HTTPException(status_code=404, detail="Claim not found")
    return _claims_store[claim_id]


frontend_candidates = [
    Path(__file__).resolve().parents[2] / "frontend" / "dist",
    Path(__file__).resolve().parents[1] / "frontend" / "dist",
]
for frontend_dist in frontend_candidates:
    if frontend_dist.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
        break
