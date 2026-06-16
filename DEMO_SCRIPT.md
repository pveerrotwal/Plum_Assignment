# Demo Video Script (8–12 minutes)

Use this script to record your submission demo video.

---

## Segment 1: Introduction (1 min)

- Show the Plum Claims UI at `http://localhost:5173`
- Briefly explain: multi-agent pipeline for automated health insurance claim processing
- Mention: document gatekeeper → extractor → fraud detector → policy evaluator → explainable decision

---

## Segment 2: Early Document Block (2–3 min)

**Scenario:** TC001 — Wrong document uploaded

1. Go to **Submit Claim** tab
2. Select member: Rajesh Kumar (EMP001)
3. Category: CONSULTATION
4. Upload two prescription files (or use API with test case JSON)
5. Submit

**Alternative via API (curl):**
```bash
curl -X POST http://localhost:8000/api/claims/submit \
  -H "Content-Type: application/json" \
  -d @- << 'EOF'
{
  "member_id": "EMP001",
  "claim_category": "CONSULTATION",
  "treatment_date": "2024-11-01",
  "claimed_amount": 1500,
  "documents": [
    {"file_id": "F001", "file_name": "prescription1.jpg", "actual_type": "PRESCRIPTION"},
    {"file_id": "F002", "file_name": "prescription2.jpg", "actual_type": "PRESCRIPTION"}
  ]
}
EOF
```

**Show:**
- Decision: BLOCKED
- Specific message naming "Medical Prescription" uploaded vs "Hospital/Clinic Bill" required
- Full trace showing DocumentGatekeeperAgent FAILED at check_document_requirements
- Emphasize: no claim decision was made — stopped early

---

## Segment 3: End-to-End Approval with Full Trace (3–4 min)

**Scenario:** TC004 — Clean consultation approval

```bash
curl -X POST http://localhost:8000/api/claims/submit \
  -H "Content-Type: application/json" \
  -d @- << 'EOF'
{
  "member_id": "EMP001",
  "claim_category": "CONSULTATION",
  "treatment_date": "2024-11-01",
  "claimed_amount": 1500,
  "documents": [
    {"file_id": "F007", "actual_type": "PRESCRIPTION", "content": {"patient_name": "Rajesh Kumar", "diagnosis": "Viral Fever", "doctor_name": "Dr. Arun Sharma"}},
    {"file_id": "F008", "actual_type": "HOSPITAL_BILL", "content": {"patient_name": "Rajesh Kumar", "total": 1500, "line_items": [{"description": "Consultation Fee", "amount": 1000}, {"description": "CBC Test", "amount": 300}, {"description": "Dengue NS1 Test", "amount": 200}]}}
  ]
}
EOF
```

**Show:**
- Decision: APPROVED
- Approved amount: ₹1,350 (10% co-pay on ₹1,500)
- Confidence: 95%
- Walk through each trace step:
  1. Gatekeeper PASSED — member verified, docs present
  2. Extractor PASSED — fields extracted
  3. Fraud PASSED — no signals
  4. Policy PASSED — co-pay calculated
  5. DecisionAgent — final APPROVED
- Switch to **Ops Review** tab to show claim in history

---

## Segment 4: Technical Decisions (2–3 min)

### Decision I'm Proud Of: Policy-as-Data with Ordered Rule Evaluation

- All rules from `policy_terms.json` — no hardcoded limits or exclusions
- Deliberate evaluation order: exclusions → waiting period → pre-auth → category-specific (dental) → per-claim limit → financial
- Explain TC007 vs TC008: same ₹ amount range but different rejection reasons depending on check order
- Network discount before co-pay (TC010) encoded in policy config

### What I'd Change With More Time

1. **Persistent claim store** (PostgreSQL) instead of in-memory dict
2. **Async job queue** for LLM extraction — don't block HTTP request
3. **Dedicated document classifier model** fine-tuned on Indian medical doc formats
4. **Pre-auth integration** — API to verify pre-auth reference numbers against insurer
5. **Human-in-the-loop UI** for MANUAL_REVIEW claims with approve/reject actions

---

## Segment 5: Bonus — Component Failure (1 min)

Run TC011 with `simulate_component_failure: true` — show APPROVED with degraded confidence, component_failures listed, manual_review_recommended flag.

---

## Recording Tips

- Use screen recording with browser + terminal side by side
- Expand trace steps slowly so reviewers can read them
- Total target: 8–12 minutes
