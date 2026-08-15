# -*- coding: utf-8 -*-
"""🌙 اختبارات التمرير المسائي (طلب المالك 2026-08-15).

الغرض: تقليل انتظار ✓/✗ من ~24 ساعة إلى ~12 **بلا أي مقايضة على دقة القياس**.
لذلك أهم اختبارين هنا ليسا عن التقييم بل عمّا يجب ألا يفعله هذا المسار:

  (أ) لا يلمس history.json — تقدّمه هو قناة إنذار deadman.py. لو حرّكه المساء
      لظنّ الحارس أن التقييم الصباحي جرى وسكت: ثقب الصمت نفسه الذي كلّف
      19 ساعة في 14 أغسطس، يُعاد فتحه من باب آخر.
  (ب) لا نداء Claude — لا دروس ولا تقييم تقارير. تشغيلهما مرتين يومياً يضاعف
      الفاتورة التي أسقطت المحرك ثلاث مرات في يوليو (القاعدة 4).

وواحد عن الصحة: عرف الـ90 دقيقة محفوظ — وهو سبب رفض التقييم اللحظي أصلاً.

صفر شبكة: كل نداء خارجي مُستبدَل ببديل في الاختبار.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import predict_v2 as P

ROOT = Path(__file__).resolve().parent.parent


class TestEveningResolveBoundaries(unittest.TestCase):
    """ما لا يفعله المسار المسائي — الحدود التي تحميه من أن يصير ضرراً."""

    def _run(self, pending=None, resolved_rows=None):
        """يشغّل resolve_only على ملفات مؤقتة مع تعطيل كل أثر خارجي."""
        tmp = {name: Path(tempfile.mkstemp(suffix=".json")[1])
               for name in ("v2", "user", "radar")}
        tmp["v2"].write_text(json.dumps(
            {"pending": pending or {}, "resolved": resolved_rows or []}),
            encoding="utf-8")
        tmp["user"].write_text(json.dumps({"pending": {}, "resolved": []}),
                               encoding="utf-8")
        tmp["radar"].write_text(json.dumps({}), encoding="utf-8")
        orig = (P.PREDICTIONS_FILE, P.USER_PREDICTIONS_FILE, P.RADAR_LOG_FILE,
                P.resolve_pending, P.post_grading_alerts)
        P.PREDICTIONS_FILE = tmp["v2"]
        P.USER_PREDICTIONS_FILE = tmp["user"]
        P.RADAR_LOG_FILE = tmp["radar"]
        self.alerted = []
        P.post_grading_alerts = lambda nr, st: self.alerted.append(list(nr))
        # التسوية تُحاكى: الاختبار عن حدود المسار لا عن منطق التقييم نفسه
        self.resolve_calls = []

        def fake_resolve(store):
            self.resolve_calls.append(store)
            rows = list((store.get("pending") or {}).values())
            for r in rows:
                store["resolved"].append(dict(r, correct=r.get("_correct", True),
                                              score="1-0"))
            store["pending"] = {}
            return len(rows), store["resolved"][-len(rows):] if rows else []
        P.resolve_pending = fake_resolve
        try:
            import contextlib
            import io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                n = P.resolve_only()
            return n, buf.getvalue(), json.loads(tmp["v2"].read_text(encoding="utf-8"))
        finally:
            (P.PREDICTIONS_FILE, P.USER_PREDICTIONS_FILE, P.RADAR_LOG_FILE,
             P.resolve_pending, P.post_grading_alerts) = orig
            for f in tmp.values():
                f.unlink(missing_ok=True)

    @staticmethod
    def _code_only(fn):
        """مصدر الدالة بلا docstring وبلا تعليقات — الكود المنفَّذ وحده.

        ضروري لأن هذه الدالة **توثّق** في شرحها ما لا تستدعيه، فالفحص النصي
        الساذج كان سيقرأ التوثيق استدعاءً ويفشل على نص صحيح.
        """
        import inspect
        import re
        src = inspect.getsource(fn)
        src = re.sub(r'"""[\s\S]*?"""', "", src, count=1)   # docstring
        return "\n".join(l.split("#", 1)[0] for l in src.split("\n"))

    # ---------- (أ) الحارس الذي لا يجوز إسكاته ----------
    def test_never_touches_history_json(self):
        """⛔ الأهم: history.json ملك الصباح وحده — تقدّمه إنذار deadman."""
        self.assertNotIn("update_history", self._code_only(P.resolve_only))

    def test_history_file_is_untouched_on_disk(self):
        """فحص سلوكي لا نصي: الملف نفسه لا يتغيّر بعد تمرير مسائي كامل."""
        hist = ROOT / "history.json"
        before = hist.read_text(encoding="utf-8") if hist.exists() else None
        self._run(pending={"1": {"fid": "1", "home": "A", "away": "B",
                                 "confidence": 60}})
        after = hist.read_text(encoding="utf-8") if hist.exists() else None
        self.assertEqual(before, after, "التمرير المسائي حرّك الأرشيف الدائم")

    # ---------- (ب) لا فاتورة Claude مضاعفة ----------
    def test_no_claude_paths_in_the_evening(self):
        """لا دروس ولا تقييم تقارير ولا دمج — كلها نداء Claude لكل عنصر."""
        code = self._code_only(P.resolve_only)
        for forbidden in ("generate_lessons", "consolidate_lessons",
                          "resolve_scenarios", "build_digest",
                          "send_telegram_long"):
            with self.subTest(fn=forbidden):
                self.assertNotIn(forbidden + "(", code)

    def test_workflow_runs_resolve_only_flag(self):
        yml = (ROOT / ".github" / "workflows"
               / "predict_v2_evening.yml").read_text(encoding="utf-8")
        self.assertIn("predict_v2.py --resolve-only", yml)
        self.assertIn("group: football-monitor", yml)   # لا سباق على الدفع
        self.assertIn("git add -A", yml)                # درس 9 أغسطس
        self.assertNotIn("sportmonks_shadow.py", yml)   # المجمّع صباحي وحده

    def test_morning_workflow_still_owns_the_full_cycle(self):
        """التمرير المسائي إضافة لا بديل — الصباح يبقى كما هو حرفياً."""
        yml = (ROOT / ".github" / "workflows"
               / "predict_v2.yml").read_text(encoding="utf-8")
        self.assertIn("run: python predict_v2.py", yml)
        self.assertNotIn("--resolve-only", yml)

    # ---------- ما يفعله فعلاً ----------
    def test_grades_pending_and_saves(self):
        n, out, store = self._run(pending={
            "1": {"fid": "1", "home": "A", "away": "B", "confidence": 60}})
        self.assertEqual(n, 1)
        self.assertEqual(len(store["resolved"]), 1)
        self.assertEqual(store["pending"], {})
        self.assertIn("التمرير المسائي", out)

    def test_high_confidence_miss_alert_fires_in_the_evening(self):
        """خطأ بثقة 70%+ يصل المالك مساءً.

        وهذا ليس تحسيناً اختيارياً: post_grading_alerts يقرأ newly_resolved،
        وهذه الصفوف ستكون قد سُوّيت هنا — فلو تُرك الحارس للصباح لما أطلق
        أصلاً، ولضاع إنذار الشريحة الذهبية بالكامل (درس حادثة WK-League).
        """
        self._run(pending={"1": {"fid": "1", "home": "A", "away": "B",
                                 "confidence": 75, "_correct": False}})
        self.assertEqual(len(self.alerted), 1)
        self.assertEqual(self.alerted[0][0]["confidence"], 75)

    def test_nothing_pending_is_a_clean_no_op(self):
        n, out, store = self._run()
        self.assertEqual(n, 0)
        self.assertEqual(store["resolved"], [])

    def test_ninety_minute_convention_is_still_the_grading_basis(self):
        """عرف الـ90 دقيقة سليم — وهو سبب رفض التقييم من النتيجة الحية.

        لو انتقل التقييم يوماً إلى النتيجة النهائية بعد التمديد، لصارت كل
        مباراة كأس حُسمت في الوقت الإضافي مُقيَّمة خطأً — وهي بالضبط الحالة
        التي لوّثت خانة الـ70%+ من قبل (كيري × شيلبورن).
        """
        import inspect
        src = inspect.getsource(P.resolve_pending)
        self.assertIn("fulltime", src)


if __name__ == "__main__":
    unittest.main()
