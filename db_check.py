"""One-off DB check: count rows in the serving tables.

Runs in GitHub Actions with the DATABASE_URL repo secret. Prints counts and a
small sample only — never the connection string (GitHub masks it regardless).
"""
import os
import sys

url = os.environ.get("DATABASE_URL") or ""
print("DATABASE_URL present:", bool(url), "| looks_internal:", "railway.internal" in url)
if not url:
    print("RESULT: DATABASE_URL secret is not set / empty")
    sys.exit(1)

import psycopg

try:
    conn = psycopg.connect(url, connect_timeout=15)
except Exception as exc:
    print("RESULT: CONNECT FAILED ->", type(exc).__name__, str(exc)[:180])
    sys.exit(1)

cur = conn.cursor()


def q(sql):
    try:
        cur.execute(sql)
        return cur.fetchall()
    except Exception as exc:
        conn.rollback()
        return "(missing/error: %s)" % type(exc).__name__


print("betting_games rows:", q("select count(*) from betting_games"))
print("team_stats rows:", q("select count(*) from team_stats"))
print("team_stats by split:", q("select split, count(*) from team_stats group by split order by split"))
print("betting freshness:", q("select max(game_date), max(updated_at) from betting_games"))
print("betting sample:", q(
    "select game_key, current_spread, spread_pct_bets_away, spread_pct_money_away, "
    "sharp_spread, total_rlm from betting_games order by game_date desc, game_key limit 6"))
print("team_stats sample:", q(
    "select team_name, points, round(offensive_rating::numeric,1) from team_stats "
    "where split='last7' order by offensive_rating desc nulls last limit 5"))

conn.close()
print("RESULT: query complete")
