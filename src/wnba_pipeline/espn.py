"""ESPN-sourced team statistics: fetchers + pure transforms -> RawFetchResult.

Owner decision 2026-08-04: stats.wnba.com is unreachable for ANY unattended
client (GitHub-hosted runners, residential curl, this pipeline's own client run
residentially, and a real Chromium session all stall at the Akamai edge — see
docs/compliance.md section 1), so team stats now come from ESPN's public JSON
APIs, which serve datacenter IPs. The rest of the pipeline is unchanged: this
module produces a :class:`~wnba_pipeline.contract.RawFetchResult` whose payload
is the SAME ``resultSets`` envelope the validator already speaks, so
``validation.py`` (range/duplicate/expected-team rules), ``storage.py``,
``runner.py``, and ``db.TeamStatsPublisher`` all apply verbatim.

Two ESPN hosts are used (docs/compliance.md section "ESPN" governs budgets):

  site API   https://site.api.espn.com/apis/site/v2/sports/basketball/wnba
             /teams, /teams/{id} (record), /teams/{id}/schedule, /summary
  core API   https://sports.core.api.espn.com/v2/sports/basketball/leagues/wnba
             /seasons/{y}/types/2/teams/{id}/statistics   (types/2 = regular)

Definitional decisions (each verified against the live captures recorded
2026-08-04 in ``fixtures/espn/``):

TURNOVERS — use ESPN's ``turnovers`` counter, NOT ``totalTurnovers``.
  ESPN's season payload carries three counters (ATL, 29 GP):
  ``turnovers``=377 (13.0/game), ``teamTurnovers``=286 (9.9/game) and
  ``totalTurnovers``=663=377+286 (22.9/game). At GAME level the same names are
  sane: box ``teamTurnovers`` is a tiny single digit (0-2 observed; shot-clock
  type violations) and box ``totalTurnovers`` = ``turnovers`` + that digit. A
  season ``teamTurnovers`` of 286 cannot be the sum of 29 such values, and 22.9
  turnovers/game is outside any believable WNBA team range (roughly 8-22, with
  league extremes near 11-18) while 13.0/game sits right in it. So the season
  ``totalTurnovers``/``avgTotalTurnovers`` pair is internally consistent with
  itself (22.862*29=663) but not with ESPN's own game boxes — the counter that
  IS box-consistent and believable is ``turnovers``, and both splits use it so
  cross-split comparisons never carry a definitional offset.

PERCENTAGES — core-API ``value`` fields are 0-100 (``fieldGoalPct``
  value=43.741 with 898/2053=0.43741); the record endpoint's ``winPercent`` is
  already 0-1 (0.62068...). The contract wants fraction 0-1 (see the existing
  fixture: FG_PCT=0.462), so shooting percentages are recomputed from
  makes/attempts at full precision (identical to ESPN's value/100, and
  ``PCT_RECOMPUTE_FAIL`` in validation.py stays a live tripwire), and
  ``winPercent`` is taken as-is.

MINUTES — game length per game, matching the existing fixture (MIN=40.0).
  ESPN's ``avgMinutes`` is 0.0 at both season and event level, so minutes are
  DERIVED: regulation WNBA = 40, plus 5 per overtime period, read from each
  completed event's ``status.period`` in the schedule payload (4 = regulation).
  Both splits average game length over exactly the events they cover.

POSSESSIONS — the DB columns come from ``derived.py`` (FGA - OREB + TOV +
  0.44*FTA), unchanged. ESPN's ``avgEstimatedPossessions`` is cross-checked as
  a WARN-only tripwire with a wide tolerance: ESPN feeds its estimate the
  inflated ``totalTurnovers`` counter (ATL ytd: 92.5 vs our 83.6), so the two
  formulas legitimately diverge by ~10% — divergence beyond 15% is logged,
  never fatal.

TEAM IDENTITY — rows are published under the canonical team ids the serving
  layer already uses (the expected-team universe of ``teams.py``), resolved
  from ESPN ``displayName`` via the PR#33 name normalizer
  (``enrich.normalize_team_name`` + ``TEAM_NAME_ALIASES``). An ESPN team whose
  name cannot be crosswalked keeps a sentinel id so validation FAILS loudly
  (UNEXPECTED_TEAM + MISSING_EXPECTED_TEAM) instead of guessing. The ESPN team
  id and the exact event ids behind a Last-N row ride along in ``extras`` (file
  snapshots) for audit.

RAW PRESERVATION — one run makes up to ~150 HTTP requests (schedule payloads
  are ~650 KB each), so the raw store keeps the assembled envelope plus a
  ``espnSources`` map of every fetched URL -> sha256 of its exact bytes, not
  the response bodies themselves. ``source_checksum`` is the sha256 of the
  canonical envelope bytes, so identical upstream data still deduplicates to
  ``SUCCESS_UNCHANGED`` exactly like the historical pipeline.

This module NEVER contacts stats.wnba.com.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from wnba_pipeline import contract
from wnba_pipeline.contract import (
    ExpectedTeamSet,
    ExtractionParams,
    RawFetchResult,
    UpstreamUnavailable,
    _slug,
    sha256_hex,
)
from wnba_pipeline.derived import possessions as derived_possessions
from wnba_pipeline.enrich import TEAM_NAME_ALIASES, normalize_team_name
from wnba_pipeline.http_client import CircuitBreaker, HttpConfig, get_json

logger = logging.getLogger("wnba_pipeline.espn")

ESPN_SOURCE = "espn"
SITE_API = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba"
CORE_API = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/wnba"

# types/2 = regular season on the core API; the schedule endpoint takes the
# same value as ?seasontype= and each event also carries seasonType.type. Both
# filters are applied (parameter AND per-event field) so a served-anyway
# preseason or Commissioner's Cup game can never leak into a split.
REGULAR_SEASON_TYPE = 2

REGULATION_MINUTES = 40.0
OVERTIME_MINUTES = 5.0
REGULATION_PERIODS = 4

# Honest identification (docs/compliance.md section "ESPN"): a plain pipeline
# UA, not a browser disguise — ESPN's APIs serve datacenter clients openly.
ESPN_REQUEST_HEADERS: dict[str, str] = {
    "User-Agent": "offdutylocks-pipeline/1.0 (+https://offdutylocks.com)",
    "Accept": "application/json, text/plain, */*",
}

# Politeness spacing between consecutive live requests within one run.
DEFAULT_SPACING_S = 0.5

# |record GP - completed regular-season events| beyond this fails the run
# (a forfeit or an ESPN bookkeeping lag can explain 1; nothing explains more).
GP_CONSISTENCY_TOLERANCE = 1

# WARN threshold for |ours - ESPN| / ESPN on estimated possessions. Wide on
# purpose — see POSSESSIONS in the module docstring.
POSSESSIONS_WARN_FRACTION = 0.15

# Extra envelope columns preserved verbatim into TeamRecord.extras.
_H_ESPN_TEAM_ID = "ESPN_TEAM_ID"
_H_ESPN_EVENT_IDS = "ESPN_EVENT_IDS"

# Sentinel prefix for an ESPN team that could not be crosswalked onto the
# expected-team universe. Guaranteed to fail expected-team validation.
_UNMATCHED_ID_PREFIX = "espn-unmatched-"


@dataclass(frozen=True)
class EspnExtractionParams(ExtractionParams):
    """ExtractionParams whose key names ESPN as the source.

    The v2 key keeps provenance unambiguous end-to-end: storage directories,
    manifests, snapshots, and the ``team_stats.extraction_key`` column all say
    ``source=espn``, and ESPN-sourced runs can never be confused with (or
    dedupe against) historical stats.wnba.com data.
    """

    def extraction_key(self) -> str:
        return (
            f"wnba-teamstats:v2:source=espn"
            f":season={_slug(self.season)}"
            f":type={_slug(self.season_type)}"
            f":lastn={self.last_n_games}"
            f":measure={_slug(self.measure_type)}"
            f":permode={_slug(self.per_mode)}"
        )


# ---------------------------------------------------------------------------
# HTTP client (memoizing; reuses http_client's retry/backoff/circuit breaker)
# ---------------------------------------------------------------------------

# fetch_raw(url, params) -> (payload, raw_bytes, request_count, retry_count).
FetchRaw = Callable[[str, Mapping[str, str]], tuple[dict[str, Any], bytes, int, int]]


class EspnClient:
    """Memoizing ESPN fetcher shared across both splits of one CLI run.

    Transport policy is exactly :func:`wnba_pipeline.http_client.get_json`
    (bounded retries, backoff + jitter, Retry-After, fail-fast 403/404, one
    circuit breaker across every ESPN request in the run) with ESPN's honest
    header set. The memo cache means the teams list, schedules, and any event
    shared between the Last-N and YTD extractions is fetched exactly once.
    """

    def __init__(
        self,
        http: HttpConfig | None = None,
        *,
        fetch_raw: FetchRaw | None = None,
        sleep: Callable[[float], None] = time.sleep,
        spacing_s: float | None = None,
    ) -> None:
        self._config = http if http is not None else HttpConfig()
        self._fetch_raw = fetch_raw if fetch_raw is not None else self._live_fetch_raw
        self._sleep = sleep
        # Resolved at construction (not def-time) so tests can zero the module
        # default; None means "the polite production spacing".
        self._spacing_s = DEFAULT_SPACING_S if spacing_s is None else spacing_s
        self._breaker = CircuitBreaker.from_config(self._config)
        self._session: requests.Session | None = None
        self._cache: dict[str, tuple[dict[str, Any], str]] = {}
        self.request_count = 0
        self.retry_count = 0

    def _live_fetch_raw(
        self, url: str, params: Mapping[str, str]
    ) -> tuple[dict[str, Any], bytes, int, int]:
        if self._session is None:
            self._session = requests.Session()
        payload, raw, _status, _date, requests_made, retries = get_json(
            url,
            params,
            self._config,
            session=self._session,
            breaker=self._breaker,
            sleep=self._sleep,
            headers=ESPN_REQUEST_HEADERS,
        )
        return payload, raw, requests_made, retries

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    @staticmethod
    def _key(url: str, params: Mapping[str, str]) -> str:
        query = urllib.parse.urlencode(sorted(params.items()))
        return f"{url}?{query}" if query else url

    def get(self, url: str, params: Mapping[str, str] | None = None
            ) -> tuple[dict[str, Any], str]:
        """GET one ESPN JSON document. Returns ``(payload, sha256_of_bytes)``.

        Memoized on the full URL: a cache hit costs no request and no spacing
        delay. Raises :class:`UpstreamUnavailable` (via the shared transport
        policy) on every non-recoverable condition — never returns partial data.
        """
        params = dict(params or {})
        key = self._key(url, params)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        if self.request_count > 0 and self._spacing_s > 0:
            self._sleep(self._spacing_s)
        payload, raw, requests_made, retries = self._fetch_raw(url, params)
        self.request_count += requests_made
        self.retry_count += retries
        result = (payload, sha256_hex(raw))
        self._cache[key] = result
        return result


# ---------------------------------------------------------------------------
# Pure parsers (fixture-tested; no I/O)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EspnTeam:
    espn_id: str
    display_name: str
    abbreviation: str


@dataclass(frozen=True)
class CompletedEvent:
    """One completed regular-season game from the team's schedule."""

    event_id: str
    date: str
    won: bool
    points_for: float
    points_against: float
    periods: int


def _malformed(what: str) -> UpstreamUnavailable:
    """ESPN answered, but not in a shape we can safely use. That is an
    unusable upstream response, never an empty dataset."""
    return UpstreamUnavailable(f"espn_malformed:{what}")


def parse_team_directory(payload: Mapping[str, Any]) -> list[EspnTeam]:
    """Teams-list payload -> active teams. Raises on malformed/empty."""
    try:
        entries = payload["sports"][0]["leagues"][0]["teams"]
    except (KeyError, IndexError, TypeError):
        raise _malformed("teams_list_shape") from None
    teams: list[EspnTeam] = []
    for entry in entries:
        team = entry.get("team") if isinstance(entry, dict) else None
        if not isinstance(team, dict):
            raise _malformed("teams_list_entry")
        espn_id = str(team.get("id") or "").strip()
        name = str(team.get("displayName") or "").strip()
        if not espn_id or not name:
            raise _malformed("teams_list_identity")
        if team.get("isActive") is False:
            continue
        teams.append(EspnTeam(espn_id=espn_id, display_name=name,
                              abbreviation=str(team.get("abbreviation") or "")))
    if not teams:
        raise _malformed("teams_list_empty")
    return teams


def stat_values(core_payload: Mapping[str, Any]) -> dict[str, float]:
    """Core-API statistics payload -> ``{stat name: numeric value}``.

    Non-numeric values are skipped (never coerced); duplicate names keep the
    first occurrence (ESPN repeats e.g. ``threePointPct``/
    ``threePointFieldGoalPct`` with identical values).
    """
    try:
        categories = core_payload["splits"]["categories"]
    except (KeyError, TypeError):
        raise _malformed("statistics_shape") from None
    out: dict[str, float] = {}
    for category in categories:
        for stat in category.get("stats", []):
            name = stat.get("name")
            value = stat.get("value")
            if (isinstance(name, str) and name not in out
                    and isinstance(value, (int, float))
                    and not isinstance(value, bool)):
                out[name] = float(value)
    if not out:
        raise _malformed("statistics_empty")
    return out


def parse_record(team_detail_payload: Mapping[str, Any]) -> dict[str, float]:
    """Team-detail payload -> the overall record item's stats by name.

    Wants at least wins/losses/winPercent/gamesPlayed; raises when the overall
    ("total") record item is absent — a season split cannot be built without
    an authoritative W-L record.
    """
    try:
        items = team_detail_payload["team"]["record"]["items"]
    except (KeyError, TypeError):
        raise _malformed("record_shape") from None
    for item in items:
        if isinstance(item, dict) and item.get("type") == "total":
            stats = {
                s["name"]: float(s["value"])
                for s in item.get("stats", [])
                if isinstance(s, dict) and isinstance(s.get("name"), str)
                and isinstance(s.get("value"), (int, float))
                and not isinstance(s.get("value"), bool)
            }
            if {"wins", "losses", "winPercent", "gamesPlayed"} <= stats.keys():
                return stats
            raise _malformed("record_totals_incomplete")
    raise _malformed("record_total_item_missing")


def parse_completed_regular_season(
    schedule_payload: Mapping[str, Any], espn_team_id: str
) -> list[CompletedEvent]:
    """Schedule payload -> completed REGULAR-SEASON events, oldest first.

    Filters on each event's own ``seasonType.type`` (belt-and-braces with the
    ``seasontype`` request parameter — verified live 2026-08-04: the parameter
    works, and the field agrees with it). An event that is completed but
    missing scores/winner/period is a malformed answer, not a skippable row.
    """
    events = schedule_payload.get("events")
    if not isinstance(events, list):
        raise _malformed("schedule_shape")
    out: list[CompletedEvent] = []
    for event in events:
        if not isinstance(event, dict):
            raise _malformed("schedule_event")
        season_type = (event.get("seasonType") or {}).get("type")
        if season_type != REGULAR_SEASON_TYPE:
            continue
        competitions = event.get("competitions") or []
        if not competitions or not isinstance(competitions[0], dict):
            raise _malformed("schedule_competition")
        competition = competitions[0]
        status = competition.get("status") or {}
        if not ((status.get("type") or {}).get("completed") is True):
            continue
        periods = status.get("period")
        if not isinstance(periods, int) or periods < REGULATION_PERIODS:
            raise _malformed("schedule_period")
        ours = theirs = None
        for competitor in competition.get("competitors", []):
            if str(competitor.get("id")) == str(espn_team_id):
                ours = competitor
            else:
                theirs = competitor
        if ours is None or theirs is None:
            raise _malformed("schedule_competitors")
        won = ours.get("winner")
        score_for = ((ours.get("score") or {}).get("value"))
        score_against = ((theirs.get("score") or {}).get("value"))
        if not isinstance(won, bool) or not isinstance(score_for, (int, float)) \
                or not isinstance(score_against, (int, float)):
            raise _malformed("schedule_result_fields")
        out.append(CompletedEvent(
            event_id=str(event.get("id")),
            date=str(event.get("date") or ""),
            won=won,
            points_for=float(score_for),
            points_against=float(score_against),
            periods=periods,
        ))
    out.sort(key=lambda e: (e.date, e.event_id))
    return out


def game_length_minutes(periods: int) -> float:
    """Team minutes for one game: regulation 40, plus 5 per overtime."""
    return REGULATION_MINUTES + OVERTIME_MINUTES * max(0, periods - REGULATION_PERIODS)


# Summary boxscore stat name -> parsed keys. "made-attempted" entries split.
_BOX_PAIRS = {
    "fieldGoalsMade-fieldGoalsAttempted": ("fgm", "fga"),
    "threePointFieldGoalsMade-threePointFieldGoalsAttempted": ("fg3m", "fg3a"),
    "freeThrowsMade-freeThrowsAttempted": ("ftm", "fta"),
}
_BOX_SINGLES = {
    "offensiveRebounds": "oreb",
    "defensiveRebounds": "dreb",
    "totalRebounds": "reb",
    "assists": "ast",
    "turnovers": "tov",       # see TURNOVERS in the module docstring
    "steals": "stl",
    "blocks": "blk",
    "fouls": "pf",
}


def parse_summary_box(summary_payload: Mapping[str, Any],
                      espn_team_id: str) -> dict[str, float]:
    """Summary payload -> one team's box totals (integers as floats).

    The summary boxscore carries values only as display strings ("44-71",
    "30"); made-attempted pairs are split and every required stat must parse —
    a missing or unparseable required stat is a malformed upstream answer.
    Points deliberately come from the schedule's score fields instead (one
    authoritative surface for event outcomes).
    """
    teams = ((summary_payload.get("boxscore") or {}).get("teams")) or []
    stats_list = None
    for team in teams:
        if str(((team or {}).get("team") or {}).get("id")) == str(espn_team_id):
            stats_list = team.get("statistics")
            break
    if not isinstance(stats_list, list):
        raise _malformed(f"summary_box_team_{espn_team_id}")
    raw = {s.get("name"): s.get("displayValue")
           for s in stats_list if isinstance(s, dict)}
    out: dict[str, float] = {}
    try:
        for name, (made_key, att_key) in _BOX_PAIRS.items():
            made_text, att_text = str(raw[name]).split("-", 1)
            out[made_key] = float(int(made_text))
            out[att_key] = float(int(att_text))
        for name, key in _BOX_SINGLES.items():
            out[key] = float(int(str(raw[name])))
    except (KeyError, ValueError, TypeError):
        raise _malformed("summary_box_stats") from None
    return out


# ---------------------------------------------------------------------------
# Split builders (pure; canonical stat dicts keyed like SOURCE_HEADER_MAP)
# ---------------------------------------------------------------------------

def _ratio(made: float, attempted: float) -> float | None:
    """Full-precision shooting percentage; None (never 0) when no attempts."""
    return made / attempted if attempted > 0 else None


def ytd_stats(values: Mapping[str, float], record: Mapping[str, float],
              events: list[CompletedEvent], team_label: str) -> dict[str, Any]:
    """Season-to-date per-game stats from totals + record + schedule.

    Averages divide season TOTALS by the statistics payload's own
    ``gamesPlayed`` (full precision, internally consistent). The published
    ``games_played`` is the record's W+L; the three game counts (record,
    statistics payload, completed schedule events) must agree within
    ``GP_CONSISTENCY_TOLERANCE`` or the run FAILS — the pipeline never picks
    one silently (a mismatch means ESPN's surfaces disagree about how many
    games happened, so no number derived from them is trustworthy).
    """
    wins, losses = record["wins"], record["losses"]
    record_gp = record["gamesPlayed"]
    stats_gp = values.get("gamesPlayed")
    event_gp = float(len(events))
    counts = {"record": record_gp, "statistics": stats_gp, "schedule": event_gp}
    if stats_gp is None or stats_gp <= 0:
        raise _malformed("statistics_games_played")
    for a in counts:
        for b in counts:
            if abs(counts[a] - counts[b]) > GP_CONSISTENCY_TOLERANCE:
                raise UpstreamUnavailable(
                    "espn_gp_inconsistent:"
                    f"{team_label}:record={record_gp:g}:statistics={stats_gp:g}"
                    f":schedule={event_gp:g}"
                )
    if len({record_gp, stats_gp, event_gp}) > 1:
        logger.warning(
            "gp divergence within tolerance for %s: %s", team_label, counts)

    gp = stats_gp

    def per_game(name: str) -> float | None:
        total = values.get(name)
        return None if total is None else total / gp

    fgm, fga = per_game("fieldGoalsMade"), per_game("fieldGoalsAttempted")
    fg3m, fg3a = (per_game("threePointFieldGoalsMade"),
                  per_game("threePointFieldGoalsAttempted"))
    ftm, fta = per_game("freeThrowsMade"), per_game("freeThrowsAttempted")
    avg_points, avg_allowed = per_game("points"), per_game("pointsAllowed")
    if avg_allowed is None:
        # pointsAllowed only exists as a season average; fall back to it.
        avg_allowed = values.get("avgPointsAllowed")
    minutes = (sum(game_length_minutes(e.periods) for e in events) / len(events)
               if events else None)
    return {
        "games_played": wins + losses,   # record GP; exact for W+L=GP
        "wins": wins,
        "losses": losses,
        "win_pct": record["winPercent"],   # already fraction 0-1 (verified)
        "minutes": minutes,
        "field_goals_made": fgm,
        "field_goals_attempted": fga,
        "field_goal_pct": None if fgm is None or fga is None else _ratio(fgm, fga),
        "three_pointers_made": fg3m,
        "three_pointers_attempted": fg3a,
        "three_point_pct": None if fg3m is None or fg3a is None else _ratio(fg3m, fg3a),
        "free_throws_made": ftm,
        "free_throws_attempted": fta,
        "free_throw_pct": None if ftm is None or fta is None else _ratio(ftm, fta),
        "offensive_rebounds": per_game("offensiveRebounds"),
        "defensive_rebounds": per_game("defensiveRebounds"),
        "total_rebounds": per_game("totalRebounds"),
        "assists": per_game("assists"),
        "turnovers": per_game("turnovers"),   # NOT totalTurnovers — docstring
        "steals": per_game("steals"),
        "blocks": per_game("blocks"),
        "blocked_attempts": None,   # ESPN has no BLKA equivalent
        "personal_fouls": per_game("fouls"),
        "personal_fouls_drawn": None,   # ESPN has no PFD equivalent
        "points": avg_points,
        "plus_minus": (None if avg_points is None or avg_allowed is None
                       else avg_points - avg_allowed),
    }


def lastn_stats(events: list[CompletedEvent],
                boxes: Mapping[str, Mapping[str, float]]) -> dict[str, Any]:
    """Last-N per-game stats aggregated from event boxes + schedule results.

    ``events`` is exactly the window (newest N completed regular-season
    events); ``boxes`` maps event_id -> :func:`parse_summary_box` output for
    OUR team. Every event must have a box — a partial window would silently
    understate the averages.
    """
    if not events:
        raise _malformed("lastn_no_completed_events")
    missing = [e.event_id for e in events if e.event_id not in boxes]
    if missing:
        raise _malformed(f"lastn_boxes_missing:{','.join(missing)}")
    gp = float(len(events))
    wins = float(sum(1 for e in events if e.won))

    def mean(key: str) -> float:
        return sum(float(boxes[e.event_id][key]) for e in events) / gp

    def total(key: str) -> float:
        return sum(float(boxes[e.event_id][key]) for e in events)

    return {
        "games_played": gp,
        "wins": wins,
        "losses": gp - wins,
        "win_pct": wins / gp,
        "minutes": sum(game_length_minutes(e.periods) for e in events) / gp,
        "field_goals_made": mean("fgm"),
        "field_goals_attempted": mean("fga"),
        "field_goal_pct": _ratio(total("fgm"), total("fga")),
        "three_pointers_made": mean("fg3m"),
        "three_pointers_attempted": mean("fg3a"),
        "three_point_pct": _ratio(total("fg3m"), total("fg3a")),
        "free_throws_made": mean("ftm"),
        "free_throws_attempted": mean("fta"),
        "free_throw_pct": _ratio(total("ftm"), total("fta")),
        "offensive_rebounds": mean("oreb"),
        "defensive_rebounds": mean("dreb"),
        "total_rebounds": mean("reb"),
        "assists": mean("ast"),
        "turnovers": mean("tov"),   # box `turnovers` — see module docstring
        "steals": mean("stl"),
        "blocks": mean("blk"),
        "blocked_attempts": None,
        "personal_fouls": mean("pf"),
        "personal_fouls_drawn": None,
        "points": sum(e.points_for for e in events) / gp,
        "plus_minus": sum(e.points_for - e.points_against for e in events) / gp,
    }


# ---------------------------------------------------------------------------
# Crosswalk + envelope
# ---------------------------------------------------------------------------

def crosswalk_team_id(display_name: str, expected: ExpectedTeamSet,
                      espn_id: str) -> str:
    """Canonical team id for an ESPN displayName, or a loud sentinel.

    Matching is the PR#33 normalizer (case/punctuation-insensitive full name)
    plus the observed-alias table. A miss returns
    ``espn-unmatched-<espn id>`` — an id guaranteed to fail expected-team
    validation, so an unrecognized team can never be silently dropped or
    published under a guessed identity.
    """
    by_name = {normalize_team_name(name): team_id
               for team_id, name in expected.teams.items()}
    key = normalize_team_name(display_name)
    team_id = by_name.get(TEAM_NAME_ALIASES.get(key, key))
    return team_id if team_id is not None else f"{_UNMATCHED_ID_PREFIX}{espn_id}"


def build_envelope(rows: list[dict[str, Any]], params: ExtractionParams,
                   espn_sources: Mapping[str, str]) -> dict[str, Any]:
    """Canonical ``resultSets`` envelope from per-team row dicts.

    ``rows`` entries carry ``team_id``/``team_name``/``espn_team_id``/
    ``event_ids`` plus a canonical stats dict. The envelope speaks the exact
    dialect ``validation.py`` validates (headers by name, fraction
    percentages, None for missing) with two extra provenance columns that land
    in ``TeamRecord.extras``. ``espnSources`` (url -> sha256 of the exact
    response bytes) makes the multi-request provenance auditable and keeps the
    envelope checksum stable iff the upstream data is unchanged.
    """
    headers = list(contract.SOURCE_HEADER_MAP) + [_H_ESPN_TEAM_ID, _H_ESPN_EVENT_IDS]
    canonical_by_header = contract.SOURCE_HEADER_MAP
    row_set = []
    for row in sorted(rows, key=lambda r: (str(r["team_name"]).casefold(),
                                           str(r["team_id"]))):
        cells: list[Any] = []
        for header in headers:
            if header == "TEAM_ID":
                cells.append(row["team_id"])
            elif header == "TEAM_NAME":
                cells.append(row["team_name"])
            elif header == _H_ESPN_TEAM_ID:
                cells.append(row["espn_team_id"])
            elif header == _H_ESPN_EVENT_IDS:
                cells.append(",".join(row.get("event_ids", ())))
            else:
                cells.append(row["stats"].get(canonical_by_header[header]))
        row_set.append(cells)
    return {
        "resource": "espn-teamstats",
        "parameters": {
            "Season": params.season,
            "SeasonType": params.season_type,
            "LastNGames": params.last_n_games,
            "MeasureType": params.measure_type,
            "PerMode": params.per_mode,
        },
        "espnSources": dict(sorted(espn_sources.items())),
        "resultSets": [{
            "name": "LeagueDashTeamStats",
            "headers": headers,
            "rowSet": row_set,
        }],
    }


def _check_possessions(team_label: str, stats: Mapping[str, Any],
                       espn_avg_possessions: float | None) -> None:
    """WARN-only cross-check of derived possessions vs ESPN's estimate."""
    if espn_avg_possessions is None or espn_avg_possessions <= 0:
        return
    ours = derived_possessions(stats)
    if ours is None:
        return
    divergence = abs(ours - espn_avg_possessions) / espn_avg_possessions
    if divergence > POSSESSIONS_WARN_FRACTION:
        logger.warning(
            "possessions divergence for %s: derived=%.1f espn=%.1f (%.0f%% > %.0f%%)"
            " — formulas differ (ESPN uses its inflated totalTurnovers);"
            " investigate only if this drifts further",
            team_label, ours, espn_avg_possessions,
            divergence * 100, POSSESSIONS_WARN_FRACTION * 100,
        )


# ---------------------------------------------------------------------------
# Orchestration: ExtractionParams -> RawFetchResult
# ---------------------------------------------------------------------------

def _team_urls(season: str, espn_id: str) -> dict[str, tuple[str, dict[str, str]]]:
    return {
        "statistics": (
            f"{CORE_API}/seasons/{season}/types/{REGULAR_SEASON_TYPE}"
            f"/teams/{espn_id}/statistics", {}),
        "record": (f"{SITE_API}/teams/{espn_id}", {}),
        "schedule": (f"{SITE_API}/teams/{espn_id}/schedule",
                     {"season": season, "seasontype": str(REGULAR_SEASON_TYPE)}),
    }


def fetch_team_stats(
    params: ExtractionParams,
    client: EspnClient,
    expected: ExpectedTeamSet | None = None,
) -> RawFetchResult:
    """Fetch + transform one split from ESPN into a ``RawFetchResult``.

    ``params.last_n_games == 0`` builds the YTD split (season statistics +
    record + schedule per team); ``> 0`` builds the Last-N split (schedule +
    one summary per event, both teams' boxes served by each summary and
    memoized across teams/splits). ``expected`` is the crosswalk universe;
    when None it is resolved offline via :func:`teams.resolve_expected_teams`
    (fixture fallback — this module never contacts stats.wnba.com).
    """
    if expected is None:
        from wnba_pipeline.teams import resolve_expected_teams

        expected = resolve_expected_teams(params.season)

    requests_before = client.request_count
    retries_before = client.retry_count
    sources: dict[str, str] = {}

    def fetch(url: str, query: Mapping[str, str] | None = None) -> dict[str, Any]:
        payload, checksum = client.get(url, query)
        sources[EspnClient._key(url, dict(query or {}))] = checksum
        return payload

    teams_payload = fetch(f"{SITE_API}/teams")
    directory = parse_team_directory(teams_payload)

    rows: list[dict[str, Any]] = []
    for team in sorted(directory, key=lambda t: t.display_name.casefold()):
        urls = _team_urls(params.season, team.espn_id)
        schedule = fetch(*urls["schedule"])
        events = parse_completed_regular_season(schedule, team.espn_id)
        label = f"{team.display_name} ({team.espn_id})"

        if params.last_n_games == 0:
            values = stat_values(fetch(*urls["statistics"]))
            record = parse_record(fetch(*urls["record"]))
            stats = ytd_stats(values, record, events, label)
            _check_possessions(label, stats, values.get("avgEstimatedPossessions"))
            event_ids: tuple[str, ...] = ()
        else:
            window = events[-params.last_n_games:]
            boxes = {
                e.event_id: parse_summary_box(
                    fetch(f"{SITE_API}/summary", {"event": e.event_id}),
                    team.espn_id,
                )
                for e in window
            }
            stats = lastn_stats(window, boxes)
            event_ids = tuple(e.event_id for e in window)

        rows.append({
            "team_id": crosswalk_team_id(team.display_name, expected, team.espn_id),
            "team_name": team.display_name,
            "espn_team_id": team.espn_id,
            "event_ids": event_ids,
            "stats": stats,
        })

    payload = build_envelope(rows, params, sources)
    raw_bytes = json.dumps(payload, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
    made = client.request_count - requests_before
    logger.info(
        "espn fetch ok key=%s teams=%d requests=%d retries=%d",
        params.extraction_key(), len(rows), made,
        client.retry_count - retries_before,
    )
    return RawFetchResult(
        endpoint=SITE_API if params.last_n_games else CORE_API,
        url=f"{SITE_API}/teams",   # the one URL every split starts from
        params=params,
        payload=payload,
        raw_bytes=raw_bytes,
        source_checksum=sha256_hex(raw_bytes),
        fetched_at_utc=datetime.now(timezone.utc).isoformat(),
        http_status=200,
        request_count=made,
        retry_count=client.retry_count - retries_before,
        source_observed_at_utc=None,
        source=ESPN_SOURCE,
    )
