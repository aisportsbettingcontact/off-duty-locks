"""Tests for wnba_pipeline.http_client.

All network I/O is mocked with the `responses` library; sleep and clock are
injected so nothing ever waits. No live requests are ever attempted (the
sandbox blocks *.wnba.com anyway) — a neutral host is used here to prove the
client is URL-agnostic.
"""

from __future__ import annotations

import email.utils
import json
import logging
from datetime import datetime, timedelta, timezone

import pytest
import requests
import responses

from wnba_pipeline.contract import UpstreamUnavailable
from wnba_pipeline.http_client import (
    PUBLIC_BROWSER_HEADERS,
    CircuitBreaker,
    HttpConfig,
    get_json,
)

URL = "https://stats.example.test/stats/leaguedashteamstats"


class RecordingSleep:
    """Injected in place of time.sleep; records requested delays."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class StubRng:
    """Deterministic stand-in for random: uniform() returns a fixed value."""

    def __init__(self, value: float = 0.0) -> None:
        self.value = value
        self.calls: list[tuple[float, float]] = []

    def uniform(self, low: float, high: float) -> float:
        self.calls.append((low, high))
        return self.value


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

def test_success_returns_payload_bytes_status_date_and_counts():
    body = json.dumps(
        {"resultSets": [{"name": "X", "headers": ["A"], "rowSet": [[1]]}]}
    )
    sleeper = RecordingSleep()
    with responses.RequestsMock() as rsps:
        rsps.get(
            URL,
            body=body,
            status=200,
            content_type="application/json",
            headers={"Date": "Fri, 17 Jul 2026 14:30:00 GMT"},
        )
        payload, raw, status, date_header, request_count, retry_count = get_json(
            URL, {"Season": "2026"}, HttpConfig(), sleep=sleeper
        )
        sent_headers = rsps.calls[0].request.headers

    assert payload == json.loads(body)
    assert raw == body.encode("utf-8")
    assert status == 200
    assert date_header == "Fri, 17 Jul 2026 14:30:00 GMT"
    assert request_count == 1
    assert retry_count == 0
    assert sleeper.calls == []
    # Browser-consistent public headers were sent; no cookies, no auth.
    for name, value in PUBLIC_BROWSER_HEADERS.items():
        assert sent_headers[name] == value
    assert "Authorization" not in sent_headers
    assert "Cookie" not in sent_headers


def test_missing_date_header_returned_as_none():
    with responses.RequestsMock() as rsps:
        rsps.get(URL, json={"ok": True}, status=200)
        _, _, _, date_header, _, _ = get_json(
            URL, {}, HttpConfig(), sleep=RecordingSleep()
        )
    assert date_header is None


# ---------------------------------------------------------------------------
# Fail-fast statuses
# ---------------------------------------------------------------------------

def test_403_fails_fast_with_exactly_one_request():
    sleeper = RecordingSleep()
    with responses.RequestsMock() as rsps:
        rsps.get(URL, status=403, body="forbidden")
        with pytest.raises(UpstreamUnavailable) as excinfo:
            get_json(URL, {}, HttpConfig(), sleep=sleeper)
        assert len(rsps.calls) == 1
    err = excinfo.value
    assert err.reason == "http_403_forbidden"
    assert err.http_status == 403
    assert err.request_count == 1
    assert err.retry_count == 0
    assert sleeper.calls == []


def test_404_fails_fast_with_exactly_one_request():
    sleeper = RecordingSleep()
    with responses.RequestsMock() as rsps:
        rsps.get(URL, status=404, body="not found")
        with pytest.raises(UpstreamUnavailable) as excinfo:
            get_json(URL, {}, HttpConfig(), sleep=sleeper)
        assert len(rsps.calls) == 1
    err = excinfo.value
    assert err.reason == "http_404_not_found"
    assert err.http_status == 404
    assert err.request_count == 1
    assert err.retry_count == 0
    assert sleeper.calls == []


def test_unexpected_status_fails_fast():
    with responses.RequestsMock() as rsps:
        rsps.get(URL, status=418, body="teapot")
        with pytest.raises(UpstreamUnavailable) as excinfo:
            get_json(URL, {}, HttpConfig(), sleep=RecordingSleep())
        assert len(rsps.calls) == 1
    assert excinfo.value.reason == "http_418_unexpected"
    assert excinfo.value.http_status == 418


# ---------------------------------------------------------------------------
# 429 rate limiting
# ---------------------------------------------------------------------------

def test_429_honors_numeric_retry_after():
    sleeper = RecordingSleep()
    with responses.RequestsMock() as rsps:
        rsps.get(URL, status=429, headers={"Retry-After": "7"})
        rsps.get(URL, json={"ok": True})
        payload, _, status, _, request_count, retry_count = get_json(
            URL, {}, HttpConfig(), sleep=sleeper
        )
        assert len(rsps.calls) == 2
    assert payload == {"ok": True}
    assert status == 200
    # The injected sleep received exactly the header value.
    assert sleeper.calls == [7.0]
    assert request_count == 2
    assert retry_count == 1


def test_429_with_absurd_retry_after_is_capped():
    sleeper = RecordingSleep()
    with responses.RequestsMock() as rsps:
        rsps.get(URL, status=429, headers={"Retry-After": "999999"})
        rsps.get(URL, json={"ok": True})
        get_json(URL, {}, HttpConfig(), sleep=sleeper)
    assert sleeper.calls == [120.0]  # retry_after_cap_s


def test_429_with_http_date_retry_after_far_future_is_capped():
    sleeper = RecordingSleep()
    far_future = email.utils.format_datetime(
        datetime.now(timezone.utc) + timedelta(hours=6), usegmt=True
    )
    with responses.RequestsMock() as rsps:
        rsps.get(URL, status=429, headers={"Retry-After": far_future})
        rsps.get(URL, json={"ok": True})
        get_json(URL, {}, HttpConfig(), sleep=sleeper)
    assert sleeper.calls == [120.0]


def test_429_without_retry_after_falls_back_to_backoff():
    sleeper = RecordingSleep()
    rng = StubRng(0.0)
    with responses.RequestsMock() as rsps:
        rsps.get(URL, status=429)
        rsps.get(URL, json={"ok": True})
        get_json(URL, {}, HttpConfig(), sleep=sleeper, rng=rng)
    assert sleeper.calls == [1.5]  # backoff_base_s * 2**0, zero jitter
    assert rng.calls == [(-0.25, 0.25)]


def test_429_with_unparseable_retry_after_falls_back_to_backoff():
    sleeper = RecordingSleep()
    with responses.RequestsMock() as rsps:
        rsps.get(URL, status=429, headers={"Retry-After": "soonish"})
        rsps.get(URL, json={"ok": True})
        get_json(URL, {}, HttpConfig(), sleep=sleeper, rng=StubRng(0.0))
    assert sleeper.calls == [1.5]


def test_429_exhausted_raises_with_counts():
    cfg = HttpConfig(max_retries=2)
    sleeper = RecordingSleep()
    with responses.RequestsMock() as rsps:
        rsps.get(URL, status=429, headers={"Retry-After": "1"})
        with pytest.raises(UpstreamUnavailable) as excinfo:
            get_json(URL, {}, cfg, sleep=sleeper)
        assert len(rsps.calls) == 3
    err = excinfo.value
    assert err.reason == "http_429_rate_limited"
    assert err.http_status == 429
    assert err.request_count == 3
    assert err.retry_count == 2


# ---------------------------------------------------------------------------
# 5xx and transport failures
# ---------------------------------------------------------------------------

def test_5xx_then_success_retries():
    sleeper = RecordingSleep()
    with responses.RequestsMock() as rsps:
        rsps.get(URL, status=503)
        rsps.get(URL, json={"ok": True})
        payload, _, status, _, request_count, retry_count = get_json(
            URL, {}, HttpConfig(), sleep=sleeper, rng=StubRng(0.0)
        )
    assert payload == {"ok": True}
    assert status == 200
    assert request_count == 2
    assert retry_count == 1
    assert sleeper.calls == [1.5]


def test_5xx_exhausted_raises_with_last_reason_and_counts():
    cfg = HttpConfig(max_retries=4)
    sleeper = RecordingSleep()
    with responses.RequestsMock() as rsps:
        rsps.get(URL, status=503)  # last registration repeats
        with pytest.raises(UpstreamUnavailable) as excinfo:
            get_json(URL, {}, cfg, sleep=sleeper, rng=StubRng(0.0))
        assert len(rsps.calls) == 5  # 1 initial + 4 retries
    err = excinfo.value
    assert err.reason == "http_503_server_error"
    assert err.http_status == 503
    assert err.request_count == 5
    assert err.retry_count == 4
    assert sleeper.calls == [1.5, 3.0, 6.0, 12.0]


def test_connect_timeout_is_retried_then_succeeds():
    sleeper = RecordingSleep()
    with responses.RequestsMock() as rsps:
        rsps.get(URL, body=requests.exceptions.ConnectTimeout("connect timed out"))
        rsps.get(URL, json={"ok": True})
        payload, _, _, _, request_count, retry_count = get_json(
            URL, {}, HttpConfig(), sleep=sleeper, rng=StubRng(0.0)
        )
    assert payload == {"ok": True}
    assert request_count == 2
    assert retry_count == 1
    assert sleeper.calls == [1.5]


def test_read_timeout_is_retried_then_succeeds():
    sleeper = RecordingSleep()
    with responses.RequestsMock() as rsps:
        rsps.get(URL, body=requests.exceptions.ReadTimeout("read timed out"))
        rsps.get(URL, json={"ok": True})
        payload, _, _, _, request_count, retry_count = get_json(
            URL, {}, HttpConfig(), sleep=sleeper, rng=StubRng(0.0)
        )
    assert payload == {"ok": True}
    assert request_count == 2
    assert retry_count == 1


def test_connection_errors_exhausted_raise_with_reason():
    cfg = HttpConfig(max_retries=1)
    with responses.RequestsMock() as rsps:
        rsps.get(URL, body=requests.exceptions.ConnectionError("refused"))
        with pytest.raises(UpstreamUnavailable) as excinfo:
            get_json(URL, {}, cfg, sleep=RecordingSleep(), rng=StubRng(0.0))
        assert len(rsps.calls) == 2
    err = excinfo.value
    assert err.reason == "connection_error"
    assert err.http_status is None
    assert err.request_count == 2
    assert err.retry_count == 1


# ---------------------------------------------------------------------------
# Malformed / truncated JSON
# ---------------------------------------------------------------------------

def test_malformed_json_retried_once_then_raises():
    sleeper = RecordingSleep()
    with responses.RequestsMock() as rsps:
        rsps.get(URL, status=200, body="{not json at all")
        rsps.get(URL, status=200, body="{not json at all")
        with pytest.raises(UpstreamUnavailable) as excinfo:
            get_json(URL, {}, HttpConfig(), sleep=sleeper, rng=StubRng(0.0))
        assert len(rsps.calls) == 2  # exactly one retry
    err = excinfo.value
    assert err.reason == "malformed_json"
    assert err.http_status == 200
    assert err.request_count == 2
    assert err.retry_count == 1
    assert sleeper.calls == [1.5]


def test_truncated_json_body_recovers_on_retry():
    truncated = '{"resultSets": [{"name": "LeagueDashTeamStats", "hea'
    with responses.RequestsMock() as rsps:
        rsps.get(URL, status=200, body=truncated)
        rsps.get(URL, json={"ok": True})
        payload, _, _, _, request_count, retry_count = get_json(
            URL, {}, HttpConfig(), sleep=RecordingSleep(), rng=StubRng(0.0)
        )
    assert payload == {"ok": True}
    assert request_count == 2
    assert retry_count == 1


def test_non_object_json_is_treated_as_malformed():
    with responses.RequestsMock() as rsps:
        rsps.get(URL, status=200, body=json.dumps([1, 2, 3]))
        rsps.get(URL, status=200, body=json.dumps([1, 2, 3]))
        with pytest.raises(UpstreamUnavailable) as excinfo:
            get_json(URL, {}, HttpConfig(), sleep=RecordingSleep(), rng=StubRng(0.0))
    assert excinfo.value.reason == "malformed_json"


# ---------------------------------------------------------------------------
# Backoff shape
# ---------------------------------------------------------------------------

def test_backoff_sequence_is_exponential_and_capped():
    cfg = HttpConfig(max_retries=4, backoff_base_s=10.0, backoff_max_s=60.0)
    sleeper = RecordingSleep()
    with responses.RequestsMock() as rsps:
        rsps.get(URL, status=502)
        with pytest.raises(UpstreamUnavailable):
            get_json(URL, {}, cfg, sleep=sleeper, rng=StubRng(0.0))
    # 10, 20, 40, then 80 capped to 60.
    assert sleeper.calls == [10.0, 20.0, 40.0, 60.0]


def test_backoff_jitter_uses_random_uniform_on_jitter_fraction():
    rng = StubRng(0.25)  # always the +jitter_fraction edge
    sleeper = RecordingSleep()
    with responses.RequestsMock() as rsps:
        rsps.get(URL, status=503)
        rsps.get(URL, json={"ok": True})
        get_json(URL, {}, HttpConfig(), sleep=sleeper, rng=rng)
    assert rng.calls == [(-0.25, 0.25)]
    assert sleeper.calls == [pytest.approx(1.5 * 1.25)]


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

def test_circuit_breaker_state_machine_with_injected_clock():
    clock = FakeClock(0.0)
    breaker = CircuitBreaker(threshold=2, cooldown_s=300.0, clock=clock)

    breaker.before_request()  # closed
    breaker.record_failure()
    assert breaker.state == "closed"
    breaker.before_request()  # still closed below threshold
    breaker.record_failure()  # hits threshold -> open
    assert breaker.state == "open"
    with pytest.raises(UpstreamUnavailable) as excinfo:
        breaker.before_request()
    assert excinfo.value.reason == "circuit breaker open"

    clock.advance(299.9)  # still cooling
    assert breaker.state == "open"
    with pytest.raises(UpstreamUnavailable):
        breaker.before_request()

    clock.advance(0.2)  # past cooldown -> half-open probe allowed
    assert breaker.state == "half_open"
    breaker.before_request()

    breaker.record_failure()  # probe failed -> re-open with fresh cooldown
    assert breaker.state == "open"
    with pytest.raises(UpstreamUnavailable):
        breaker.before_request()

    clock.advance(300.1)  # half-open again; this time the probe succeeds
    breaker.before_request()
    breaker.record_success()
    assert breaker.state == "closed"
    breaker.record_failure()  # a single new failure does not re-open
    assert breaker.state == "closed"
    breaker.before_request()


def test_circuit_breaker_opens_after_threshold_and_half_opens_in_get_json():
    clock = FakeClock(1000.0)
    breaker = CircuitBreaker(threshold=3, cooldown_s=300.0, clock=clock)
    cfg = HttpConfig(max_retries=0)
    sleeper = RecordingSleep()

    with responses.RequestsMock() as rsps:
        rsps.get(URL, body=requests.exceptions.ConnectionError("refused"))
        for _ in range(3):
            with pytest.raises(UpstreamUnavailable) as excinfo:
                get_json(URL, {}, cfg, breaker=breaker, sleep=sleeper)
            assert excinfo.value.reason == "connection_error"
        assert len(rsps.calls) == 3

        # Breaker is now open: no request is made at all.
        with pytest.raises(UpstreamUnavailable) as excinfo:
            get_json(URL, {}, cfg, breaker=breaker, sleep=sleeper)
        assert excinfo.value.reason == "circuit breaker open"
        assert excinfo.value.request_count == 0
        assert len(rsps.calls) == 3

        # After the cooldown it half-opens and a successful probe closes it.
        clock.advance(300.0)
        rsps.get(URL, json={"ok": True})
        payload, _, status, _, _, _ = get_json(
            URL, {}, cfg, breaker=breaker, sleep=sleeper
        )
        assert payload == {"ok": True}
        assert status == 200
        assert breaker.state == "closed"


def test_http_responses_reset_breaker_failure_count():
    clock = FakeClock(0.0)
    breaker = CircuitBreaker(threshold=2, cooldown_s=300.0, clock=clock)
    cfg = HttpConfig(max_retries=0)
    with responses.RequestsMock() as rsps:
        rsps.get(URL, body=requests.exceptions.ConnectionError("refused"))
        with pytest.raises(UpstreamUnavailable):
            get_json(URL, {}, cfg, breaker=breaker, sleep=RecordingSleep())
        # A received HTTP response (even an error status) proves transport
        # works and resets the consecutive transport-failure count.
        rsps.get(URL, status=404)
        with pytest.raises(UpstreamUnavailable):
            get_json(URL, {}, cfg, breaker=breaker, sleep=RecordingSleep())
        rsps.get(URL, body=requests.exceptions.ConnectionError("refused"))
        with pytest.raises(UpstreamUnavailable) as excinfo:
            get_json(URL, {}, cfg, breaker=breaker, sleep=RecordingSleep())
        assert excinfo.value.reason == "connection_error"
    assert breaker.state == "closed"  # 1 consecutive failure < threshold 2


def test_circuit_breaker_from_config_uses_config_values():
    clock = FakeClock(0.0)
    cfg = HttpConfig(circuit_breaker_threshold=1, circuit_breaker_cooldown_s=50.0)
    breaker = CircuitBreaker.from_config(cfg, clock=clock)
    breaker.record_failure()
    assert breaker.state == "open"
    clock.advance(50.0)
    assert breaker.state == "half_open"


# ---------------------------------------------------------------------------
# Logging hygiene
# ---------------------------------------------------------------------------

def test_logs_one_record_per_attempt_and_never_header_values(caplog):
    caplog.set_level(logging.DEBUG, logger="wnba_pipeline.http")
    date_value = "Fri, 17 Jul 2026 14:30:00 GMT"
    with responses.RequestsMock() as rsps:
        rsps.get(URL, status=503)
        rsps.get(URL, json={"ok": True}, headers={"Date": date_value})
        get_json(
            URL, {"Season": "2026"}, HttpConfig(),
            sleep=RecordingSleep(), rng=StubRng(0.0),
        )

    records = [r for r in caplog.records if r.name == "wnba_pipeline.http"]
    assert len(records) == 2  # one structured record per attempt
    messages = [r.getMessage() for r in records]
    # Attempt metadata is present: path, attempt number, status, backoff.
    assert "/stats/leaguedashteamstats" in messages[0]
    assert "attempt=1" in messages[0] and "status=503" in messages[0]
    assert "attempt=2" in messages[1] and "status=200" in messages[1]
    # No header value — request or response — ever appears in a log record.
    for message in messages:
        for header_value in PUBLIC_BROWSER_HEADERS.values():
            assert header_value not in message
        assert date_value not in message
        assert "Mozilla" not in message
        assert "https://stats.example.test" not in message  # host never logged
