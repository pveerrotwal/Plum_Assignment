# Architecture Document — Plum Claims Processing System

## Overview

This system automates health insurance claim processing through a **multi-agent pipeline** orchestrated by a central coordinator. Each agent has a single responsibility, explicit inputs/outputs, and contributes structured trace steps to an explainability log. Policy rules are loaded dynamically from `data/policy_terms.json` — no business logic is hardcoded.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         React UI (Vite)                              │
│   Submit Claim Form  │  Ops Review Dashboard  │  Decision Trace      │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ REST API
┌──────────────────────────────▼──────────────────────────────────────┐
│                      FastAPI Backend                                   │
│  POST /api/claims/submit  │  GET /api/claims/{id}  │  /api/policy   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                   Pipeline Orchestrator                              │
│  Coordinates agents, handles failures, assembles final decision      │
└──┬──────────┬──────────────┬──────────────┬──────────────┬──────────┘
   │          │              │              │              │
   ▼          ▼              ▼              ▼              ▼
 Gatekeeper  Extractor    Fraud Detector  Policy Eval   Decision Agent
 (early      (structured  (pattern       (rules from   (confidence,
  block)      OCR/LLM)    detection)     JSON)         final assembly)
```

## Design Principles

### 1. Fail Fast on Document Problems (Gatekeeper First)
The **DocumentGatekeeperAgent** runs before any expensive extraction or policy evaluation. Wrong documents, unreadable files, and patient mismatches are caught immediately with **specific, actionable messages** — never generic errors.

### 2. Policy as Data, Not Code
All coverage rules, waiting periods, exclusions, document requirements, and fraud thresholds are read from `policy_terms.json`. The **PolicyEvaluatorAgent** interprets this configuration at runtime. Adding a new OPD category or changing co-pay requires a JSON update, not a code deploy.

### 3. Explainability by Construction
Every agent emits `TraceStep` objects with component name, action, status (PASSED/FAILED/SKIPPED/DEGRADED/WARNING), details, and human-readable messages. The full trace is returned with every claim decision — ops can reconstruct exactly what happened.

### 4. Graceful Degradation
When a component fails (simulated in TC011, or real LLM timeouts), the pipeline continues with fallback data, records the failure in `component_failures`, lowers confidence, and flags `manual_review_recommended`. The system never crashes with a 500.

## Agent Responsibilities

| Agent | Responsibility | Stops Pipeline? |
|-------|---------------|-----------------|
| **DocumentGatekeeperAgent** | Member validation, doc type classification, requirement check, quality check, patient consistency | Yes — returns blocked response |
| **DocumentExtractorAgent** | Structured field extraction from documents (content/LLM/fallback) | No — degrades on failure |
| **FraudDetectionAgent** | Same-day claim patterns, high-value flags | No — routes to MANUAL_REVIEW |
| **PolicyEvaluatorAgent** | Exclusions, waiting periods, pre-auth, per-claim limits, financial calc | No — produces decision |
| **DecisionAgent** | Final decision assembly, confidence scoring | No |
| **PipelineOrchestrator** | Sequencing, error boundaries, response assembly | — |

## Policy Evaluation Order

The order of checks matters and was tuned against the 12 test cases:

1. **Exclusions** (diagnosis + line items, except dental → handled per-item)
2. **Waiting periods** (condition-specific, with eligibility date in message)
3. **Pre-authorization** (diagnostic high-value tests like MRI > ₹10K)
4. **Dental line-item evaluation** (partial approval for mixed covered/excluded procedures)
5. **Per-claim limit** (OPD categories only — CONSULTATION, PHARMACY, etc.)
6. **Financial calculation** (network discount **before** co-pay)

## AI Integration Strategy

| Use Case | Approach |
|----------|----------|
| Document classification | Filename heuristics + metadata; LLM vision when files uploaded with API key |
| Field extraction | Structured content for eval; OpenAI GPT-4o-mini vision for real uploads |
| Policy decisions | **Deterministic** rule engine — LLM not used for financial/policy decisions |
| Confidence scoring | Derived from extraction quality + component failures + decision type |

**Why not LLM for policy decisions?** Insurance adjudication requires reproducibility, auditability, and precise arithmetic. LLMs are used where they excel (messy document parsing) and avoided where determinism is required.

## Data Flow Example (TC004 — Full Approval)

1. Gatekeeper: EMP001 verified, PRESCRIPTION + HOSPITAL_BILL present → PASS
2. Extractor: Pulls diagnosis "Viral Fever", total ₹1,500, patient "Rajesh Kumar" → confidence 0.95
3. Fraud: No signals → PASS
4. Policy: No exclusions, no waiting period, within per-claim limit → 10% co-pay on ₹1,500 = **₹1,350 approved**
5. Decision: APPROVED, confidence 0.95, full trace returned

## What We Considered and Rejected

| Alternative | Why Rejected |
|-------------|--------------|
| Single monolithic LLM prompt for entire pipeline | No explainability, non-deterministic policy math, hard to test |
| Hardcoded policy rules in Python | Violates assignment requirement; brittle for policy changes |
| Blocking on extraction failure | TC011 requires continuing with degraded data |
| Generic "invalid documents" errors | Assignment explicitly requires specific actionable messages |
| Applying per-claim limit before pre-auth/exclusion checks | Wrong rejection reason for TC007, TC012 |

## Limitations

1. **Document classification without LLM** relies on filename heuristics — real-world uploads need `OPENAI_API_KEY` for vision extraction
2. **In-memory claim store** — no persistence; claims lost on restart
3. **No async job queue** — claims processed synchronously in request handler
4. **Single-tenant** — one policy file, no multi-policy routing
5. **Fraud detection** is rule-based only — no ML model for anomaly detection

## Scaling to 10x Load

| Component | Current | At 10x |
|-----------|---------|--------|
| API | Single uvicorn process | Horizontal pod autoscaling behind load balancer |
| Claim processing | Synchronous | Celery/SQS job queue with worker pool |
| Document storage | Local filesystem | S3 + pre-signed upload URLs |
| Policy config | File read + LRU cache | Redis cache with pub/sub invalidation on policy update |
| LLM extraction | Direct API call | Batched requests with rate limiting, circuit breaker, retry with backoff |
| Trace storage | In response body | Append-only event store (e.g., DynamoDB/PostgreSQL JSONB) for ops querying |
| Observability | Trace in response | OpenTelemetry spans per agent + structured logging |

## Technology Choices

- **FastAPI** — async-ready, automatic OpenAPI docs, Pydantic validation
- **Pydantic v2** — strict schemas for all agent contracts
- **React + Vite** — fast dev experience, lightweight production bundle
- **pytest** — parametrized tests for all 12 eval cases
