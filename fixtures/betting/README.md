# Betting fixtures

Sanitized, trimmed captures used by the offline betting tests.

| File | Source | Notes |
|---|---|---|
| `an_scoreboard_wnba.json` | Action Network v2 scoreboard (`/web/v2/scoreboard/wnba`), 2026-07-22 | First 2 WNBA games, minimal fields (teams, book 30 "open" + book 68 "DraftKings" markets incl. `bet_info` percentages). |
| `vsin_dk_wnba.html` | VSIN betting-splits (`?source=DK&view=today`), captured 2026-07-22 | The WNBA `sp-table` block only. |
| `vsin_circa_wnba.html` | VSIN betting-splits (`?source=circa&view=today`), captured 2026-07-22 | The WNBA `sp-table` block only (Circa sharp line). |

No credentials, cookies, or personal data are present — these are public
market pages/endpoints trimmed to the WNBA block. Line values reflect the
moment of capture and are used only to pin parser/merge behavior in tests.
