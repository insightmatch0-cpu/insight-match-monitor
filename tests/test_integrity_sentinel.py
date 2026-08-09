# -*- coding: utf-8 -*-
"""اختبارات حارس النزاهة اليومي — أمر المالك 2026-08-09.

كل حادثة أرقام وقعت فعلاً (الحذف الصامت، تضخم الحكام، التقرير العالق،
تناقض الذاكرة والأرشيف) لها هنا اختبار يثبت أن الحارس يلتقطها آلياً.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import predict_v2 as P


def _row(date, conf, correct, fid=None):
    return {"fid": fid or f"{date}-{conf}-{correct}", "date": date,
            "confidence": conf, "correct": correct, "pick": "home",
            "actual": "home" if correct else "away",
            "prob_home": 50, "prob_draw": 30, "prob_away": 20,
            "home": "H", "away": "A", "league": "L"}


class SentinelHarness(unittest.TestCase):
    """يبني ملفات مؤقتة نظيفة ثم يسمح لكل اختبار بإفساد قانون واحد."""

    FILES = ("PREDICTIONS_FILE", "V1_PREDICTIONS_FILE", "USER_PREDICTIONS_FILE",
             "HISTORY_FILE", "SCENARIOS_FILE", "RADAR_LOG_FILE", "REFEREES_FILE")

    def setUp(self):
        self.paths = {}
        for name in self.FILES:
            tmp = Path(tempfile.mkstemp(suffix=".json")[1])
            orig = getattr(P, name)
            setattr(P, name, tmp)
            self.paths[name] = tmp
            self.addCleanup(lambda n=name, o=orig, t=tmp: (
                setattr(P, n, o), t.unlink(missing_ok=True)))
        yesterday = (P.now_utc() - P.timedelta(days=1)).strftime("%Y-%m-%d")
        self.yesterday = yesterday
        self.write("PREDICTIONS_FILE",
                   {"pending": {}, "resolved": [_row(yesterday, 72, True)]})
        self.write("V1_PREDICTIONS_FILE", {"pending": {}, "resolved": []})
        self.write("USER_PREDICTIONS_FILE", {"pending": {}, "resolved": []})
        self.write("HISTORY_FILE", {"days": {}})
        self.write("SCENARIOS_FILE", {"pending": {}, "resolved": []})
        self.write("RADAR_LOG_FILE", {})
        self.write("REFEREES_FILE", {})

    def write(self, name, data):
        self.paths[name].write_text(json.dumps(data, ensure_ascii=False),
                                    encoding="utf-8")

    def read(self, name):
        return json.loads(self.paths[name].read_text(encoding="utf-8"))

    def broken(self):
        return {name for name, v in P.integrity_check() if v}


class TestCleanState(SentinelHarness):

    def test_clean_data_passes_all_checks(self):
        self.assertEqual(self.broken(), set(),
                         "بيانات نظيفة يجب أن تمر 12/12")

    def test_checkpoint_written_for_tomorrow(self):
        P.integrity_check()
        chk = self.read("HISTORY_FILE")["integrity"]["resolved_counts"]
        self.assertEqual(chk["v2"], 1)


class TestCatchesRealIncidents(SentinelHarness):

    def test_silent_deletion_detected(self):
        """حادثة النافذة المتحركة معمّمة: أي نقص في العدد = إنذار."""
        P.integrity_check()   # يكتب نقطة التفتيش (v2=1)
        self.write("PREDICTIONS_FILE", {"pending": {}, "resolved": []})
        self.assertIn("عدم النقصان في السجلات", self.broken())

    def test_memory_archive_mismatch_detected(self):
        """طريقان مستقلان لنفس الرقم — اختلافهما إنذار (درس 70%+)."""
        self.write("HISTORY_FILE", {"days": {self.yesterday:
                                             {"v2": {"correct": 9, "total": 9}}}})
        self.assertIn("تطابق الذاكرة والأرشيف (المجاميع اليومية)", self.broken())

    def test_bucket_mismatch_detected(self):
        self.write("HISTORY_FILE", {"days": {self.yesterday: {
            "v2": {"correct": 1, "total": 1,
                   "buckets": {"70+": {"correct": 0, "total": 5}}}}}})
        self.assertIn("تطابق شرائح الثقة مع الأرشيف", self.broken())

    def test_referee_inflation_detected(self):
        """حادثة الحكام 2.6×: مجموع المباريات لا يطابق المعرفات المسجلة."""
        self.write("REFEREES_FILE", {"_meta": {"fids": ["1"]},
                                     "حكم": {"matches": 3, "yellows": 9, "reds": 3}})
        self.assertIn("تطابق الحكام 1:1", self.broken())

    def test_stuck_scenario_detected(self):
        """حادثة التقرير العالق 11 يوماً."""
        self.write("SCENARIOS_FILE", {"pending": {"9": {"date": "2026-07-29"}},
                                      "resolved": []})
        self.assertIn("لا تقرير معلقاً فوق مهلته", self.broken())

    def test_wrong_correct_flag_detected(self):
        r = _row(self.yesterday, 60, True)
        r["actual"] = "away"          # الاختيار home والنتيجة away لكن correct=True
        self.write("PREDICTIONS_FILE", {"pending": {}, "resolved": [r]})
        self.assertIn("صحة عمود correct (إعادة اشتقاق)", self.broken())

    def test_probability_sum_violation_detected(self):
        r = _row(self.yesterday, 60, True)
        r["prob_home"] = 70           # المجموع 120
        self.write("PREDICTIONS_FILE", {"pending": {}, "resolved": [r]})
        self.assertIn("الاحتمالات تجمع 100", self.broken())

    def test_confidence_out_of_bounds_detected(self):
        r = _row(self.yesterday, 95, True)
        self.write("PREDICTIONS_FILE", {"pending": {}, "resolved": [r]})
        self.assertIn("الثقة ضمن الحدود", self.broken())

    def test_duplicate_fid_detected(self):
        """ازدواج الحكام معمّماً: نفس المباراة تُحتسب مرتين في أي سجل."""
        r = _row(self.yesterday, 60, True, fid="dup")
        self.write("PREDICTIONS_FILE",
                   {"pending": {}, "resolved": [r, dict(r)]})
        self.assertIn("لا ازدواج معرفات", self.broken())


class TestSentinelRunner(SentinelHarness):

    def _capture_telegram(self):
        sent = []
        orig_tok, orig_chat = P.TELEGRAM_TOKEN, P.TELEGRAM_CHAT_ID
        orig = P.send_telegram_long
        P.TELEGRAM_TOKEN, P.TELEGRAM_CHAT_ID = "t", "c"
        P.send_telegram_long = lambda text: sent.append(text)
        self.addCleanup(lambda: (setattr(P, "send_telegram_long", orig),
                                 setattr(P, "TELEGRAM_TOKEN", orig_tok),
                                 setattr(P, "TELEGRAM_CHAT_ID", orig_chat)))
        return sent

    def test_clean_run_gives_green_line_and_no_telegram(self):
        sent = self._capture_telegram()
        line = P.run_integrity_sentinel()
        self.assertIn("✓", line)
        self.assertEqual(sent, [], "لا إزعاج حين تكون الحسبة سليمة")

    def test_violation_sends_same_day_alert(self):
        """جوهر الأمر: الإنذار في نفس اليوم لا في مراجعة بعد شهر."""
        sent = self._capture_telegram()
        self.write("SCENARIOS_FILE", {"pending": {"9": {"date": "2026-07-01"}},
                                      "resolved": []})
        line = P.run_integrity_sentinel()
        self.assertIn("⚠️", line)
        self.assertEqual(len(sent), 1)
        self.assertIn("حارس النزاهة", sent[0])
        self.assertIn("لا تقرير معلقاً فوق مهلته", sent[0])

    def test_revert_switch_disables_everything(self):
        old = P.INTEGRITY_SENTINEL
        try:
            P.INTEGRITY_SENTINEL = False
            self.assertEqual(P.run_integrity_sentinel(), "")
        finally:
            P.INTEGRITY_SENTINEL = old

    def test_sentinel_failure_never_kills_the_run(self):
        """عطل في الحارس نفسه لا يوقف التشغيلة الصباحية أبداً."""
        orig = P.integrity_check
        P.integrity_check = lambda: 1 / 0
        self.addCleanup(lambda: setattr(P, "integrity_check", orig))
        line = P.run_integrity_sentinel()
        self.assertIn("تعذر التشغيل", line)
