# WNBA Research Dashboard + Model v0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship offdutylocks.com's one-stop research dashboard — two-sided splits, line-history charts, auto signals, Model v0 projections, and Offensive Power Rankings — under full brand law.

**Architecture:** Append-only `betting_line_snapshots` written by the existing betting publish transaction; pure modules `signals.py` (signal detection), `model.py` (Model v0 formulas), `enrich.py` (both-side derivation + game enrichment pipeline); web layer adds `/api/games/<key>/history` and enriches `/api/betting`; UI is server-rendered Jinja (`templates/dashboard.html`) + static CSS/vanilla-JS with an inline-SVG movement chart — zero build step, ships in the existing Docker image.

**Tech Stack:** Python 3.11, Flask + Jinja (already deps), pytest + monkeypatched fetchers (existing pattern), vanilla JS + SVG, Google Fonts (Barlow Condensed 700, Inter).

**Spec:** `docs/superpowers/specs/2026-08-02-research-dashboard-design.md` (approved; §2b Model v0 owner revision).

## Global Constraints

- Branch `feat/research-dashboard`; single PR to `main` (merge = deploy). Schema change is additive `CREATE TABLE IF NOT EXISTS` only.
- Brand law verbatim (`design-system/off-duty-locks/MASTER.md`): bg `#0B0B0D`, panel `#141417`, border `#26262B`, text `#E7E7EA`, muted `#9CA3AF`; ONE accent `#FF5C1C`; signals green `#22C55E` / blue `#3B82F6` / yellow `#EAB308` / red `#EF4444` / gray `#6B7280`; Barlow Condensed 700 uppercase display, Inter with `tabular-nums` for data; ~140ms ease-out; no gradients; never color-only meaning.
- Footer copy exact: "All data provided for informational purposes only. Please wager responsibly." plus "21+" and "1-800-GAMBLER".
- Model v0 constants exact: `HOME_COURT_POINTS = 2.5`, `MODEL_EDGE_SPREAD_MIN = 1.5`, `MODEL_EDGE_TOTAL_MIN = 2.0`; signal thresholds `SHARP_DIVERGENCE_PCT = 15`, `PUBLIC_HEAVY_PCT = 70`.
- Null-safety law: missing input → null output ("—" in UI), never a fabricated value; no signal fires from null.
- The UI labels the model "MODEL v0" and the ring "EDGE".
- Web app stays read-only; only whitelisted/parameterized SQL.
- Verification after every task: `.venv/bin/python -m pytest -q` green; full battery before PR.
- All work in `~/src/off-duty-locks`.

---

### Task 1: Line-snapshot data layer

**Files:**
- Modify: `src/wnba_pipeline/schema.sql` (append table + index)
- Modify: `src/wnba_pipeline/db.py` (snapshot columns/rows/sql + write in `BettingPublisher.publish`)
- Test: `tests/test_db.py` (append tests)

**Interfaces:**
- Produces: `db.SNAPSHOT_COLUMNS: tuple[str, ...]`, `db.snapshot_rows(games) -> list[dict]`, `db.snapshot_insert_sql() -> str` (ON CONFLICT DO NOTHING), and `BettingPublisher.publish` writing snapshots in the same transaction. Task 5 reads the table via `web.fetch_line_history`.

- [ ] **Step 1: Append to `src/wnba_pipeline/schema.sql`**

```sql

-- Append-only line-movement history: one row per game per scrape run.
-- PK makes re-publishing the same fetch idempotent (DO NOTHING on conflict).
CREATE TABLE IF NOT EXISTS betting_line_snapshots (
    game_key                TEXT        NOT NULL,
    captured_at_utc         TIMESTAMPTZ NOT NULL,
    spread                  DOUBLE PRECISION,
    total                   DOUBLE PRECISION,
    ml_away                 INTEGER,
    ml_home                 INTEGER,
    spread_pct_bets_away    INTEGER,
    spread_pct_money_away   INTEGER,
    total_pct_bets_over     INTEGER,
    total_pct_money_over    INTEGER,
    ml_pct_bets_away        INTEGER,
    ml_pct_money_away       INTEGER,
    public_book             TEXT,
    PRIMARY KEY (game_key, captured_at_utc)
);

CREATE INDEX IF NOT EXISTS idx_line_snapshots_game
    ON betting_line_snapshots (game_key, captured_at_utc);
```

- [ ] **Step 2: Write failing tests** — append to `tests/test_db.py`:

```python
def test_snapshot_rows_map_current_values():
    from wnba_pipeline import db

    class G:  # minimal dataclass stand-in
        pass

    import dataclasses

    @dataclasses.dataclass
    class Game:
        game_key: str = "2026-08-02:PHX@LAS"
        fetched_at_utc: str = "2026-08-02T18:00:00Z"
        current_spread: float = -6.5
        current_total: float = 165.5
        current_ml_away: int = 220
        current_ml_home: int = -275
        spread_pct_bets_away: int = 72
        spread_pct_money_away: int = 81
        total_pct_bets_over: int = 47
        total_pct_money_over: int = 53
        ml_pct_bets_away: int = 30
        ml_pct_money_away: int = 25
        public_book: str = "draftkings"

    rows = db.snapshot_rows([Game()])
    assert len(rows) == 1
    row = rows[0]
    assert row["game_key"] == "2026-08-02:PHX@LAS"
    assert row["captured_at_utc"] == "2026-08-02T18:00:00Z"
    assert row["spread"] == -6.5
    assert row["total"] == 165.5
    assert row["ml_away"] == 220 and row["ml_home"] == -275
    assert row["public_book"] == "draftkings"


def test_snapshot_insert_sql_is_do_nothing():
    from wnba_pipeline import db

    sql = db.snapshot_insert_sql()
    assert "betting_line_snapshots" in sql
    assert "ON CONFLICT (game_key, captured_at_utc) DO NOTHING" in sql
    assert sql.count("%s") == len(db.SNAPSHOT_COLUMNS)


def test_schema_creates_snapshot_table():
    from pathlib import Path

    schema = (Path(__file__).resolve().parents[1] / "src/wnba_pipeline/schema.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS betting_line_snapshots" in schema
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_db.py -q`
Expected: FAIL — `db` has no attribute `snapshot_rows`.

- [ ] **Step 4: Implement in `src/wnba_pipeline/db.py`** — add after `betting_games_rows`:

```python
# Line-movement history: the CURRENT values of each game at fetch time,
# appended once per scrape run (PK = game_key + captured_at_utc).
SNAPSHOT_COLUMNS: tuple[str, ...] = (
    "game_key", "captured_at_utc", "spread", "total", "ml_away", "ml_home",
    "spread_pct_bets_away", "spread_pct_money_away",
    "total_pct_bets_over", "total_pct_money_over",
    "ml_pct_bets_away", "ml_pct_money_away", "public_book",
)

_SNAPSHOT_SOURCE_FIELDS = {
    "captured_at_utc": "fetched_at_utc",
    "spread": "current_spread",
    "total": "current_total",
    "ml_away": "current_ml_away",
    "ml_home": "current_ml_home",
}


def snapshot_rows(games: list[Any]) -> list[dict[str, Any]]:
    """BettingGame dataclasses -> betting_line_snapshots rows (current values)."""
    import dataclasses

    rows: list[dict[str, Any]] = []
    for g in games:
        d = dataclasses.asdict(g)
        rows.append({
            col: d.get(_SNAPSHOT_SOURCE_FIELDS.get(col, col))
            for col in SNAPSHOT_COLUMNS
        })
    return rows


def snapshot_insert_sql() -> str:
    """Append-only insert; identical re-publishes are no-ops."""
    col_list = ", ".join(SNAPSHOT_COLUMNS)
    placeholders = ", ".join(["%s"] * len(SNAPSHOT_COLUMNS))
    return (
        f"INSERT INTO betting_line_snapshots ({col_list}) VALUES ({placeholders})\n"
        "ON CONFLICT (game_key, captured_at_utc) DO NOTHING"
    )
```

and extend `BettingPublisher.publish` — replace its body's cursor block:

```python
            with conn.cursor() as cur:
                cur.executemany(sql, params)
```

with:

```python
            snap_rows = snapshot_rows(games)
            snap_params = [
                tuple(row[c] for c in SNAPSHOT_COLUMNS) for row in snap_rows
            ]
            with conn.cursor() as cur:
                cur.executemany(sql, params)
                if snap_params:
                    cur.executemany(snapshot_insert_sql(), snap_params)
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_db.py -q`
Expected: all pass (existing 4 + 3 new).

- [ ] **Step 6: Commit**

```bash
git add src/wnba_pipeline/schema.sql src/wnba_pipeline/db.py tests/test_db.py
git commit -m "feat(data): append-only betting_line_snapshots written by every betting publish"
```

---

### Task 2: Signal engine — `signals.py` (TDD)

**Files:**
- Create: `src/wnba_pipeline/signals.py`
- Test: `tests/test_signals.py`

**Interfaces:**
- Produces: `detect_signals(game: Mapping, model: Mapping | None = None) -> list[dict]` where each dict is `{"market": "spread"|"total"|"moneyline", "type": "sharp-money"|"public-heavy"|"rlm"|"model-edge"|"conflict", "side": str|None}`. Constants `SHARP_DIVERGENCE_PCT = 15`, `PUBLIC_HEAVY_PCT = 70`. Task 4 consumes; Task 6's legend mirrors the five base types + model-edge.

- [ ] **Step 1: Write failing tests** — `tests/test_signals.py`:

```python
"""Signal engine: thresholds, null-safety, conflict semantics, contract shape."""

from wnba_pipeline.signals import detect_signals


def g(**kw):
    base = {
        "spread_pct_bets_away": None, "spread_pct_money_away": None,
        "total_pct_bets_over": None, "total_pct_money_over": None,
        "ml_pct_bets_away": None, "ml_pct_money_away": None,
        "spread_rlm": None, "total_rlm": None, "ml_rlm": None,
        "spread_line_move": None, "total_line_move": None,
    }
    base.update(kw)
    return base


def types_for(signals, market):
    return {s["type"] for s in signals if s["market"] == market}


def test_all_null_yields_no_signals():
    assert detect_signals(g()) == []


def test_sharp_money_fires_at_exact_threshold():
    s = detect_signals(g(spread_pct_bets_away=40, spread_pct_money_away=55))
    assert {"market": "spread", "type": "sharp-money", "side": "away"} in s


def test_sharp_money_below_threshold_is_silent():
    s = detect_signals(g(spread_pct_bets_away=40, spread_pct_money_away=54))
    assert types_for(s, "spread") == set()


def test_sharp_money_side_follows_money_lean_home():
    # money on away 30 vs tickets 45 -> divergence 15 toward HOME side
    s = detect_signals(g(spread_pct_bets_away=45, spread_pct_money_away=30))
    assert {"market": "spread", "type": "sharp-money", "side": "home"} in s


def test_public_heavy_fires_at_70_on_either_side():
    s = detect_signals(g(spread_pct_bets_away=70, spread_pct_money_away=70))
    assert {"market": "spread", "type": "public-heavy", "side": "away"} in s
    s2 = detect_signals(g(spread_pct_bets_away=30, spread_pct_money_away=30))
    assert {"market": "spread", "type": "public-heavy", "side": "home"} in s2


def test_public_heavy_69_is_silent():
    s = detect_signals(g(spread_pct_bets_away=69, spread_pct_money_away=69))
    assert types_for(s, "spread") == set()


def test_rlm_comes_from_stored_booleans_only():
    s = detect_signals(g(spread_rlm=True, total_rlm=False, ml_rlm=None,
                         spread_line_move=1.0))
    assert {"market": "spread", "type": "rlm", "side": "home"} in s
    assert types_for(s, "total") == set()
    assert types_for(s, "moneyline") == set()


def test_rlm_side_is_direction_of_move():
    # away line moved DOWN (toward away) -> RLM side away
    s = detect_signals(g(spread_rlm=True, spread_line_move=-1.0))
    assert {"market": "spread", "type": "rlm", "side": "away"} in s


def test_total_signals_use_over_under_sides():
    s = detect_signals(g(total_pct_bets_over=75, total_pct_money_over=75))
    assert {"market": "total", "type": "public-heavy", "side": "over"} in s


def test_conflict_sharp_vs_rlm_opposite_sides():
    # sharp money toward AWAY while RLM says movement toward HOME -> conflict
    s = detect_signals(g(
        spread_pct_bets_away=40, spread_pct_money_away=60,
        spread_rlm=True, spread_line_move=1.0,
    ))
    assert {"market": "spread", "type": "conflict", "side": None} in s


def test_no_conflict_when_sharp_and_rlm_agree():
    s = detect_signals(g(
        spread_pct_bets_away=60, spread_pct_money_away=40,  # sharp toward home
        spread_rlm=True, spread_line_move=1.0,              # move toward home
    ))
    assert {"market": "spread", "type": "conflict", "side": None} not in s


def test_model_edge_joins_contract():
    s = detect_signals(g(), model={"edge_spread": 2.0, "edge_total": None})
    assert {"market": "spread", "type": "model-edge", "side": "away"} in s
    s2 = detect_signals(g(), model={"edge_spread": -2.0, "edge_total": 2.5})
    assert {"market": "spread", "type": "model-edge", "side": "home"} in s2
    assert {"market": "total", "type": "model-edge", "side": "over"} in s2


def test_model_edge_below_threshold_silent():
    s = detect_signals(g(), model={"edge_spread": 1.4, "edge_total": 1.9})
    assert s == []
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_signals.py -q`
Expected: import error — module does not exist.

- [ ] **Step 3: Implement `src/wnba_pipeline/signals.py`**

```python
"""Betting-signal detection (serving layer, pure functions).

Signals derive ONLY from stored scrape data (and Model v0 edges when
provided). Null inputs never fire a signal — missing data is missing, not
neutral. Colors are a UI concern (design-system/off-duty-locks/MASTER.md).

Contract: detect_signals(game, model) -> list of
    {"market": "spread"|"total"|"moneyline",
     "type": "sharp-money"|"public-heavy"|"rlm"|"model-edge"|"conflict",
     "side": "away"|"home"|"over"|"under"|None}
"""

from __future__ import annotations

from typing import Any, Mapping

SHARP_DIVERGENCE_PCT = 15  # |money% - tickets%| to call money sharp
PUBLIC_HEAVY_PCT = 70      # tickets% on one side to call the public heavy

from wnba_pipeline.model import MODEL_EDGE_SPREAD_MIN, MODEL_EDGE_TOTAL_MIN

# (market, bets column, money column, side when pct refers to it, other side)
_MARKETS = (
    ("spread", "spread_pct_bets_away", "spread_pct_money_away", "away", "home"),
    ("total", "total_pct_bets_over", "total_pct_money_over", "over", "under"),
    ("moneyline", "ml_pct_bets_away", "ml_pct_money_away", "away", "home"),
)

_RLM_FIELDS = {"spread": "spread_rlm", "total": "total_rlm", "moneyline": "ml_rlm"}
_MOVE_FIELDS = {"spread": "spread_line_move", "total": "total_line_move"}


def _sig(market: str, type_: str, side: str | None) -> dict[str, Any]:
    return {"market": market, "type": type_, "side": side}


def _rlm_side(market: str, game: Mapping[str, Any], pct_side: str, other: str) -> str | None:
    """Direction the line moved (the side RLM points toward)."""
    move = game.get(_MOVE_FIELDS.get(market, ""), None)
    if move is None:
        return None
    if market == "total":
        return "over" if move > 0 else "under"
    # away-side line: moving UP = toward home, DOWN = toward away
    return other if move > 0 else pct_side


def detect_signals(
    game: Mapping[str, Any], model: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    for market, bets_col, money_col, pct_side, other in _MARKETS:
        bets, money = game.get(bets_col), game.get(money_col)
        sharp_side: str | None = None
        if bets is not None and money is not None:
            if abs(money - bets) >= SHARP_DIVERGENCE_PCT:
                sharp_side = pct_side if money > bets else other
                out.append(_sig(market, "sharp-money", sharp_side))
        if bets is not None:
            if bets >= PUBLIC_HEAVY_PCT:
                out.append(_sig(market, "public-heavy", pct_side))
            elif (100 - bets) >= PUBLIC_HEAVY_PCT:
                out.append(_sig(market, "public-heavy", other))

        rlm_toward: str | None = None
        if game.get(_RLM_FIELDS[market]) is True:
            rlm_toward = _rlm_side(market, game, pct_side, other)
            out.append(_sig(market, "rlm", rlm_toward))

        # Conflict: sharp money points one way while the line moved the other.
        if sharp_side and rlm_toward and sharp_side != rlm_toward:
            out.append(_sig(market, "conflict", None))

    if model:
        es = model.get("edge_spread")
        if es is not None and abs(es) >= MODEL_EDGE_SPREAD_MIN:
            out.append(_sig("spread", "model-edge", "away" if es > 0 else "home"))
        et = model.get("edge_total")
        if et is not None and abs(et) >= MODEL_EDGE_TOTAL_MIN:
            out.append(_sig("total", "model-edge", "over" if et > 0 else "under"))

    return out
```

- [ ] **Step 4: Run tests** (model.py does not exist yet — Task 3 provides the two constants; for THIS task create the constants-only stub as part of Step 3 if executing strictly in order):

If `src/wnba_pipeline/model.py` is absent, create it now with only:

```python
"""Model v0 — transparent formula projections (filled in by the model task)."""

MODEL_EDGE_SPREAD_MIN = 1.5
MODEL_EDGE_TOTAL_MIN = 2.0
```

Run: `.venv/bin/python -m pytest tests/test_signals.py -q`
Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
git add src/wnba_pipeline/signals.py src/wnba_pipeline/model.py tests/test_signals.py
git commit -m "feat(signals): sharp-money / public-heavy / rlm / conflict / model-edge detection (TDD)"
```

---

### Task 3: Model v0 — `model.py` (TDD)

**Files:**
- Modify: `src/wnba_pipeline/model.py` (full implementation over the stub)
- Test: `tests/test_model.py`

**Interfaces:**
- Produces: `project_game(away: Mapping|None, home: Mapping|None, current_spread: float|None, current_total: float|None) -> dict|None` returning `{"spread", "total", "edge_spread", "edge_total", "edge_score"}`; constants `HOME_COURT_POINTS = 2.5`, `MODEL_EDGE_SPREAD_MIN = 1.5`, `MODEL_EDGE_TOTAL_MIN = 2.0`. `away`/`home` are team_stats rows (need `offensive_rating`, `possessions`). Task 4 consumes.

- [ ] **Step 1: Write failing tests** — `tests/test_model.py`:

```python
"""Model v0: exact formulas, null-safety as a unit, edge score bounds."""

import pytest

from wnba_pipeline.model import HOME_COURT_POINTS, project_game


AWAY = {"offensive_rating": 104.0, "possessions": 80.0}
HOME = {"offensive_rating": 110.0, "possessions": 84.0}
# poss_avg = 82; margin_home = (110-104)*82/100 + 2.5 = 4.92 + 2.5 = 7.42
# model_spread_away = 7.42 ; model_total = (104+110)*82/100 = 175.48


def test_projection_formulas_exact():
    m = project_game(AWAY, HOME, current_spread=6.0, current_total=170.0)
    assert m["spread"] == pytest.approx(7.42)
    assert m["total"] == pytest.approx(175.48)
    # edge_spread = current - model = 6.0 - 7.42 = -1.42 (value on HOME)
    assert m["edge_spread"] == pytest.approx(-1.42)
    # edge_total = model - current = 5.48 (value on OVER)
    assert m["edge_total"] == pytest.approx(5.48)
    # edge_score = min(10, 2*1.42 + 5.48) = 8.32
    assert m["edge_score"] == pytest.approx(8.32)


def test_home_court_constant_is_in_formula():
    no_hca = project_game(AWAY, {**HOME, "offensive_rating": 104.0},
                          current_spread=None, current_total=None)
    assert no_hca["spread"] == pytest.approx(HOME_COURT_POINTS)


def test_missing_stats_yields_none_as_a_unit():
    assert project_game(None, HOME, 6.0, 170.0) is None
    assert project_game(AWAY, {"offensive_rating": None, "possessions": 80.0},
                        6.0, 170.0) is None


def test_missing_market_leaves_edges_null_but_projects():
    m = project_game(AWAY, HOME, current_spread=None, current_total=None)
    assert m["spread"] == pytest.approx(7.42)
    assert m["edge_spread"] is None
    assert m["edge_total"] is None
    assert m["edge_score"] is None


def test_edge_score_caps_at_10():
    m = project_game(AWAY, HOME, current_spread=-20.0, current_total=100.0)
    assert m["edge_score"] == 10


def test_edge_score_with_one_edge_only():
    m = project_game(AWAY, HOME, current_spread=6.0, current_total=None)
    # only spread edge (1.42): score = 2*1.42 = 2.84
    assert m["edge_total"] is None
    assert m["edge_score"] == pytest.approx(2.84)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_model.py -q`
Expected: FAIL — `project_game` not defined (stub has constants only).

- [ ] **Step 3: Implement `src/wnba_pipeline/model.py`** (replace file):

```python
"""Model v0 — transparent, deterministic projections (serving layer).

Not a trained model: a documented formula over the pipeline's verified
team stats (same spirit as the owner's offensive-rating formula in
``derived.py``), labeled "MODEL v0" in the UI. Upgrading to a trained model
later only changes this module.

Formulas (away-side spread convention matches betting_games.current_spread):

    poss_avg          = (poss_away + poss_home) / 2
    proj_margin_home  = (ORtg_home - ORtg_away) * poss_avg / 100 + HOME_COURT_POINTS
    model_spread_away = proj_margin_home        # negative = away favored
    model_total       = (ORtg_away + ORtg_home) * poss_avg / 100
    edge_spread       = current_spread - model_spread   # > 0 value on AWAY
    edge_total        = model_total - current_total     # > 0 value on OVER
    edge_score        = min(10, 2.0*|edge_spread| + 1.0*|edge_total|)

Null-safety: missing team stats -> None (no model at all); missing market
values -> that edge (and the score, if no edge remains) is None. Never a
fabricated number.
"""

from __future__ import annotations

from typing import Any, Mapping

HOME_COURT_POINTS = 2.5      # WNBA home advantage (documented constant)
MODEL_EDGE_SPREAD_MIN = 1.5  # spread edge (pts) to fire the model-edge signal
MODEL_EDGE_TOTAL_MIN = 2.0   # total edge (pts) to fire the model-edge signal

_SPREAD_WEIGHT = 2.0
_TOTAL_WEIGHT = 1.0
_SCORE_CAP = 10.0


def _num(row: Mapping[str, Any] | None, key: str) -> float | None:
    if row is None:
        return None
    value = row.get(key)
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def project_game(
    away: Mapping[str, Any] | None,
    home: Mapping[str, Any] | None,
    current_spread: float | None,
    current_total: float | None,
) -> dict[str, Any] | None:
    ortg_away, ortg_home = _num(away, "offensive_rating"), _num(home, "offensive_rating")
    poss_away, poss_home = _num(away, "possessions"), _num(home, "possessions")
    if None in (ortg_away, ortg_home, poss_away, poss_home):
        return None

    poss_avg = (poss_away + poss_home) / 2
    model_spread = (ortg_home - ortg_away) * poss_avg / 100 + HOME_COURT_POINTS
    model_total = (ortg_away + ortg_home) * poss_avg / 100

    edge_spread = None if current_spread is None else current_spread - model_spread
    edge_total = None if current_total is None else model_total - current_total

    if edge_spread is None and edge_total is None:
        edge_score = None
    else:
        edge_score = min(
            _SCORE_CAP,
            _SPREAD_WEIGHT * abs(edge_spread or 0.0) + _TOTAL_WEIGHT * abs(edge_total or 0.0),
        )

    return {
        "spread": model_spread,
        "total": model_total,
        "edge_spread": edge_spread,
        "edge_total": edge_total,
        "edge_score": edge_score,
    }
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_model.py tests/test_signals.py -q`
Expected: 20 passed (6 model + 14 signals — signals still import the constants).

- [ ] **Step 5: Commit**

```bash
git add src/wnba_pipeline/model.py tests/test_model.py
git commit -m "feat(model): Model v0 — transparent projections, edges, capped edge score (TDD)"
```

---

### Task 4: Enrichment pipeline — `enrich.py` + `/api/betting` (TDD)

**Files:**
- Create: `src/wnba_pipeline/enrich.py`
- Modify: `src/wnba_pipeline/web.py` (enrich in `api_betting` + fetch team stats for it)
- Test: `tests/test_enrich.py`, extend `tests/test_web.py`

**Interfaces:**
- Produces: `enrich.with_both_sides(game: dict) -> dict` (adds `spread_pct_bets_home`, `spread_pct_money_home`, `total_pct_bets_under`, `total_pct_money_under`, `ml_pct_bets_home`, `ml_pct_money_home`; null stays null), `enrich.enrich_games(games: list[dict], stats_by_team_id: Mapping[str, Mapping]) -> list[dict]` (adds both sides + `model` + `signals` to each). `web.fetch_stats_by_team(split="last7") -> dict[str, dict]`. Task 6 renders these fields.

- [ ] **Step 1: Write failing tests** — `tests/test_enrich.py`:

```python
"""Enrichment: both-side math, null passthrough, model+signals attachment."""

from wnba_pipeline.enrich import enrich_games, with_both_sides


def test_both_sides_complement():
    g = with_both_sides({
        "spread_pct_bets_away": 72, "spread_pct_money_away": 81,
        "total_pct_bets_over": 47, "total_pct_money_over": 53,
        "ml_pct_bets_away": 30, "ml_pct_money_away": 25,
    })
    assert g["spread_pct_bets_home"] == 28
    assert g["spread_pct_money_home"] == 19
    assert g["total_pct_bets_under"] == 53
    assert g["total_pct_money_under"] == 47
    assert g["ml_pct_bets_home"] == 70
    assert g["ml_pct_money_home"] == 75


def test_null_stays_null_never_fifty():
    g = with_both_sides({"spread_pct_bets_away": None})
    assert g["spread_pct_bets_home"] is None


def test_enrich_attaches_model_and_signals():
    games = [{
        "game_key": "2026-08-02:PHX@LAS",
        "away_team_id": "1611661317", "home_team_id": "1611661319",
        "current_spread": 6.0, "current_total": 170.0,
        "spread_pct_bets_away": 72, "spread_pct_money_away": 81,
        "total_pct_bets_over": None, "total_pct_money_over": None,
        "ml_pct_bets_away": None, "ml_pct_money_away": None,
        "spread_rlm": None, "total_rlm": None, "ml_rlm": None,
        "spread_line_move": None, "total_line_move": None,
    }]
    stats = {
        "1611661317": {"offensive_rating": 104.0, "possessions": 80.0},
        "1611661319": {"offensive_rating": 110.0, "possessions": 84.0},
    }
    out = enrich_games(games, stats)[0]
    assert out["model"]["spread"] is not None
    assert isinstance(out["signals"], list)
    assert {"market": "spread", "type": "public-heavy", "side": "away"} in out["signals"]


def test_enrich_without_stats_has_null_model():
    games = [{"game_key": "k", "away_team_id": "x", "home_team_id": "y",
              "current_spread": None, "current_total": None}]
    out = enrich_games(games, {})[0]
    assert out["model"] is None
    assert out["signals"] == []
```

and append to `tests/test_web.py`:

```python
def test_api_betting_is_enriched(client, monkeypatch):
    monkeypatch.setattr(web, "fetch_betting", lambda: [{
        "game_key": "2026-08-02:PHX@LAS",
        "away_team_id": "a", "home_team_id": "h",
        "current_spread": 6.0, "current_total": 170.0,
        "spread_pct_bets_away": 72, "spread_pct_money_away": 81,
    }])
    monkeypatch.setattr(web, "fetch_stats_by_team", lambda split="last7": {
        "a": {"offensive_rating": 104.0, "possessions": 80.0},
        "h": {"offensive_rating": 110.0, "possessions": 84.0},
    })
    body = client.get("/api/betting").get_json()
    game = body["games"][0]
    assert game["spread_pct_bets_home"] == 28
    assert game["model"]["edge_spread"] is not None
    assert isinstance(game["signals"], list)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_enrich.py tests/test_web.py -q`
Expected: import error (`enrich` missing) + web test failure.

- [ ] **Step 3: Implement `src/wnba_pipeline/enrich.py`**

```python
"""Game enrichment for the serving layer: both-side splits, Model v0, signals.

Pure functions over plain dicts (the web layer's row shape). Null-safety law:
a missing percentage stays None on BOTH sides — never a fabricated 50/50.
"""

from __future__ import annotations

from typing import Any, Mapping

from wnba_pipeline.model import project_game
from wnba_pipeline.signals import detect_signals

_COMPLEMENTS = {
    "spread_pct_bets_away": "spread_pct_bets_home",
    "spread_pct_money_away": "spread_pct_money_home",
    "total_pct_bets_over": "total_pct_bets_under",
    "total_pct_money_over": "total_pct_money_under",
    "ml_pct_bets_away": "ml_pct_bets_home",
    "ml_pct_money_away": "ml_pct_money_home",
}


def with_both_sides(game: dict[str, Any]) -> dict[str, Any]:
    out = dict(game)
    for src, dst in _COMPLEMENTS.items():
        value = out.get(src)
        out[dst] = None if value is None else 100 - value
    return out


def enrich_games(
    games: list[dict[str, Any]],
    stats_by_team_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    out = []
    for game in games:
        g = with_both_sides(game)
        model = project_game(
            stats_by_team_id.get(str(g.get("away_team_id"))),
            stats_by_team_id.get(str(g.get("home_team_id"))),
            g.get("current_spread"),
            g.get("current_total"),
        )
        g["model"] = model
        g["signals"] = detect_signals(g, model)
        out.append(g)
    return out
```

- [ ] **Step 4: Wire into `src/wnba_pipeline/web.py`** — add import near the top (after `from wnba_pipeline import db`):

```python
from wnba_pipeline.enrich import enrich_games
```

add after `fetch_betting`:

```python
def fetch_stats_by_team(split: str = "last7") -> dict[str, dict[str, Any]]:
    """team_id -> stats row, for Model v0 inputs and W-L records."""
    return {str(r["team_id"]): r for r in _rows(
        "SELECT team_id, team_name, wins, losses, possessions, offensive_rating, points "
        "FROM team_stats WHERE split = %s", (split,),
    )}
```

and replace `api_betting`'s try block body:

```python
        return jsonify({"games": fetch_betting()})
```

with:

```python
        games = enrich_games(fetch_betting(), fetch_stats_by_team("last7"))
        return jsonify({"games": games})
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_enrich.py tests/test_web.py tests/test_signals.py tests/test_model.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/wnba_pipeline/enrich.py src/wnba_pipeline/web.py tests/test_enrich.py tests/test_web.py
git commit -m "feat(api): /api/betting enriched — both-side splits, Model v0, signals"
```

---

### Task 5: History endpoint (TDD)

**Files:**
- Modify: `src/wnba_pipeline/web.py` (fetcher + route)
- Test: extend `tests/test_web.py`

**Interfaces:**
- Produces: `web.fetch_line_history(game_key: str) -> dict | None` and `GET /api/games/<game_key>/history` → `{"game_key", "opening": {"spread","total","ml_away","ml_home"}, "snapshots": [...]}`, 404 unknown key, 503 on DB error. Task 6's chart consumes.

- [ ] **Step 1: Write failing tests** — append to `tests/test_web.py`:

```python
def test_history_endpoint_ok(client, monkeypatch):
    monkeypatch.setattr(web, "fetch_line_history", lambda key: {
        "game_key": key,
        "opening": {"spread": -6.5, "total": 168.5, "ml_away": 220, "ml_home": -275},
        "snapshots": [
            {"captured_at_utc": "2026-08-02T12:00:00+00:00", "spread": -6.5},
            {"captured_at_utc": "2026-08-02T15:00:00+00:00", "spread": -7.0},
        ],
    })
    body = client.get("/api/games/2026-08-02:PHX@LAS/history").get_json()
    assert body["opening"]["spread"] == -6.5
    assert len(body["snapshots"]) == 2


def test_history_unknown_game_404(client, monkeypatch):
    monkeypatch.setattr(web, "fetch_line_history", lambda key: None)
    assert client.get("/api/games/nope/history").status_code == 404


def test_history_db_error_503(client, monkeypatch):
    def boom(key):
        raise RuntimeError("db down")
    monkeypatch.setattr(web, "fetch_line_history", boom)
    assert client.get("/api/games/x/history").status_code == 503
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_web.py -q`
Expected: new tests FAIL (no attribute / 404 route).

- [ ] **Step 3: Implement in `src/wnba_pipeline/web.py`** — after `fetch_stats_by_team`:

```python
def fetch_line_history(game_key: str) -> dict[str, Any] | None:
    """Opening values + ordered snapshots for one game; None if unknown."""
    opening = _rows(
        "SELECT open_spread AS spread, open_total AS total, "
        "open_ml_away AS ml_away, open_ml_home AS ml_home "
        "FROM betting_games WHERE game_key = %s", (game_key,),
    )
    if not opening:
        return None
    cols = ", ".join(db.SNAPSHOT_COLUMNS)
    snapshots = _rows(
        f"SELECT {cols} FROM betting_line_snapshots "
        "WHERE game_key = %s ORDER BY captured_at_utc", (game_key,),
    )
    return {"game_key": game_key, "opening": opening[0], "snapshots": snapshots}
```

and after `api_betting`:

```python
@app.get("/api/games/<path:game_key>/history")
def api_game_history(game_key: str):
    try:
        history = fetch_line_history(game_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("history query failed: %s", exc)
        return jsonify({"error": "data temporarily unavailable"}), 503
    if history is None:
        return jsonify({"error": "unknown game"}), 404
    return jsonify(history)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_web.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/wnba_pipeline/web.py tests/test_web.py
git commit -m "feat(api): /api/games/<key>/history — opening line + ordered snapshots"
```

---

### Task 6: Dashboard UI — templates, styles, chart

**Files:**
- Create: `src/wnba_pipeline/templates/dashboard.html`
- Create: `src/wnba_pipeline/static/odl.css`
- Create: `src/wnba_pipeline/static/dashboard.js`
- Modify: `src/wnba_pipeline/web.py` (`/` renders dashboard; `/tables` keeps legacy page)
- Test: extend `tests/test_web.py`

**Interfaces:**
- Consumes: enriched games (Task 4 fields incl. `model`, `signals`, both-side pcts), `/api/games/<key>/history` (Task 5), `fetch_stats_by_team` for records, `fetch_team_stats(split)` rankings order.
- Produces: `GET /` → dashboard (200 even with empty data), `GET /tables` → legacy page.

- [ ] **Step 1: Write failing tests** — append to `tests/test_web.py`:

```python
def _empty_dashboard(monkeypatch):
    monkeypatch.setattr(web, "fetch_betting", lambda: [])
    monkeypatch.setattr(web, "fetch_stats_by_team", lambda split="last7": {})
    monkeypatch.setattr(web, "fetch_team_stats", lambda split: [])


def test_dashboard_renders_with_brand_copy(client, monkeypatch):
    _empty_dashboard(monkeypatch)
    r = client.get("/")
    html = r.data.decode()
    assert r.status_code == 200
    assert "OFF DUTY LOCKS" in html
    assert "Please wager responsibly." in html
    assert "1-800-GAMBLER" in html
    assert "MODEL v0" in html
    assert "SIGNAL LEGEND" in html


def test_dashboard_renders_games_and_rankings(client, monkeypatch):
    monkeypatch.setattr(web, "fetch_betting", lambda: [{
        "game_key": "2026-08-02:PHX@LAS", "game_date": "2026-08-02",
        "away_abbr": "PHX", "home_abbr": "LAS",
        "away_name": "Mercury", "home_name": "Aces",
        "away_team_id": "a", "home_team_id": "h",
        "open_spread": -6.5, "current_spread": -7.0, "sharp_spread": -7.5,
        "spread_pct_bets_away": 72, "spread_pct_money_away": 81,
        "open_total": 168.5, "current_total": 169.0, "sharp_total": 170.5,
        "total_pct_bets_over": 47, "total_pct_money_over": 53,
        "current_ml_away": 220, "current_ml_home": -275,
        "spread_rlm": True, "spread_line_move": -0.5,
    }])
    monkeypatch.setattr(web, "fetch_stats_by_team", lambda split="last7": {
        "a": {"offensive_rating": 104.0, "possessions": 80.0,
              "wins": 11, "losses": 12, "team_name": "Mercury", "points": 80.9},
        "h": {"offensive_rating": 110.0, "possessions": 84.0,
              "wins": 18, "losses": 4, "team_name": "Aces", "points": 90.1},
    })
    monkeypatch.setattr(web, "fetch_team_stats", lambda split: [
        {"team_name": "Aces", "offensive_rating": 110.0, "possessions": 84.0,
         "points": 90.1, "wins": 18, "losses": 4},
    ])
    html = client.get("/").data.decode()
    assert "PHX" in html and "LAS" in html
    assert "OFFENSIVE POWER RANKINGS" in html
    assert "Aces" in html


def test_legacy_tables_route(client, monkeypatch):
    _empty_dashboard(monkeypatch)
    assert client.get("/tables").status_code == 200
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_web.py -q`
Expected: FAIL — `/` lacks brand copy; `/tables` is 404.

- [ ] **Step 3: Write `src/wnba_pipeline/static/odl.css`**

```css
/* Off Duty Locks — brand law: design-system/off-duty-locks/MASTER.md.
   Surfaces #0B0B0D/#141417, border #26262B, ONE accent #FF5C1C,
   signal colors as data meaning, Barlow Condensed + Inter tabular. */
:root {
  --odl-bg: #0B0B0D;
  --odl-panel: #141417;
  --odl-border: #26262B;
  --odl-text: #E7E7EA;
  --odl-text-muted: #9CA3AF;
  --odl-accent: #FF5C1C;
  --odl-signal-sharp: #22C55E;
  --odl-signal-model: #3B82F6;
  --odl-signal-public: #EAB308;
  --odl-signal-warn: #EF4444;
  --odl-signal-none: #6B7280;
  --ease: 140ms ease-out;
}
* { box-sizing: border-box; margin: 0; }
body {
  background: var(--odl-bg);
  color: var(--odl-text);
  font-family: "Inter", system-ui, sans-serif;
  font-size: 14px;
  line-height: 1.45;
}
.display, h1, h2, h3, th {
  font-family: "Barlow Condensed", "Inter", sans-serif;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}
.num { font-variant-numeric: tabular-nums; }
a { color: var(--odl-text); text-decoration: none; }

/* Header */
.hdr {
  position: sticky; top: 0; z-index: 10;
  display: flex; align-items: center; gap: 28px;
  padding: 14px 24px;
  background: var(--odl-bg);
  border-bottom: 1px solid var(--odl-border);
}
.hdr .mark { font-size: 22px; }
.hdr .mark em { color: var(--odl-accent); font-style: normal; }
.hdr nav { display: flex; gap: 20px; font-size: 15px; }
.hdr nav a { color: var(--odl-text-muted); padding: 4px 0; border-bottom: 2px solid transparent; transition: color var(--ease); }
.hdr nav a.active { color: var(--odl-text); border-bottom-color: var(--odl-accent); }
.hdr .updated { margin-left: auto; color: var(--odl-text-muted); font-size: 12px; }

/* Layout */
.wrap { max-width: 1280px; margin: 0 auto; padding: 20px 24px 40px; }
.page-title { font-size: 30px; margin-bottom: 2px; }
.subtitle { color: var(--odl-text-muted); margin-bottom: 18px; }
.panel {
  background: var(--odl-panel);
  border: 1px solid var(--odl-border);
  border-radius: 10px;
  padding: 16px;
  margin-bottom: 20px;
}
.panel h2 { font-size: 18px; margin-bottom: 12px; }

/* Games table */
.games { width: 100%; border-collapse: collapse; }
.games th {
  color: var(--odl-text-muted); font-size: 12px; text-align: right;
  padding: 6px 10px; border-bottom: 1px solid var(--odl-border);
}
.games th:first-child, .games td:first-child { text-align: left; }
.games td { padding: 8px 10px; text-align: right; vertical-align: middle; }
.games tbody tr { border-bottom: 1px solid var(--odl-border); cursor: pointer; transition: background var(--ease); }
.games tbody tr:hover { background: rgba(255, 92, 28, 0.06); }
.teams .abbr { font-weight: 600; }
.teams .rec { color: var(--odl-text-muted); font-size: 12px; margin-left: 6px; }
.sub { color: var(--odl-text-muted); font-size: 11px; }
.pos { color: var(--odl-signal-sharp); }
.neg { color: var(--odl-signal-warn); }
.na { color: var(--odl-signal-none); }

/* Signal dots */
.sig-dot {
  display: inline-block; width: 10px; height: 10px; border-radius: 50%;
  margin-left: 5px; vertical-align: middle;
}
.sig-sharp-money { background: var(--odl-signal-sharp); }
.sig-model-edge { background: var(--odl-signal-model); }
.sig-public-heavy { background: var(--odl-signal-public); }
.sig-rlm { background: var(--odl-accent); }
.sig-conflict { background: var(--odl-signal-warn); }
.sig-none { background: var(--odl-signal-none); }

/* Edge ring */
.ring {
  display: inline-flex; align-items: center; justify-content: center;
  width: 44px; height: 44px; border-radius: 50%;
  border: 2px solid var(--odl-signal-none);
  font-weight: 700; font-size: 14px;
}
.ring.hot { border-color: var(--odl-signal-sharp); }
.ring.warm { border-color: var(--odl-signal-public); }
.ring.cool { border-color: var(--odl-accent); }
.ring-label { display: block; font-size: 10px; color: var(--odl-text-muted); text-align: center; }

/* Legend */
.legend { display: flex; flex-wrap: wrap; gap: 18px; align-items: center; font-size: 12px; color: var(--odl-text-muted); }
.legend .display { font-size: 13px; color: var(--odl-text); }

/* Detail panel */
.detail[hidden] { display: none; }
.detail .tabs, .rank-tabs { display: inline-flex; border: 1px solid var(--odl-border); border-radius: 8px; overflow: hidden; margin-bottom: 12px; }
.detail .tabs button, .rank-tabs button {
  background: none; border: 0; color: var(--odl-text-muted);
  font: inherit; padding: 6px 14px; cursor: pointer; transition: all var(--ease);
}
.detail .tabs button.active, .rank-tabs button.active { background: var(--odl-accent); color: #0B0B0D; font-weight: 600; }
.chart-box { overflow-x: auto; }
.chart-box svg { display: block; }
.empty { color: var(--odl-text-muted); padding: 18px 0; }

/* Snapshot table */
.snaps { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 12px; }
.snaps th { color: var(--odl-text-muted); text-align: right; padding: 4px 8px; border-bottom: 1px solid var(--odl-border); }
.snaps td { text-align: right; padding: 4px 8px; }
.snaps th:first-child, .snaps td:first-child { text-align: left; }

/* Rankings */
.ranks { width: 100%; border-collapse: collapse; }
.ranks th { color: var(--odl-text-muted); font-size: 12px; text-align: right; padding: 6px 10px; border-bottom: 1px solid var(--odl-border); }
.ranks td { padding: 7px 10px; text-align: right; }
.ranks th:nth-child(2), .ranks td:nth-child(2) { text-align: left; }
.ranks td.rank { color: var(--odl-accent); font-weight: 700; }

/* Footer */
footer {
  border-top: 1px solid var(--odl-border);
  color: var(--odl-text-muted); font-size: 12px;
  text-align: center; padding: 18px 24px;
}

@media (max-width: 900px) {
  .games { font-size: 12px; }
  .hdr { gap: 14px; }
  .wrap { padding: 14px 12px 32px; }
}
```

- [ ] **Step 4: Write `src/wnba_pipeline/templates/dashboard.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Off Duty Locks — WNBA Research Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@700&family=Inter:wght@400;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{{ url_for('static', filename='odl.css') }}">
</head>
<body>
<header class="hdr">
  <div class="mark display">OFF DUTY <em>LOCKS</em></div>
  <nav>
    <a class="active" href="/">Dashboard</a>
    <a href="/tables">Tables</a>
  </nav>
  <div class="updated num">{% if updated_at %}Last updated: {{ updated_at }}{% endif %}</div>
</header>

<main class="wrap">
  <h1 class="page-title">WNBA Dashboard</h1>
  <p class="subtitle">Betting splits · line history · signals · MODEL v0 · power rankings</p>

  {% if not db_ok %}
  <section class="panel"><p class="empty">Data temporarily unavailable — the database can’t be reached right now.</p></section>
  {% endif %}

  <section class="panel">
    <h2>Today’s Slate</h2>
    {% if games %}
    <table class="games num">
      <thead>
        <tr>
          <th>Game</th>
          <th>Spread<br><span class="sub">open · cur · sharp</span></th>
          <th>Tickets %<br><span class="sub">away / home</span></th>
          <th>Money %<br><span class="sub">away / home</span></th>
          <th>Total<br><span class="sub">open · cur · sharp</span></th>
          <th>O/U %<br><span class="sub">over / under</span></th>
          <th>ML<br><span class="sub">away / home</span></th>
          <th>Model v0<br><span class="sub">spread · total</span></th>
          <th>Edge</th>
          <th>Signals</th>
        </tr>
      </thead>
      <tbody>
        {% for game in games %}
        <tr data-game-key="{{ game.game_key }}">
          <td class="teams">
            <div><span class="abbr">{{ game.away_abbr or game.away_name or "—" }}</span><span class="rec">{{ game.away_record or "" }}</span></div>
            <div><span class="abbr">{{ game.home_abbr or game.home_name or "—" }}</span><span class="rec">{{ game.home_record or "" }}</span></div>
          </td>
          <td>{{ fmt(game.open_spread) }} · <strong>{{ fmt(game.current_spread) }}</strong> · {{ fmt(game.sharp_spread) }}</td>
          <td>{{ pct(game.spread_pct_bets_away) }} / {{ pct(game.spread_pct_bets_home) }}</td>
          <td>{{ pct(game.spread_pct_money_away) }} / {{ pct(game.spread_pct_money_home) }}</td>
          <td>{{ fmt(game.open_total) }} · <strong>{{ fmt(game.current_total) }}</strong> · {{ fmt(game.sharp_total) }}</td>
          <td>{{ pct(game.total_pct_bets_over) }} / {{ pct(game.total_pct_bets_under) }}</td>
          <td>{{ ml(game.current_ml_away) }} / {{ ml(game.current_ml_home) }}</td>
          <td>
            {% if game.model %}{{ fmt(game.model.spread) }} · {{ fmt(game.model.total) }}{% else %}<span class="na">—</span>{% endif %}
          </td>
          <td>
            {% if game.model and game.model.edge_score is not none %}
            <span class="ring {{ ring_class(game.model.edge_score) }}">{{ "%.1f"|format(game.model.edge_score) }}</span>
            <span class="ring-label">EDGE</span>
            {% else %}<span class="na">—</span>{% endif %}
          </td>
          <td>
            {% for s in game.signals %}<span class="sig-dot sig-{{ s.type }}" title="{{ s.market }}: {{ s.type }}{% if s.side %} → {{ s.side }}{% endif %}"></span>{% else %}<span class="sig-dot sig-none" title="no clear signal"></span>{% endfor %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% else %}
    <p class="empty">No games on the slate right now. Check back on game day.</p>
    {% endif %}
  </section>

  <section class="panel detail" id="detail" hidden>
    <h2 id="detail-title">Line History</h2>
    <div class="tabs" role="tablist">
      <button data-market="spread" class="active">Spread</button>
      <button data-market="total">Total</button>
      <button data-market="moneyline">Moneyline</button>
    </div>
    <div class="chart-box" id="chart"></div>
    <div id="snaps"></div>
  </section>

  <section class="panel">
    <h2>Signal Legend</h2>
    <div class="legend">
      <span><span class="sig-dot sig-sharp-money"></span> Sharp Money — money % diverges ≥15 pts from tickets</span>
      <span><span class="sig-dot sig-model-edge"></span> Model Edge — MODEL v0 disagrees with the market</span>
      <span><span class="sig-dot sig-public-heavy"></span> Public Heavy — ≥70% of tickets on one side</span>
      <span><span class="sig-dot sig-rlm"></span> Reverse Line Move — line moved against the ticket majority</span>
      <span><span class="sig-dot sig-conflict"></span> Warning / Conflict — sharp money and line move disagree</span>
      <span><span class="sig-dot sig-none"></span> No Clear Signal</span>
      <span class="display">MODEL v0</span>
      <span>= formula projection from Last-7 offensive ratings &amp; pace (points/possession × pace + {{ home_court }} home court). Not a trained model; EDGE measures model-vs-market disagreement.</span>
    </div>
  </section>

  <section class="panel">
    <h2>Offensive Power Rankings</h2>
    <div class="rank-tabs" role="tablist">
      <button data-split="last7" class="active">Last 7 Games</button>
      <button data-split="ytd">Year to Date</button>
    </div>
    <table class="ranks num" id="ranks">
      <thead><tr><th>Rank</th><th>Team</th><th>Off Rtg</th><th>Est. Poss</th><th>PTS/G</th><th>Record</th></tr></thead>
      <tbody>
        {% for team in rankings %}
        <tr>
          <td class="rank">{{ loop.index }}</td>
          <td>{{ team.team_name }}</td>
          <td>{{ fmt(team.offensive_rating) }}</td>
          <td>{{ fmt(team.possessions) }}</td>
          <td>{{ fmt(team.points) }}</td>
          <td>{{ team.wins }}-{{ team.losses }}</td>
        </tr>
        {% else %}
        <tr><td colspan="6" class="empty">No team stats published yet.</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </section>
</main>

<footer>
  All data provided for informational purposes only. Please wager responsibly. · 21+ · 1-800-GAMBLER
</footer>

<script src="{{ url_for('static', filename='dashboard.js') }}"></script>
</body>
</html>
```

- [ ] **Step 5: Write `src/wnba_pipeline/static/dashboard.js`**

```javascript
/* Off Duty Locks dashboard: detail panel + inline-SVG line-history chart +
   rankings tabs. Vanilla JS, no dependencies; every state change ~140ms. */
"use strict";

const ACCENT = "#FF5C1C";
const MUTED = "#6B7280";

const detail = document.getElementById("detail");
const chartBox = document.getElementById("chart");
const snapsBox = document.getElementById("snaps");
const titleEl = document.getElementById("detail-title");
let history = null;
let market = "spread";

function fmtTime(iso) {
  const d = new Date(iso);
  return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" }) + " ET";
}

function seriesFor(mkt) {
  if (!history) return { points: [], opening: null, label: "" };
  const snaps = history.snapshots || [];
  if (mkt === "spread") return { points: snaps.map(s => [s.captured_at_utc, s.spread]), opening: history.opening.spread, label: "Spread (away)" };
  if (mkt === "total") return { points: snaps.map(s => [s.captured_at_utc, s.total]), opening: history.opening.total, label: "Total" };
  return { points: snaps.map(s => [s.captured_at_utc, s.ml_away]), opening: history.opening.ml_away, label: "Moneyline (away)" };
}

function drawChart() {
  const { points, opening, label } = seriesFor(market);
  const data = points.filter(p => p[1] !== null && p[1] !== undefined);
  if (!data.length) {
    chartBox.innerHTML = '<p class="empty">No movement history yet — snapshots record every 30 minutes on game days from here forward.' +
      (opening !== null && opening !== undefined ? ` Opening ${label.toLowerCase()}: <strong class="num">${opening}</strong>.` : "") + "</p>";
    return;
  }
  const W = 720, H = 220, PAD = 42;
  const xs = data.map(p => new Date(p[0]).getTime());
  const ys = data.map(p => p[1]).concat(opening !== null && opening !== undefined ? [opening] : []);
  const x0 = Math.min(...xs), x1 = Math.max(...xs) || x0 + 1;
  let yMin = Math.min(...ys), yMax = Math.max(...ys);
  if (yMin === yMax) { yMin -= 1; yMax += 1; }
  const X = t => PAD + ((t - x0) / (x1 - x0 || 1)) * (W - 2 * PAD);
  const Y = v => H - PAD - ((v - yMin) / (yMax - yMin)) * (H - 2 * PAD);

  let path = "";
  data.forEach((p, i) => {
    const px = X(new Date(p[0]).getTime()), py = Y(p[1]);
    path += i === 0 ? `M${px},${py}` : `H${px}V${py}`; // step chart
  });

  const dots = data.map(p =>
    `<circle cx="${X(new Date(p[0]).getTime())}" cy="${Y(p[1])}" r="3.5" fill="${ACCENT}"/>`).join("");
  const openLine = (opening !== null && opening !== undefined)
    ? `<line x1="${PAD}" x2="${W - PAD}" y1="${Y(opening)}" y2="${Y(opening)}" stroke="${MUTED}" stroke-dasharray="4 4"/>` +
      `<text x="${W - PAD}" y="${Y(opening) - 6}" fill="${MUTED}" font-size="11" text-anchor="end">open ${opening}</text>`
    : "";
  const yTicks = [yMin, (yMin + yMax) / 2, yMax].map(v =>
    `<text x="${PAD - 8}" y="${Y(v) + 4}" fill="${MUTED}" font-size="11" text-anchor="end" class="num">${(+v).toFixed(1)}</text>`).join("");
  const tLabels = [data[0], data[data.length - 1]].map(p =>
    `<text x="${X(new Date(p[0]).getTime())}" y="${H - PAD + 18}" fill="${MUTED}" font-size="11" text-anchor="middle">${fmtTime(p[0])}</text>`).join("");

  chartBox.innerHTML =
    `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="${label} movement">` +
    `<line x1="${PAD}" x2="${W - PAD}" y1="${H - PAD}" y2="${H - PAD}" stroke="#26262B"/>` +
    openLine + yTicks + tLabels +
    `<path d="${path}" fill="none" stroke="${ACCENT}" stroke-width="2"/>` + dots +
    `</svg>`;
}

function drawSnaps() {
  const snaps = (history && history.snapshots) || [];
  if (!snaps.length) { snapsBox.innerHTML = ""; return; }
  const rows = snaps.map(s => `<tr>
    <td>${fmtTime(s.captured_at_utc)}</td>
    <td class="num">${s.spread ?? "—"}</td>
    <td class="num">${s.total ?? "—"}</td>
    <td class="num">${s.ml_away ?? "—"} / ${s.ml_home ?? "—"}</td>
    <td class="num">${s.spread_pct_bets_away ?? "—"}% / ${s.spread_pct_bets_away != null ? 100 - s.spread_pct_bets_away : "—"}%</td>
    <td class="num">${s.spread_pct_money_away ?? "—"}% / ${s.spread_pct_money_away != null ? 100 - s.spread_pct_money_away : "—"}%</td>
    <td>${s.public_book ?? "—"}</td>
  </tr>`).join("");
  snapsBox.innerHTML = `<table class="snaps">
    <thead><tr><th>Time</th><th>Spread</th><th>Total</th><th>ML a/h</th><th>Tickets a/h</th><th>Money a/h</th><th>Book</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

async function openGame(key) {
  detail.hidden = false;
  titleEl.textContent = "Line History — " + key.split(":").pop();
  chartBox.innerHTML = '<p class="empty">Loading…</p>';
  snapsBox.innerHTML = "";
  try {
    const r = await fetch(`/api/games/${encodeURIComponent(key)}/history`);
    if (!r.ok) throw new Error(String(r.status));
    history = await r.json();
  } catch {
    history = null;
    chartBox.innerHTML = '<p class="empty">History unavailable right now.</p>';
    return;
  }
  drawChart();
  drawSnaps();
  detail.scrollIntoView({ behavior: "smooth", block: "nearest" });
  window.location.hash = "game=" + encodeURIComponent(key);
}

document.querySelectorAll(".games tbody tr[data-game-key]").forEach(tr =>
  tr.addEventListener("click", () => openGame(tr.dataset.gameKey)));

document.querySelectorAll(".detail .tabs button").forEach(btn =>
  btn.addEventListener("click", () => {
    document.querySelectorAll(".detail .tabs button").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    market = btn.dataset.market;
    drawChart();
  }));

document.querySelectorAll(".rank-tabs button").forEach(btn =>
  btn.addEventListener("click", async () => {
    document.querySelectorAll(".rank-tabs button").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    const body = document.querySelector("#ranks tbody");
    try {
      const r = await fetch(`/api/team-stats?split=${btn.dataset.split}`);
      const data = await r.json();
      body.innerHTML = (data.teams || []).map((t, i) => `<tr>
        <td class="rank">${i + 1}</td><td>${t.team_name}</td>
        <td class="num">${t.offensive_rating?.toFixed?.(1) ?? "—"}</td>
        <td class="num">${t.possessions?.toFixed?.(1) ?? "—"}</td>
        <td class="num">${t.points?.toFixed?.(1) ?? "—"}</td>
        <td class="num">${t.wins ?? "—"}-${t.losses ?? "—"}</td></tr>`).join("") ||
        '<tr><td colspan="6" class="empty">No team stats published yet.</td></tr>';
    } catch {
      body.innerHTML = '<tr><td colspan="6" class="empty">Rankings unavailable right now.</td></tr>';
    }
  }));

const m = window.location.hash.match(/game=([^&]+)/);
if (m) openGame(decodeURIComponent(m[1]));
```

- [ ] **Step 6: Wire routes in `src/wnba_pipeline/web.py`** — add imports (`render_template` from flask, `HOME_COURT_POINTS` from model):

```python
from flask import Flask, jsonify, render_template, request

from wnba_pipeline.model import HOME_COURT_POINTS
```

add template helpers after `fetch_line_history`:

```python
def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    return f"{value:+.1f}" if isinstance(value, float) else str(value)


def _pct(value: Any) -> str:
    return "—" if value is None else f"{value}%"


def _ml(value: Any) -> str:
    if value is None:
        return "—"
    return f"+{value}" if value > 0 else str(value)


def _ring_class(score: float) -> str:
    if score >= 7.5:
        return "hot"
    if score >= 5.0:
        return "warm"
    return "cool"
```

replace the existing `index` route function body with:

```python
@app.get("/")
def index():
    db_ok = True
    games: list[dict[str, Any]] = []
    rankings: list[dict[str, Any]] = []
    stats: dict[str, dict[str, Any]] = {}
    try:
        stats = fetch_stats_by_team("last7")
        games = enrich_games(fetch_betting(), stats)
        rankings = fetch_team_stats("last7")
    except Exception as exc:  # noqa: BLE001 - render an empty state, not a 500
        logger.warning("dashboard queries failed: %s", exc)
        db_ok = False
    for g in games:
        away = stats.get(str(g.get("away_team_id")))
        home = stats.get(str(g.get("home_team_id")))
        g["away_record"] = f"{away['wins']}-{away['losses']}" if away and away.get("wins") is not None else ""
        g["home_record"] = f"{home['wins']}-{home['losses']}" if home and home.get("wins") is not None else ""
    updated = max((g.get("fetched_at_utc") or "" for g in games), default="")
    return render_template(
        "dashboard.html",
        games=games, rankings=rankings, db_ok=db_ok,
        updated_at=updated, home_court=HOME_COURT_POINTS,
        fmt=_fmt, pct=_pct, ml=_ml, ring_class=_ring_class,
    )
```

and register the legacy page (keep `_render_page` and its data flow exactly as the old index used them):

```python
@app.get("/tables")
def tables():
    db_ok = True
    last7: list[dict[str, Any]] = []
    ytd: list[dict[str, Any]] = []
    betting: list[dict[str, Any]] = []
    try:
        last7 = fetch_team_stats("last7")
        ytd = fetch_team_stats("ytd")
        betting = fetch_betting()
    except Exception as exc:  # noqa: BLE001
        logger.warning("tables queries failed: %s", exc)
        db_ok = False
    return _render_page(last7, ytd, betting, db_ok)
```

(Adjust the OLD `index` body away — `/` now renders the dashboard; `/tables` owns the legacy renderer. Update any existing `test_index_renders_data`-style test that asserted legacy content on `/` to point at `/tables` instead.)

- [ ] **Step 7: Run the web tests**

Run: `.venv/bin/python -m pytest tests/test_web.py -q`
Expected: all pass (new dashboard tests + updated legacy-route tests).

- [ ] **Step 8: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass, 0 failures.

- [ ] **Step 9: Commit**

```bash
git add src/wnba_pipeline/templates/ src/wnba_pipeline/static/ src/wnba_pipeline/web.py tests/test_web.py
git commit -m "feat(ui): brand-law research dashboard — games grid, SVG line history, legend, rankings; legacy page at /tables"
```

---

### Task 7: Final verification + PR

**Files:** none new.

- [ ] **Step 1: Full offline battery, real output**

Run:
```bash
cd ~/src/off-duty-locks
.venv/bin/python -m pytest
.venv/bin/python qa/verify.py --repo-root .
.venv/bin/python qa/pi_harness_audit.py --repo-root .
.venv/bin/python qa/qm_pack_verify.py --repo-root .
```
Expected: pytest all green (270 prior + ~29 new); verify.py 9 pass exit 0; both audits PASS.

- [ ] **Step 2: Smoke the dashboard locally without a DB** (empty-state render)

Run:
```bash
.venv/bin/python - <<'EOF'
from wnba_pipeline import web
web.fetch_betting = lambda: []
web.fetch_stats_by_team = lambda split="last7": {}
web.fetch_team_stats = lambda split: []
client = web.app.test_client()
r = client.get("/")
assert r.status_code == 200 and b"OFF DUTY LOCKS" in r.data
assert b"1-800-GAMBLER" in r.data
print("dashboard-smoke-OK", len(r.data), "bytes")
EOF
```
Expected: `dashboard-smoke-OK <n> bytes`.

- [ ] **Step 3: Production-surface diff is intentional**

Run: `git diff main --stat -- src/ | cat`
Expected: exactly the intended files (schema.sql, db.py, web.py, signals.py, model.py, enrich.py, templates/, static/) — nothing else.

- [ ] **Step 4: Push and open PR**

```bash
git push -u origin feat/research-dashboard
gh pr create --title "feat(dashboard): WNBA research dashboard — two-sided splits, line history, signals, Model v0, power rankings" --body "$(cat <<'EOF'
## Summary

offdutylocks.com becomes a one-stop WNBA research page. Spec: docs/superpowers/specs/2026-08-02-research-dashboard-design.md (approved; Model v0 owner revision).

- **Two-sided splits** — tickets % and money % for BOTH sides of spread/total/ML (null-safe complement; never fabricated 50/50)
- **Line & odds history** — append-only `betting_line_snapshots` written by every scrape publish (30-min cadence in season); click any game → inline-SVG step chart (opening line annotated) + snapshot table; honest empty state until history accrues
- **Auto signals + legend** — Sharp Money / Public Heavy / RLM / Model Edge / Conflict, thresholds as tested constants, brand-law colors, tooltips
- **MODEL v0** — transparent formula projections (Last-7 ORtg × pace + 2.5 home court), spread/total edges vs market, capped 0–10 EDGE ring; explicitly labeled a formula, not a trained model
- **Offensive Power Rankings** — auto-ranked by the existing offensive-rating formula, Last 7 / YTD tabs
- Dashboard is the new `/`; legacy tables live at `/tables`; DB-outage → clean empty state; responsible-gaming footer on every render

Schema change is additive `CREATE TABLE IF NOT EXISTS` (bootstraps at next publish). Zero new dependencies, zero build step, zero API-billed automation.

## Verification

(real output pasted from Task 7 Steps 1–3)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: CI green**

Run: `gh pr checks --watch`
Expected: required "CI" check passes.

---

## Self-Review (completed)

- **Spec coverage:** §1 → Task 1; §2 → Task 2; §2b → Task 3 (+ model-edge in Task 2); §3 → Tasks 4–5; §4 → Task 6 (grid incl. MODEL columns + EDGE ring + legend + rankings + footer + `/tables`); testing section → every task's TDD steps; delivery → Task 7.
- **Placeholder scan:** all file contents inline; no TBDs; the one behavioral migration (legacy index test → `/tables`) is explicit in Task 6 Step 6.
- **Type consistency:** `project_game` signature identical in Tasks 3/4; `detect_signals(game, model)` identical in Tasks 2/4; `SNAPSHOT_COLUMNS` used in Tasks 1/5; template context names (`games`, `rankings`, `db_ok`, `updated_at`, `home_court`, helpers `fmt/pct/ml/ring_class`) match between web.py and dashboard.html; signal CSS classes `sig-<type>` match the exact type strings from signals.py.
