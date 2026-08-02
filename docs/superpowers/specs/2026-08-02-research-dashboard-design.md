# Off Duty Locks — WNBA Research Dashboard (Design)

**Date:** 2026-08-02
**Status:** Approved (all four sections user-approved in brainstorming)
**Reference:** WNBA-Alpha-style dashboard screenshot (product owner), adapted
to this repo's data reality and `design-system/off-duty-locks/MASTER.md`
brand law. Execution: built on subscription auth under full pi-harness law;
a pi session performs the live verification pass (owner runs `/login` on
subscription — no API credits are billed by this build).

## Goal

Make offdutylocks.com a one-stop WNBA research page: betting splits for BOTH
sides of every market, line/odds movement history with a visual timeline,
automatic color-coded betting signals with a legend, and Offensive Power
Rankings (Last 7 / YTD) — all visible on one dashboard without opening other
sites.

**Non-goals / honesty constraints:**
- **Model surface = Model v0 (owner revision 2026-08-02).** The owner directed
  the model columns stay populated. Since no trained model exists, the model
  surface is **Model v0**: a transparent, deterministic formula over the
  team-stats the pipeline already verifies (exactly like the owner's
  offensive-rating formula — documented math, never fabricated numbers, and
  labeled "MODEL v0" in the UI). See §2b. It upgrades to a trained model
  later without changing the surface.
- **No history backfill.** Line snapshots accrue from deploy forward; the
  detail panel shows an honest "history begins <date>" empty state until
  snapshots exist. Opening lines (already stored) always render.
- Read-only web app stays read-only (SELECT only); no user input reaches SQL
  beyond whitelisted values.

## Current state (verified in code)

- `betting_games` already stores: open/current/sharp spread+total+ML,
  away-side `spread_pct_bets/money`, over-side `total_pct_bets/money`,
  away-side `ml_pct_bets/money`, and per-market RLM booleans
  (`spread_rlm`, `total_rlm`, `ml_rlm` — computed in `betting/merge.py`).
- `team_stats` already stores `offensive_rating` (owner formula in
  `derived.py`: possessions = FGA − OREB + TOV + 0.44·FTA; rating =
  points/possessions·100) for splits `last7` and `ytd`;
  `/api/teams?split=` already orders by `offensive_rating DESC`.
- Scrapers (VSIN + Action Network) run every 30 min through pregame/in-play
  windows May–Oct (`scrape.yml`) and UPSERT `betting_games` — no time series
  is currently kept (the gap §1 closes).
- `web.py` (275 lines) renders one inline-HTML page + JSON endpoints;
  `schema.sql` is idempotent and bootstraps at publish.

## 1. Data layer — line history

New append-only table in `schema.sql` (additive `CREATE TABLE IF NOT EXISTS`
— safe idempotent rollout at next publish):

```sql
CREATE TABLE IF NOT EXISTS betting_line_snapshots (
    game_key                TEXT        NOT NULL,
    captured_at_utc         TIMESTAMPTZ NOT NULL,
    spread                  DOUBLE PRECISION,   -- away side
    total                   DOUBLE PRECISION,   -- over side
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

Write path: the betting publish step (`betting/runner.py` → `db.py` upsert)
additionally INSERTs one snapshot row per game per run (ON CONFLICT DO
NOTHING on the PK — reruns within the same second are idempotent), using the
game's `fetched_at_utc` as `captured_at_utc`. Values snapshot the CURRENT
line and both-market percentages at that moment. No retention limit (a WNBA
season at 30-min cadence is small).

## 2. Signal engine — `src/wnba_pipeline/signals.py`

Pure, null-safe functions; thresholds are named module constants:

- `SHARP_DIVERGENCE_PCT = 15` — **sharp-money**: `|money% − tickets%| ≥ 15`
  on a market (side = where money leans).
- `PUBLIC_HEAVY_PCT = 70` — **public-heavy**: tickets% ≥ 70 on a side.
- **rlm**: the stored per-market RLM boolean is true.
- **conflict**: two or more of the above fire on the SAME market pointing at
  OPPOSITE sides (e.g., public heavy on away + RLM toward home is *expected*
  sharp/public divergence — NOT conflict; conflict = sharp-money toward one
  side while RLM indicates movement toward the other).
- **none**: nothing fired for the game.

Output contract per game: `signals: [{market: "spread"|"total"|"moneyline",
type: "sharp-money"|"public-heavy"|"rlm"|"conflict", side: "away"|"home"|
"over"|"under"|null}]` — empty list = no clear signal. Null inputs never
fire a signal (missing data is missing, not neutral). Colors are presentation
concerns mapped in the UI from brand-law tokens: green sharp / yellow public /
orange RLM / red conflict / gray none.

## 2b. Model v0 — `src/wnba_pipeline/model.py` (pure, tested)

Deterministic projections from `team_stats` split `last7` (current form),
computed at read time in the web layer (not stored — v0 has no training
state). All functions null-safe: any missing input → all model fields null,
no model signal, "—" rendered.

Named constants and formulas (away-side spread convention matches
`betting_games.current_spread`):

```
HOME_COURT_POINTS       = 2.5      # WNBA home advantage, documented constant
MODEL_EDGE_SPREAD_MIN   = 1.5      # points of spread edge to fire model-edge
MODEL_EDGE_TOTAL_MIN    = 2.0      # points of total edge to fire model-edge

poss_avg          = (possessions_away + possessions_home) / 2
proj_margin_home  = (ORtg_home − ORtg_away) × poss_avg / 100 + HOME_COURT_POINTS
model_spread_away = proj_margin_home          # away line: negative = away favored
model_total       = (ORtg_away + ORtg_home) × poss_avg / 100

edge_spread = current_spread − model_spread_away   # > 0 → value on AWAY, < 0 → HOME
edge_total  = model_total − current_total          # > 0 → value on OVER,  < 0 → UNDER

edge_score  = min(10, 2.0 × |edge_spread| + 1.0 × |edge_total|)   # 0–10, ring-colored
```

- **model-edge signal** (blue `#3B82F6`, brand token `--odl-signal-model`):
  fires when `|edge_spread| ≥ MODEL_EDGE_SPREAD_MIN` (side per sign) or
  `|edge_total| ≥ MODEL_EDGE_TOTAL_MIN` (over/under per sign); joins the §2
  signal contract as `type: "model-edge"`.
- **Edge Score ring** uses the brand-law rating-ring thresholds (green ≥ 7.5,
  yellow 5.0–7.4, orange < 5.0) and is labeled **EDGE** — it measures
  model-vs-market disagreement, and the UI's legend says exactly that.
- API: `/api/betting` games gain `model: {spread, total, edge_spread,
  edge_total, edge_score}` (nullable as a unit).
- UI: games grid gains MODEL columns (model spread · model total · edges) and
  the EDGE ring; a "MODEL v0" chip links the legend entry explaining the
  formula in one sentence.

## 3. API

- **`/api/betting` (enriched, backward-compatible additions):** each game
  row gains derived both-side percentages — `spread_pct_bets_home =
  100 − spread_pct_bets_away` (same for money, and total under-side, ML
  home-side; null stays null — never a fabricated 50) — plus the `signals`
  array from §2 and `open_ml_*` / `current_ml_*` passthroughs already stored.
- **`/api/games/<game_key>/history` (new):** `{game_key, opening:
  {spread, total, ml_away, ml_home}, snapshots: [...ordered rows...]}`.
  404 for unknown game_key (whitelisted lookup by exact key, parameterized).
- **Rankings:** reuse `/api/teams?split=last7|ytd` (already ordered by
  `offensive_rating DESC NULLS LAST`).

## 4. Dashboard UI

Server-rendered Jinja templates (`src/wnba_pipeline/templates/`) + static
assets (`src/wnba_pipeline/static/`): vanilla JS, inline-SVG chart, zero
build step, ships in the existing Docker image (src/ is included). Routes:
dashboard becomes `/` (index); the legacy table page moves to `/tables`
unchanged. Every token from `design-system/off-duty-locks/MASTER.md`:
surfaces `#0B0B0D`/`#141417`/border `#26262B`, ONE accent `#FF5C1C`, signal
colors green `#22C55E`/yellow `#EAB308`/red `#EF4444`/gray `#6B7280`, Barlow
Condensed 700 uppercase display + Inter `tabular-nums` data (Google Fonts
link), ~140 ms ease-out transitions, no gradients.

Page structure (top → bottom):

1. **Header** — OFF DUTY LOCKS wordmark, nav (Dashboard active in accent),
   "Last updated <time>" from data.
2. **Slate bar** — Today / Tomorrow toggle (games already publish both).
3. **Games grid** — one card-row per game: two team rows (abbr, name, W-L
   from team_stats when joinable); columns: Spread (open · current · sharp),
   Tickets % (BOTH sides), Money % (BOTH sides), Total (open · current ·
   sharp) with O/U %, Moneyline (both sides), Signals (color dots + market
   letter, tooltip naming the signal). Favorable/unfavorable coloring pairs
   text with color (never color alone).
4. **Detail panel** (click a game; also deep-linkable `#game=<key>`) —
   Spread / Total / ML tabs; inline-SVG step chart of the selected market
   over the day (accent line, dot per snapshot, opening line annotated);
   snapshot table (time ET, line/odds, both-side bets%/money%, book);
   honest empty state when no snapshots yet.
5. **Signal legend** — the five states with their colors and one-line
   meanings.
6. **Offensive Power Rankings** — Last 7 / YTD tabs; rank, team, OffRtg,
   possessions, PTS/G, record; data from `/api/teams` (server-rendered
   initial state, tab switch via fetch).
7. **Footer** — "All data provided for informational purposes only. Please
   wager responsibly." + "21+" + "1-800-GAMBLER".

Degradation: DB outage → clean 503 page (existing behavior preserved);
empty slate → friendly empty state.

## Testing

- `tests/test_signals.py` — every signal rule: fires, respects thresholds
  exactly at boundaries, null-safety (no signal from null), conflict
  definition both ways, output contract shape.
- `tests/test_web.py` extensions — both-side derivation (incl. null stays
  null), `/api/games/<key>/history` (found, 404, ordering), dashboard route
  renders (200, brand copy present, RG footer present), `/tables` legacy
  route.
- Snapshot write path — unit test on the publish step: one snapshot per game
  per run, idempotent on rerun (PK conflict ignored).
- Full suite + `qa/verify.py` + both harness audits stay green.

## Delivery

Branch `feat/research-dashboard`, TDD for signals/API/snapshot logic, single
PR to `main` (merge = deploy). Schema change is additive-idempotent (§1) —
bootstraps at next publish; no manual migration. Verification before PR: the
full offline battery; after deploy: deploy-verify workflow + live dashboard
check. pi's live session verification (owner-driven, subscription auth)
covers: dashboard renders under real data, signal dots match API output,
history chart draws once snapshots accrue.
