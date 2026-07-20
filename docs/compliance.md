# Compliance — stats.wnba.com Access Policy

Owner: Subagent 1. Applies to every component that touches the live source:
`scripts/capture_live_contract.py`, the extractor (`http_client.py` /
`extractor.py`), and the GitHub Actions `live-smoke` workflow.

## 1. robots.txt / Terms-of-Service review status

**Status: UNVERIFIED — cannot be checked from this development sandbox.**
The sandbox's egress policy blocks `*.wnba.com` (proxy 403 / timeouts), so no
robots.txt or ToS document has been fetched or reviewed yet. This is recorded
honestly rather than assumed.

The live-smoke workflow (open network) MUST check these exact URLs on its
first run and record the findings in this file before the pipeline is allowed
to run on a schedule:

| # | URL | What to record |
|---|---|---|
| 1 | `https://stats.wnba.com/robots.txt` | Any `Disallow` rules matching `/stats/` or `/teams/`; any `Crawl-delay` |
| 2 | `https://www.wnba.com/robots.txt` | Same, for the parent property |
| 3 | `https://www.wnba.com/terms-of-use` | Clauses on automated access, data collection, and data reuse |
| 4 | `https://www.nba.com/termsofuse` | The platform operator's terms (stats.wnba.com runs NBA Digital's shared stats platform; its terms commonly govern) |
| 5 | `https://www.wnba.com/privacy-policy` | Confirm no personal data is implicated (team aggregates only) |

Review rule: if any of the above disallows automated access to the `/stats/*`
endpoints, **the pipeline stops running until a human resolves the
conflict** — technical workarounds are not an option.

Note: robots.txt conventionally governs crawlers, and this pipeline is not a
crawler (one fixed endpoint, no link following); we nevertheless commit to
honoring `Disallow` and `Crawl-delay` rules that match our paths, as the
conservative reading.

## 2. Frequency & request-budget policy (hard commitments)

These limits are enforced in code (`scripts/capture_live_contract.py`
constants `MAX_REQUESTS` / `MIN_SPACING_SECONDS`; the extractor's `HttpConfig`
must adopt the same ceilings):

1. **One scheduled run per day.** The dataset is a daily-granularity rolling
   window (`LastNGames=7`); more frequent polling has no analytical value.
   Manual dispatches are for debugging only and follow the same in-run limits.
2. **Maximum 5 HTTP requests per run**, including retries and probes. The
   normal run needs 2 (team stats + team years).
3. **Minimum 3 seconds spacing** between consecutive requests.
4. **`Retry-After` is honored in full** on HTTP 429 — never truncated, never
   ignored. At most one retry per request within the run budget; then the run
   reports `UPSTREAM_UNAVAILABLE` and stops.
5. **Explicit blocking stops the run immediately.** HTTP 403 (edge/Akamai
   block) causes a hard abort: no retry, no User-Agent/IP rotation, no header
   experimentation in-run, no CAPTCHA solving, no proxying to evade controls.
   Repeated 403s across runs escalate to a human, not to more aggressive
   automation.
6. **No access-control, CAPTCHA, or rate-limit bypass of any kind, ever.**
7. **Timeouts are bounded** (connect ≈10 s, read ≈30 s) so a hung connection
   never turns into connection pileup.
8. **Failure never triggers hot-looping**: a failed run exits with its status
   code and waits for the next scheduled slot.

## 3. Identification & secrets policy

- Requests send ordinary public browser headers (User-Agent, Accept,
  Accept-Language, Referer, Origin) — see `docs/source-contract.md` section 3.
  A browser-type User-Agent is used because the platform edge rejects
  obviously non-browser clients; we accept the documented tension there and
  pair it with the stop-on-block policy above rather than any evasion.
- **No cookies, no `Authorization` headers, no tokens, no session replay —
  neither sent, nor captured, nor stored** in code, logs, fixtures, or docs
  (`fixtures/README.md` rule 1). Sanitized captures retain only: URL, query
  params, HTTP status, response body, and the response `Date` header.
- The static platform hint headers (`x-nba-stats-origin: stats`,
  `x-nba-stats-token: true`) are public constants, not credentials; they are
  used only if plain browser headers prove insufficient (`--compat-headers`).

## 4. Data-use notes

- **Content:** aggregated team statistics only — facts about professional
  games. No personal data, no user-generated content, no accounts.
- **Attribution & provenance:** every stored record carries
  `source: stats.wnba.com` plus the exact endpoint and fetch time; we never
  misrepresent the data's origin, and stale data is never presented as fresh.
- **Reuse caution:** raw sports facts are generally not copyrightable, but the
  WNBA/NBA Terms of Use are a contract that may restrict automated collection
  and commercial redistribution of content from their properties. Until the
  ToS review in section 1 is completed and recorded, treat the extracted data
  as **internal-only** (development, validation, monitoring). Any commercial
  or public redistribution — including use in betting-model products — needs
  an explicit human legal review against the recorded ToS findings, and
  official licensed data feeds should be evaluated for that purpose.
- **Storage discipline:** raw payloads are kept immutably for auditability
  (`data/raw/...`), pruned by retention policy, and contain nothing beyond the
  public JSON bodies.

## 5. Change management

- Any change to spacing, budget, schedule, headers, or endpoints requires an
  update to this file and to `docs/source-contract.md` in the same change.
- If the source introduces authentication, paywalls, or explicit bot terms,
  the pipeline halts (runs report `UPSTREAM_UNAVAILABLE`) until the compliance
  review is redone by a human.
