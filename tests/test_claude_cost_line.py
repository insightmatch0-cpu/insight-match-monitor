# -*- coding: utf-8 -*-
"""💳 سطر كلفة Claude اليومي عبر Usage & Cost Admin API (HOLD-013-3، قرار المالك
2026-09-05). قاعدة 14 أغسطس: «اجعل المورد الذي تعتمد عليه مرئياً كل يوم» —
الرصيد الذي أطفأ المحرك 4 مرات لم يكن يُرى أبداً. بلا مفتاح إداري = بلا سطر."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import api_guard
import predict_v2 as P2

SAMPLE = {"data": [
    {"starting_at": "2026-09-03T00:00:00Z", "ending_at": "2026-09-04T00:00:00Z",
     "results": [{"currency": "USD", "amount": "1234.00"}, {"currency": "USD", "amount": "100"}]},
    {"starting_at": "2026-09-04T00:00:00Z", "ending_at": "2026-09-05T00:00:00Z",
     "results": [{"currency": "USD", "amount": "4200.00"}]},
], "has_more": False, "next_page": None}


class TestCostLine(unittest.TestCase):
    def setUp(self):
        self._old = P2.ANTHROPIC_ADMIN_KEY

    def tearDown(self):
        P2.ANTHROPIC_ADMIN_KEY = self._old

    def test_summary_converts_cents_to_dollars_per_day(self):
        days = P2.summarize_cost_report(SAMPLE)
        self.assertAlmostEqual(days["2026-09-03"], 13.34, places=3)
        self.assertAlmostEqual(days["2026-09-04"], 42.0, places=3)

    def test_line_shows_yesterday_and_window_total(self):
        P2.ANTHROPIC_ADMIN_KEY = "sk-ant-admin-test-0000"
        calls = []
        def fetch(start, end):
            calls.append((start, end)); return SAMPLE
        now = datetime(2026, 9, 5, 5, 0, tzinfo=timezone.utc)
        line = P2.claude_cost_line(fetch=fetch, now=now)
        self.assertIn("أمس: $42.00", line)
        self.assertIn("$55.34", line)
        self.assertEqual(calls, [("2026-08-29T00:00:00Z", "2026-09-05T00:00:00Z")])

    def test_no_admin_key_means_no_line_and_no_call(self):
        P2.ANTHROPIC_ADMIN_KEY = ""
        def fetch(start, end):
            raise AssertionError("must not be called without a key")
        self.assertEqual(P2.claude_cost_line(fetch=fetch), "")

    def test_failure_is_silent(self):
        P2.ANTHROPIC_ADMIN_KEY = "sk-ant-admin-test-0000"
        def boom(start, end):
            raise RuntimeError("401")
        self.assertEqual(P2.claude_cost_line(fetch=boom), "")

    def test_kill_switch(self):
        P2.ANTHROPIC_ADMIN_KEY = "sk-ant-admin-test-0000"
        old = P2.CLAUDE_COST_LINE
        try:
            P2.CLAUDE_COST_LINE = False
            self.assertEqual(P2.claude_cost_line(fetch=lambda a, b: SAMPLE), "")
        finally:
            P2.CLAUDE_COST_LINE = old

    def test_admin_key_is_redacted_and_passed_by_workflow(self):
        self.assertIn("ANTHROPIC_ADMIN_KEY", api_guard.SECRET_ENV_NAMES)
        wf = (ROOT / ".github" / "workflows" / "predict_v2.yml").read_text(encoding="utf-8")
        self.assertIn("ANTHROPIC_ADMIN_KEY: ${{ secrets.ANTHROPIC_ADMIN_KEY }}", wf)

    def test_wired_into_digest_before_send(self):
        src = (ROOT / "predict_v2.py").read_text(encoding="utf-8")
        body = src[src.index("def main("):]
        self.assertLess(body.index("claude_cost_line()"), body.index("send_telegram_long(digest)"))


if __name__ == "__main__":
    unittest.main()
