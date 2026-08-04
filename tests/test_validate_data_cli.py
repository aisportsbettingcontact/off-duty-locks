"""The ``validate-data`` command — the gate the scheduled scrapes actually run.

The checks themselves are pure and covered in ``test_dataquality.py``; what was
never covered is the CLI layer that CI depends on: which checks a scope runs,
how findings map to exit codes, and the flags that tune the gate. These tests
drive ``_cmd_validate_data`` against a fake psycopg connection (the pattern
from ``test_split_labeling.py``), so they stay offline and deterministic.
"""

from __future__ import annotations

import argparse
import datetime as dt

from wnba_pipeline import __main__ as cli


def test_betting_fresh_hours_flag_parses_with_default():
    """--betting-fresh-hours exists, is a float, and defaults to 6 hours."""
    args = cli.build_parser().parse_args(["validate-data"])
    assert args.betting_fresh_hours == 6.0
    args = cli.build_parser().parse_args(
        ["validate-data", "--betting-fresh-hours", "3"])
    assert args.betting_fresh_hours == 3.0
