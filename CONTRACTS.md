# Component Contracts

Precise interfaces for each significant component. Another engineer could reimplement any component from these contracts alone.

---

## Shared Types

### `ClaimSubmission`
```
Input:
  member_id: string
  policy_id: string (default "PLUM_GHI_2024")
  claim_category: enum [CONSULTATION, DIAGNOSTIC, PHARMACY, DENTAL, VISION, ALTERNATIVE_MEDICINE]
  treatment_date: string (ISO date)
  claimed_amount: float
  documents: DocumentInput[]
  ytd_claims_amount: float (default 0)
  claims_history: ClaimHistoryEntry[] (default [])
  simulate_component_failure: bool (default false)
  hospital_name: string | null

Errors: ValidationError if required fields missing or invalid enum values
```

### `DocumentInput`
```
Input:
  file_id: string (required)
  file_name: string | null
  actual_type: string | null  — e.g. PRESCRIPTION, HOSPITAL_BILL
  quality: string | null       — "GOOD" | "UNREADABLE"
  patient_name_on_doc: string | null
  content: dict | null         — pre-extracted structured data
  file_path: string | null     — path to uploaded file on disk
```

### `TraceStep`
```
Output:
  step_id: string
  component: string
  action: string
  status: enum [PASSED, FAILED, SKIPPED, DEGRADED, WARNING]
  details: dict
  message: string
  timestamp: ISO datetime string
```

### `ClaimDecision`
```
Output:
  claim_id: string
  decision: enum [APPROVED, PARTIAL, REJECTED, MANUAL_REVIEW] | null
  approved_amount: float
  reason: string
  confidence_score: float (0.0–1.0)
  rejection_reasons: string[]
  line_item_decisions: LineItemDecision[]
  trace: TraceStep[]
  member_message: string
  ops_notes: string
  component_failures: string[]
  manual_review_recommended: bool
  financial_breakdown: dict
  blocked: bool
  block_reason: string
```

---

## DocumentGatekeeperAgent

**Purpose:** Early validation before any processing. Stops pipeline on document problems.

### `verify_documents(claim: ClaimSubmission) -> DocumentGatekeeperResult`

**Input:** Full claim submission with documents.

**Output:**
```
DocumentGatekeeperResult:
  blocked: bool
  block_reason: string  — WRONG_DOCUMENT_TYPE | MISSING_DOCUMENTS | UNREADABLE_DOCUMENT | PATIENT_MISMATCH | INVALID_MEMBER
  member_message: string  — specific, actionable message for the member
  trace_steps: TraceStep[]
  classified_docs: list[tuple[DocumentInput, str]]  — (doc, detected_type)
```

**Errors:** None raised — all failures returned as `blocked=True`.

**Behavior:**
1. Validate member exists in policy roster
2. Classify each document by `actual_type`, filename, or UNKNOWN
3. Check required document types for claim category against `policy_terms.json → document_requirements`
4. If wrong types uploaded (e.g., duplicate prescriptions), produce message naming uploaded vs required types
5. Check document quality — UNREADABLE stops pipeline with re-upload instruction
6. Check patient name consistency across documents

---

## DocumentExtractorAgent

**Purpose:** Extract structured medical information from documents.

### `extract_documents(classified_docs, simulate_failure=False) -> ExtractionResult`

**Input:**
```
classified_docs: list[tuple[DocumentInput, str]]
simulate_failure: bool
```

**Output:**
```
ExtractionResult:
  extracted: dict[file_id → dict]  — per-document extracted fields
  trace_steps: TraceStep[]
  failed: bool
  failure_reason: string
```

**Extracted fields (per document):**
```
patient_name, doctor_name, doctor_registration, diagnosis, treatment,
date, line_items[], total, hospital_name, tests_ordered[], medicines[],
confidence (float), extraction_source, document_type
```

**Errors:** Individual document failures captured as DEGRADED trace steps; never raises.

**Behavior:**
1. If `simulate_failure=True`, skip extraction, use fallback from doc.content
2. If doc.content provided, use directly (confidence 0.95)
3. If file_path + OPENAI_API_KEY, call GPT-4o-mini vision
4. Otherwise metadata-only extraction (confidence 0.4–0.5)

### `aggregate_extraction(extracted) -> dict`

**Output:** Merged view with `primary_diagnosis`, `primary_hospital`, `bill_total`, `line_items[]`, `min_confidence`.

---

## FraudDetectionAgent

**Purpose:** Flag suspicious claim patterns for manual review.

### `detect_fraud(claim: ClaimSubmission) -> FraudDetectionResult`

**Input:** Claim with optional `claims_history`.

**Output:**
```
FraudDetectionResult:
  fraud_score: float (0.0–1.0)
  signals: string[]
  manual_review_required: bool
  trace_steps: TraceStep[]
```

**Thresholds (from policy):**
- `same_day_claims_limit`: 2 — flag if prior same-day claims exceed this
- `fraud_score_manual_review_threshold`: 0.80
- `auto_manual_review_above`: ₹25,000

**Errors:** None raised.

---

## PolicyEvaluatorAgent

**Purpose:** Apply policy rules from JSON and compute financial outcome.

### `evaluate_policy(claim, aggregated, has_pre_auth=False) -> PolicyEvaluationResult`

**Input:**
```
claim: ClaimSubmission
aggregated: dict  — from aggregate_extraction()
has_pre_auth: bool (default false)
```

**Output:**
```
PolicyEvaluationResult:
  decision: DecisionType | null
  approved_amount: float
  rejection_reasons: string[]  — WAITING_PERIOD | EXCLUDED_CONDITION | PRE_AUTH_MISSING | PER_CLAIM_EXCEEDED
  line_item_decisions: LineItemDecision[]
  trace_steps: TraceStep[]
  financial_breakdown: dict
  reason: string
  eligibility_date: string | null
```

**Errors:** None raised — rejections returned as decision=REJECTED.

**Financial calculation order:**
1. Network discount (% from category config, if network hospital)
2. Co-pay (% on post-discount amount)

---

## DecisionAgent (within Orchestrator)

**Purpose:** Assemble final decision, compute confidence, route manual review.

### Confidence Formula
```
base = extraction min_confidence
if component_failures: base *= 0.65
if REJECTED: base = max(base, 0.90)
if MANUAL_REVIEW: base = min(base, 0.75)
if APPROVED (normal): base = max(base, 0.85)
if simulate_failure + APPROVED: base = min(base, 0.72)
```

---

## PipelineOrchestrator

**Purpose:** Coordinate all agents in sequence.

### `process_claim(claim: ClaimSubmission) -> ClaimResponse`

**Input:** Validated ClaimSubmission.

**Output:**
```
ClaimResponse:
  success: bool (always true — failures are graceful)
  claim_id: string
  result: ClaimDecision
  processing_time_ms: float
```

**Sequence:**
1. DocumentGatekeeper → if blocked, return immediately
2. DocumentExtractor → continue even on failure
3. FraudDetection
4. PolicyEvaluator
5. Decision assembly (manual review override if fraud flagged)
6. Return full trace

**Errors:** Never raises HTTP 500 for component failures.

---

## API Endpoints

### `POST /api/claims/submit`
- Body: `ClaimSubmission` JSON
- Response: `ClaimResponse`
- Errors: 422 validation error

### `POST /api/claims/submit-with-files`
- Form: member_id, claim_category, treatment_date, claimed_amount, files[]
- Response: `ClaimResponse`

### `GET /api/claims/{claim_id}`
- Response: `ClaimDecision`
- Errors: 404 if not found

### `GET /api/policy`
- Response: Full policy_terms.json contents

### `GET /api/document-requirements/{category}`
- Response: `{ required: string[], optional: string[] }`

---

## PolicyLoader Service

### `load_policy() -> dict`
- Reads and caches `data/policy_terms.json`
- Raises: FileNotFoundError if policy file missing

### Helper functions
| Function | Input | Output |
|----------|-------|--------|
| `get_member(id)` | member_id | member dict or None |
| `get_document_requirements(category)` | claim category | required/optional doc types |
| `is_network_hospital(name)` | hospital name | bool |
| `requires_pre_auth(test, amount)` | test name, amount | bool |
| `is_excluded_condition(diagnosis, treatment)` | strings | (bool, reason) |
| `is_dental_excluded(description)` | line item desc | (bool, reason) |
| `match_condition_to_waiting_period(diagnosis)` | diagnosis | condition key or None |
| `compute_eligibility_date(join_date, condition)` | dates | ISO date string |
