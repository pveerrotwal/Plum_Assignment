# Sample Documents Guide

See assignment package for full Indian medical document format reference. Key document types handled:

- **PRESCRIPTION** — doctor Rx with diagnosis, medicines, registration number
- **HOSPITAL_BILL** — clinic invoice with line items and total
- **PHARMACY_BILL** — medicine purchase receipt
- **LAB_REPORT** — diagnostic test results

The DocumentExtractorAgent uses GPT-4o-mini vision when `OPENAI_API_KEY` is set, with prompts designed around the field layouts described in the original assignment guide.
