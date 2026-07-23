-- WNBA pipeline serving-layer schema (PostgreSQL).
--
-- The file-based Store remains the source of truth and audit trail; these
-- tables are the read model the site consumes. All statements are idempotent
-- (CREATE ... IF NOT EXISTS), so bootstrapping on every publish is safe.

-- One row per team per split. `split` distinguishes the site's two sections:
--   'ytd'   = Year-to-Date  (LastNGames=0, full season)
--   'last7' = Last 7 Games  (LastNGames=7)
CREATE TABLE IF NOT EXISTS team_stats (
    season               TEXT             NOT NULL,
    season_type          TEXT             NOT NULL,
    per_mode             TEXT             NOT NULL,
    split                TEXT             NOT NULL,
    team_id              TEXT             NOT NULL,
    team_name            TEXT             NOT NULL,
    games_played         INTEGER,
    wins                 INTEGER,
    losses               INTEGER,
    win_pct              DOUBLE PRECISION,
    minutes              DOUBLE PRECISION,
    points               DOUBLE PRECISION,
    fgm                  DOUBLE PRECISION,
    fga                  DOUBLE PRECISION,
    fg_pct               DOUBLE PRECISION,
    fg3m                 DOUBLE PRECISION,
    fg3a                 DOUBLE PRECISION,
    fg3_pct              DOUBLE PRECISION,
    ftm                  DOUBLE PRECISION,
    fta                  DOUBLE PRECISION,
    ft_pct               DOUBLE PRECISION,
    oreb                 DOUBLE PRECISION,
    dreb                 DOUBLE PRECISION,
    reb                  DOUBLE PRECISION,
    ast                  DOUBLE PRECISION,
    tov                  DOUBLE PRECISION,
    stl                  DOUBLE PRECISION,
    blk                  DOUBLE PRECISION,
    pf                   DOUBLE PRECISION,
    possessions          DOUBLE PRECISION,
    offensive_rating     DOUBLE PRECISION,
    extraction_key       TEXT             NOT NULL,
    source_checksum      TEXT,
    normalized_checksum  TEXT,
    fetched_at_utc       TIMESTAMPTZ,
    updated_at           TIMESTAMPTZ      NOT NULL DEFAULT now(),
    PRIMARY KEY (season, season_type, per_mode, split, team_id)
);

CREATE INDEX IF NOT EXISTS idx_team_stats_split ON team_stats (split, season);

-- One wide row per game (maps 1:1 to a betting card on the site). Populated by
-- the betting feed (VSIN + Action Network). Odds are American integers; spread
-- and total lines are for the AWAY team / the OVER respectively.
CREATE TABLE IF NOT EXISTS betting_games (
    game_key                TEXT PRIMARY KEY,
    game_date               DATE,
    start_time              TIMESTAMPTZ,
    status                  TEXT,
    away_team_id            TEXT,
    home_team_id            TEXT,
    away_abbr               TEXT,
    home_abbr               TEXT,
    away_name               TEXT,
    home_name               TEXT,
    -- Spread (away side)
    open_spread             DOUBLE PRECISION,
    current_spread          DOUBLE PRECISION,
    sharp_spread            DOUBLE PRECISION,
    spread_pct_bets_away    INTEGER,
    spread_pct_money_away   INTEGER,
    spread_line_move        DOUBLE PRECISION,
    spread_rlm              BOOLEAN,
    -- Total (over side)
    open_total              DOUBLE PRECISION,
    current_total           DOUBLE PRECISION,
    sharp_total             DOUBLE PRECISION,
    total_pct_bets_over     INTEGER,
    total_pct_money_over    INTEGER,
    total_line_move         DOUBLE PRECISION,
    total_rlm               BOOLEAN,
    -- Moneyline
    open_ml_away            INTEGER,
    open_ml_home            INTEGER,
    current_ml_away         INTEGER,
    current_ml_home         INTEGER,
    sharp_ml_away           INTEGER,
    sharp_ml_home           INTEGER,
    ml_pct_bets_away        INTEGER,
    ml_pct_money_away       INTEGER,
    ml_rlm                  BOOLEAN,
    -- provenance
    public_book             TEXT,
    sharp_book              TEXT,
    an_game_id              TEXT,
    vsin_game_id            TEXT,
    fetched_at_utc          TIMESTAMPTZ,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_betting_games_date ON betting_games (game_date);
