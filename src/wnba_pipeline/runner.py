"""Run orchestration: one locked, fully-manifested extraction run.

``run_once`` wires fetch -> team resolution -> raw preservation -> validation
-> storage into a single run with exactly one ``RunManifest`` emitted as one
JSON line on **stdout** (logs are JSON lines on **stderr**). Every outcome —
success, unchanged, unavailable upstream, quarantined candidate, held lock,
config problem, internal error — maps to the contract's run states and exit
codes. Core guarantees enforced here:

  * overlapping runs are rejected (``LOCK_HELD``), never interleaved;
  * the raw payload is preserved BEFORE validation, even for candidates
    that will fail;
  * validation failure quarantines the candidate and leaves the
    last-known-good (LKG) snapshot untouched;
  * missing/invalid/unavailable data is never converted to zero and a failed
    run is never presented as an empty-but-successful dataset;
  * freshness always reflects the LKG's real age, never the failed candidate.

Freshness on non-success paths (documented semantics):
  * no LKG at all                          -> ``MISSING``
  * LKG present, age <= max_age_hours (36) -> ``FRESH``   (still trustworthy)
  * LKG present, age  > max_age_hours      -> ``STALE``   (failed refresh means
    the data is no longer known-fresh)
  * LKG present but unreadable/corrupt     -> ``INVALID`` (rare; see runbook —
    this is the only case where the runner itself reports INVALID)

Dependency injection: ``fetch_fn`` / ``resolve_teams_fn`` / ``validate_fn``
default to the real modules via *lazy* imports so tests can inject fakes
without importing modules that are authored independently. The defaults are
called strictly through the frozen contract signatures
(docs/data-contract.md).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from wnba_pipeline import contract
from wnba_pipeline.contract import (
    ConfigError,
    EXIT_CODE_BY_STATUS,
    ExpectedTeamSet,
    ExtractionParams,
    FreshnessState,
    LockHeld,
    RawFetchResult,
    RunManifest,
    RunStatus,
    StorageError,
    UpstreamUnavailable,
    ValidationState,
    canonical_json_checksum,
    sha256_hex,
)
from wnba_pipeline.locking import DEFAULT_LOCK_MAX_AGE_SECONDS, RunLock
from wnba_pipeline.storage import Store

logger = logging.getLogger("wnba_pipeline.runner")

DEFAULT_MAX_AGE_HOURS = 36.0
EXPECTED_TEAMS_FIXTURE_DIR = Path("fixtures") / "expected_teams"

_ISO_Z = "%Y-%m-%dT%H:%M:%SZ"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_ISO_Z)


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value, _ISO_Z).replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def make_run_id(now: datetime) -> str:
    """``<YYYYMMDDTHHMMSSZ>-<8 hex>`` — sortable and collision-safe."""
    return f"{_iso(now).replace('-', '').replace(':', '')}-{os.urandom(4).hex()}"


def _log(event: str, **fields: Any) -> None:
    logger.info(json.dumps({"event": event, **fields}, default=str))


# ---------------------------------------------------------------------------
# Default (lazy) collaborators — real modules, frozen contract signatures.
# ---------------------------------------------------------------------------

def _default_fetch(params: ExtractionParams) -> RawFetchResult:
    from wnba_pipeline.extractor import fetch_team_stats

    return fetch_team_stats(params)


def _default_resolve_teams(season: str) -> ExpectedTeamSet:
    from wnba_pipeline.teams import resolve_expected_teams

    try:
        return resolve_expected_teams(season, http=None, fallback_path=None)
    except TypeError:
        # Signature-compatibility shim: contract fixes the name and return
        # type; tolerate a resolver that defaults its keyword-only params.
        return resolve_expected_teams(season)


def _default_validate(raw: RawFetchResult, expected: ExpectedTeamSet) -> Any:
    from wnba_pipeline.validation import validate_and_normalize

    return validate_and_normalize(raw, expected)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _freshness_from_lkg(
    store: Store, key: str, now: datetime, max_age_hours: float
) -> FreshnessState:
    """Freshness of the *stored* LKG — never of a failed candidate."""
    try:
        lkg, _ = store.load_last_known_good(key)
    except StorageError:
        # LKG file exists but is unreadable: the stored data is INVALID.
        return FreshnessState.INVALID
    if lkg is None:
        return FreshnessState.MISSING
    fetched = _parse_iso(lkg.get("fetchedAtUtc"))
    if fetched is None:
        # Unknown age can not be claimed fresh.
        return FreshnessState.STALE
    age_hours = (now - fetched).total_seconds() / 3600.0
    return FreshnessState.FRESH if age_hours <= max_age_hours else FreshnessState.STALE


def _raw_row_count(payload: Any) -> int | None:
    """Row count from the official envelope, or None (never a fake zero)."""
    if not isinstance(payload, dict):
        return None
    result_sets = payload.get("resultSets")
    if not isinstance(result_sets, list):
        return None
    for rs in result_sets:
        if isinstance(rs, dict) and isinstance(rs.get("rowSet"), list):
            return len(rs["rowSet"])
    return None


def _fixture_raw(fixture_path: str | os.PathLike[str],
                 params: ExtractionParams, now: datetime) -> RawFetchResult:
    """Synthesize a RawFetchResult from a recorded envelope file.

    Provenance is honest: ``url`` is the ``file://`` URI of the fixture,
    ``request_count``/``retry_count`` are 0 (no network I/O happened), and
    the checksum is of the exact file bytes. ``http_status`` is set to 200
    only because the contract requires an int for an accepted envelope; the
    ``file://`` URL makes the offline origin unambiguous.
    """
    path = Path(fixture_path)
    try:
        raw_bytes = path.read_bytes()
        payload = json.loads(raw_bytes)
    except FileNotFoundError as exc:
        raise ConfigError(f"fixture not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"fixture unreadable/not JSON: {path}: {exc}") from exc
    return RawFetchResult(
        endpoint=contract.SOURCE_ENDPOINT,
        url=path.resolve().as_uri(),
        params=params,
        payload=payload,
        raw_bytes=raw_bytes,
        source_checksum=sha256_hex(raw_bytes),
        fetched_at_utc=_iso(now),
        http_status=200,
        request_count=0,
        retry_count=0,
        source_observed_at_utc=None,
    )


def _team_set_from_fixture_file(path: Path, season: str) -> ExpectedTeamSet:
    """Parse an expected-teams fixture file (lenient about exact shape)."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    teams_field = doc.get("teams")
    teams: dict[str, str] = {}
    if isinstance(teams_field, dict):
        teams = {str(k): str(v) for k, v in teams_field.items()}
    elif isinstance(teams_field, list):
        for entry in teams_field:
            if isinstance(entry, dict):
                tid = entry.get("teamId") or entry.get("team_id")
                name = entry.get("teamName") or entry.get("team_name")
                if tid is not None and name is not None:
                    teams[str(tid)] = str(name)
    if not teams:
        raise ConfigError(f"expected-teams fixture has no teams: {path}")
    file_season = str(doc.get("season", season))
    return ExpectedTeamSet(
        season=file_season,
        source=f"fixture:{path.as_posix()}",
        source_url=path.resolve().as_uri(),
        resolved_at_utc=str(doc.get("resolvedAtUtc", "")),
        teams=teams,
        version_checksum=str(
            doc.get("versionChecksum")
            or canonical_json_checksum({"season": file_season, "teams": teams})
        ),
    )


def _resolve_expected_teams(
    params: ExtractionParams,
    store: Store,
    resolve_teams_fn: Callable[[str], ExpectedTeamSet],
    *,
    offline: bool,
) -> ExpectedTeamSet:
    """Live resolution -> stored team set -> expected-teams fixture.

    In offline/fixture mode the live attempt is skipped (no network I/O may
    happen). All three sources unavailable raises ConfigError — the expected
    team set is never hardcoded and never guessed.
    """
    season = params.season
    reasons: list[str] = []

    if offline:
        reasons.append("live: skipped (offline fixture mode)")
    else:
        try:
            team_set = resolve_teams_fn(season)
            _log("teams_resolved_live", season=season,
                 teamCount=team_set.team_count,
                 versionChecksum=team_set.version_checksum)
            try:
                store.save_team_set(team_set)
            except StorageError as exc:
                _log("team_set_persist_failed", error=str(exc))
            return team_set
        except UpstreamUnavailable as exc:
            reasons.append(f"live: {exc.reason}")
            _log("teams_resolve_live_unavailable", reason=exc.reason)

    stored = store.load_latest_team_set(season)
    if stored is not None:
        _log("teams_resolved_stored", season=season, teamCount=stored.team_count)
        return stored
    reasons.append(f"stored: no team set under {store.teams_dir}")

    fixture = EXPECTED_TEAMS_FIXTURE_DIR / f"{season}.json"
    if fixture.exists():
        team_set = _team_set_from_fixture_file(fixture, season)
        _log("teams_resolved_fixture", season=season, path=str(fixture),
             teamCount=team_set.team_count)
        return team_set
    reasons.append(f"fixture: {fixture} not found")

    raise ConfigError(
        "expected-team set unavailable from all sources: " + "; ".join(reasons)
    )


# ---------------------------------------------------------------------------
# run_once
# ---------------------------------------------------------------------------

def run_once(
    params: ExtractionParams,
    data_root: str | os.PathLike[str],
    *,
    fetch_fn: Callable[[ExtractionParams], RawFetchResult] | None = None,
    resolve_teams_fn: Callable[[str], ExpectedTeamSet] | None = None,
    validate_fn: Callable[[RawFetchResult, ExpectedTeamSet], Any] | None = None,
    publish_fn: Callable[[Any], Any] | None = None,
    fixture_path: str | os.PathLike[str] | None = None,
    now_fn: Callable[[], datetime] | None = None,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    lock_max_age_seconds: float = DEFAULT_LOCK_MAX_AGE_SECONDS,
) -> tuple[RunManifest, int]:
    """Execute one full extraction run. Returns ``(manifest, exit_code)``.

    Also writes the manifest to ``<data_root>/manifests/<run_id>.json`` and
    emits it as ONE structured JSON line on stdout.
    """
    now_fn = now_fn or _utcnow
    fetch_fn = fetch_fn or _default_fetch
    resolve_teams_fn = resolve_teams_fn or _default_resolve_teams
    validate_fn = validate_fn or _default_validate

    started = now_fn()
    run_id = make_run_id(started)
    key = params.extraction_key()
    store = Store(data_root)

    manifest = RunManifest(
        run_id=run_id,
        status=RunStatus.INTERNAL_ERROR,   # pessimistic until proven otherwise
        extraction_key=key,
        params=asdict(params),
        started_at_utc=_iso(started),
        ended_at_utc=_iso(started),
        duration_seconds=0.0,
        request_count=0,
        response_status=None,
        retry_count=0,
        raw_row_count=None,
        valid_row_count=None,
        rejected_row_count=None,
        expected_team_count=None,
        actual_team_count=None,
        source_checksum=None,
        normalized_checksum=None,
        freshness_state=FreshnessState.MISSING,
        validation_state=ValidationState.NOT_RUN,
    )

    _log("run_started", runId=run_id, extractionKey=key,
         dataRoot=str(data_root), fixtureMode=fixture_path is not None)

    lock = RunLock(data_root, run_id,
                   max_age_seconds=lock_max_age_seconds, now_fn=now_fn)
    lock_acquired = False
    try:
        try:
            lock.acquire()
            lock_acquired = True
        except LockHeld as exc:
            manifest.status = RunStatus.LOCK_HELD
            manifest.failure_reason = str(exc)
            manifest.storage_result = "NONE"
            manifest.freshness_state = _freshness_from_lkg(
                store, key, now_fn(), max_age_hours)
            return _finalize(manifest, store, now_fn)

        try:
            _run_pipeline(
                manifest, params, store, key,
                fetch_fn=fetch_fn,
                resolve_teams_fn=resolve_teams_fn,
                validate_fn=validate_fn,
                publish_fn=publish_fn,
                fixture_path=fixture_path,
                now_fn=now_fn,
                max_age_hours=max_age_hours,
            )
        finally:
            lock.release()
        return _finalize(manifest, store, now_fn)

    except ConfigError as exc:
        manifest.status = RunStatus.CONFIG_ERROR
        manifest.failure_reason = str(exc)
        manifest.storage_result = manifest.storage_result or "NONE"
        manifest.freshness_state = _safe_freshness(store, key, now_fn, max_age_hours)
        return _finalize(manifest, store, now_fn)
    except StorageError as exc:
        manifest.status = RunStatus.STORAGE_ERROR
        manifest.failure_reason = str(exc)
        manifest.freshness_state = _safe_freshness(store, key, now_fn, max_age_hours)
        return _finalize(manifest, store, now_fn)
    except Exception as exc:  # noqa: BLE001 - top-level guard by design
        logger.exception("internal error in run %s", run_id)
        manifest.status = RunStatus.INTERNAL_ERROR
        manifest.failure_reason = f"{type(exc).__name__}: {exc}"
        manifest.freshness_state = _safe_freshness(store, key, now_fn, max_age_hours)
        return _finalize(manifest, store, now_fn)
    finally:
        if lock_acquired:
            lock.release()   # idempotent; covers exception paths


def _safe_freshness(store: Store, key: str,
                    now_fn: Callable[[], datetime],
                    max_age_hours: float) -> FreshnessState:
    try:
        return _freshness_from_lkg(store, key, now_fn(), max_age_hours)
    except Exception:  # noqa: BLE001 - freshness must never mask the real error
        return FreshnessState.MISSING


def _maybe_publish(
    manifest: RunManifest,
    publish_fn: Callable[[Any], Any] | None,
    snapshot: Any,
) -> None:
    """Publish an accepted snapshot to the serving layer (best-effort).

    The file store is the source of truth: a publish failure is recorded on
    the manifest and logged, but NEVER changes the run's status or exit code.
    """
    if publish_fn is None:
        return
    try:
        result = publish_fn(snapshot)
        manifest.publish_result = (
            f"PUBLISHED:{result}" if isinstance(result, int) else "PUBLISHED"
        )
        _log("published", runId=manifest.run_id,
             publishResult=manifest.publish_result)
    except Exception as exc:  # noqa: BLE001 - publish must not fail a good run
        manifest.publish_result = f"FAILED:{type(exc).__name__}"
        logger.warning("publish failed for run %s: %s", manifest.run_id, exc)
        _log("publish_failed", runId=manifest.run_id, error=str(exc))


def _run_pipeline(
    manifest: RunManifest,
    params: ExtractionParams,
    store: Store,
    key: str,
    *,
    fetch_fn: Callable[[ExtractionParams], RawFetchResult],
    resolve_teams_fn: Callable[[str], ExpectedTeamSet],
    validate_fn: Callable[[RawFetchResult, ExpectedTeamSet], Any],
    publish_fn: Callable[[Any], Any] | None,
    fixture_path: str | os.PathLike[str] | None,
    now_fn: Callable[[], datetime],
    max_age_hours: float,
) -> None:
    """Steps 3-8 of the run, mutating ``manifest`` in place."""
    run_id = manifest.run_id
    offline = fixture_path is not None

    # -- expected teams (live -> stored -> fixture; else ConfigError) -------
    expected = _resolve_expected_teams(
        params, store, resolve_teams_fn, offline=offline)
    manifest.expected_team_count = expected.team_count

    # -- fetch ---------------------------------------------------------------
    try:
        if offline:
            raw = _fixture_raw(fixture_path, params, now_fn())
        else:
            raw = fetch_fn(params)
    except UpstreamUnavailable as exc:
        manifest.status = RunStatus.UPSTREAM_UNAVAILABLE
        manifest.failure_reason = exc.reason
        manifest.request_count = exc.request_count
        manifest.retry_count = exc.retry_count
        manifest.response_status = exc.http_status
        manifest.storage_result = "NONE"
        manifest.last_known_good_preserved = True
        manifest.freshness_state = _freshness_from_lkg(
            store, key, now_fn(), max_age_hours)
        _log("upstream_unavailable", runId=run_id, reason=exc.reason,
             freshness=manifest.freshness_state.value)
        return

    manifest.request_count = raw.request_count
    manifest.retry_count = raw.retry_count
    manifest.response_status = raw.http_status
    manifest.source_checksum = raw.source_checksum
    manifest.raw_row_count = _raw_row_count(raw.payload)

    # -- raw preservation ALWAYS, before validation ---------------------------
    raw_path = store.save_raw(run_id, key, raw)
    _log("raw_saved", runId=run_id, path=str(raw_path),
         sourceChecksum=raw.source_checksum, rawRowCount=manifest.raw_row_count)

    # -- validation ------------------------------------------------------------
    outcome = validate_fn(raw, expected)
    manifest.validation_state = outcome.state
    manifest.valid_row_count = outcome.valid_row_count
    manifest.rejected_row_count = outcome.rejected_row_count
    manifest.expected_team_count = outcome.expected_team_count
    manifest.actual_team_count = outcome.actual_team_count
    manifest.validation_failures = [f.to_json_dict() for f in outcome.failures]

    if outcome.state == ValidationState.FAILED:
        q_path = store.quarantine(
            run_id, key, raw.payload, outcome.failures, asdict(params),
            quarantined_at_utc=_iso(now_fn()),
        )
        manifest.status = RunStatus.VALIDATION_FAILED
        codes = sorted({f.code for f in outcome.failures})
        manifest.failure_reason = (
            f"{len(outcome.failures)} validation failure(s): {', '.join(codes)}"
        )
        manifest.storage_result = "QUARANTINED"
        manifest.last_known_good_preserved = True   # candidate never touched LKG
        manifest.freshness_state = _freshness_from_lkg(
            store, key, now_fn(), max_age_hours)
        _log("validation_failed", runId=run_id, quarantinePath=str(q_path),
             failureCodes=codes, freshness=manifest.freshness_state.value)
        return

    snapshot = outcome.snapshot
    if outcome.state != ValidationState.PASSED or snapshot is None:
        # Contract violation by the validator — refuse to store anything.
        raise RuntimeError(
            f"validator returned state={outcome.state!r} with "
            f"snapshot={'present' if snapshot else 'None'}; cannot accept"
        )

    manifest.normalized_checksum = snapshot.normalized_checksum()

    # -- idempotency vs LKG -------------------------------------------------
    lkg, lkg_path = store.load_last_known_good(key)
    if lkg is not None and lkg.get("sourceChecksum") == snapshot.source_checksum:
        manifest.status = RunStatus.SUCCESS_UNCHANGED
        manifest.storage_result = "UNCHANGED_LKG_KEPT"
        manifest.last_known_good_preserved = True
        manifest.freshness_state = FreshnessState.FRESH
        _log("success_unchanged", runId=run_id,
             sourceChecksum=snapshot.source_checksum, lkgPath=str(lkg_path))
        _maybe_publish(manifest, publish_fn, snapshot)
        return

    snap_path = store.accept_snapshot(run_id, key, snapshot)
    store.prune(key)
    manifest.status = RunStatus.SUCCESS
    manifest.storage_result = "SNAPSHOT_ACCEPTED"
    # True: the previous LKG was superseded by a *validated* snapshot, never
    # damaged or replaced by unverified data (its file remains in snapshots/).
    manifest.last_known_good_preserved = True
    manifest.freshness_state = FreshnessState.FRESH
    _log("success", runId=run_id, snapshotPath=str(snap_path),
         teamCount=snapshot.team_count, rowCount=snapshot.row_count)
    _maybe_publish(manifest, publish_fn, snapshot)


def _finalize(
    manifest: RunManifest,
    store: Store,
    now_fn: Callable[[], datetime],
) -> tuple[RunManifest, int]:
    """Stamp timing, persist the manifest, emit ONE JSON line on stdout."""
    ended = now_fn()
    manifest.ended_at_utc = _iso(ended)
    started = _parse_iso(manifest.started_at_utc)
    if started is not None:
        manifest.duration_seconds = max(0.0, (ended - started).total_seconds())

    try:
        store.write_manifest(manifest)
    except (StorageError, OSError) as exc:
        # Manifest persistence failed. Never report success without the run
        # record on disk; failure statuses keep their (more specific) status.
        if EXIT_CODE_BY_STATUS[manifest.status] == 0:
            manifest.status = RunStatus.STORAGE_ERROR
            manifest.failure_reason = f"manifest write failed: {exc}"
            try:
                store.write_manifest(manifest)
            except (StorageError, OSError):
                pass
        _log("manifest_write_failed", runId=manifest.run_id, error=str(exc))

    line = json.dumps(manifest.to_json_dict(), separators=(",", ":"),
                      default=str)
    print(line, file=sys.stdout, flush=True)
    exit_code = EXIT_CODE_BY_STATUS[manifest.status]
    _log("run_finished", runId=manifest.run_id,
         status=manifest.status.value, exitCode=exit_code,
         freshness=manifest.freshness_state.value)
    return manifest, exit_code
