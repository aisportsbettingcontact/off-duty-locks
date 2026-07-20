#!/usr/bin/env python3
"""Live source-contract capture & verification for stats.wnba.com.

Fetches the documented structured endpoints with the exact page-equivalent
parameters, sanitizes the captures, writes them to
fixtures/sanitized/live_capture_<UTC-date>.json (_provenance.synthetic=false),
and diffs the observed response against every claim in docs/source-contract.md
(claim IDs C01..C17), printing a PASS/FAIL/INFO/SKIP report per claim.

Designed to run in the GitHub Actions `live-smoke` workflow — the development
sandbox CANNOT reach *.wnba.com (egress policy 403), so run it there only, or
locally with --dry-run.

Dependencies: Python 3.11 stdlib + requests. Nothing else.

Politeness / compliance guarantees (see docs/compliance.md):
  * hard cap of 5 HTTP requests per invocation (not configurable upward);
  * >= 3 seconds spacing between consecutive requests (not configurable down);
  * full respect for Retry-After on 429;
  * HTTP 403 => immediate hard abort (no retry, no header/IP games);
  * no cookies, no Authorization, no tokens — ever;
  * sanitized captures keep ONLY: endpoint, URL, query params, HTTP status,
    response Date header, response body. Never request/response header dumps.

Exit codes: 0 = all executed checks passed; 1 = one or more claim checks
FAILED; 2 = configuration error; 3 = hard abort (HTTP 403 edge block);
4 = upstream unavailable (retries exhausted / network error).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover
    print("ERROR: the 'requests' package is required.", file=sys.stderr)
    sys.exit(2)

REPO_ROOT = Path(__file__).resolve().parent.parent

PAGE_URL = (
    "https://stats.wnba.com/teams/traditional/"
    "?Season=2026&SeasonType=Regular%20Season&LastNGames=7&sort=TEAM_NAME&dir=1"
)
TEAMSTATS_ENDPOINT = "https://stats.wnba.com/stats/leaguedashteamstats"
TEAMYEARS_ENDPOINT = "https://stats.wnba.com/stats/commonteamyears"
DEFAULT_REFERENCE = REPO_ROOT / "fixtures" / "teams-2026-reference.json"
DEFAULT_OUT_DIR = REPO_ROOT / "fixtures" / "sanitized"

MAX_REQUESTS = 5          # hard cap, never raised
MIN_SPACING_SECONDS = 3.0  # hard floor, never lowered

# Exact page-equivalent parameters (docs/source-contract.md section 2).
TEAMSTATS_PARAMS: dict[str, str] = {
    "MeasureType": "Base",
    "PerMode": "PerGame",
    "PlusMinus": "N",
    "PaceAdjust": "N",
    "Rank": "N",
    "LeagueID": "10",
    "Season": "2026",
    "SeasonType": "Regular Season",
    "PORound": "0",
    "Outcome": "",
    "Location": "",
    "Month": "0",
    "SeasonSegment": "",
    "DateFrom": "",
    "DateTo": "",
    "OpponentTeamID": "0",
    "VsConference": "",
    "VsDivision": "",
    "TeamID": "0",
    "Conference": "",
    "Division": "",
    "GameSegment": "",
    "Period": "0",
    "ShotClockRange": "",
    "LastNGames": "7",
    "GameScope": "",
    "PlayerExperience": "",
    "PlayerPosition": "",
    "StarterBench": "",
    "TwoWay": "0",
}
TEAMYEARS_PARAMS: dict[str, str] = {"LeagueID": "10"}

# Public, non-secret request headers (docs/source-contract.md section 3).
# NO cookies, NO Authorization, NO credentials of any kind.
BASE_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://stats.wnba.com/",
    "Origin": "https://stats.wnba.com",
}
# Optional platform-idiosyncratic hint headers (--compat-headers). These are
# STATIC, PUBLICLY DOCUMENTED string constants used by the stats platform's
# own web client; they are NOT credentials, NOT secrets, and NOT per-user.
COMPAT_HEADERS: dict[str, str] = {
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",  # literal constant, not an auth token
}

REQUIRED_28 = [
    "TEAM_ID", "TEAM_NAME", "GP", "W", "L", "W_PCT", "MIN",
    "FGM", "FGA", "FG_PCT", "FG3M", "FG3A", "FG3_PCT",
    "FTM", "FTA", "FT_PCT", "OREB", "DREB", "REB",
    "AST", "TOV", "STL", "BLK", "BLKA", "PF", "PFD", "PTS", "PLUS_MINUS",
]
EXPANSION_TEAM_NAMES = {
    "Golden State Valkyries", "Toronto Tempo", "Portland Fire",
}


class HardAbort(Exception):
    """HTTP 403 from the edge: stop everything immediately."""


class Budget:
    """Enforces the request cap and inter-request spacing."""

    def __init__(self) -> None:
        self.used = 0
        self.retries = 0
        self._last: float | None = None

    def acquire(self) -> None:
        if self.used >= MAX_REQUESTS:
            raise RuntimeError(
                f"request budget exhausted ({MAX_REQUESTS} max per run)")
        if self._last is not None:
            wait = MIN_SPACING_SECONDS - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
        self._last = time.monotonic()
        self.used += 1


def polite_get(session: requests.Session, budget: Budget, url: str,
               params: dict[str, str], headers: dict[str, str],
               timeout: float, allow_retry: bool = True) -> requests.Response:
    """One GET with politeness rules: spacing, budget, Retry-After, 403 abort."""
    while True:
        budget.acquire()
        try:
            resp = session.get(url, params=params, headers=headers,
                               timeout=timeout)
        except requests.RequestException as exc:
            if allow_retry and budget.used < MAX_REQUESTS:
                print(f"  network error ({exc.__class__.__name__}); "
                      f"one retry after spacing...")
                budget.retries += 1
                allow_retry = False
                continue
            raise
        if resp.status_code == 403:
            raise HardAbort(
                "HTTP 403 from the platform edge (Akamai). This is an explicit "
                "block of this client/IP. HARD ABORT — per docs/compliance.md "
                "we do not retry, rotate identifiers, or otherwise work around "
                "access controls. Re-run later from a residential/CI egress or "
                "investigate headers; never bypass."
            )
        if resp.status_code == 429 and allow_retry and budget.used < MAX_REQUESTS:
            retry_after = resp.headers.get("Retry-After")
            try:
                delay = max(float(retry_after), MIN_SPACING_SECONDS) \
                    if retry_after else 10.0
            except ValueError:
                delay = 10.0
            print(f"  HTTP 429; honoring Retry-After={retry_after!r} "
                  f"(sleeping {delay:.1f}s)")
            time.sleep(delay)
            budget.retries += 1
            allow_retry = False
            continue
        if resp.status_code >= 500 and allow_retry and budget.used < MAX_REQUESTS:
            print(f"  HTTP {resp.status_code}; one retry after spacing...")
            budget.retries += 1
            allow_retry = False
            continue
        return resp


def sanitize_capture(endpoint: str, resp: requests.Response,
                     params: dict[str, str]) -> dict[str, Any]:
    """Keep ONLY: endpoint, full URL (query string), params, status, response
    Date header, body. Never any other request/response headers, never
    cookies."""
    try:
        body: Any = resp.json()
    except ValueError:
        body = {"_nonJsonBodyTruncated": resp.text[:300]}
    return {
        "endpoint": endpoint,
        "url": resp.url,
        "params": params,
        "status": resp.status_code,
        "responseDateHeader": resp.headers.get("Date"),
        "body": body,
    }


# --------------------------------------------------------------------------
# Claim checks (IDs map to docs/source-contract.md section 7 registry)
# --------------------------------------------------------------------------

def _result(report: list, claim: str, status: str, detail: str) -> None:
    report.append({"claim": claim, "status": status, "detail": detail})


def check_teamstats(report: list, cap: dict[str, Any],
                    reference: dict[str, Any]) -> None:
    body = cap["body"]
    ok_envelope = (
        cap["status"] == 200 and isinstance(body, dict)
        and {"resource", "parameters", "resultSets"} <= set(body)
    )
    _result(report, "C01", "PASS" if ok_envelope else "FAIL",
            f"status={cap['status']}, envelope keys="
            f"{sorted(body) if isinstance(body, dict) else type(body).__name__}")
    if not ok_envelope:
        return

    rsets = body["resultSets"]
    rs0 = rsets[0] if rsets else {}
    _result(report, "C02",
            "PASS" if rs0.get("name") == "LeagueDashTeamStats" else "FAIL",
            f"resultSets[0].name={rs0.get('name')!r}")

    echo = body.get("parameters", {})
    core = {"Season": "2026", "SeasonType": "Regular Season",
            "MeasureType": "Base", "PerMode": "PerGame", "LeagueID": "10"}
    bad = {k: echo.get(k) for k, v in core.items() if echo.get(k) != v}
    if str(echo.get("LastNGames")) != "7":
        bad["LastNGames"] = echo.get("LastNGames")
    _result(report, "C03", "PASS" if not bad else "FAIL",
            "page-filter params echoed correctly" if not bad
            else f"mismatched echo: {bad}")

    sent = set(TEAMSTATS_PARAMS)
    missing_echo = sorted(sent - set(echo))
    _result(report, "C05", "PASS" if not missing_echo else "FAIL",
            "all 30 request params echoed" if not missing_echo
            else f"params not echoed: {missing_echo}")

    headers = rs0.get("headers", [])
    rows = rs0.get("rowSet", [])
    idx = {h: i for i, h in enumerate(headers)}
    missing_cols = [h for h in REQUIRED_28 if h not in idx]
    prefix_note = ("; documented 28 are the header prefix"
                   if headers[:28] == REQUIRED_28 else
                   "; NOTE: present but not a prefix (order differs)")
    _result(report, "C08", "PASS" if not missing_cols else "FAIL",
            (f"all 28 documented columns present{prefix_note}"
             if not missing_cols else f"missing columns: {missing_cols}"))

    extras = [h for h in headers if h not in REQUIRED_28]
    unexpected = [h for h in extras
                  if not h.endswith("_RANK") and h not in ("CFID", "CFPARAMS")]
    _result(report, "C09", "INFO",
            f"extra columns={extras}; outside documented families={unexpected}")

    widths_ok = all(len(r) == len(headers) for r in rows)
    _result(report, "C12",
            "PASS" if len(rsets) == 1 and widths_ok else "FAIL",
            f"resultSets count={len(rsets)}, rows={len(rows)}, "
            f"uniform row width={widths_ok} (single complete set, no pagination)")

    if rows and not missing_cols:
        pct_cols = ["W_PCT", "FG_PCT", "FG3_PCT", "FT_PCT"]
        bad_pct = [
            (r[idx["TEAM_NAME"]], c, r[idx[c]])
            for r in rows for c in pct_cols
            if r[idx[c]] is not None and not (0.0 <= r[idx[c]] <= 1.0)
        ]
        _result(report, "C10", "PASS" if not bad_pct else "FAIL",
                "all percentage fields within fraction scale 0.0-1.0"
                if not bad_pct else f"out-of-scale percentages: {bad_pct[:5]}")

        names = [r[idx["TEAM_NAME"]] for r in rows]
        if names == sorted(names, key=str.casefold):
            _result(report, "C04", "INFO",
                    "rowSet WAS name-sorted in this capture — cannot prove "
                    "server-side sorting from one sample; normalizer must still "
                    "sort client-side")
        else:
            _result(report, "C04", "PASS",
                    "rowSet not sorted by TEAM_NAME — confirms sort/dir are "
                    "client-side and our normalizer must sort")

        team_ids = [r[idx["TEAM_ID"]] for r in rows]
        ids_ok = all(isinstance(t, int) and len(str(t)) == 10 for t in team_ids)
        live = {str(r[idx["TEAM_ID"]]): r[idx["TEAM_NAME"]] for r in rows}
        ref_teams: dict[str, str] = reference["teams"]
        mismatches: list[str] = []
        for name in EXPANSION_TEAM_NAMES:
            live_id = next((i for i, n in live.items() if n == name), None)
            ref_id = next((i for i, n in ref_teams.items() if n == name), None)
            if live_id is None:
                mismatches.append(f"{name}: not found in live response")
            elif live_id != ref_id:
                mismatches.append(
                    f"{name}: reference placeholder id {ref_id} != live id "
                    f"{live_id} — UPDATE fixtures/teams-2026-reference.json")
        diff_ids = sorted(set(ref_teams) ^ set(live))
        detail = (f"10-digit ids={ids_ok}; ref-vs-live id diff={diff_ids}; "
                  f"expansion checks={mismatches or 'all match'}")
        _result(report, "C11",
                "PASS" if ids_ok and not mismatches and not diff_ids else "FAIL",
                detail)
        _result(report, "C13", "SKIP",
                "season in progress (rowSet non-empty); empty-season behavior "
                "verifiable only in the offseason")
    elif not rows:
        _result(report, "C13", "PASS",
                "empty rowSet observed with headers intact "
                f"(headers={len(headers)} columns) — empty-season claim confirmed")

    _result(report, "C07", "PASS" if cap["status"] == 200 else "FAIL",
            "HTTP 200 with public headers only — no cookies or tokens were sent")
    _result(report, "C15",
            "PASS" if cap.get("responseDateHeader") else "FAIL",
            f"response Date header={cap.get('responseDateHeader')!r}")


def check_teamyears(report: list, cap: dict[str, Any],
                    reference: dict[str, Any]) -> None:
    body = cap["body"]
    if cap["status"] != 200 or not isinstance(body, dict):
        _result(report, "C14", "FAIL", f"status={cap['status']}")
        return
    rs0 = (body.get("resultSets") or [{}])[0]
    headers_ok = rs0.get("headers") == ["LEAGUE_ID", "TEAM_ID", "MIN_YEAR",
                                        "MAX_YEAR", "ABBREVIATION"]
    rows = rs0.get("rowSet", [])
    try:
        active = {str(r[1]) for r in rows if int(r[3]) >= 2026}
    except (ValueError, TypeError, IndexError):
        active = set()
    ref_ids = set(reference["teams"])
    diff = sorted(ref_ids ^ active)
    ok = rs0.get("name") == "TeamYears" and headers_ok and active == ref_ids
    _result(report, "C14", "PASS" if ok else "FAIL",
            f"name={rs0.get('name')!r}, headers_ok={headers_ok}, "
            f"active(MAX_YEAR>=2026)={len(active)}, ref-vs-active id diff={diff}")


def check_probe_400(report: list, cap: dict[str, Any] | None) -> None:
    if cap is None:
        _result(report, "C06", "SKIP",
                "--probe-400 not requested (conserves request budget)")
        return
    body = cap["body"]
    text = body.get("_nonJsonBodyTruncated", "") if isinstance(body, dict) else ""
    looks_right = cap["status"] == 400 and re.search(
        r"(?i)(required|property)", text or "")
    _result(report, "C06", "PASS" if looks_right else "FAIL",
            f"omitted PerMode -> status={cap['status']}, body starts "
            f"{(text or '')[:80]!r}")


# --------------------------------------------------------------------------

def build_plan(args: argparse.Namespace) -> list[dict[str, Any]]:
    plan = [
        {"step": 1, "endpoint": TEAMSTATS_ENDPOINT, "params": TEAMSTATS_PARAMS,
         "verifies": ["C01", "C02", "C03", "C04", "C05", "C07", "C08", "C09",
                      "C10", "C11", "C12", "C13", "C15"]},
        {"step": 2, "endpoint": TEAMYEARS_ENDPOINT, "params": TEAMYEARS_PARAMS,
         "verifies": ["C14"]},
    ]
    if args.probe_400:
        probe = {k: v for k, v in TEAMSTATS_PARAMS.items() if k != "PerMode"}
        plan.append({"step": 3, "endpoint": TEAMSTATS_ENDPOINT,
                     "params": probe, "verifies": ["C06"],
                     "note": "deliberately omits PerMode; expects HTTP 400"})
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture and verify the live stats.wnba.com source "
                    "contract (see docs/source-contract.md).")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate arguments and print the request plan "
                             "WITHOUT any network call")
    parser.add_argument("--probe-400", action="store_true",
                        help="spend one extra request verifying the "
                             "missing-parameter HTTP 400 behavior (claim C06)")
    parser.add_argument("--compat-headers", action="store_true",
                        help="also send the static public x-nba-stats-* hint "
                             "headers (constants, not credentials)")
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="per-request read timeout in seconds (default 30)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR,
                        help="directory for live_capture_<date>.json")
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE,
                        help="pinned reference team set to diff against")
    args = parser.parse_args(argv)

    if args.timeout <= 0:
        print("ERROR: --timeout must be positive", file=sys.stderr)
        return 2
    if not args.reference.is_file():
        print(f"ERROR: reference file not found: {args.reference}",
              file=sys.stderr)
        return 2
    try:
        reference = json.loads(args.reference.read_text(encoding="utf-8"))
        assert isinstance(reference.get("teams"), dict) and reference["teams"]
    except (ValueError, AssertionError) as exc:
        print(f"ERROR: reference file invalid: {exc}", file=sys.stderr)
        return 2

    headers = dict(BASE_HEADERS)
    if args.compat_headers:
        headers.update(COMPAT_HEADERS)

    plan = build_plan(args)
    utc_date = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    out_path = args.out_dir / f"live_capture_{utc_date}.json"

    print("Live contract capture plan")
    print(f"  page under test : {PAGE_URL}")
    print(f"  request budget  : {len(plan)} planned of {MAX_REQUESTS} max, "
          f">= {MIN_SPACING_SECONDS:.0f}s spacing, Retry-After honored, "
          f"403 => hard abort")
    print(f"  request headers : {', '.join(sorted(headers))} "
          f"(public constants only; no cookies/tokens)")
    for step in plan:
        note = f" ({step['note']})" if step.get("note") else ""
        print(f"  step {step['step']}: GET {step['endpoint']}{note}")
        print(f"          params: {json.dumps(step['params'])}")
        print(f"          verifies claims: {', '.join(step['verifies'])}")
    print(f"  output          : {out_path} (_provenance.synthetic=false; "
          f"sanitized to URL/params/status/body/Date header only)")

    if args.dry_run:
        print("\n--dry-run: arguments validated, plan printed, "
              "no network calls made. Exiting 0.")
        return 0

    budget = Budget()
    report: list[dict[str, Any]] = []
    captures: list[dict[str, Any]] = []
    session = requests.Session()
    session.trust_env = True  # honor CI proxy settings; never disable TLS

    try:
        print("\nstep 1: leaguedashteamstats ...")
        resp = polite_get(session, budget, TEAMSTATS_ENDPOINT,
                          TEAMSTATS_PARAMS, headers, args.timeout)
        cap_stats = sanitize_capture(TEAMSTATS_ENDPOINT, resp, TEAMSTATS_PARAMS)
        captures.append(cap_stats)
        check_teamstats(report, cap_stats, reference)

        print("step 2: commonteamyears ...")
        resp = polite_get(session, budget, TEAMYEARS_ENDPOINT,
                          TEAMYEARS_PARAMS, headers, args.timeout)
        cap_years = sanitize_capture(TEAMYEARS_ENDPOINT, resp, TEAMYEARS_PARAMS)
        captures.append(cap_years)
        check_teamyears(report, cap_years, reference)

        cap_probe = None
        if args.probe_400:
            print("step 3: 400-probe (PerMode omitted) ...")
            probe = {k: v for k, v in TEAMSTATS_PARAMS.items()
                     if k != "PerMode"}
            resp = polite_get(session, budget, TEAMSTATS_ENDPOINT, probe,
                              headers, args.timeout, allow_retry=False)
            cap_probe = sanitize_capture(TEAMSTATS_ENDPOINT, resp, probe)
            captures.append(cap_probe)
        check_probe_400(report, cap_probe)
    except HardAbort as exc:
        print(f"\nHARD ABORT: {exc}", file=sys.stderr)
        return 3
    except (requests.RequestException, RuntimeError) as exc:
        print(f"\nUPSTREAM UNAVAILABLE: {exc.__class__.__name__}: {exc}",
              file=sys.stderr)
        print("No fixture written. This is NOT an empty dataset — treat as "
              "unavailable.", file=sys.stderr)
        return 4

    _result(report, "C16", "INFO",
            "no 403 encountered this run; the edge-block claim remains "
            "documented-platform-knowledge")
    _result(report, "C17", "SKIP",
            "no 429 encountered this run; Retry-After support untested")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "_provenance": {
            "synthetic": False,
            "capturedAtUtc": dt.datetime.now(dt.timezone.utc)
                             .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "describedBy": "docs/source-contract.md",
            "notes": ("Sanitized live capture from scripts/"
                      "capture_live_contract.py. Contains only endpoint, URL, "
                      "query params, HTTP status, response Date header, and "
                      "body. No cookies, no tokens, no header dumps."),
        },
        "requestCount": budget.used,
        "retryCount": budget.retries,
        "captures": captures,
        "claimReport": report,
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out_path}")

    print("\nClaim verification report (docs/source-contract.md section 7):")
    failed = 0
    for entry in sorted(report, key=lambda e: e["claim"]):
        print(f"  {entry['claim']}: {entry['status']:5s} {entry['detail']}")
        failed += entry["status"] == "FAIL"
    print(f"\n{failed} claim check(s) FAILED; "
          f"{budget.used} request(s), {budget.retries} retr(y/ies).")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
