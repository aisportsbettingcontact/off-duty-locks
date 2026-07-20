"""Authoritative expected-team resolution for a WNBA season.

The active-team set is NEVER hardcoded (no magic team counts anywhere).
It is resolved, in order of preference:

1. Live, from the official franchise-history endpoint
   ``https://stats.wnba.com/stats/commonteamyears?LeagueID=10`` via an
   injectable ``fetch_json(url, params) -> dict`` callable. Rows in the
   ``TeamYears`` result set are filtered to franchises active for the
   requested season (``MIN_YEAR <= season <= MAX_YEAR``); defunct teams
   (``MAX_YEAR`` < season) are excluded.
2. Fallback, from a versioned local fixture
   (default ``fixtures/expected_teams/<season>.json``) when no fetcher is
   supplied, the fetch raises ``UpstreamUnavailable``, or the live payload
   is malformed/empty. The resulting ``ExpectedTeamSet.source`` records
   which path produced it — a fallback is never disguised as live data.

If neither source can produce a non-empty team set, ``ConfigError`` is
raised: an empty or missing expected-team set must never silently pass
through (it would make every real team look "unexpected" or mask a
partial dataset).

Every resolution carries ``version_checksum`` =
``contract.canonical_json_checksum({"season": ..., "teams": ...})`` so
snapshots can pin exactly which team universe they were validated against
(``expectedTeamsVersion`` in the snapshot contract).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wnba_pipeline import contract
from wnba_pipeline.contract import ConfigError, ExpectedTeamSet, UpstreamUnavailable

logger = logging.getLogger("wnba_pipeline.teams")

COMMONTEAMYEARS_ENDPOINT = "https://stats.wnba.com/stats/commonteamyears"
TEAM_YEARS_RESULT_SET = "TeamYears"

# Sources recorded on the ExpectedTeamSet (stable machine-readable markers).
SOURCE_LIVE = "stats.wnba.com/stats/commonteamyears"
SOURCE_FALLBACK = "fallback-fixture"

# Header names. MIN_YEAR/MAX_YEAR arrive as strings on the wire; TEAM_ID as a
# number. The endpoint historically carries no display-name column — when only
# ABBREVIATION is available it is used as the name; a real name column is
# preferred whenever the source adds one.
_H_LEAGUE_ID = "LEAGUE_ID"
_H_TEAM_ID = "TEAM_ID"
_H_MIN_YEAR = "MIN_YEAR"
_H_MAX_YEAR = "MAX_YEAR"
_H_ABBREVIATION = "ABBREVIATION"
_NAME_COLUMN_PREFERENCE = ("TEAM_NAME", "NAME", _H_ABBREVIATION)

FetchJson = Callable[[str, dict[str, str]], dict[str, Any]]


class _MalformedTeamYears(Exception):
    """Internal: live payload unusable; triggers fallback (never silent)."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_fallback_path(season: str) -> Path:
    # src/wnba_pipeline/teams.py -> repo root -> fixtures/expected_teams/
    root = Path(__file__).resolve().parents[2]
    return root / "fixtures" / "expected_teams" / f"{season}.json"


def _canonical_team_id(value: Any) -> str:
    """Stringify a TEAM_ID cell deterministically (numbers on the wire)."""
    if isinstance(value, bool):  # bool is an int subclass; never a team id
        raise _MalformedTeamYears(f"boolean TEAM_ID: {value!r}")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise _MalformedTeamYears(f"unusable TEAM_ID cell: {value!r}")


def _season_year(season: str) -> int:
    try:
        return int(str(season).strip())
    except (TypeError, ValueError):
        raise ConfigError(f"season must be a year string, got {season!r}") from None


def _parse_team_years(payload: dict[str, Any], season_year: int) -> dict[str, str]:
    """TeamYears payload -> {team_id: name} for teams active in season_year.

    Raises _MalformedTeamYears on any structural defect or an empty active
    set — a malformed or empty live answer must trigger the fallback path,
    never a silently empty team universe.
    """
    result_sets = payload.get("resultSets")
    if not isinstance(result_sets, list):
        raise _MalformedTeamYears("resultSets missing or not a list")

    entry = None
    for candidate in result_sets:  # accept the result set at any index
        if isinstance(candidate, dict) and candidate.get("name") == TEAM_YEARS_RESULT_SET:
            entry = candidate
            break
    if entry is None:
        raise _MalformedTeamYears(f"no result set named {TEAM_YEARS_RESULT_SET!r}")

    headers = entry.get("headers")
    rows = entry.get("rowSet")
    if not isinstance(headers, list) or not all(isinstance(h, str) for h in headers):
        raise _MalformedTeamYears("headers missing or not a list of strings")
    if not isinstance(rows, list):
        raise _MalformedTeamYears("rowSet missing or not a list")

    # Lookup strictly by header name — never positional assumptions.
    index_of = {name: i for i, name in enumerate(headers)}
    for required in (_H_TEAM_ID, _H_MIN_YEAR, _H_MAX_YEAR):
        if required not in index_of:
            raise _MalformedTeamYears(f"missing required column {required!r}")

    name_column = next((c for c in _NAME_COLUMN_PREFERENCE if c in index_of), None)
    if name_column is None:
        raise _MalformedTeamYears("no usable name column (TEAM_NAME/NAME/ABBREVIATION)")

    teams: dict[str, str] = {}
    for row_index, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != len(headers):
            raise _MalformedTeamYears(f"rowSet[{row_index}] width != headers length")
        if _H_LEAGUE_ID in index_of:
            league_id = row[index_of[_H_LEAGUE_ID]]
            if str(league_id) != contract.WNBA_LEAGUE_ID:
                continue  # tolerate foreign-league rows; only keep LeagueID=10
        try:
            min_year = int(str(row[index_of[_H_MIN_YEAR]]).strip())
            max_year = int(str(row[index_of[_H_MAX_YEAR]]).strip())
        except (TypeError, ValueError):
            raise _MalformedTeamYears(
                f"rowSet[{row_index}] has non-numeric MIN_YEAR/MAX_YEAR"
            ) from None
        if not (min_year <= season_year <= max_year):
            continue  # defunct (MAX_YEAR < season) or not yet active
        team_id = _canonical_team_id(row[index_of[_H_TEAM_ID]])
        name_value = row[index_of[name_column]]
        if not isinstance(name_value, str) or not name_value.strip():
            raise _MalformedTeamYears(f"rowSet[{row_index}] has unusable team name")
        if team_id in teams:
            raise _MalformedTeamYears(f"duplicate TEAM_ID {team_id} in TeamYears")
        teams[team_id] = name_value.strip()

    if not teams:
        raise _MalformedTeamYears(f"no active teams for season {season_year}")
    return teams


def _load_fallback(path: Path, season: str) -> dict[str, str]:
    """Read a versioned local team-set fixture. Raises ConfigError on any
    problem — a broken fallback is a configuration failure, never an empty
    team set."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"expected-team fallback unreadable: {path} ({exc})") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"expected-team fallback is not valid JSON: {path}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"expected-team fallback malformed (not an object): {path}")
    file_season = str(data.get("season", "")).strip()
    if file_season != str(season).strip():
        raise ConfigError(
            f"expected-team fallback season mismatch: file says {file_season!r},"
            f" requested {season!r} ({path})"
        )
    teams = data.get("teams")
    if (
        not isinstance(teams, dict)
        or not teams
        or not all(
            isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip()
            for k, v in teams.items()
        )
    ):
        raise ConfigError(
            f"expected-team fallback has no usable non-empty 'teams' mapping: {path}"
        )
    return dict(teams)


def _build(season: str, source: str, source_url: str, teams: dict[str, str]) -> ExpectedTeamSet:
    return ExpectedTeamSet(
        season=season,
        source=source,
        source_url=source_url,
        resolved_at_utc=_utc_now_iso(),
        teams=teams,
        version_checksum=contract.canonical_json_checksum(
            {"season": season, "teams": teams}
        ),
    )


def resolve_expected_teams(
    season: str,
    *,
    fetch_json: FetchJson | None = None,
    fallback_path: str | Path | None = None,
) -> ExpectedTeamSet:
    """Resolve the authoritative active-team set for ``season``.

    ``fetch_json`` is an injectable ``(url, params) -> dict`` callable used to
    hit the official commonteamyears endpoint. When it is ``None``, raises
    ``UpstreamUnavailable``, or returns a malformed/empty payload, the local
    fallback fixture is used instead (``fallback_path`` or the default
    ``fixtures/expected_teams/<season>.json``) and the result's ``source``
    says so. With no live answer AND no readable fallback, ``ConfigError``
    is raised — the pipeline never proceeds with an unknown team universe.
    """
    season = str(season).strip()
    season_year = _season_year(season)

    live_failure: str | None = None
    if fetch_json is not None:
        params = {"LeagueID": contract.WNBA_LEAGUE_ID}
        source_url = f"{COMMONTEAMYEARS_ENDPOINT}?LeagueID={contract.WNBA_LEAGUE_ID}"
        try:
            payload = fetch_json(COMMONTEAMYEARS_ENDPOINT, params)
            teams = _parse_team_years(payload, season_year)
        except UpstreamUnavailable as exc:
            live_failure = f"upstream unavailable: {exc.reason}"
            logger.warning("commonteamyears unavailable (%s); using fallback", exc.reason)
        except _MalformedTeamYears as exc:
            live_failure = f"malformed payload: {exc}"
            logger.warning("commonteamyears payload malformed (%s); using fallback", exc)
        else:
            logger.info(
                "expected teams resolved live: season=%s teams=%d", season, len(teams)
            )
            return _build(season, SOURCE_LIVE, source_url, teams)
    else:
        live_failure = "no fetcher supplied"

    path = Path(fallback_path) if fallback_path is not None else _default_fallback_path(season)
    if not path.is_file():
        raise ConfigError(
            "expected-team set unresolvable: live source unusable"
            f" ({live_failure}) and fallback missing: {path}"
        )
    teams = _load_fallback(path, season)
    logger.info(
        "expected teams resolved from fallback %s (live: %s): season=%s teams=%d",
        path,
        live_failure,
        season,
        len(teams),
    )
    return _build(season, SOURCE_FALLBACK, path.as_posix(), teams)
