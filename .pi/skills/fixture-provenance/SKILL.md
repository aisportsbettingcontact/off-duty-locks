---
name: fixture-provenance
description: Use when creating, editing, or regenerating anything under fixtures/ — provenance object rules, the synthetic flag, secret hygiene, and the adversarial-fixture generator
---

# Fixture provenance law

Rules for every fixture (fixtures/README.md is authoritative):

1. **No secrets, ever.** No cookies, Authorization headers, tokens, full
   request-header dumps, or private infrastructure values. Sanitized captures
   keep only: URL, query parameters, response body, response status, and the
   response Date header.
2. **Provenance is mandatory.** Every JSON fixture carries a top-level
   `_provenance` object: `{"synthetic": true|false, "capturedAtUtc": ...,
   "describedBy": "docs/source-contract.md", "notes": ...}`. Schema-accurate
   fixtures that were NOT captured live MUST say `"synthetic": true` — the flag
   is an honesty contract, not metadata.
3. Tree layout: `sanitized/` source-contract fixtures · `expected_teams/`
   versioned expected-team sets · `adversarial/` malformed/hostile payloads ·
   `betting/` betting-feed captures.

## Regeneration

- Adversarial payloads are generated, not hand-edited:
  `python3 qa/gen_adversarial_fixtures.py` (writes into `fixtures/adversarial/`).
- Never fabricate a "captured" fixture. If you cannot capture live (sandbox
  blocks *.wnba.com), build it synthetic and label it synthetic.
- After any fixture change run `python -m pytest -q` — the suite pins fixture
  shape — and `python3 qa/verify.py --repo-root .` (secret sweep covers fixtures).
