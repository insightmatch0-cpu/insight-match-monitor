# -*- coding: utf-8 -*-
"""اختبارات إلغاء أسقف سجلات القياس المتبقية — امتداد أمر المالك 2026-08-09.

المالك اكتشف بنفسه أن سجل الرادار مشبع عند 300/300 (الأرقام "شبه ثابتة")
وسجل التقارير كان سيشبع عند 100 خلال أيام — نفس فئة نافذة الـ 1000.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import predict_v2 as P


class TestCapsDisabled(unittest.TestCase):

    def test_caps_are_zero_by_default(self):
        self.assertEqual(P.RADAR_RESOLVED_CAP, 0,
                         "سقف الرادار عاد — ممنوع بأمر المالك 2026-08-09")
        self.assertEqual(P.SCENARIOS_RESOLVED_CAP, 0,
                         "سقف التقارير عاد — ممنوع بأمر المالك 2026-08-09")


class TestRadarLogNeverTruncated(unittest.TestCase):

    def _run_resolve(self, log_content):
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps(log_content, ensure_ascii=False),
                       encoding="utf-8")
        orig = P.RADAR_LOG_FILE
        P.RADAR_LOG_FILE = tmp
        self.addCleanup(lambda: (setattr(P, "RADAR_LOG_FILE", orig),
                                 tmp.unlink(missing_ok=True)))
        P.resolve_radar_log({"resolved": [
            {"fid": "9", "correct": False, "score": "0-1"}]})
        return json.loads(tmp.read_text(encoding="utf-8"))

    def test_301st_graded_warning_keeps_all_300_before_it(self):
        """اللحظة التي كانت تقص أقدم إنذار — الآن يبقى الجميع."""
        old = [{"fid": f"w{i}", "level": "red", "failed": True,
                "graded_on": "2026-08-01"} for i in range(300)]
        log = self._run_resolve(
            {"resolved": old, "alerts": [], "alerts_resolved": [],
             "warnings": [{"fid": "9", "level": "amber", "date": "2026-08-09"}]})
        self.assertEqual(len(log["resolved"]), 301,
                         "إنذار 301 يجب ألا يطرد الأقدم")

    def test_emergency_cap_switch_still_works(self):
        oldcap = P.RADAR_RESOLVED_CAP
        try:
            P.RADAR_RESOLVED_CAP = 300
            old = [{"fid": f"w{i}", "level": "red", "failed": True,
                    "graded_on": "2026-08-01"} for i in range(300)]
            log = self._run_resolve(
                {"resolved": old, "alerts": [], "alerts_resolved": [],
                 "warnings": [{"fid": "9", "level": "amber",
                               "date": "2026-08-09"}]})
            self.assertEqual(len(log["resolved"]), 300)
        finally:
            P.RADAR_RESOLVED_CAP = oldcap


class TestScenariosNeverTruncated(unittest.TestCase):

    def test_resolve_scenarios_keeps_all_graded_reports(self):
        """التقرير الـ 101 كان سيطرد الأقدم — الآن يبقى الجميع."""
        old = [{"fid": f"s{i}", "shadow": True} for i in range(100)]
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps(
            {"pending": {"new": {"fid": "new", "date": "2026-08-08",
                                 "kickoff": "2026-08-08T10:00:00+00:00",
                                 "home": "H", "away": "A", "report": "x"}},
             "resolved": old}, ensure_ascii=False), encoding="utf-8")
        orig_file = P.SCENARIOS_FILE
        orig_actual = P.actual_match_data
        orig_grade = P.grade_scenario_report
        orig_tg = P.send_telegram_long
        P.SCENARIOS_FILE = tmp
        P.actual_match_data = lambda fid: ("النتيجة 1-0", {"referee": ""})
        P.grade_scenario_report = lambda e, a: {
            "grades": [{"claim": "c", "result": "صح"}], "lessons": []}
        P.send_telegram_long = lambda text: None
        self.addCleanup(lambda: (setattr(P, "SCENARIOS_FILE", orig_file),
                                 setattr(P, "actual_match_data", orig_actual),
                                 setattr(P, "grade_scenario_report", orig_grade),
                                 setattr(P, "send_telegram_long", orig_tg),
                                 tmp.unlink(missing_ok=True)))
        P.resolve_scenarios()
        scen = json.loads(tmp.read_text(encoding="utf-8"))
        self.assertEqual(len(scen["resolved"]), 101,
                         "التقرير 101 يجب ألا يطرد الأقدم")


class TestSentinelCoversTheseRecords(unittest.TestCase):

    def test_no_deletion_law_watches_radar_and_scenarios(self):
        """الفجوة التي جعلت القص الصامت يمر: قانون عدم النقصان لم يكن يراقب
        هذه السجلات — الآن يراقبها للأبد."""
        import inspect
        src = inspect.getsource(P.integrity_check)
        for key in ("radar_resolved", "radar_alerts_resolved", "scen_resolved"):
            self.assertIn(key, src)
