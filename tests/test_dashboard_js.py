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
    src = DASHBOARD_JS.read_text(encoding="utf-8")
    # The two sinks confirmed in review: rankings team_name, snapshot book.
    assert "${esc(t.team_name)}" in src
    assert "esc(s.public_book" in src
    assert "${t.team_name}" not in src
    assert "${s.public_book" not in src


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
