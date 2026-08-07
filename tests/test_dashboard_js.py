"""XSS regression guard for the dashboard's innerHTML templates.

dashboard.js builds HTML with template literals and assigns them to innerHTML.
Every interpolated value that originates from data (scraped team names, book
labels, snapshot fields) must pass through its esc() helper — upstream sites
are not trusted to keep markup out of a string field. There is no JS test
runner in this repo, so this is a static check: it extracts every ``${...}``
placeholder and fails if a data-derived expression reaches the DOM unescaped.
"""

from __future__ import annotations

import re
from pathlib import Path

DASHBOARD_JS = (
    Path(__file__).resolve().parents[1] / "src/wnba_pipeline/static/dashboard.js"
)

# An interpolation is data-derived when it touches a fetched row (``s.``/``t.``
# map variables, the raw ``p[...]`` chart points, ``opening``/``history`` from
# the history endpoint) or formats one via fmtTime().
_DATA_REF = re.compile(r"\b[st]\.|\bp\[|\bopening\b|\bhistory\b|fmtTime\(")


def _placeholders() -> list[str]:
    return re.findall(r"\$\{([^{}]*)\}", DASHBOARD_JS.read_text(encoding="utf-8"))


# Optional-chain number formatter used by the rankings table, anchored to the
# WHOLE placeholder: yields digits for numbers; for anything else the chain
# collapses to undefined and the em-dash fallback renders.
_FIXED_CHAIN = re.compile(r'^[\w$]+(?:\.[\w$]+)*\?\.toFixed\?\.\(\d+\) \?\? "—"$')
# Unconditional formatter on an already-numeric chart value: ``(+v).toFixed(1)``.
_FIXED_CAST = re.compile(r'^\(\+[\w$]+\)\.toFixed\(\d+\)$')
# Chart geometry: an integer offset on a pixel-mapper result, e.g. ``Y(v) + 4``.
_INT_OFFSET = re.compile(r" [+-] \d+$")


def _whole_call(expr: str, name: str) -> bool:
    """True when ``expr`` is exactly one ``name(...)`` call: the paren opened
    after ``name`` closes at the very last character. This is what keeps a
    compound like ``esc(a) + b`` out — its call closes before the ``+``."""
    if not expr.startswith(name + "("):
        return False
    depth = 0
    for i in range(len(name), len(expr)):
        if expr[i] == "(":
            depth += 1
        elif expr[i] == ")":
            depth -= 1
            if depth == 0:
                return i == len(expr) - 1
    return False


def _is_safe(expr: str) -> bool:
    expr = expr.strip()
    # Structural whitelist: the WHOLE placeholder must be one provably-safe
    # form. Containment checks ("esc(" in expr) let a compound expression
    # smuggle raw data alongside an escaped fragment.
    if _whole_call(expr, "esc"):
        return True
    # X()/Y() are the pixel-coordinate mappers: pure arithmetic, always Number.
    core = _INT_OFFSET.sub("", expr)
    if _whole_call(core, "X") or _whole_call(core, "Y"):
        return True
    if _FIXED_CHAIN.match(expr) or _FIXED_CAST.match(expr):
        return True
    return not _DATA_REF.search(expr)


def test_esc_helper_covers_all_five_entities():
    src = DASHBOARD_JS.read_text(encoding="utf-8")
    assert "function esc(" in src
    body = src.split("function esc(", 1)[1][:400]
    for entity in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
        assert entity in body, f"esc() does not emit {entity}"


def test_confirmed_sinks_are_escaped():
    """Every snapshot field that reaches innerHTML goes through esc().

    The rankings table is server-rendered now, so its ``t.team_name`` sink no
    longer exists in this file; the snapshot table grew the remaining ones.
    Each is asserted positively (escaped form present) and negatively (raw form
    absent), so removing an esc() fails here rather than shipping.
    """
    src = DASHBOARD_JS.read_text(encoding="utf-8")
    for field in ("public_book", "spread", "total", "ml_away", "ml_home"):
        assert f"esc(s.{field}" in src, f"snapshot {field} reaches the DOM unescaped"
        assert f"${{s.{field}" not in src, f"snapshot {field} interpolated raw"
    # Timestamps are formatted before display; the formatter output is still
    # data and still escaped.
    assert "esc(fmtTime(s.captured_at_utc))" in src
    assert "esc(fmtDay(s.captured_at_utc))" in src
    assert "${fmtTime(" not in src
    # The client no longer renders team names at all — if that ever returns,
    # it must arrive escaped.
    assert "${t.team_name}" not in src


def test_no_data_derived_interpolation_reaches_dom_unescaped():
    offenders = [e for e in _placeholders() if not _is_safe(e)]
    assert not offenders, (
        "data-derived interpolations without esc(): " + "; ".join(offenders)
    )


def test_compound_expressions_are_flagged_unsafe():
    """The guard must match structurally, not by containment: a compound
    placeholder that merely CONTAINS an esc() call or a .toFixed chain still
    concatenates raw data into the DOM and must be flagged."""
    assert not _is_safe('t.name + x.toFixed(1)')
    assert not _is_safe('esc(t.name) + t.other')
    assert not _is_safe('s.public_book + esc(s.spread)')
    assert not _is_safe('fmtTime(s.captured_at_utc)')  # formatter, no esc()


def test_whole_expression_safe_forms_still_pass():
    """The exact shapes dashboard.js actually uses stay whitelisted."""
    assert _is_safe('esc(s.spread ?? "—")')
    assert _is_safe('esc(fmtTime(s.captured_at_utc))')      # nested call
    assert _is_safe('t.offensive_rating?.toFixed?.(1) ?? "—"')
    assert _is_safe('(+v).toFixed(1)')
    assert _is_safe('X(new Date(p[0]).getTime())')
    assert _is_safe('Y(opening) - 6')                        # integer offset


# --------------------------------------------------------------------------- #
# Real-time status layer (static checks — this repo has no JS runner)
# --------------------------------------------------------------------------- #

def _src() -> str:
    return DASHBOARD_JS.read_text(encoding="utf-8")


def test_status_poll_every_60s_with_r_ok_check():
    src = _src()
    assert 'fetch("/api/status"' in src
    # The audit dinged a fetch without an r.ok check elsewhere — the status
    # poll must not repeat it: an error response is skipped, not parsed.
    poll = src.split('fetch("/api/status"', 1)[1][:200]
    assert "if (!r.ok) return;" in poll
    assert "setInterval(pollStatus, 60 * 1000)" in src


def test_reload_is_data_driven_with_guard_not_blind_30min():
    src = _src()
    # The blind 30-minute reload is gone; the fallback hard reload is 60 min.
    assert "30 * 60 * 1000" not in src
    assert "60 * 60 * 1000" in src
    # Data-driven reload: strict increase only, throttled to one per 2 min.
    assert "2 * 60 * 1000" in src
    assert "location.reload()" in src


def test_reload_recovers_from_a_nan_baseline():
    """A page rendered with no slate stamp (empty window / degraded render)
    must still reload once valid data exists — PR #41's review finding. The
    guard bails on an invalid POLLED timestamp, but a NaN RENDERED baseline
    alone must not disable the reload."""
    src = _src()
    assert "if (Number.isNaN(fresh)) return;" in src
    # The old combined bail that froze NaN-baseline pages must not return:
    assert "Number.isNaN(fresh) || Number.isNaN(renderedSlateMs)" not in src
    assert "!Number.isNaN(renderedSlateMs) && fresh <= renderedSlateMs" in src


def test_stamps_render_precision_graded_relative_time():
    src = _src()
    # Graded precision: seconds under a minute, minutes under an hour, then
    # hours+minutes — never a vague "recently" and no bare "min ago" rounding.
    assert "s ago" in src and "m ago" in src and "h " in src
    assert "recently" not in src
    # Absolute UTC time rides in the title attribute for hover precision.
    assert ".title =" in src
    # Both stamps re-render from the same renderer.
    assert '"updated"' in src and '"stats-updated"' in src


def test_no_cadence_promise_in_copy():
    """User-facing copy must not promise a refresh cadence, or name a
    scheduler that no longer runs the job.

    History: the copy once said "every 30 minutes on game days", which GitHub's
    best-effort `schedule` could not keep (measured gaps 30 min to 3.5 hours),
    so it was softened to "as GitHub delivers them". The betting scrape moved
    to a Railway cron service on 2026-08-04 and scrape.yml's schedule was
    retired, which makes any GitHub attribution wrong too. The durable
    invariant is that the interface describes WHEN data arrives relative to a
    run, never how often the clock says it will.
    """
    src = _src()
    assert "every 30 minutes" not in src
    assert "GitHub" not in src
    assert "scrape run" in src
