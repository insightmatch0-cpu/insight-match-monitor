# -*- coding: utf-8 -*-
"""🔬 اختبارات مجمّع ظل xG (المرحلة أ — خطة 2026-07-31، انطلقت 2026-08-12).

المبادئ المحروسة: صفر تأثير على المحركات، فشل صامت بلا مفتاح، مطابقة أسماء
محافظة بين المزودين، ترجيح فورمة xG من التاريخ السابق فقط (لا تسريب مستقبل)،
سجل قياس بلا قص، وسطر الرؤية اليومية في النشرة (قاعدة المالك هـ).
صفر شبكة بالتصميم: كل اختبار يعمل بلا مفاتيح وبلا نداء خارجي.
"""

import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import predict_v2 as P
import sportmonks_shadow as S


class TestNameMatching(unittest.TestCase):
    """مطابقة أسماء الفرق بين API-Football وSportmonks — محافظة لا متساهلة."""

    def test_exact_and_decorated_names_match(self):
        self.assertTrue(S.names_match("Al Hilal", "Al-Hilal"))
        self.assertTrue(S.names_match("Manchester United", "Manchester United FC"))
        self.assertTrue(S.names_match("Bayern München", "Bayern Munchen"))

    def test_subset_names_match(self):
        self.assertTrue(S.names_match("Barcelona", "FC Barcelona"))

    def test_different_teams_do_not_match(self):
        self.assertFalse(S.names_match("Manchester United", "Manchester City"))
        self.assertFalse(S.names_match("Al Hilal", "Al Nassr"))

    def test_empty_names_never_match(self):
        self.assertFalse(S.names_match("", "Arsenal"))
        self.assertFalse(S.names_match("FC", "SC"))   # لواحق فقط = لا هوية


class TestXgFormPick(unittest.TestCase):
    """ترجيح فورمة xG: من التاريخ فقط، ولا ادعاء قبل عينة كافية."""

    def _hist(self, *pairs):
        return [{"date": "2026-08-01", "xf": f, "xa": a} for f, a in pairs]

    def test_no_pick_before_min_matches(self):
        h = self._hist((2, 1), (2, 1))          # مباراتان فقط
        a = self._hist((1, 1), (1, 1), (1, 1))
        self.assertIsNone(S.xgform_pick(h, a))

    def test_clear_edge_picks_stronger_side(self):
        h = self._hist((2.5, 0.5), (2.0, 0.8), (1.9, 0.6))
        a = self._hist((0.8, 1.5), (0.6, 2.0), (1.0, 1.8))
        self.assertEqual(S.xgform_pick(h, a), "home")
        self.assertEqual(S.xgform_pick(a, h), "away")

    def test_tiny_gap_is_draw(self):
        h = self._hist((1.2, 1.0), (1.1, 1.0), (1.0, 1.0))
        a = self._hist((1.1, 1.0), (1.0, 1.0), (1.1, 1.1))
        self.assertEqual(S.xgform_pick(h, a), "draw")

    def test_form_uses_window_only(self):
        """القديم خارج النافذة لا يؤثر — لكن التاريخ الكامل يبقى محفوظاً."""
        old_bad = [(0.1, 3.0)] * 10
        recent_good = [(2.0, 0.5)] * S.FORM_WINDOW
        h = self._hist(*(old_bad + recent_good))
        a = self._hist(*([(1.0, 1.0)] * 5))
        self.assertEqual(S.xgform_pick(h, a), "home")

    def test_outcome_parsing(self):
        self.assertEqual(S._outcome("2-1"), "home")
        self.assertEqual(S._outcome("0-0"), "draw")
        self.assertEqual(S._outcome("1-3"), "away")
        self.assertEqual(S._outcome("سيئ"), "")


class TestSilentFailZeroImpact(unittest.TestCase):
    """عقيدة الظل: بلا مفتاح = تخطٍ نظيف؛ وفشل المجمّع لا يمس أي محرك."""

    def test_no_key_exits_cleanly_without_writing(self):
        orig_key, orig_file = S.KEY, S.SHADOW_FILE
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.unlink()   # يجب ألا يُنشأ
        S.KEY = ""
        S.SHADOW_FILE = tmp
        try:
            S.main()   # لا استثناء
            self.assertFalse(tmp.exists())
        finally:
            S.KEY, S.SHADOW_FILE = orig_key, orig_file

    def test_workflow_step_cannot_fail_the_run(self):
        yml = (Path(__file__).resolve().parent.parent
               / ".github" / "workflows" / "predict_v2.yml").read_text(encoding="utf-8")
        self.assertIn("sportmonks_shadow.py ||", yml)
        self.assertIn("SPORTMONKS_KEY", yml)

    def test_collector_never_writes_engine_memories(self):
        """المجمّع يقرأ ذاكرتي المحركين ولا يكتب فيهما أبداً."""
        src = inspect.getsource(S)
        self.assertNotIn("V2_FILE.write", src)
        self.assertNotIn("V1_FILE.write", src)
        # الكتابة الوحيدة في المجمّع كله هي ملف الظل
        self.assertEqual(src.count(".write_text("), 1)
        self.assertIn("SHADOW_FILE.write_text(", src)


class TestMeasurementIntegrity(unittest.TestCase):
    """سجل الظل سجل قياس: لا قص، والترجيح لا يرى مستقبله."""

    def test_no_truncation_of_measurement_lists(self):
        """القص الوحيد المسموح: نافذة حساب الفورمة — التاريخ نفسه لا يُقص."""
        src = inspect.getsource(S)
        import re
        slices = re.findall(r"\[-(?:\d+|[A-Z_]+)\s*:\s*\]", src)
        self.assertEqual(set(slices), {"[-FORM_WINDOW:]"},
                         "قص جديد في المجمّع — صنّفه (عقيدة لا-أسقف-قياس)")

    def test_pick_computed_before_history_append(self):
        """الترجيح يُحسب قبل إلحاق مباراة اليوم — وإلا تسرّب المستقبل للقياس."""
        src = inspect.getsource(S.main)
        self.assertLess(src.index("xgform_pick("), src.index("h_hist.append"))


class TestDigestVisibility(unittest.TestCase):
    """قاعدة المالك (هـ): تجربة نشطة = سطر يومي في النشرة بلا سؤال."""

    def test_line_appears_with_data(self):
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps({"meta": {
            "started": "2026-08-13", "total": 24,
            "last_day_matched": 9, "last_day_unmatched": 2,
            "xgform": {"n": 10, "correct": 6}}}), encoding="utf-8")
        src = inspect.getsource(P.sportmonks_shadow_line)
        self.assertIn('sportmonks_shadow.json', src)
        orig = P.load_json
        P.load_json = lambda path, default: json.loads(tmp.read_text(encoding="utf-8"))
        try:
            line = P.sportmonks_shadow_line()
        finally:
            P.load_json = orig
            tmp.unlink(missing_ok=True)
        self.assertIn("ظل xG", line)
        self.assertIn("9", line)
        self.assertIn("6/10", line)

    def test_silent_without_data(self):
        orig = P.load_json
        P.load_json = lambda path, default: {}
        try:
            self.assertEqual(P.sportmonks_shadow_line(), "")
        finally:
            P.load_json = orig

    def test_wired_into_digest(self):
        src = inspect.getsource(P.main)
        self.assertIn("sportmonks_shadow_line()", src)


if __name__ == "__main__":
    unittest.main()
