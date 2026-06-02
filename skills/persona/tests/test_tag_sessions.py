#!/usr/bin/env python3
"""Unit tests for the /persona run-estimate helpers in tag_sessions.py.

These back the token-usage disclosure shown at the consent gate (SKILL.md
Step 4b). Run directly:  python tests/test_tag_sessions.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TAGGING = HERE.parent / "tagging"
sys.path.insert(0, str(TAGGING))  # so tag_sessions + system_prompt_template import

import tag_sessions as ts  # noqa: E402


class TestRunEstimate(unittest.TestCase):
    def _taxonomy(self) -> dict:
        return json.loads(ts.DEFAULT_TAXONOMY.read_text(encoding="utf-8"))

    def test_empty_dir_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            est = ts.estimate_run(Path(d), self._taxonomy(), ts.DEFAULT_MODEL)
        self.assertEqual(est["n_structured"], 0)
        self.assertEqual(est["total_high_millions"], 0.0)

    def test_counts_sessions_and_scales_with_size(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            for i in range(3):
                (dd / f"sess{i}.txt").write_text("x" * 7000, encoding="utf-8")
            est = ts.estimate_run(dd, self._taxonomy(), ts.DEFAULT_MODEL)
        # One file per session counted; open subset capped at OPEN_PASS_SESSIONS.
        self.assertEqual(est["n_structured"], 3)
        self.assertEqual(est["n_open"], 3)
        self.assertGreater(est["base_input_tokens"], 0)
        # Band is ordered and uses the documented end-to-end factors.
        self.assertGreaterEqual(est["total_high_millions"], est["total_low_millions"])
        expected_low = round(est["base_input_tokens"] * ts.END_TO_END_FACTOR_LOW / 1e6, 1)
        self.assertEqual(est["total_low_millions"], expected_low)

    def test_open_subset_capped(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            for i in range(ts.OPEN_PASS_SESSIONS + 5):
                (dd / f"sess{i:02d}.txt").write_text("y" * 1000, encoding="utf-8")
            est = ts.estimate_run(dd, self._taxonomy(), ts.DEFAULT_MODEL)
        self.assertEqual(est["n_structured"], ts.OPEN_PASS_SESSIONS + 5)
        self.assertEqual(est["n_open"], ts.OPEN_PASS_SESSIONS)

    def test_format_run_estimate_renders_band(self) -> None:
        out = ts.format_run_estimate({
            "n_structured": 20, "n_open": 12,
            "base_input_tokens": 700000,
            "total_low_millions": 8.4, "total_high_millions": 15.4,
        })
        self.assertIn("Sessions to analyze   : 20", out)
        self.assertIn("~8.4-15.4 million tokens", out)
        self.assertIn("nothing leaves your machine", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
