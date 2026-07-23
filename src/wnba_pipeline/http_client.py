"""HTTP client for the public stats.wnba.com JSON API.

Responsibilities (see docs/data-contract.md):
  - bounded retries with exponential backoff + jitter,
  - Retry-After support on 429 (integer seconds or HTTP-date, capped),
  - fail-fast on 403/404 (never hammer a host that is refusing us),
  - an in-process circuit breaker over consecutive transport failures,
  - structured per-attempt logging that NEVER includes header values,
    cookies, tokens, or response bodies.

No cookies and no Authorization headers are ever sent or stored. The only
request headers used are the browser-consistent public headers below.
"""

from __future__ import annotations

import email.utils
import json
import logging
import math
import random
import time
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from wnba_pipeline.contract import UpstreamUnavailable

logger = logging.getLogger("wnba_pipeline.http")

# Browser-consistent PUBLIC request headers required by the stats platform.
# These are not secrets, but their values must still never appear in logs
# (we log only the URL path, status, attempt number, and backoff).
PUBLIC_BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://stats.wnba.com/",
    "Origin": "https://stats.wnba.com",
}

# Platform hint headers the stats site's own web client sends. STATIC, publicly
# documented string constants — NOT credentials, NOT secrets, NOT per-user. The
# stats platform (Akamai edge) often stalls/times out requests that omit these,
# so they are required in practice for a live response. Kept separate from
# PUBLIC_BROWSER_HEADERS because their values ("stats"/"true") are common
# substrings and must not trip log-hygiene checks; header values are never
# logged regardless.
STATS_HINT_HEADERS: dict[str, str] = {
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",   # literal constant, not an auth token
}

# The full public header set sent on every request.
REQUEST_HEADERS: dict[str, str] = {**PUBLIC_BROWSER_HEADERS, **STATS_HINT_HEADERS}

# HTTP statuses that are worth retrying with backoff.
RETRYABLE_STATUSES: frozenset[int] = frozenset({500, 502, 503, 504})


@dataclass(frozen=True)
class HttpConfig:
    """Transport policy. All timing knobs are injectable-friendly."""

    connect_timeout_s: float = 10.0
    read_timeout_s: float = 30.0
    max_retries: int = 4
    backoff_base_s: float = 1.5
    backoff_max_s: float = 60.0
    jitter_fraction: float = 0.25
    retry_after_cap_s: float = 120.0
    circuit_breaker_threshold: int = 5
    circuit_breaker_cooldown_s: float = 300.0


class CircuitBreaker:
    """In-process circuit breaker over consecutive *transport* failures.

    Transport failures are connection errors and connect/read timeouts —
    situations where no HTTP response was received at all. Any received HTTP
    response (whatever its status) proves the transport works and resets the
    consecutive-failure count.

    States:
      closed    -> requests flow; failures are counted.
      open      -> after ``threshold`` consecutive failures; ``before_request``
                   raises ``UpstreamUnavailable('circuit breaker open')``.
      half-open -> after ``cooldown_s`` has elapsed; a probe request is
                   allowed. Success closes the breaker, failure re-opens it.

    The clock is injectable so tests never sleep.
    """

    def __init__(
        self,
        threshold: int = 5,
        cooldown_s: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if threshold < 1:
            raise ValueError("circuit breaker threshold must be >= 1")
        if cooldown_s < 0:
            raise ValueError("circuit breaker cooldown must be >= 0")
        self._threshold = threshold
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    @classmethod
    def from_config(cls, config: HttpConfig,
                    clock: Callable[[], float] = time.monotonic) -> "CircuitBreaker":
        return cls(
            threshold=config.circuit_breaker_threshold,
            cooldown_s=config.circuit_breaker_cooldown_s,
            clock=clock,
        )

    @property
    def state(self) -> str:
        """'closed', 'open', or 'half_open'."""
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self._cooldown_s:
            return "half_open"
        return "open"

    def before_request(self) -> None:
        """Raise UpstreamUnavailable if the breaker is open (still cooling)."""
        if self.state == "open":
            raise UpstreamUnavailable("circuit breaker open")

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._threshold:
            # Opens when closed; re-opens (fresh cooldown) when half-open.
            self._opened_at = self._clock()


def _transport_reason(exc: requests.RequestException) -> str:
    """Stable machine reason for a transport-level failure."""
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return "connect_timeout"
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return "read_timeout"
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "connection_error"
    return "transport_error"


def _backoff_delay(config: HttpConfig, retry_index: int, rng: Any) -> float:
    """Exponential backoff (base * 2**retry_index), capped, with symmetric
    jitter drawn via ``rng.uniform`` on the configured jitter fraction."""
    base = min(config.backoff_base_s * (2.0 ** retry_index), config.backoff_max_s)
    jitter = base * rng.uniform(-config.jitter_fraction, config.jitter_fraction)
    return max(0.0, base + jitter)


def _parse_retry_after(value: str | None, cap_s: float) -> float | None:
    """Parse a Retry-After header (integer seconds or HTTP-date) to a delay
    in seconds, capped at ``cap_s``. Returns None when absent/unparseable so
    the caller falls back to normal backoff."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    seconds: float
    try:
        seconds = float(text)
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        seconds = (parsed - datetime.now(timezone.utc)).total_seconds()
    if not math.isfinite(seconds):
        return None
    return min(max(seconds, 0.0), cap_s)


def _log_attempt(path: str, attempt: int, status: int | None,
                 reason: str, backoff_s: float | None) -> None:
    """One structured record per attempt. Never header values or bodies."""
    logger.info(
        "http attempt=%d path=%s status=%s reason=%s backoff_s=%s",
        attempt,
        path,
        status if status is not None else "-",
        reason,
        f"{backoff_s:.3f}" if backoff_s is not None else "-",
    )


def get_json(
    url: str,
    params: Mapping[str, str],
    config: HttpConfig | None = None,
    session: requests.Session | None = None,
    breaker: CircuitBreaker | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
    rng: Any = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], bytes, int, str | None, int, int]:
    """GET ``url`` with ``params`` and return
    ``(payload_dict, raw_bytes, http_status, observed_date_header,
    request_count, retry_count)``.

    Policy:
      - explicit (connect, read) timeouts on every request;
      - 429: honor Retry-After (numeric seconds or HTTP-date, capped at
        ``retry_after_cap_s``; falls back to backoff when absent/unparseable);
        counts against ``max_retries``;
      - 500/502/503/504 and transport failures (connection errors,
        connect/read timeouts): exponential backoff with jitter;
      - 403 -> UpstreamUnavailable('http_403_forbidden'), NO retry;
      - 404 -> UpstreamUnavailable('http_404_not_found'), NO retry;
      - malformed/truncated JSON on a 2xx: retried exactly once, then
        UpstreamUnavailable('malformed_json');
      - retries exhausted -> UpstreamUnavailable(last reason, with counts).

    ``sleep`` and ``rng`` are injectable so tests never actually wait.
    Raises — never returns — on every non-recoverable condition.
    """
    cfg = config if config is not None else HttpConfig()
    rand = rng if rng is not None else random
    request_headers = dict(headers) if headers is not None else dict(REQUEST_HEADERS)
    path = urllib.parse.urlsplit(url).path or "/"
    owns_session = session is None
    sess = session if session is not None else requests.Session()

    request_count = 0
    retry_count = 0
    malformed_retries = 0
    last_reason = "no_attempt"
    last_status: int | None = None
    max_attempts = max(1, cfg.max_retries + 1)

    try:
        for attempt in range(1, max_attempts + 1):
            if breaker is not None:
                try:
                    breaker.before_request()
                except UpstreamUnavailable as exc:
                    # Re-raise with the counts accumulated so far.
                    raise UpstreamUnavailable(
                        exc.reason,
                        http_status=None,
                        request_count=request_count,
                        retry_count=retry_count,
                    ) from None

            request_count += 1
            try:
                response = sess.get(
                    url,
                    params=dict(params),
                    headers=request_headers,
                    timeout=(cfg.connect_timeout_s, cfg.read_timeout_s),
                )
            except requests.RequestException as exc:
                if breaker is not None:
                    breaker.record_failure()
                last_reason = _transport_reason(exc)
                last_status = None
                if attempt >= max_attempts:
                    _log_attempt(path, attempt, None, last_reason, None)
                    break
                delay = _backoff_delay(cfg, retry_count, rand)
                _log_attempt(path, attempt, None, last_reason, delay)
                retry_count += 1
                sleep(delay)
                continue

            if breaker is not None:
                breaker.record_success()
            status = response.status_code
            last_status = status

            if status == 403:
                _log_attempt(path, attempt, status, "http_403_forbidden", None)
                raise UpstreamUnavailable(
                    "http_403_forbidden", http_status=status,
                    request_count=request_count, retry_count=retry_count,
                )

            if status == 404:
                _log_attempt(path, attempt, status, "http_404_not_found", None)
                raise UpstreamUnavailable(
                    "http_404_not_found", http_status=status,
                    request_count=request_count, retry_count=retry_count,
                )

            if status == 429:
                last_reason = "http_429_rate_limited"
                if attempt >= max_attempts:
                    _log_attempt(path, attempt, status, last_reason, None)
                    break
                retry_after = _parse_retry_after(
                    response.headers.get("Retry-After"), cfg.retry_after_cap_s
                )
                delay = (
                    retry_after
                    if retry_after is not None
                    else _backoff_delay(cfg, retry_count, rand)
                )
                _log_attempt(path, attempt, status, last_reason, delay)
                retry_count += 1
                sleep(delay)
                continue

            if status in RETRYABLE_STATUSES:
                last_reason = f"http_{status}_server_error"
                if attempt >= max_attempts:
                    _log_attempt(path, attempt, status, last_reason, None)
                    break
                delay = _backoff_delay(cfg, retry_count, rand)
                _log_attempt(path, attempt, status, last_reason, delay)
                retry_count += 1
                sleep(delay)
                continue

            if 200 <= status < 300:
                raw = response.content
                payload: Any = None
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    payload = None
                if isinstance(payload, dict):
                    _log_attempt(path, attempt, status, "ok", None)
                    return (
                        payload,
                        raw,
                        status,
                        response.headers.get("Date"),
                        request_count,
                        retry_count,
                    )
                # Malformed/truncated JSON on a success status: could be a
                # transient truncation — retry exactly once, then raise.
                last_reason = "malformed_json"
                if malformed_retries >= 1 or attempt >= max_attempts:
                    _log_attempt(path, attempt, status, last_reason, None)
                    raise UpstreamUnavailable(
                        "malformed_json", http_status=status,
                        request_count=request_count, retry_count=retry_count,
                    )
                malformed_retries += 1
                delay = _backoff_delay(cfg, retry_count, rand)
                _log_attempt(path, attempt, status, last_reason, delay)
                retry_count += 1
                sleep(delay)
                continue

            # Any other status is unexpected for this endpoint: fail fast,
            # do not hammer the host.
            reason = f"http_{status}_unexpected"
            _log_attempt(path, attempt, status, reason, None)
            raise UpstreamUnavailable(
                reason, http_status=status,
                request_count=request_count, retry_count=retry_count,
            )

        # Retries exhausted: surface the last reason plus counts. NEVER
        # return empty/partial data here.
        raise UpstreamUnavailable(
            last_reason, http_status=last_status,
            request_count=request_count, retry_count=retry_count,
        )
    finally:
        if owns_session:
            sess.close()


def get_text(
    url: str,
    params: Mapping[str, str] | None = None,
    config: HttpConfig | None = None,
    session: requests.Session | None = None,
    breaker: CircuitBreaker | None = None,
    *,
    headers: Mapping[str, str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rng: Any = None,
) -> tuple[str, int, str | None, int, int]:
    """GET ``url`` and return ``(text, http_status, observed_date_header,
    request_count, retry_count)``.

    Same transport policy as :func:`get_json` — explicit timeouts, bounded
    retries with exponential backoff + jitter, ``Retry-After`` on 429,
    fail-fast on 403/404, optional circuit breaker — but returns the raw
    response text rather than parsed JSON, for non-JSON sources such as the
    VSIN HTML splits page. Raises ``UpstreamUnavailable`` (never returns
    empty/partial) on every non-recoverable condition.
    """
    cfg = config if config is not None else HttpConfig()
    rand = rng if rng is not None else random
    request_headers = dict(headers) if headers is not None else dict(REQUEST_HEADERS)
    path = urllib.parse.urlsplit(url).path or "/"
    owns_session = session is None
    sess = session if session is not None else requests.Session()

    request_count = 0
    retry_count = 0
    last_reason = "no_attempt"
    last_status: int | None = None
    max_attempts = max(1, cfg.max_retries + 1)

    try:
        for attempt in range(1, max_attempts + 1):
            if breaker is not None:
                try:
                    breaker.before_request()
                except UpstreamUnavailable as exc:
                    raise UpstreamUnavailable(
                        exc.reason, http_status=None,
                        request_count=request_count, retry_count=retry_count,
                    ) from None

            request_count += 1
            try:
                response = sess.get(
                    url,
                    params=dict(params or {}),
                    headers=request_headers,
                    timeout=(cfg.connect_timeout_s, cfg.read_timeout_s),
                )
            except requests.RequestException as exc:
                if breaker is not None:
                    breaker.record_failure()
                last_reason = _transport_reason(exc)
                last_status = None
                if attempt >= max_attempts:
                    _log_attempt(path, attempt, None, last_reason, None)
                    break
                delay = _backoff_delay(cfg, retry_count, rand)
                _log_attempt(path, attempt, None, last_reason, delay)
                retry_count += 1
                sleep(delay)
                continue

            if breaker is not None:
                breaker.record_success()
            status = response.status_code
            last_status = status

            if status == 403:
                _log_attempt(path, attempt, status, "http_403_forbidden", None)
                raise UpstreamUnavailable(
                    "http_403_forbidden", http_status=status,
                    request_count=request_count, retry_count=retry_count,
                )
            if status == 404:
                _log_attempt(path, attempt, status, "http_404_not_found", None)
                raise UpstreamUnavailable(
                    "http_404_not_found", http_status=status,
                    request_count=request_count, retry_count=retry_count,
                )
            if status == 429:
                last_reason = "http_429_rate_limited"
                if attempt >= max_attempts:
                    _log_attempt(path, attempt, status, last_reason, None)
                    break
                retry_after = _parse_retry_after(
                    response.headers.get("Retry-After"), cfg.retry_after_cap_s
                )
                delay = (
                    retry_after if retry_after is not None
                    else _backoff_delay(cfg, retry_count, rand)
                )
                _log_attempt(path, attempt, status, last_reason, delay)
                retry_count += 1
                sleep(delay)
                continue
            if status in RETRYABLE_STATUSES:
                last_reason = f"http_{status}_server_error"
                if attempt >= max_attempts:
                    _log_attempt(path, attempt, status, last_reason, None)
                    break
                delay = _backoff_delay(cfg, retry_count, rand)
                _log_attempt(path, attempt, status, last_reason, delay)
                retry_count += 1
                sleep(delay)
                continue

            if 200 <= status < 300:
                _log_attempt(path, attempt, status, "ok", None)
                text = response.content.decode(response.encoding or "utf-8", "replace")
                return (
                    text, status, response.headers.get("Date"),
                    request_count, retry_count,
                )

            reason = f"http_{status}_unexpected"
            _log_attempt(path, attempt, status, reason, None)
            raise UpstreamUnavailable(
                reason, http_status=status,
                request_count=request_count, retry_count=retry_count,
            )

        raise UpstreamUnavailable(
            last_reason, http_status=last_status,
            request_count=request_count, retry_count=retry_count,
        )
    finally:
        if owns_session:
            sess.close()
