"""Extractor: ExtractionParams -> RawFetchResult from stats.wnba.com.

Fetches the leaguedashteamstats endpoint with the complete page-equivalent
parameter set and returns a fully populated ``RawFetchResult``. Raises
``UpstreamUnavailable`` on every non-recoverable condition — it NEVER
returns an empty or dummy result on failure.

Scope boundaries:
  - envelope shape only is checked here (non-empty ``resultSets`` list of
    objects carrying ``name``/``headers``/``rowSet``); deep row/column
    validation belongs to ``validation.py``;
  - idempotency, locking, and atomic writes belong to storage/runner.
"""

from __future__ import annotations

import email.utils
import logging
import time
import urllib.parse
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import requests

from wnba_pipeline import contract
from wnba_pipeline.contract import (
    ExtractionParams,
    RawFetchResult,
    UpstreamUnavailable,
)
from wnba_pipeline.http_client import CircuitBreaker, HttpConfig, get_json

logger = logging.getLogger("wnba_pipeline.extractor")

_REQUIRED_RESULT_SET_KEYS: tuple[str, ...] = ("name", "headers", "rowSet")


def build_api_params(params: ExtractionParams) -> dict[str, str]:
    """Complete leaguedashteamstats query-parameter set.

    This is the authoritative page-equivalent parameter list (see
    docs/data-contract.md; docs/source-contract.md documents the live
    capture). Every key the endpoint requires is present — the empty-string
    and zero-valued defaults are mandatory: omitting them changes (or
    breaks) the endpoint's behaviour. All values are strings.
    """
    return {
        "Conference": "",
        "DateFrom": "",
        "DateTo": "",
        "Division": "",
        "GameScope": "",
        "GameSegment": "",
        "LastNGames": str(params.last_n_games),
        "LeagueID": contract.WNBA_LEAGUE_ID,
        "Location": "",
        "MeasureType": params.measure_type,
        "Month": "0",
        "OpponentTeamID": "0",
        "Outcome": "",
        "PORound": "0",
        "PaceAdjust": "N",
        "PerMode": params.per_mode,
        "Period": "0",
        "PlayerExperience": "",
        "PlayerPosition": "",
        "PlusMinus": "N",
        "Rank": "N",
        "Season": params.season,
        "SeasonSegment": "",
        "SeasonType": params.season_type,
        "ShotClockRange": "",
        "StarterBench": "",
        "TeamID": "0",
        "TwoWay": "0",
        "VsConference": "",
        "VsDivision": "",
    }


def _sanitized_url(api_params: dict[str, str]) -> str:
    """Full endpoint URL with query string. Query parameters only — request
    headers are never part of any persisted or logged URL."""
    query = urllib.parse.urlencode(api_params, quote_via=urllib.parse.quote)
    return f"{contract.SOURCE_ENDPOINT}?{query}"


def _http_date_to_iso_utc(value: str | None) -> str | None:
    """Parse an HTTP Date header to an ISO-8601 UTC timestamp, or None."""
    if value is None or not value.strip():
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _envelope_problem(payload: dict[str, Any]) -> str | None:
    """Return a short description of the envelope defect, or None if the
    top-level envelope is usable. Shape check only — deep validation is
    validation.py's job."""
    result_sets = payload.get("resultSets")
    if not isinstance(result_sets, list) or not result_sets:
        return "resultSets missing, not a list, or empty"
    for index, entry in enumerate(result_sets):
        if not isinstance(entry, dict):
            return f"resultSets[{index}] is not an object"
        for key in _REQUIRED_RESULT_SET_KEYS:
            if key not in entry:
                return f"resultSets[{index}] missing key {key!r}"
    return None


def fetch_team_stats(
    params: ExtractionParams,
    *,
    http: HttpConfig | None = None,
    session: requests.Session | None = None,
    breaker: CircuitBreaker | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rng: Any = None,
) -> RawFetchResult:
    """Fetch one traditional team-stats dataset and return a fully populated
    ``RawFetchResult``. Raises ``UpstreamUnavailable`` (never returns a
    dummy/empty result) whenever the upstream cannot produce a usable
    response.

    ``sleep``/``rng`` are pass-throughs to the HTTP layer so tests never
    actually wait.
    """
    config = http if http is not None else HttpConfig()
    api_params = build_api_params(params)

    payload, raw_bytes, http_status, date_header, request_count, retry_count = get_json(
        contract.SOURCE_ENDPOINT,
        api_params,
        config,
        session=session,
        breaker=breaker,
        sleep=sleep,
        rng=rng,
    )

    problem = _envelope_problem(payload)
    if problem is not None:
        # Key names only — never bodies or header values.
        logger.warning("unexpected envelope: %s", problem)
        raise UpstreamUnavailable(
            "unexpected_envelope",
            http_status=http_status,
            request_count=request_count,
            retry_count=retry_count,
        )

    source_checksum = contract.sha256_hex(raw_bytes)
    logger.info(
        "fetch ok key=%s status=%d requests=%d retries=%d sha256=%s bytes=%d",
        params.extraction_key(),
        http_status,
        request_count,
        retry_count,
        source_checksum,
        len(raw_bytes),
    )

    return RawFetchResult(
        endpoint=contract.SOURCE_ENDPOINT,
        url=_sanitized_url(api_params),
        params=params,
        payload=payload,
        raw_bytes=raw_bytes,
        source_checksum=source_checksum,
        fetched_at_utc=datetime.now(timezone.utc).isoformat(),
        http_status=http_status,
        request_count=request_count,
        retry_count=retry_count,
        source_observed_at_utc=_http_date_to_iso_utc(date_header),
    )
