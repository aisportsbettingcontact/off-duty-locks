"""Adversarial HTTP-layer and runner-concurrency challenges.

Uses the ``responses`` library to simulate hostile upstream behavior and drives
the runner concurrently to prove overlap rejection and last-known-good
protection. Sleeps are injected so nothing actually waits.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
import requests
import responses

from wnba_pipeline import contract, storage
from wnba_pipeline.contract import (
    EXIT_LOCK_HELD,
    EXIT_OK,
    ExtractionParams,
    RunStatus,
    UpstreamUnavailable,
)
from wnba_pipeline.extractor import fetch_team_stats
from wnba_pipeline.http_client import HttpConfig
from wnba_pipeline.runner import run_once
from wnba_pipeline.storage import Store

from tests._builders import (
    make_expected_team_set,
    make_outcome_passed,
    make_raw,
    make_snapshot,
)

REPO = Path(__file__).resolve().parents[2]
ADV = REPO / "fixtures" / "adversarial"
ENDPOINT = contract.SOURCE_ENDPOINT
VALID_BODY = (REPO / "fixtures" / "sanitized"
              / "leaguedashteamstats_2026_lastn7.json").read_text()


def _no_sleep_collector():
    sleeps: list[float] = []
    return sleeps, (lambda s: sleeps.append(s))


def _fetch(**kw):
    """fetch_team_stats with sleeps captured and a tight retry budget."""
    sleeps, sleep = _no_sleep_collector()
    cfg = kw.pop("config", HttpConfig(max_retries=3, backoff_base_s=0.01,
                                      backoff_max_s=0.05, retry_after_cap_s=120.0))
    return sleeps, lambda: fetch_team_stats(
        ExtractionParams(), http=cfg, sleep=sleep, **kw)


# --------------------------------------------------------------------------- #
# HTTP status challenges
# --------------------------------------------------------------------------- #

@responses.activate
def test_403_fails_fast_single_request():
    responses.add(responses.GET, ENDPOINT, status=403)
    responses.add(responses.GET, ENDPOINT, status=200, body=VALID_BODY)
    _, call = _fetch()
    with pytest.raises(UpstreamUnavailable) as exc:
        call()
    assert exc.value.http_status == 403
    assert len(responses.calls) == 1  # never proceeded past the refusal


@responses.activate
def test_404_fails_fast():
    responses.add(responses.GET, ENDPOINT, status=404)
    _, call = _fetch()
    with pytest.raises(UpstreamUnavailable) as exc:
        call()
    assert exc.value.http_status == 404
    assert len(responses.calls) == 1


@responses.activate
def test_429_honors_retry_after_then_succeeds():
    responses.add(responses.GET, ENDPOINT, status=429, headers={"Retry-After": "7"})
    responses.add(responses.GET, ENDPOINT, status=200, body=VALID_BODY)
    sleeps, call = _fetch()
    raw = call()
    assert raw.http_status == 200
    assert raw.retry_count >= 1
    # The injected sleep received the Retry-After value (7s), not a backoff guess.
    assert any(abs(s - 7.0) < 1e-6 for s in sleeps), sleeps


@responses.activate
def test_429_absurd_retry_after_is_capped():
    responses.add(responses.GET, ENDPOINT, status=429,
                  headers={"Retry-After": "99999"})
    responses.add(responses.GET, ENDPOINT, status=200, body=VALID_BODY)
    sleeps, call = _fetch()
    call()
    assert max(sleeps) <= 120.0  # retry_after_cap_s


@responses.activate
def test_429_exhausted_raises():
    for _ in range(8):
        responses.add(responses.GET, ENDPOINT, status=429)
    _, call = _fetch(config=HttpConfig(max_retries=2, backoff_base_s=0.01))
    with pytest.raises(UpstreamUnavailable):
        call()
    assert len(responses.calls) <= 3  # 1 + max_retries


@responses.activate
def test_500_storm_is_bounded():
    for _ in range(12):
        responses.add(responses.GET, ENDPOINT, status=500)
    _, call = _fetch(config=HttpConfig(max_retries=2, backoff_base_s=0.01))
    with pytest.raises(UpstreamUnavailable):
        call()
    assert len(responses.calls) <= 3  # bounded: 1 + max_retries


@responses.activate
def test_5xx_then_success_recovers():
    responses.add(responses.GET, ENDPOINT, status=503)
    responses.add(responses.GET, ENDPOINT, status=200, body=VALID_BODY)
    _, call = _fetch()
    raw = call()
    assert raw.http_status == 200
    assert raw.retry_count >= 1


@responses.activate
def test_connection_error_retried_then_succeeds():
    responses.add(responses.GET, ENDPOINT,
                  body=requests.exceptions.ConnectionError("reset"))
    responses.add(responses.GET, ENDPOINT, status=200, body=VALID_BODY)
    _, call = _fetch()
    raw = call()
    assert raw.http_status == 200


@responses.activate
def test_read_timeout_retried_then_succeeds():
    responses.add(responses.GET, ENDPOINT,
                  body=requests.exceptions.ReadTimeout("slow"))
    responses.add(responses.GET, ENDPOINT, status=200, body=VALID_BODY)
    _, call = _fetch()
    raw = call()
    assert raw.http_status == 200


@responses.activate
def test_malformed_json_raises_upstream_unavailable():
    for _ in range(6):
        responses.add(responses.GET, ENDPOINT, status=200, body="{not valid json")
    _, call = _fetch(config=HttpConfig(max_retries=3, backoff_base_s=0.01))
    with pytest.raises(UpstreamUnavailable) as exc:
        call()
    assert "malformed" in exc.value.reason.lower()


@responses.activate
def test_truncated_body_raises():
    truncated = (ADV / "truncated.json").read_text()
    for _ in range(6):
        responses.add(responses.GET, ENDPOINT, status=200, body=truncated)
    _, call = _fetch(config=HttpConfig(max_retries=3, backoff_base_s=0.01))
    with pytest.raises(UpstreamUnavailable):
        call()


@responses.activate
def test_no_secrets_in_logs(caplog):
    """Log records must never carry cookies/authorization/header values."""
    import logging
    responses.add(responses.GET, ENDPOINT, status=200, body=VALID_BODY)
    _, call = _fetch()
    with caplog.at_level(logging.DEBUG, logger="wnba_pipeline"):
        call()
    blob = "\n".join(r.getMessage() for r in caplog.records).lower()
    for bad in ("cookie", "authorization", "set-cookie", "bearer", "user-agent"):
        assert bad not in blob, f"log leaked {bad!r}"


# --------------------------------------------------------------------------- #
# Runner concurrency + LKG-protection challenges
# --------------------------------------------------------------------------- #

TEAMS = {"1611661319": "Las Vegas Aces", "1611661324": "Minnesota Lynx"}


def _passing_fakes(checksum="c1"):
    expected = make_expected_team_set(TEAMS)
    snap = make_snapshot(teams=TEAMS, source_checksum=checksum)
    return (lambda p: make_raw(source_checksum=checksum, params=p),
            lambda s: expected,
            lambda r, e: make_outcome_passed(snap, e))


def test_concurrent_runs_one_is_rejected(tmp_path):
    """Two overlapping runs on the same data root: exactly one proceeds, the
    other is rejected with LOCK_HELD (overlap is never interleaved)."""
    fetch_fn, resolve_fn, validate_fn = _passing_fakes()
    first_holds = threading.Event()
    release_first = threading.Event()

    def slow_fetch(params):
        first_holds.set()
        # Hold the lock until the second run has had its chance.
        release_first.wait(timeout=5)
        return make_raw(source_checksum="c1", params=params)

    results: dict[str, RunStatus] = {}

    def run_first():
        m, _ = run_once(ExtractionParams(), tmp_path, fetch_fn=slow_fetch,
                        resolve_teams_fn=resolve_fn, validate_fn=validate_fn)
        results["first"] = m.status

    t = threading.Thread(target=run_first)
    t.start()
    assert first_holds.wait(timeout=5), "first run never acquired the lock"
    # Second run attempts while the first still holds the lock.
    m2, code2 = run_once(ExtractionParams(), tmp_path, fetch_fn=fetch_fn,
                         resolve_teams_fn=resolve_fn, validate_fn=validate_fn)
    release_first.set()
    t.join(timeout=5)

    assert m2.status is RunStatus.LOCK_HELD
    assert code2 == EXIT_LOCK_HELD
    assert results["first"] is RunStatus.SUCCESS


def test_interrupted_lkg_write_preserves_previous(tmp_path, monkeypatch):
    """A crash during the LKG swap must leave the previous LKG intact and the
    run must not report success."""
    fetch_fn, resolve_fn, validate_fn = _passing_fakes(checksum="good")
    run_once(ExtractionParams(), tmp_path, fetch_fn=fetch_fn,
             resolve_teams_fn=resolve_fn, validate_fn=validate_fn)
    key = ExtractionParams().extraction_key()
    store = Store(tmp_path)
    before = store.current_path(key).read_bytes()

    real_replace = storage.os.replace
    target = str(store.current_path(key))

    def failing_replace(src, dst, *a, **k):
        if str(dst) == target:
            raise OSError("crash during LKG swap")
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(storage.os, "replace", failing_replace)
    fetch2, resolve2, validate2 = _passing_fakes(checksum="new")
    manifest, code = run_once(ExtractionParams(), tmp_path, fetch_fn=fetch2,
                              resolve_teams_fn=resolve2, validate_fn=validate2)
    monkeypatch.undo()

    assert manifest.status is not RunStatus.SUCCESS         # not reported as success
    assert code != EXIT_OK
    assert store.current_path(key).read_bytes() == before   # previous LKG intact


def test_failed_run_cannot_overwrite_lkg(tmp_path):
    """A validation-failing candidate never replaces good LKG data."""
    # Establish a good LKG via the real offline pipeline.
    good, code = run_once(
        ExtractionParams(), tmp_path,
        fixture_path=str(REPO / "fixtures" / "sanitized"
                         / "leaguedashteamstats_2026_lastn7.json"))
    assert code == EXIT_OK
    key = ExtractionParams().extraction_key()
    store = Store(tmp_path)
    before = store.current_path(key).read_bytes()

    # Now feed an adversarial failing candidate through the real validator.
    manifest, code = run_once(
        ExtractionParams(), tmp_path,
        fixture_path=str(ADV / "makes_exceed_attempts.json"))
    assert manifest.status is RunStatus.VALIDATION_FAILED
    assert store.current_path(key).read_bytes() == before   # LKG untouched


def test_idempotent_rerun_creates_no_duplicate(tmp_path):
    fixture = str(REPO / "fixtures" / "sanitized"
                  / "leaguedashteamstats_2026_lastn7.json")
    run_once(ExtractionParams(), tmp_path, fixture_path=fixture)
    run_once(ExtractionParams(), tmp_path, fixture_path=fixture)
    key = ExtractionParams().extraction_key()
    from wnba_pipeline.storage import encode_key
    snaps = list((Store(tmp_path).snapshots_dir / encode_key(key)).glob("*.json"))
    assert len(snaps) == 1  # second run was SUCCESS_UNCHANGED, no new snapshot
