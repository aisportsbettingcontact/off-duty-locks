# Source Contract — stats.wnba.com Traditional Team Statistics

Owner: Subagent 1 (source discovery & contract).
Companion docs: `docs/data-contract.md` (canonical pipeline contract, frozen),
`docs/compliance.md` (robots/ToS/frequency policy).
Verifier: `scripts/capture_live_contract.py` (run in the GitHub Actions
`live-smoke` workflow — see section 6).

## Evidence levels

Every claim in this document carries one of these labels:

| Label | Meaning |
|---|---|
| **[DPK]** | documented-platform-knowledge (pending live verification): stats.wnba.com runs the same stats platform as stats.nba.com (same `/stats/*` API family; the WNBA is `LeagueID=10`). The claim is derived from well-documented behavior of that shared platform and has **not** yet been confirmed against the live WNBA host. |
| **[LV]** | live-verified: confirmed by a sanitized capture produced by `scripts/capture_live_contract.py` (`fixtures/sanitized/live_capture_<date>.json`, `_provenance.synthetic=false`). Cite the capture date when flipping a claim to this level. |
| **[SANDBOX]** | verified inside this development sandbox only (e.g. facts about the sandbox network policy), not about the live source. |

**Current status: no claim is [LV] yet.** [SANDBOX] The development sandbox's
egress policy blocks `*.wnba.com` (proxy 403 / connection timeouts), so every
source-behavior claim below is [DPK] until the live-smoke workflow runs the
capture script from an environment with open network access. Section 7 is the
claim registry the script checks one-by-one.

## 1. Page → endpoint relationship

Target page:

```
https://stats.wnba.com/teams/traditional/?Season=2026&SeasonType=Regular%20Season&LastNGames=7&sort=TEAM_NAME&dir=1
```

- **[DPK]** The page is a client-rendered React application. The table is not
  present in the initial HTML; the browser issues an XHR **GET** to the
  structured stats endpoint and renders the JSON client-side:

  ```
  GET https://stats.wnba.com/stats/leaguedashteamstats?<query params, section 2>
  ```

- **[DPK]** The endpoint returns a JSON envelope:

  ```json
  {
    "resource": "leaguedashteamstats",
    "parameters": { "...": "each request parameter echoed back ..." },
    "resultSets": [
      { "name": "LeagueDashTeamStats", "headers": ["TEAM_ID", "..."], "rowSet": [["..."]] }
    ]
  }
  ```

  `resultSets` is an array with exactly one entry for this resource;
  `rowSet` is an array of arrays positionally aligned with `headers`.

- **[DPK]** Page-filter → API-parameter mapping:

  | Page query param | API param | Value for our dataset |
  |---|---|---|
  | `Season=2026` | `Season` | `2026` (WNBA seasons are single calendar years, unlike NBA `2025-26`) |
  | `SeasonType=Regular%20Season` | `SeasonType` | `Regular Season` |
  | `LastNGames=7` | `LastNGames` | `7` |
  | measure tab "Traditional" | `MeasureType` | `Base` |
  | per-mode toggle (page default) | `PerMode` | `PerGame` |
  | `sort=TEAM_NAME` | — | **no API equivalent** |
  | `dir=1` | — | **no API equivalent** |

- **[DPK]** **`sort` / `dir` are client-side only.** The API accepts no
  team-name sort parameter for this resource and does not return `rowSet`
  ordered by `TEAM_NAME` (ordering is by internal team id / unspecified).
  Consequence for the pipeline: the normalizer performs the deterministic sort
  itself (case-insensitive `team_name` ascending, `team_id` tiebreak — see
  `docs/data-contract.md`); sorting is presentation and is excluded from the
  extraction key. The synthetic fixture
  `fixtures/sanitized/leaguedashteamstats_2026_lastn7.json` deliberately ships
  its rows in TEAM_ID order (not name order) so no consumer can accidentally
  depend on source ordering.

## 2. Full request-parameter map

- **[DPK]** The endpoint requires the **full** parameter set below on every
  request; the platform validates presence, not just values. "Empty" means the
  parameter must be present with an empty-string value.

  | Param | Value (our dataset) | Notes |
  |---|---|---|
  | `MeasureType` | `Base` | "Traditional" stats |
  | `PerMode` | `PerGame` | page default; `Totals` also valid |
  | `PlusMinus` | `N` | |
  | `PaceAdjust` | `N` | |
  | `Rank` | `N` | `*_RANK` columns are returned regardless (section 4) |
  | `LeagueID` | `10` | WNBA (`00` = NBA, `20` = G League) |
  | `Season` | `2026` | single-year format for WNBA |
  | `SeasonType` | `Regular Season` | |
  | `PORound` | `0` | |
  | `Month` | `0` | 0 = all |
  | `OpponentTeamID` | `0` | 0 = all |
  | `TeamID` | `0` | 0 = all teams |
  | `Period` | `0` | 0 = full game |
  | `LastNGames` | `7` | 0 = all games; our page filter sets 7 |
  | `TwoWay` | `0` | |
  | `Outcome`, `Location`, `SeasonSegment`, `DateFrom`, `DateTo`, `VsConference`, `VsDivision`, `Conference`, `Division`, `GameSegment`, `ShotClockRange`, `GameScope`, `PlayerExperience`, `PlayerPosition`, `StarterBench` | *(empty string)* | must be present but empty |

- **[DPK]** **Missing-parameter behavior:** omitting a required parameter
  (e.g. `PerMode`, `SeasonType`, `MeasureType`, or one of the required
  empty-string parameters) returns **HTTP 400** with a short **plain-text**
  body naming the parameter (e.g. `PerMode is required`), *not* a JSON
  envelope. Extra/unknown parameters are ignored. Verified by the optional
  `--probe-400` step of the capture script (claim C06).

- **[DPK]** **Parameter echo:** the response `parameters` object echoes every
  request parameter, with two normalizations: numeric-ish parameters
  (`Month`, `OpponentTeamID`, `TeamID`, `Period`, `PORound`, `LastNGames`,
  `TwoWay`) come back as JSON **numbers**, and empty-string parameters come
  back as **`null`**. The pipeline uses the echo as a cheap server-side
  confirmation that the intended filters were applied (claims C03/C05).

## 3. Required request headers (public — no auth)

- **[DPK]** The endpoint is public and requires **no authentication: no
  cookies, no `Authorization` header, no API key, no session token. None are
  required and none may be captured, stored, or replayed** (see
  `docs/compliance.md` and `fixtures/README.md`).

- **[DPK]** The platform's edge (Akamai) filters obviously non-browser
  traffic. Requests should send these ordinary, public browser headers:

  | Header | Value |
  |---|---|
  | `User-Agent` | a current desktop browser string (the capture script pins one Chrome UA) |
  | `Accept` | `application/json, text/plain, */*` |
  | `Accept-Language` | `en-US,en;q=0.9` |
  | `Referer` | `https://stats.wnba.com/` |
  | `Origin` | `https://stats.wnba.com` |

- **[DPK]** Requests missing `User-Agent`/`Referer` may **hang until timeout**
  or be rejected rather than returning a clean error — treat client timeouts
  as `UPSTREAM_UNAVAILABLE`, never as empty data.

- **[DPK]** The platform's own web client also sends two **static, public,
  non-credential** hint headers: `x-nba-stats-origin: stats` and
  `x-nba-stats-token: true` (a literal constant string, not an auth token).
  They are believed unnecessary today; the capture script only sends them when
  `--compat-headers` is passed, and they must never be treated as secrets.

- **[DPK]** Even with correct headers, the edge may return **HTTP 403** for
  datacenter/cloud egress IPs. Policy: hard stop, no retry, no
  identifier rotation (claim C16; `docs/compliance.md`).

## 4. Response schema

### 4.1 `LeagueDashTeamStats` result set

- **[DPK]** `headers` begins with these 28 documented columns, in this order:

  ```
  TEAM_ID, TEAM_NAME, GP, W, L, W_PCT, MIN,
  FGM, FGA, FG_PCT, FG3M, FG3A, FG3_PCT, FTM, FTA, FT_PCT,
  OREB, DREB, REB, AST, TOV, STL, BLK, BLKA, PF, PFD, PTS, PLUS_MINUS
  ```

  followed by rank columns (`GP_RANK` … `PLUS_MINUS_RANK`, one per stat
  column, returned **even when `Rank=N`**) and possibly `CFID` / `CFPARAMS`
  (internal platform cross-filter fields). The pipeline maps the 28 documented
  columns via `contract.SOURCE_HEADER_MAP` and preserves everything else
  verbatim in `extras` — official fields are never discarded. Consumers must
  select columns **by header name, never by position**, because the tail
  columns can change (claims C08/C09).

- **[DPK]** Types and units:

  | Columns | JSON type | Unit (PerMode=PerGame) |
  |---|---|---|
  | `TEAM_ID` | number (integer) | 10-digit platform franchise id, `1611661XXX` for WNBA |
  | `TEAM_NAME` | string | full display name (e.g. `Golden State Valkyries`) |
  | `GP`, `W`, `L` | number (integer) | games; **not** per-game averages. With `LastNGames=7`, `GP <= 7` and `W + L == GP` |
  | `W_PCT` | number | fraction 0.0–1.0 = `round(W/GP, 3)` |
  | `MIN` | number | team minutes per game ≈ 40.0 (40-minute WNBA games; overtime raises it) |
  | `FGM/FGA/FG3M/FG3A/FTM/FTA`, `OREB/DREB/REB`, `AST/TOV/STL/BLK/BLKA/PF/PFD/PTS` | number | per-game averages **rounded to 1 decimal** by the source |
  | `FG_PCT`, `FG3_PCT`, `FT_PCT` | number | **fraction 0.0–1.0, rounded to 3 decimals** (never 0–100) |
  | `PLUS_MINUS` | number | per-game point differential; the only column that may be negative |
  | `*_RANK` | number (integer) | 1 = best; competition ranking; lower-is-better for `L`, `TOV`, `PF`, `BLKA` |
  | `CFID` | number | internal; preserve in `extras` |
  | `CFPARAMS` | string | internal; preserve in `extras` |

  With `PerMode=Totals`, counting columns become season/window totals
  (integers) and rebounds add exactly; the percentage columns keep the same
  0–1 fraction scale. Rounding drives the validation tolerances pinned in
  `docs/data-contract.md` (±0.02 recomputed pct, ±0.15 REB in PerGame).

- **[DPK]** `null` can appear in stat cells (e.g. `FT_PCT` with `FTA=0`).
  Nulls are preserved as missing (`None`) — **never coerced to 0**.

### 4.2 Pagination, caching, empty seasons

- **[DPK]** **No pagination.** One request returns the complete result set for
  all active teams (15 rows for the 2026 season). There are no page/offset
  parameters, no continuation tokens (claim C12).
- **[DPK]** **Caching:** responses pass through the platform CDN and may be
  cached for a short period (seconds–minutes); the response `Date` header
  reflects the edge's response time and is recorded as
  `source_observed_at_utc` (claim C15). Do not assume second-level freshness;
  the pipeline's daily cadence makes this irrelevant.
- **[DPK]** **Empty season / no matching games:** HTTP **200** with the full
  envelope, intact `headers`, and an **empty `rowSet`** — not an error status.
  Synthetic replica: `fixtures/sanitized/leaguedashteamstats_offseason_empty.json`.
  Pipeline rule: an empty `rowSet` is a *missing dataset* (expected-team
  coverage fails) — never a valid zero-stat snapshot (claim C13; verifiable
  live only during the offseason).

### 4.3 Authoritative team set: `commonteamyears`

- **[DPK]** `GET https://stats.wnba.com/stats/commonteamyears?LeagueID=10`
  returns result set `TeamYears` with headers
  `LEAGUE_ID, TEAM_ID, MIN_YEAR, MAX_YEAR, ABBREVIATION`.
  `LEAGUE_ID`, `MIN_YEAR`, `MAX_YEAR` are **strings** on the wire; `TEAM_ID`
  is a number. It lists **every franchise ever**, including defunct ones —
  the active set for season *S* is `rows where int(MAX_YEAR) >= S`.
  The expected-team set is *always* resolved from this endpoint (or the
  versioned fallback fixture), **never hardcoded** (claim C14).
- **[DPK]** `commonteamyears` carries **no display names** — only IDs and
  abbreviations. Canonical team names come from the `TEAM_NAME` column of
  `leaguedashteamstats` itself (join on `TEAM_ID`); the
  `franchisehistory?LeagueID=10` resource is an alternative name source if an
  independent one is ever needed.
- **[SANDBOX]** The 12 legacy franchise IDs in
  `fixtures/teams-2026-reference.json` are the well-known platform IDs; the
  IDs for **Golden State Valkyries (2025), Toronto Tempo (2026), and Portland
  Fire (2026) are placeholders pending live verification** — the capture
  script diffs them against the live response and flags mismatches loudly
  (claim C11).

### 4.4 Error behaviors

| Condition | Observable behavior | Pipeline handling |
|---|---|---|
| Missing required param **[DPK]** | HTTP 400, short plain-text body naming the parameter; no JSON envelope | `CONFIG_ERROR` (our bug) — do not retry |
| Edge block **[DPK]** | HTTP 403, HTML body (Akamai reference) | `UPSTREAM_UNAVAILABLE`; hard stop, no retry, no bypass |
| Wrong path **[DPK]** | HTTP 404, HTML body | `CONFIG_ERROR` |
| Rate limited **[DPK]** | HTTP 429, may carry `Retry-After` seconds | honor `Retry-After` fully; at most one retry within budget; then `UPSTREAM_UNAVAILABLE` (claim C17) |
| Platform error **[DPK]** | HTTP 5xx, HTML or empty body | bounded retry; then `UPSTREAM_UNAVAILABLE` |
| Missing browser headers **[DPK]** | connection hangs until client timeout | `UPSTREAM_UNAVAILABLE` |
| Malformed/truncated JSON **[DPK]** | 200 with unparseable body (edge truncation) | `UPSTREAM_UNAVAILABLE` — **never** an empty dataset |

## 5. Access & rate-limit assessment; extraction method

- **[DPK]** Access: public, anonymous, no quota accounting, no published rate
  limit. The practical constraints are the Akamai edge (403 on bot-like
  traffic) and unpublished throttling (429). Our own policy is far below any
  plausible threshold: one scheduled run per day, ≤5 requests per run, ≥3 s
  spacing (`docs/compliance.md`).

- **Recommended method — structured JSON endpoint (primary and only
  implemented path):**
  - it is the exact data source the page itself renders from — zero
    transformation distance from "what the page shows" **[DPK]**;
  - typed values (numbers stay numbers; 0–1 fractions preserved), stable
    header names, machine-checkable parameter echo;
  - complete dataset in one request — minimal load on the source;
  - schema drift is detectable (header-name diffing) rather than silent.

- **Rendered-table scraping — explicitly rejected; documented fallback-only:**
  - the table does not exist in the server HTML; scraping would require a
    headless browser (a dependency this project forbids) **[DPK]**;
  - the DOM is a styling surface, not a contract: column order, formatting
    (e.g. percentages re-scaled to 0–100 for display), and markup change
    without notice;
  - it consumes strictly more source resources (page + JS + fonts + the same
    XHR underneath) for strictly worse data;
  - it would silently break on UI redesigns — the JSON endpoint fails loudly
    instead. If the `/stats/*` API family were ever retired, scraping would be
    re-evaluated as a *new discovery project*, not toggled on as a fallback.

## 6. Live verification procedure

Sandbox rule **[SANDBOX]**: `*.wnba.com` is unreachable from the development
environment; nothing here may be "verified" from the sandbox. Verification
runs in GitHub Actions (open egress) via the `live-smoke` workflow (owned by
Subagent 4), which executes:

```
python scripts/capture_live_contract.py            # add --probe-400 to also verify C06
```

The script (stdlib + `requests` only):

1. validates arguments and the pinned reference file; `--dry-run` stops here
   after printing the full request plan (no network);
2. performs at most **5** HTTP requests with **≥3 s** spacing: (1)
   `leaguedashteamstats` with the exact page-equivalent parameters of
   section 2, (2) `commonteamyears?LeagueID=10`, (3) optionally the
   `--probe-400` missing-parameter probe; honors `Retry-After` on 429;
   **hard-aborts on any 403** (exit 3) without retrying;
3. sanitizes each capture to *endpoint, URL, query params, HTTP status,
   response `Date` header, body* — nothing else — and writes
   `fixtures/sanitized/live_capture_<UTC date>.json` with
   `_provenance.synthetic=false`;
4. diffs the observation against the claim registry (section 7), including
   verifying every expansion-team ID against
   `fixtures/teams-2026-reference.json`, and prints a per-claim
   PASS/FAIL/INFO/SKIP report (also embedded in the capture file as
   `claimReport`);
5. exits 0 only if no claim check failed.

After a successful run: flip the verified claims' labels from [DPK] to [LV]
with the capture date, and if C11 reported placeholder-ID mismatches, update
`fixtures/teams-2026-reference.json` (orchestrator-owned) and regenerate the
synthetic fixtures.

## 7. Claim registry (checked by `capture_live_contract.py`)

| ID | Claim (summary) | Evidence | Script check |
|---|---|---|---|
| C01 | Page data comes from GET `/stats/leaguedashteamstats` returning the `resource/parameters/resultSets` JSON envelope | [DPK] | status 200 + envelope keys |
| C02 | `resultSets[0].name == "LeagueDashTeamStats"` | [DPK] | direct compare |
| C03 | Page filters map to `Season`/`SeasonType`/`LastNGames` and are echoed back applied | [DPK] | echo equals sent values |
| C04 | API does not sort by TEAM_NAME; page `sort`/`dir` are client-side | [DPK] | rowSet name-order inspection (PASS if unsorted; INFO if coincidentally sorted) |
| C05 | Full 30-parameter set accepted and echoed (empty→null, numeric→number) | [DPK] | every sent param present in echo |
| C06 | Missing required param ⇒ HTTP 400 + plain-text `<param> is required` | [DPK] | `--probe-400` step (SKIP otherwise) |
| C07 | No cookies/tokens required — public headers alone get 200 | [DPK] | request sends none; 200 observed |
| C08 | The 28 documented columns are present (expected as header prefix) | [DPK] | header diff |
| C09 | Extra columns limited to `*_RANK` + `CFID`/`CFPARAMS` families | [DPK] | INFO listing of extras |
| C10 | Percentages are fractions 0.0–1.0 | [DPK] | range check on all pct cells |
| C11 | TEAM_IDs are 10-digit platform ids matching the pinned reference set; expansion-team placeholder IDs confirmed or flagged | [DPK]/[SANDBOX] | live-vs-reference diff |
| C12 | Single complete result set; no pagination | [DPK] | one resultSet, uniform row width |
| C13 | Empty season ⇒ 200, intact headers, empty rowSet | [DPK] | PASS only if observed (offseason); SKIP in-season |
| C14 | `commonteamyears` = `TeamYears` set with documented headers; active teams = `MAX_YEAR >= season`; includes defunct franchises | [DPK] | schema + active-set diff vs reference |
| C15 | Response carries a `Date` header usable as `source_observed_at_utc` | [DPK] | header presence |
| C16 | Edge (Akamai) 403s non-browser clients / datacenter IPs | [DPK] | not provoked; script hard-aborts if encountered (INFO otherwise) |
| C17 | 429 responses may carry `Retry-After`, which we honor fully | [DPK] | opportunistic (SKIP unless a 429 occurs) |

## 8. Fixtures delivered under this contract

| File | What it replicates | Provenance |
|---|---|---|
| `fixtures/sanitized/leaguedashteamstats_2026_lastn7.json` | Full envelope for our exact dataset; 15 reference teams verbatim; internally consistent (W+L=GP, W_PCT=round(W/GP,3), PCT=round(made/att,3), REB=OREB+DREB, PTS=2·FGM+FG3M+FTM, GP≤7, mixed-sign PLUS_MINUS); rows deliberately NOT name-sorted; `*_RANK`/`CFID`/`CFPARAMS` tail columns included | synthetic |
| `fixtures/sanitized/commonteamyears_2026.json` | Authoritative team-set endpoint: 15 active teams + 2 defunct franchises (`MAX_YEAR` 2008/2009) forcing `MAX_YEAR >= season` filtering | synthetic |
| `fixtures/sanitized/leaguedashteamstats_offseason_empty.json` | Empty-season behavior: intact headers, empty rowSet, HTTP-200 semantics | synthetic |
| `fixtures/sanitized/live_capture_<date>.json` | Sanitized live captures + claim report | live (`synthetic=false`), produced only by the capture script |

All synthetic fixtures carry `_provenance.synthetic=true` and use the pinned
reference team set exactly; none contain cookies, tokens, or header dumps.
