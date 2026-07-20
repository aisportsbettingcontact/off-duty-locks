# Fixtures

Rules for every fixture in this tree:

1. **No secrets, ever.** No cookies, no `Authorization` headers, no tokens, no
   full request-header dumps, no private infrastructure values. Sanitized
   captures keep only: URL, query parameters, response body, response status,
   and the response `Date` header.
2. **Provenance is mandatory.** Every JSON fixture carries a top-level
   `_provenance` object: `{"synthetic": true|false, "capturedAtUtc": ...,
   "describedBy": "docs/source-contract.md", "notes": ...}`. Synthetic fixtures
   (schema-accurate but not captured live) MUST say `"synthetic": true`.
3. `sanitized/` — source-contract fixtures (Subagent 1).
4. `expected_teams/` — versioned expected-team sets (Subagent 3).
5. `adversarial/` — malformed/hostile payloads for QA challenges (Subagent 5).
