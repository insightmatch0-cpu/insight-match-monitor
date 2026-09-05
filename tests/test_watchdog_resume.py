# -*- coding: utf-8 -*-
"""🔁 التعافي الذاتي بعد رفض رصيد Anthropic (HOLD-013-1، قرار المالك 2026-09-05).

يوم الحادثة استغرق إكمال الفجوة ~5 ساعات من نفاد الرصيد إلى أمر يدوي.
الحارس الزمني يعيد إطلاق المحرك 2 كل ساعتين (≤4 محاولات) حتى تصفّر
تشغيلةٌ كاملة علامة الرفض."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import watchdog as W

TODAY = "2026-09-05"


class TestResumeDecide(unittest.TestCase):
    def test_fires_when_refused_today_and_gap_passed(self):
        self.assertTrue(W.resume_decide(79, f"{TODAY}T05:22:00+00:00", TODAY,
                                        1, f"{TODAY}T05:22:00Z", f"{TODAY}T07:30:00Z"))

    def test_silent_without_refusal(self):
        self.assertFalse(W.resume_decide(0, "", TODAY, 1, f"{TODAY}T05:22:00Z", f"{TODAY}T07:30:00Z"))

    def test_yesterday_refusal_is_the_morning_runs_business(self):
        self.assertFalse(W.resume_decide(79, "2026-09-04T05:22:00+00:00", TODAY,
                                         1, "2026-09-04T05:22:00Z", f"{TODAY}T07:30:00Z"))

    def test_daily_quota_caps_attempts(self):
        self.assertFalse(W.resume_decide(79, f"{TODAY}T05:22:00+00:00", TODAY,
                                         W.RESUME_MAX_RUNS_PER_DAY, f"{TODAY}T05:22:00Z", f"{TODAY}T23:00:00Z"))

    def test_two_hour_gap_enforced(self):
        self.assertFalse(W.resume_decide(79, f"{TODAY}T05:22:00+00:00", TODAY,
                                         1, f"{TODAY}T06:00:00Z", f"{TODAY}T07:30:00Z"))

    def test_kill_switch(self):
        old = W.V2_AUTO_RESUME
        try:
            W.V2_AUTO_RESUME = False
            self.assertFalse(W.resume_decide(79, f"{TODAY}T05:22:00+00:00", TODAY,
                                             1, f"{TODAY}T05:22:00Z", f"{TODAY}T09:00:00Z"))
        finally:
            W.V2_AUTO_RESUME = old


class TestPlumbing(unittest.TestCase):
    def test_summarize_runs_counts_today_and_last(self):
        now = datetime(2026, 9, 5, 9, 0, tzinfo=timezone.utc)
        runs = [{"createdAt": "2026-09-05T04:45:48Z", "status": "completed"},
                {"createdAt": "2026-09-04T03:30:00Z", "status": "completed"}]
        out = W.summarize_runs(runs, now)
        self.assertEqual(out["runs_today"], 1)
        self.assertEqual(out["last_created"], "2026-09-05T04:45:48Z")
        self.assertFalse(out["busy"])

    def test_v2_refusal_reads_meta(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "p.json"
            p.write_text(json.dumps({"meta": {"claude_refused": 79,
                                              "claude_refused_at": f"{TODAY}T05:22:00+00:00"}}))
            self.assertEqual(W.v2_refusal(p), {"refused": 79, "at": f"{TODAY}T05:22:00+00:00"})
            self.assertEqual(W.v2_refusal(Path(d) / "missing.json"), {"refused": 0, "at": ""})

    def test_engine_writes_the_flag_and_watchdog_reads_it(self):
        """بنيوي: المحرك 2 يكتب claude_refused في meta، والحارس يستدعي التعافي."""
        src = (ROOT / "predict_v2.py").read_text(encoding="utf-8")
        self.assertIn('"claude_refused": CLAUDE_REFUSED["credit"]', src)
        wsrc = (ROOT / "watchdog.py").read_text(encoding="utf-8")
        self.assertIn("maybe_resume_v2(now)", wsrc[wsrc.index("def main("):])


if __name__ == "__main__":
    unittest.main()
