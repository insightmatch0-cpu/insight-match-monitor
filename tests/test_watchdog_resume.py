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


class TestGhostRunsDoNotBurnTheQuota(unittest.TestCase):
    """حادثة 2026-09-09: بعد التعبئة أُطلق المحرك 2 (#207) فأُزيح من طابور
    مجموعة التزامن قبل أن يبدأ (0 وظائف، cancelled بعد 5 دقائق)، وتشغيلتا
    الجدولة الاحتياطية تخطّتا بحارس اليوم في 15 ثانية — وعدّها الحارس كلها
    محاولات فاستهلك «5 محاولات» بلا توقع واحد، ولم يُعد الإطلاق."""

    NOW = datetime(2026, 9, 9, 15, 30, tzinfo=timezone.utc)

    NO_JOBS = staticmethod(lambda rid: 0)

    def test_queue_displaced_dispatch_is_not_an_attempt(self):
        """#207: أُنشئت 15:05 وأُلغيت 15:10 وهي في الطابور — صفر وظائف."""
        r = {"databaseId": 34367896793, "createdAt": "2026-09-09T15:05:06Z",
             "updatedAt": "2026-09-09T15:10:20Z", "status": "completed", "conclusion": "cancelled"}
        self.assertFalse(W.is_real_attempt(r, self.NO_JOBS))
        self.assertTrue(W.is_real_attempt(r), "بلا قراءة وظائف: حكم المدة المتحفظ يعدّها محاولة")
        out = W.summarize_runs([r], self.NOW, jobs_lookup=self.NO_JOBS)
        self.assertEqual(out["runs_today"], 0)
        self.assertEqual(out["last_created"], "", "الإزاحة لا تُصفّر فاصل الساعتين")
        self.assertFalse(out["busy"], "الإزاحة لا تشغل التهدئة — يُعاد الإطلاق في الدورة التالية")

    def test_same_day_guard_skip_is_not_an_attempt(self):
        r = {"createdAt": "2026-09-09T08:17:21Z", "updatedAt": "2026-09-09T08:17:36Z",
             "status": "completed", "conclusion": "success"}
        self.assertFalse(W.is_real_attempt(r))

    def test_real_failure_and_real_success_count(self):
        fail = {"createdAt": "2026-09-09T04:30:46Z", "updatedAt": "2026-09-09T04:36:26Z",
                "status": "completed", "conclusion": "failure"}
        ok = {"createdAt": "2026-09-08T04:26:45Z", "updatedAt": "2026-09-08T04:43:58Z",
              "status": "completed", "conclusion": "success"}
        self.assertTrue(W.is_real_attempt(fail))
        self.assertTrue(W.is_real_attempt(ok))
        out = W.summarize_runs([fail, ok], self.NOW)
        self.assertEqual(out["runs_today"], 1)
        self.assertEqual(out["last_created"], "2026-09-09T04:30:46Z")

    def test_killed_mid_work_still_counts(self):
        """القاتل المتعاون: إلغاء بعد 50 دقيقة عمل محاولة حقيقية (دفعات مدفوعة)."""
        r = {"databaseId": 1, "createdAt": "2026-09-06T04:46:00Z", "updatedAt": "2026-09-06T05:36:14Z",
             "status": "completed", "conclusion": "cancelled"}
        self.assertTrue(W.is_real_attempt(r, lambda rid: 1))
        self.assertTrue(W.is_real_attempt(r, lambda rid: -1), "قراءة مجهولة = حكم المدة")

    def test_in_progress_is_busy_and_counts(self):
        r = {"createdAt": "2026-09-09T15:20:00Z", "status": "in_progress"}
        out = W.summarize_runs([r], self.NOW)
        self.assertTrue(out["busy"])
        self.assertEqual(out["runs_today"], 1)

    def test_missing_duration_is_treated_as_attempt(self):
        """قراءة ناقصة = تحفظ: لا نمنح محاولة مجانية بسبب حقل غائب."""
        self.assertTrue(W.is_real_attempt({"createdAt": "2026-09-09T04:30:46Z",
                                           "status": "completed", "conclusion": "failure"}))

    def test_incident_replay_leaves_quota_for_the_refill(self):
        """إعادة تمثيل يوم 09-09 بالتشغيلات الفعلية: محاولتان حقيقيتان لا خمس."""
        runs = [
            {"databaseId": 207, "createdAt": "2026-09-09T15:05:06Z", "updatedAt": "2026-09-09T15:10:20Z", "status": "completed", "conclusion": "cancelled"},
            {"createdAt": "2026-09-09T11:06:45Z", "updatedAt": "2026-09-09T11:14:20Z", "status": "completed", "conclusion": "failure"},
            {"createdAt": "2026-09-09T09:03:42Z", "updatedAt": "2026-09-09T09:07:05Z", "status": "completed", "conclusion": "success"},
            {"createdAt": "2026-09-09T08:17:21Z", "updatedAt": "2026-09-09T08:17:36Z", "status": "completed", "conclusion": "success"},
            {"createdAt": "2026-09-09T06:40:31Z", "updatedAt": "2026-09-09T06:48:25Z", "status": "completed", "conclusion": "failure"},
            {"createdAt": "2026-09-09T04:30:46Z", "updatedAt": "2026-09-09T04:36:26Z", "status": "completed", "conclusion": "failure"},
        ]
        out = W.summarize_runs(runs, self.NOW, jobs_lookup=lambda rid: 0 if rid == 207 else 1)
        # 04:30 و06:40 و11:06 فشل حقيقي، و09:03 تشغيلة جدولة كاملة (3.5 دقيقة) — 4 محاولات؛
        # 08:17 تخطٍّ و15:05 إزاحة لا تُحتسبان
        self.assertEqual(out["runs_today"], 4)
        self.assertLess(out["runs_today"], W.RESUME_MAX_RUNS_PER_DAY)
        self.assertTrue(W.resume_decide(30, "2026-09-09T11:14:14+00:00", "2026-09-09",
                                        out["runs_today"], out["last_created"],
                                        self.NOW.isoformat()))

    def test_gh_query_requests_the_duration_fields(self):
        src = (ROOT / "watchdog.py").read_text(encoding="utf-8")
        self.assertIn("databaseId,createdAt,updatedAt,status,conclusion", src)
        self.assertIn("jobs_lookup=lambda rid: run_job_count(repo, rid)", src)
