# -*- coding: utf-8 -*-
"""اختبارات قاعدة إيقاف تنبيهات الدراما — REC-005 (قرار المالك 2026-08-08).

القاعدة المسجّلة مسبقاً: عند 30 تنبيهاً مُقيَّماً لنوع الادعاء —
دقة < 40% → صامت (يُسجَّل بلا تيليجرام)؛ ≥ 50% → مُثبَت (بلا وسم 🧪).
تحت 30: صفر تأثير. القياس لكل نوع على حدة (قاعدة المالك ج).
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import monitor as M
import predict_v2 as P


def _graded(key, hits, total):
    """يبني تنبيهات مُقيَّمة: hits صحيحة من total لنوع الادعاء key."""
    return [{"fid": f"{key}{i}", "key": key, "hit": i < hits,
             "graded_on": "2026-08-01"} for i in range(total)]


def snap(minute, h=None, a=None):
    return {"minute": minute, "gh": 0, "ga": 0, "h": h or {}, "a": a or {}}


class TestMorningRule(unittest.TestCase):
    """التقييم الصباحي يكتب silenced/proven من السجل التراكمي — لكل نوع حكمه."""

    def _run_resolve(self, log_content):
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps(log_content, ensure_ascii=False),
                       encoding="utf-8")
        orig = P.RADAR_LOG_FILE
        P.RADAR_LOG_FILE = tmp
        self.addCleanup(lambda: (setattr(P, "RADAR_LOG_FILE", orig),
                                 tmp.unlink(missing_ok=True)))
        P.resolve_radar_log({"resolved": []})
        return json.loads(tmp.read_text(encoding="utf-8"))

    def test_thirty_graded_below_40_is_silenced(self):
        log = self._run_resolve(
            {"alerts_resolved": _graded("next_goal", 10, 30)})   # 33.3%
        self.assertIn("next_goal", log["silenced"])
        self.assertNotIn("next_goal", log["proven"])

    def test_thirty_graded_at_or_above_50_is_proven(self):
        log = self._run_resolve(
            {"alerts_resolved": _graded("goal", 16, 30)})        # 53.3%
        self.assertIn("goal", log["proven"])
        self.assertNotIn("goal", log["silenced"])

    def test_under_thirty_has_zero_effect_even_at_zero_accuracy(self):
        """تحت 30 تنبيهاً: لا إسكات ولا تثبيت مهما كان السجل."""
        log = self._run_resolve(
            {"alerts_resolved": _graded("equalizer", 0, 29)})    # 0% لكن n=29
        self.assertNotIn("equalizer", log.get("silenced", []))
        self.assertNotIn("equalizer", log.get("proven", []))

    def test_gray_zone_40_to_50_stays_trial(self):
        """بين 40% و50%: يبقى تجريبياً — لا صامتاً ولا مُثبَتاً."""
        log = self._run_resolve(
            {"alerts_resolved": _graded("flip", 13, 30)})        # 43.3%
        self.assertNotIn("flip", log.get("silenced", []))
        self.assertNotIn("flip", log.get("proven", []))

    def test_each_claim_judged_separately(self):
        """قاعدة المالك (ج): لا يُعاقب next_goal بذنب flip — كل نوع بلوحته."""
        log = self._run_resolve({"alerts_resolved":
                                 _graded("next_goal", 20, 30)     # 66% → مُثبَت
                                 + _graded("flip", 5, 30)})       # 16% → صامت
        self.assertIn("next_goal", log["proven"])
        self.assertIn("flip", log["silenced"])

    def test_silenced_type_can_recover(self):
        """القوائم تُعاد كتابتها كل صباح: نوع مُسكَت تحسّن سجله يخرج من الصمت
        (النوع المُسكَت يستمر تسجيله، فيوجد دائماً تنبيه منتظر يُدخل التقييم)."""
        log = self._run_resolve({"silenced": ["goal"], "proven": [],
                                 "alerts": [{"fid": "w1", "key": "goal",
                                             "date": "2099-01-01"}],
                                 "alerts_resolved": _graded("goal", 16, 30)})
        self.assertNotIn("goal", log["silenced"])
        self.assertIn("goal", log["proven"])

    def test_revert_switch_keeps_lists_untouched(self):
        """مفتاح التراجع: RADAR_ALERT_STOP_RULE=False لا يكتب أي قائمة."""
        old = P.RADAR_ALERT_STOP_RULE
        try:
            P.RADAR_ALERT_STOP_RULE = False
            log = self._run_resolve(
                {"alerts": [{"fid": "x", "date": "2026-08-08"}],
                 "alerts_resolved": _graded("next_goal", 0, 30)})
            self.assertNotIn("silenced", log)
            self.assertNotIn("proven", log)
        finally:
            P.RADAR_ALERT_STOP_RULE = old


class TestAlertGate(unittest.TestCase):
    """monitor.py: النوع المُسكَت يُسجَّل بلا تيليجرام؛ المُثبَت بلا وسم 🧪."""

    def setUp(self):
        # 📵 بوابة التسعة/المفضلة (قرار المالك 2026-08-24 مساءً) تُفحص في
        # جرزها المخصصة — هنا نعطلها لفحص الآليات الأخرى بمعزل عنها
        _orig_gate = M.DRAMA_MINE_ONLY
        M.DRAMA_MINE_ONLY = False
        self.addCleanup(lambda: setattr(M, "DRAMA_MINE_ONLY", _orig_gate))


    def _capture_telegram(self):
        sent = []
        orig = M.send_telegram
        M.send_telegram = lambda text: sent.append(text)
        self.addCleanup(lambda: setattr(M, "send_telegram", orig))
        return sent

    def _tmp_radar_file(self, content):
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
        orig = M.RADAR_FILE
        M.RADAR_FILE = tmp
        self.addCleanup(lambda: (setattr(M, "RADAR_FILE", orig),
                                 tmp.unlink(missing_ok=True)))
        return tmp

    def _losing_entry(self):
        """سيناريو حتمي ينتج ادعاء equalizer (متأخر 0-1 بموجة ضغط وحصار حارس)."""
        return {"score": "0-1", "minute": 78, "home": "H", "away": "A",
                "radar": {"snaps": [
                    snap(70, {"sog": 2, "cor": 3, "shots": 5}, {"sv": 2}),
                    snap(78, {"sog": 4, "cor": 5, "shots": 8}, {"sv": 4})]}}

    def test_silenced_claim_logged_without_telegram(self):
        sent = self._capture_telegram()
        tmp = self._tmp_radar_file({"silenced": ["equalizer"], "proven": []})
        budget = {"used": 0}
        self.assertTrue(M.maybe_radar_alert("9", self._losing_entry(), budget))
        self.assertEqual(sent, [], "نوع مُسكَت لا يرسل تيليجرام أبداً")
        self.assertEqual(budget["used"], 0, "الصامت لا يستهلك سقف الإرسال")
        log = json.loads(tmp.read_text(encoding="utf-8"))
        self.assertEqual(len(log["alerts"]), 1, "يُسجَّل للتقييم الصباحي كالمعتاد")
        self.assertEqual(log["alerts"][0]["key"], "equalizer")
        self.assertTrue(log["alerts"][0]["silenced"])

    def test_proven_claim_sent_without_trial_tag(self):
        sent = self._capture_telegram()
        self._tmp_radar_file({"silenced": [], "proven": ["equalizer"]})
        self.assertTrue(M.maybe_radar_alert("9", self._losing_entry(),
                                            {"used": 0}))
        self.assertEqual(len(sent), 1)
        self.assertNotIn("🧪", sent[0], "النوع المُثبَت يفقد وسم التجربة")
        self.assertIn("تنبيه دراما", sent[0])

    def test_unknown_claim_keeps_trial_behavior(self):
        """تحت 30 (قوائم فارغة): السلوك القديم حرفياً — إرسال بوسم 🧪."""
        sent = self._capture_telegram()
        tmp = self._tmp_radar_file({})
        budget = {"used": 0}
        self.assertTrue(M.maybe_radar_alert("9", self._losing_entry(), budget))
        self.assertEqual(len(sent), 1)
        self.assertIn("🧪", sent[0])
        self.assertEqual(budget["used"], 1)
        log = json.loads(tmp.read_text(encoding="utf-8"))
        self.assertFalse(log["alerts"][0]["silenced"])

    def test_revert_switch_ignores_lists(self):
        """مفتاح التراجع في monitor: القوائم تُتجاهل والنوع المُسكَت يُرسل."""
        old = M.RADAR_ALERT_STOP_RULE
        try:
            M.RADAR_ALERT_STOP_RULE = False
            sent = self._capture_telegram()
            self._tmp_radar_file({"silenced": ["equalizer"], "proven": []})
            self.assertTrue(M.maybe_radar_alert("9", self._losing_entry(),
                                                {"used": 0}))
            self.assertEqual(len(sent), 1)
        finally:
            M.RADAR_ALERT_STOP_RULE = old


class TestDigestLine(unittest.TestCase):
    """(ج) سطر النشرة يفصّل كل نوع مع عدّاد التقدم نحو حكم الـ 30."""

    def _line_for(self, log_content):
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps(log_content, ensure_ascii=False),
                       encoding="utf-8")
        orig = P.RADAR_LOG_FILE
        P.RADAR_LOG_FILE = tmp
        self.addCleanup(lambda: (setattr(P, "RADAR_LOG_FILE", orig),
                                 tmp.unlink(missing_ok=True)))
        return P.drama_scoreboard_line()

    def test_progress_counter_toward_30(self):
        # سجلات ما قبل الموسم: عدّاد الموسم صفر، والتراكمي كما هو بين قوسين
        line = self._line_for({"alerts_resolved": _graded("next_goal", 2, 7)})
        self.assertIn("الهدف القادم: بدأ العدّ اليوم (منذ البداية: 2/7)", line)
        self.assertIn("7/30 نحو الحكم", line)   # عدّاد الحكم تراكمي لا موسمي

    def test_statuses_rendered_per_type(self):
        line = self._line_for({"silenced": ["flip"], "proven": ["next_goal"],
                               "alerts_resolved":
                               _graded("next_goal", 20, 30)
                               + _graded("flip", 5, 30)})
        self.assertIn("الهدف القادم: بدأ العدّ اليوم (منذ البداية: 20/30) — "
                      "مُثبَت ✅", line)
        self.assertIn("قلب النتيجة: بدأ العدّ اليوم (منذ البداية: 5/30) — "
                      "صامت 🔇", line)

    def test_empty_record_stays_silent(self):
        self.assertEqual(self._line_for({}), "")
