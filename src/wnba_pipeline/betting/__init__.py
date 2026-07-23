"""WNBA betting-market feed: Action Network (odds, splits) + VSIN (Circa line).

Action Network is the backbone — the v2 scoreboard carries the opening line
(book 30), the current DraftKings line (book 68), and DraftKings ticket/money
percentages in each outcome's ``bet_info``. VSIN contributes the Circa sharp
line (``source=circa``), which Action Network does not carry. The two are
merged per game by ``(date, team-slug pair)`` into one wide row per game.
"""
