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


def _is_safe(expr: str) -> bool:
    expr = expr.strip()
    if "esc(" in expr:
        return True
    # X()/Y() are the pixel-coordinate mappers: pure arithmetic, always Number.
    if expr.startswith(("X(", "Y(")):
        return True
    # Number formatter: yields digits for numbers; for anything else the
    # optional chain collapses to undefined and the em-dash fallback renders.
    if ".toFixed" in expr:
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
