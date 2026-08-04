# Compliance — stats.wnba.com Access Policy

Owner: Subagent 1. Applies to every component that touches the live source:
`scripts/capture_live_contract.py`, the extractor (`http_client.py` /
`extractor.py`), and the GitHub Actions `live-smoke` workflow.

## 1. robots.txt / Terms-of-Service review status

**Status: DRAFT — reviewed 2026-08-04, PENDING OWNER SIGN-OFF; extract
schedule remains parked until signed.**

The technical review below was performed on 2026-08-04 from a residential
IP — the same access mode any future self-hosted runner would use — with a
normal desktop-browser User-Agent, exactly two HTTPS GET requests, no
retries, and no redirects followed (none were issued). This replaces the
previous UNVERIFIED status (the development sandbox blocked `*.wnba.com`, so
nothing could be checked from there). Owner sign-off is a legal/business
judgment on the findings below; it is not implied by this review, and the
extract schedule stays parked until it is recorded here.

| # | URL | Result 2026-08-04 |
|---|---|---|
| 1 | `https://stats.wnba.com/robots.txt` | HTTP 200 — findings below |
| 2 | `https://www.wnba.com/robots.txt` | NOT fetched (two-request budget) — still pending |
| 3 | `https://www.wnba.com/terms-of-use` | HTTP 200, no redirect — findings below |
| 4 | `https://www.nba.com/termsofuse` | NOT fetched (two-request budget) — still pending |
| 5 | `https://www.wnba.com/privacy-policy` | NOT fetched (two-request budget) — still pending |

**robots.txt findings (stats.wnba.com).** Verbatim and complete (58 bytes;
`Last-Modified: Mon, 29 Jun 2026 15:03:58 GMT`):

```text
Sitemap: https://stats.wnba.com/sitemap.xml
User-agent: *
```

The `User-agent: *` record carries **no `Disallow` rules and no
`Crawl-delay`** — under the robots exclusion protocol an empty record permits
all paths, including `/stats/*`. robots.txt is not the constraint here.

**Terms of Use findings (www.wnba.com/terms-of-use).** The document has no
numbered sections; citations use its heading names. Scope: the preamble
applies the terms to "the digital platforms of the Women's National
Basketball Association ('WNBA') and each of their respective teams (together,
the 'League'), including League websites (including, but not limited to
wnba.com), apps (e.g., mobile apps, tablet apps), and online content
offerings (collectively, the 'Services')" — stats.wnba.com is a League
website, so these terms govern the endpoints we poll. The terms "may be
amended or modified, or new conditions may be imposed, at any time", so this
review dates itself.

No clause mentions robots, crawlers, scraping, spidering, harvesting, or
data mining — there is no automated-access prohibition to quote. The
operative restrictions are on *use* of the data:

- **"OWNERSHIP AND USE RESTRICTIONS":** "No Basketball Content from the
  Services may be reproduced, republished, uploaded, posted, transmitted,
  distributed, copied, publicly displayed or otherwise used except as
  provided in these Terms of Use without the written permission of the
  Operator" — and "Basketball Content" expressly includes statistics.
  Downloads are licensed "only for your personal, noncommercial use";
  using materials "for public or commercial purposes on any other websites"
  requires written permission.
- **"NBA STATISTICS"** (directly on point for this pipeline; quoting the
  clauses that touch us): "(ii) the NBA Statistics may only be used,
  displayed, or published for legitimate news reporting or private,
  non-commercial purposes; ... (iv) the NBA Statistics may not be used in
  connection with any gambling activity (including legal gambling
  activity); ... (vii) the NBA Statistics may not be used in connection with
  any website, product, or service that features a database (in any medium
  or format) of comprehensive, regularly updated statistics from League ...
  games, competitions, or events without the Operator's express prior
  consent."

**Honest assessment.** The access-mode question robots.txt answers comes
back clean: nothing disallows automated retrieval of `/stats/*`, and our
request budget (section 2) sits far below any plausible burden threshold.
The ToS is the real constraint, and it cuts against this project's purpose,
not its polling mechanics: clause (iv) prohibits using WNBA statistics "in
connection with any gambling activity (including legal gambling activity)",
clause (ii) limits use to news reporting or private non-commercial purposes,
and clause (vii) reaches exactly what the serving tables are — a regularly
updated statistics database — absent "express prior consent". A
betting-research site publishing these numbers is not a defensible fit for
(ii), (iv), or (vii) as written. Per the review rule below this is a
conflict a human must resolve: the schedule stays parked, the section-4
internal-only restriction stays in force, and the revival path (self-hosted
residential runner, docs/deployment.md) is a technical plan only — it does
not cure a use-restriction problem. Licensed data feeds are the alternative
to evaluate if this data is to appear in a public or commercial product.

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
