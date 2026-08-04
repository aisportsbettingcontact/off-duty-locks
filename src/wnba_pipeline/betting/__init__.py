"""WNBA betting-market feed: Action Network (odds) + VSIN (splits, Circa line).

Action Network is the backbone — the v2 scoreboard carries the opening line
(book 30) and the current DraftKings line (book 68). VSIN contributes the
%bets / %money splits (DK view) and the Circa sharp line (``source=circa``),
neither of which comes from Action Network. The two are merged per game by
``(date, team-slug pair)`` into one wide row per game.
"""
