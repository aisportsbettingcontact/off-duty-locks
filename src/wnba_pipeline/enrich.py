"""Game enrichment for the serving layer: both-side splits, Model v0, signals.

Pure functions over plain dicts (the web layer's row shape). Null-safety law:
a missing percentage stays None on BOTH sides — never a fabricated 50/50.

Team-stats join law: betting rows carry Action Network team ids ("1341" =
Las Vegas Aces) while ``team_stats.team_id`` holds stats.wnba.com ids
("1611661319" = Las Vegas Aces), so an id-only join never matches in
production. The id join is attempted first (correct for any future
same-namespace data), then a normalized full-name join — both feeds carry
the full name ("Las Vegas Aces"). A miss on both stays None, never a
fabricated row.
"""

from __future__ import annotations

import re
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


# Known divergent spellings between the betting feed and stats.wnba.com,
# normalized form -> normalized form. Empty today: every AN full name in
# fixtures/betting/an_scoreboard_wnba.json matches teams-2026-reference.json
# exactly. Add entries only for observed real-world pairs, never guessed ones.
TEAM_NAME_ALIASES: dict[str, str] = {}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _normalize_name(name: Any) -> str:
    """Join key for a team name: lowercase, non-alphanumeric runs -> one space."""
    return _NON_ALNUM.sub(" ", str(name or "").lower()).strip()


# Public name for the join-key normalizer: espn.py uses the same helper to
# crosswalk ESPN displayNames onto the expected-team universe, and the two
# callers must never drift apart on what "the same name" means.
normalize_team_name = _normalize_name


def stats_by_team_name(
    stats_by_team_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    """Normalized ``team_name`` -> stats row. Build once, then join per game."""
    out: dict[str, Mapping[str, Any]] = {}
    for row in stats_by_team_id.values():
        key = _normalize_name(row.get("team_name"))
        if key:
            out[key] = row
    return out


def find_team_stats(
    stats_by_team_id: Mapping[str, Mapping[str, Any]],
    by_name: Mapping[str, Mapping[str, Any]],
    team_id: Any,
    team_name: Any,
) -> Mapping[str, Any] | None:
    """One team's stats row: id join first, normalized-name join as fallback."""
    row = stats_by_team_id.get(str(team_id))
    if row is not None:
        return row
    key = _normalize_name(team_name)
    return by_name.get(TEAM_NAME_ALIASES.get(key, key))


def enrich_games(
    games: list[dict[str, Any]],
    stats_by_team_id: Mapping[str, Mapping[str, Any]],
    season_by_team_id: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Both-side splits, Model v0 and signals for each game row.

    ``season_by_team_id`` is the season split. When supplied, Model v0 blends
    each team's offensive rating across the two windows (see ``model.FORM_WEIGHT``)
    instead of trusting a short trailing window alone. Omitting it keeps the
    pre-blend behaviour, so callers that only hold one split still work.
    """
    by_name = stats_by_team_name(stats_by_team_id)
    season_by_name = (stats_by_team_name(season_by_team_id)
                      if season_by_team_id else {})
    out = []
    for game in games:
        g = with_both_sides(game)
        away_recent = find_team_stats(
            stats_by_team_id, by_name, g.get("away_team_id"), g.get("away_name"))
        home_recent = find_team_stats(
            stats_by_team_id, by_name, g.get("home_team_id"), g.get("home_name"))
        away_season = home_season = None
        if season_by_team_id:
            away_season = find_team_stats(
                season_by_team_id, season_by_name,
                g.get("away_team_id"), g.get("away_name"))
            home_season = find_team_stats(
                season_by_team_id, season_by_name,
                g.get("home_team_id"), g.get("home_name"))
        model = project_game(
            away_recent, home_recent,
            g.get("current_spread"), g.get("current_total"),
            away_season=away_season, home_season=home_season,
        )
        g["model"] = model
        g["signals"] = detect_signals(g, model)
        out.append(g)
    return out
