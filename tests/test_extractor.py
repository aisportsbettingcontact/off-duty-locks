"""Tests for wnba_pipeline.extractor.

All network I/O is mocked with `responses` against the real contract
endpoint URL (never hitting the network). Synthetic payloads use the pinned
2026 reference team set from fixtures/teams-2026-reference.json verbatim.
"""

from __future__ import annotations

import email.utils
import hashlib
import json
import urllib.parse
from datetime import datetime, timezone

import pytest
import responses

from wnba_pipeline import contract
from wnba_pipeline.contract import ExtractionParams, UpstreamUnavailable
from wnba_pipeline.extractor import build_api_params, fetch_team_stats
from wnba_pipeline.http_client import HttpConfig

ENDPOINT = contract.SOURCE_ENDPOINT

# Authoritative complete parameter set for leaguedashteamstats (page
# defaults for stats.wnba.com/teams/traditional with the target filters).
EXPECTED_DEFAULT_API_PARAMS = {
    "Conference": "",
    "DateFrom": "",
    "DateTo": "",
    "Division": "",
    "GameScope": "",
    "GameSegment": "",
    "LastNGames": "7",
    "LeagueID": "10",
    "Location": "",
    "MeasureType": "Base",
    "Month": "0",
    "OpponentTeamID": "0",
    "Outcome": "",
    "PORound": "0",
    "PaceAdjust": "N",
    "PerMode": "PerGame",
    "Period": "0",
    "PlayerExperience": "",
    "PlayerPosition": "",
    "PlusMinus": "N",
    "Rank": "N",
    "Season": "2026",
    "SeasonSegment": "",
    "SeasonType": "Regular Season",
    "ShotClockRange": "",
    "StarterBench": "",
    "TeamID": "0",
    "TwoWay": "0",
    "VsConference": "",
    "VsDivision": "",
}

# Official result-set header order for MeasureType=Base (traditional),
# including a sample of rank/CF extras that must survive into the payload.
RESULT_HEADERS = [
    "TEAM_ID", "TEAM_NAME", "GP", "W", "L", "W_PCT", "MIN",
    "FGM", "FGA", "FG_PCT", "FG3M", "FG3A", "FG3_PCT",
    "FTM", "FTA", "FT_PCT", "OREB", "DREB", "REB", "AST", "TOV",
    "STL", "BLK", "BLKA", "PF", "PFD", "PTS", "PLUS_MINUS",
    "GP_RANK", "W_RANK", "PTS_RANK", "CFID", "CFPARAMS",
]


def build_reference_payload(fixtures_dir):
    """Schema-accurate synthetic leaguedashteamstats payload built from the
    pinned 2026 reference team set (used verbatim, per fixture rules)."""
    reference = json.loads(
        (fixtures_dir / "teams-2026-reference.json").read_text(encoding="utf-8")
    )
    teams = sorted(reference["teams"].items(), key=lambda kv: kv[1].lower())
    row_set = []
    for index, (team_id, team_name) in enumerate(teams):
        made = round(28.0 + 0.3 * index, 1)
        attempted = round(made + 40.0, 1)
        row_set.append([
            int(team_id), team_name, 7, 4, 3, 0.571, 201.4,
            made, attempted, round(made / attempted, 3),
            8.1, 24.3, 0.333, 15.2, 18.8, 0.809,
            8.5, 27.3, 35.8, 19.4, 13.2, 7.5, 4.1, 3.9, 17.8, 18.2,
            round(2 * made + 8.1 + 15.2, 1), 1.5,
            index + 1, index + 1, index + 1, 10, str(team_id),
        ])
    assert all(len(row) == len(RESULT_HEADERS) for row in row_set)
    return {
        "resource": "leaguedashteamstats",
        "parameters": {"LeagueID": "10", "Season": "2026"},
        "resultSets": [
            {
                "name": "LeagueDashTeamStats",
                "headers": RESULT_HEADERS,
                "rowSet": row_set,
            }
        ],
    }


class RecordingSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class ZeroRng:
    def uniform(self, low: float, high: float) -> float:
        return 0.0


def _query_of(url: str) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(
        urllib.parse.urlsplit(url).query, keep_blank_values=True
    )


# ---------------------------------------------------------------------------
# build_api_params
# ---------------------------------------------------------------------------

def test_build_api_params_is_complete_and_exact():
    api_params = build_api_params(ExtractionParams())
    assert api_params == EXPECTED_DEFAULT_API_PARAMS
    assert all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in api_params.items()
    )


def test_build_api_params_reflects_custom_extraction_params():
    params = ExtractionParams(
        season="2025",
        season_type="Playoffs",
        last_n_games=10,
        measure_type="Advanced",
        per_mode="Totals",
    )
    api_params = build_api_params(params)
    assert api_params["Season"] == "2025"
    assert api_params["SeasonType"] == "Playoffs"
    assert api_params["LastNGames"] == "10"
    assert api_params["MeasureType"] == "Advanced"
    assert api_params["PerMode"] == "Totals"
    # Everything else stays at the required defaults.
    for key, value in EXPECTED_DEFAULT_API_PARAMS.items():
        if key not in ("Season", "SeasonType", "LastNGames",
                       "MeasureType", "PerMode"):
            assert api_params[key] == value


# ---------------------------------------------------------------------------
# Success path: every RawFetchResult field
# ---------------------------------------------------------------------------

def test_fetch_team_stats_success_fills_every_field(fixtures_dir):
    payload_obj = build_reference_payload(fixtures_dir)
    body = json.dumps(payload_obj)
    observed = datetime(2026, 7, 17, 14, 30, 0, tzinfo=timezone.utc)
    date_header = email.utils.format_datetime(observed, usegmt=True)
    params = ExtractionParams()

    before = datetime.now(timezone.utc)
    with responses.RequestsMock() as rsps:
        rsps.get(
            ENDPOINT,
            body=body,
            status=200,
            content_type="application/json",
            headers={"Date": date_header},
        )
        result = fetch_team_stats(params, http=HttpConfig(), sleep=RecordingSleep())
        sent = _query_of(rsps.calls[0].request.url)
        sent_headers = rsps.calls[0].request.headers
    after = datetime.now(timezone.utc)

    # The request carried the exact page-equivalent parameters.
    assert sent["Season"] == ["2026"]
    assert sent["SeasonType"] == ["Regular Season"]
    assert sent["LastNGames"] == ["7"]
    assert sent["LeagueID"] == ["10"]
    assert sent["MeasureType"] == ["Base"]
    assert sent["PerMode"] == ["PerGame"]
    assert sent == {k: [v] for k, v in EXPECTED_DEFAULT_API_PARAMS.items()}
    assert "Cookie" not in sent_headers
    assert "Authorization" not in sent_headers

    # Every RawFetchResult field is populated correctly.
    assert result.endpoint == ENDPOINT
    split = urllib.parse.urlsplit(result.url)
    assert f"{split.scheme}://{split.netloc}{split.path}" == ENDPOINT
    assert _query_of(result.url) == {
        k: [v] for k, v in EXPECTED_DEFAULT_API_PARAMS.items()
    }
    assert result.params is params
    assert result.payload == payload_obj
    assert result.raw_bytes == body.encode("utf-8")
    assert result.source_checksum == hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert result.source_checksum == contract.sha256_hex(result.raw_bytes)
    fetched_at = datetime.fromisoformat(result.fetched_at_utc)
    assert fetched_at.tzinfo is not None
    assert before <= fetched_at <= after
    assert result.http_status == 200
    assert result.request_count == 1
    assert result.retry_count == 0
    assert result.source_observed_at_utc == "2026-07-17T14:30:00+00:00"

    # The reference team set flowed through verbatim.
    rows = result.payload["resultSets"][0]["rowSet"]
    assert len(rows) == 15
    assert [row[1] for row in rows] == sorted(
        (row[1] for row in rows), key=str.lower
    )


def test_fetch_team_stats_survives_retry_and_reports_counts(fixtures_dir):
    body = json.dumps(build_reference_payload(fixtures_dir))
    sleeper = RecordingSleep()
    with responses.RequestsMock() as rsps:
        rsps.get(ENDPOINT, status=503)
        rsps.get(ENDPOINT, body=body, status=200, content_type="application/json")
        result = fetch_team_stats(
            ExtractionParams(), http=HttpConfig(), sleep=sleeper, rng=ZeroRng()
        )
        assert len(rsps.calls) == 2
    assert result.http_status == 200
    assert result.request_count == 2
    assert result.retry_count == 1
    assert sleeper.calls == [1.5]  # injected sleep — no real waiting


# ---------------------------------------------------------------------------
# Date header handling
# ---------------------------------------------------------------------------

def test_missing_date_header_yields_none_observed(fixtures_dir):
    body = json.dumps(build_reference_payload(fixtures_dir))
    with responses.RequestsMock() as rsps:
        rsps.get(ENDPOINT, body=body, status=200, content_type="application/json")
        result = fetch_team_stats(ExtractionParams(), sleep=RecordingSleep())
    assert result.source_observed_at_utc is None


def test_unparseable_date_header_yields_none_observed(fixtures_dir):
    body = json.dumps(build_reference_payload(fixtures_dir))
    with responses.RequestsMock() as rsps:
        rsps.get(
            ENDPOINT,
            body=body,
            status=200,
            content_type="application/json",
            headers={"Date": "definitely not an http date"},
        )
        result = fetch_team_stats(ExtractionParams(), sleep=RecordingSleep())
    assert result.source_observed_at_utc is None


# ---------------------------------------------------------------------------
# Envelope verification (shape only — deep checks are validation.py's job)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad_payload",
    [
        {},                                                    # no resultSets
        {"resource": "leaguedashteamstats"},                   # no resultSets
        {"resultSets": []},                                    # empty list
        {"resultSets": "LeagueDashTeamStats"},                 # not a list
        {"resultSets": [["not", "a", "dict"]]},                # non-dict entry
        {"resultSets": [{"headers": [], "rowSet": []}]},       # missing name
        {"resultSets": [{"name": "X", "rowSet": []}]},         # missing headers
        {"resultSets": [{"name": "X", "headers": []}]},        # missing rowSet
    ],
    ids=[
        "empty-object", "no-resultsets", "empty-list", "not-a-list",
        "non-dict-entry", "missing-name", "missing-headers", "missing-rowset",
    ],
)
def test_unexpected_envelope_raises_upstream_unavailable(bad_payload):
    with responses.RequestsMock() as rsps:
        rsps.get(ENDPOINT, json=bad_payload, status=200)
        with pytest.raises(UpstreamUnavailable) as excinfo:
            fetch_team_stats(ExtractionParams(), sleep=RecordingSleep())
    err = excinfo.value
    assert err.reason == "unexpected_envelope"
    assert err.http_status == 200
    assert err.request_count == 1


def test_envelope_with_empty_rowset_is_accepted_shape(fixtures_dir):
    # Shape-valid but empty rowSet: NOT the extractor's call to reject —
    # validation.py quarantines it (empty data is never silently accepted
    # downstream, and the extractor must not deep-validate).
    payload = {
        "resultSets": [
            {"name": "LeagueDashTeamStats", "headers": RESULT_HEADERS, "rowSet": []}
        ]
    }
    with responses.RequestsMock() as rsps:
        rsps.get(ENDPOINT, json=payload, status=200)
        result = fetch_team_stats(ExtractionParams(), sleep=RecordingSleep())
    assert result.payload == payload


# ---------------------------------------------------------------------------
# Failure propagation: never a dummy result
# ---------------------------------------------------------------------------

def test_403_propagates_fail_fast_with_single_request():
    with responses.RequestsMock() as rsps:
        rsps.get(ENDPOINT, status=403, body="forbidden")
        with pytest.raises(UpstreamUnavailable) as excinfo:
            fetch_team_stats(ExtractionParams(), sleep=RecordingSleep())
        assert len(rsps.calls) == 1
    err = excinfo.value
    assert err.reason == "http_403_forbidden"
    assert err.http_status == 403
    assert err.request_count == 1
    assert err.retry_count == 0


def test_retries_exhausted_propagates_counts():
    cfg = HttpConfig(max_retries=2)
    with responses.RequestsMock() as rsps:
        rsps.get(ENDPOINT, status=503)
        with pytest.raises(UpstreamUnavailable) as excinfo:
            fetch_team_stats(
                ExtractionParams(), http=cfg, sleep=RecordingSleep(), rng=ZeroRng()
            )
        assert len(rsps.calls) == 3
    err = excinfo.value
    assert err.reason == "http_503_server_error"
    assert err.request_count == 3
    assert err.retry_count == 2
