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


class TestKillSwitch(unittest.TestCase):
    """مفتاح التراجع XG_SHADOW: إطفاؤه = تعطيل فوري بلا حذف أي كود."""

    def test_switch_off_skips_cleanly_without_writing(self):
        orig_flag, orig_key, orig_file = S.XG_SHADOW, S.KEY, S.SHADOW_FILE
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.unlink()   # يجب ألا يُنشأ
        S.XG_SHADOW, S.KEY, S.SHADOW_FILE = False, "مفتاح-وهمي", tmp
        try:
            S.main()   # لا استثناء ولا كتابة ولا نداء شبكة (يخرج قبل كل شيء)
            self.assertFalse(tmp.exists())
        finally:
            S.XG_SHADOW, S.KEY, S.SHADOW_FILE = orig_flag, orig_key, orig_file


def _sm_fixture(home, away, xh, xa):
    """جسم مباراة بصيغة Sportmonks المؤكدة من المسبار — للمحاكاة فقط."""
    return {"name": f"{home} vs {away}", "xgfixture": [
        {"type_id": S.XG_TYPE_ID, "location": "home", "data": {"value": xh}},
        {"type_id": S.XG_TYPE_ID, "location": "away", "data": {"value": xa}},
    ]}


class TestValidateMode(unittest.TestCase):
    """وضع التحقق مقابل Opta (--validate): جدول فروق صادق، وفشل HTTP لا يقتل."""

    def _run_validate(self, api_stub, persist=False):
        import contextlib
        import io
        orig_api, orig_key = S._api, S.KEY
        S._api, S.KEY = api_stub, "مفتاح-وهمي"
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                summary = S.validate(persist=persist)
        finally:
            S._api, S.KEY = orig_api, orig_key
        return summary, buf.getvalue()

    def test_prints_diff_table_and_summary(self):
        """عينة مُطابقة → صف فرق لكل مباراة مغطاة وخلاصة بمتوسط الفرق."""
        sample = S.OPTA_SAMPLE[0]
        def api_stub(path, params):
            if sample["date"] in path:
                return {"data": [_sm_fixture(sample["home"], sample["away"],
                                             sample["opta_home"] + 0.1,
                                             sample["opta_away"])],
                        "pagination": {"has_more": False}}
            return {"data": [], "pagination": {"has_more": False}}
        summary, out = self._run_validate(api_stub)
        self.assertEqual(summary["n"], 1)
        self.assertEqual(summary["no_coverage"], len(S.OPTA_SAMPLE) - 1)
        self.assertAlmostEqual(summary["mean_abs_diff"], 0.05)
        self.assertIn("توافق جيد", summary["verdict"])
        self.assertIn("لا تغطية", out)      # غير المُغطى يُسجَّل، لا يُخمَّن
        self.assertIn("Opta", out)

    def test_http_failure_never_raises(self):
        """كل النداءات تفشل → خلاصة «لا تغطية» صادقة، صفر استثناءات."""
        summary, out = self._run_validate(lambda path, params: None)
        self.assertEqual(summary["n"], 0)
        self.assertIsNone(summary["mean_abs_diff"])
        self.assertIn("لا تغطية", summary["verdict"])

    def test_no_key_skips_silently(self):
        orig_key = S.KEY
        S.KEY = ""
        try:
            self.assertEqual(S.validate(persist=False), {})
        finally:
            S.KEY = orig_key

    def test_persist_writes_summary_into_shadow_meta(self):
        """نتيجة التحقق تُحفظ في السجل ليقرأها تقرير 24 أغسطس المرحلي."""
        orig_file = S.SHADOW_FILE
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps({"fixtures": [], "meta": {"total": 3}}),
                       encoding="utf-8")
        S.SHADOW_FILE = tmp
        try:
            self._run_validate(lambda path, params: None, persist=True)
            saved = json.loads(tmp.read_text(encoding="utf-8"))
            self.assertIn("opta_validation", saved["meta"])
            self.assertEqual(saved["meta"]["total"], 3)   # لا يمس بقية السجل
        finally:
            S.SHADOW_FILE = orig_file
            tmp.unlink(missing_ok=True)

    def test_first_run_triggers_validation_once(self):
        """أول تشغيلة تُشغّل التحقق تلقائياً؛ وجود الخلاصة يمنع تكراره يومياً."""
        src = inspect.getsource(S.main)
        self.assertIn('"opta_validation" not in meta', src)
        self.assertIn("validate()", src)

    def test_opta_sample_is_sane(self):
        """عينة المرجع: مباريات إنجليزية بموسم سابق وأرقام موجبة معقولة."""
        self.assertGreaterEqual(len(S.OPTA_SAMPLE), 6)
        for s in S.OPTA_SAMPLE:
            self.assertTrue(s["date"].startswith("2022-"))
            self.assertGreaterEqual(s["opta_home"], 0)
            self.assertGreaterEqual(s["opta_away"], 0)
            self.assertLess(s["opta_home"] + s["opta_away"], 8)


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
