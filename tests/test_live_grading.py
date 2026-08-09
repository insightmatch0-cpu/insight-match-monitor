# -*- coding: utf-8 -*-
"""اختبارات التقييم اللحظي للتقارير — أمر المالك 2026-08-09.

فصل القياس عن التبليغ: التقييم فور اكتمال البيانات (يظهر على اللوحة خلال
دورة)، وبطاقات تيليجرام تبقى مجمعة في الصباح كما كانت حرفياً.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import monitor as M
import predict_v2 as P

GRADE_OK = {"grades": [{"claim": "كلا الفريقين يسجلان", "result": "صح"},
                       {"claim": "فوق 2.5", "result": "خطأ"}],
            "lessons": [], "summary": "ملخص"}


class LiveGradingHarness(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        self.sent = []
        orig_file = P.SCENARIOS_FILE
        orig_actual = P.actual_match_data
        orig_grade = P.grade_scenario_report
        orig_tg = P.send_telegram_long
        orig_lessons = P.LESSONS_FILE
        self.lessons_tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        self.lessons_tmp.write_text('{"lessons": []}', encoding="utf-8")
        P.SCENARIOS_FILE = self.tmp
        P.LESSONS_FILE = self.lessons_tmp
        P.actual_match_data = lambda fid: (
            "النتيجة 2-1\nإحصائيات الفريقين — تسديدات", {"referee": ""})
        P.grade_scenario_report = lambda e, a: dict(GRADE_OK)
        P.send_telegram_long = lambda text: self.sent.append(text)
        self.addCleanup(lambda: (
            setattr(P, "SCENARIOS_FILE", orig_file),
            setattr(P, "actual_match_data", orig_actual),
            setattr(P, "grade_scenario_report", orig_grade),
            setattr(P, "send_telegram_long", orig_tg),
            setattr(P, "LESSONS_FILE", orig_lessons),
            self.tmp.unlink(missing_ok=True),
            self.lessons_tmp.unlink(missing_ok=True)))

    def write(self, data):
        self.tmp.write_text(json.dumps(data, ensure_ascii=False),
                            encoding="utf-8")

    def read(self):
        return json.loads(self.tmp.read_text(encoding="utf-8"))

    def entry(self, fid="1", hours_ago=4):
        kick = (P.now_utc() - P.timedelta(hours=hours_ago)).isoformat()
        return {"fid": fid, "date": kick[:10], "kickoff": kick,
                "home": "H", "away": "A", "report": "تقرير", "shadow": True}


class TestLiveGrading(LiveGradingHarness):

    def test_finished_match_graded_silently(self):
        """جوهر الأمر: التقييم فوري وبلا أي تيليجرام — التبليغ للصباح."""
        self.write({"pending": {"1": self.entry()}, "resolved": []})
        self.assertEqual(P.live_grade_scenarios(), 1)
        scen = self.read()
        self.assertEqual(scen["pending"], {})
        self.assertEqual(len(scen["resolved"]), 1)
        e = scen["resolved"][0]
        self.assertFalse(e["reported"], "البطاقة مخزنة بانتظار تبليغ الصباح")
        self.assertIn("📋 تقييم تقرير المحرك 2", e["scorecard"])
        self.assertEqual(self.sent, [], "لا تيليجرام في التقييم اللحظي أبداً")

    def test_too_recent_kickoff_waits(self):
        """بوابة الوقت: لا محاولة قبل LIVE_GRADE_MIN_MINUTES من الانطلاق."""
        self.write({"pending": {"1": self.entry(hours_ago=1)}, "resolved": []})
        self.assertEqual(P.live_grade_scenarios(), 0)
        self.assertIn("1", self.read()["pending"])

    def test_missing_statistics_waits(self):
        """بوابة التحقق: نتيجة بلا إحصائيات = بيانات ناقصة — ننتظر ولا نقيّم."""
        P.actual_match_data = lambda fid: ("النتيجة 2-1", {"referee": ""})
        self.write({"pending": {"1": self.entry()}, "resolved": []})
        self.assertEqual(P.live_grade_scenarios(), 0)
        self.assertIn("1", self.read()["pending"])

    def test_per_cycle_cap_respected(self):
        pend = {str(i): self.entry(fid=str(i)) for i in range(5)}
        self.write({"pending": pend, "resolved": []})
        self.assertEqual(P.live_grade_scenarios(), P.LIVE_GRADES_PER_CYCLE)

    def test_revert_switch_disables(self):
        old = P.LIVE_SCENARIO_GRADING
        try:
            P.LIVE_SCENARIO_GRADING = False
            self.write({"pending": {"1": self.entry()}, "resolved": []})
            self.assertEqual(P.live_grade_scenarios(), 0)
        finally:
            P.LIVE_SCENARIO_GRADING = old


class TestMorningReporting(LiveGradingHarness):

    def test_morning_sends_overnight_scorecards_once(self):
        """الصباح يبلّغ بطاقات ما قُيّم ليلاً — مرة واحدة فقط، حتى لو خلا pending."""
        self.write({"pending": {},
                    "resolved": [{"fid": "1", "scorecard": "📋 بطاقة ليلية",
                                  "reported": False}]})
        P.resolve_scenarios()
        self.assertEqual(self.sent, ["📋 بطاقة ليلية"])
        self.assertTrue(self.read()["resolved"][0]["reported"])
        # تشغيلة صباح ثانية (الكرون الاحتياطي) — لا إرسال مكرر
        self.sent.clear()
        P.resolve_scenarios()
        self.assertEqual(self.sent, [])

    def test_morning_grading_path_still_sends_same_card(self):
        """شبكة الأمان: ما لم يُقيَّم ليلاً يقيّمه الصباح ويبلّغه بنفس البطاقة."""
        self.write({"pending": {"1": self.entry()}, "resolved": []})
        graded = P.resolve_scenarios()
        self.assertEqual(graded, 1)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("📋 تقييم تقرير المحرك 2", self.sent[0])
        self.assertTrue(self.read()["resolved"][0]["reported"])

    def test_old_resolved_entries_without_scorecard_untouched(self):
        self.write({"pending": {},
                    "resolved": [{"fid": "قديم", "correct": 3, "total": 8}]})
        self.assertEqual(P.resolve_scenarios(), 0)
        self.assertEqual(self.sent, [])


class TestMonitorWiring(unittest.TestCase):

    def test_monitor_calls_live_grading_each_cycle(self):
        import inspect
        src = inspect.getsource(M.main)
        self.assertIn("live_grade_scenarios", src)
        self.assertIn("import predict_v2", src)
