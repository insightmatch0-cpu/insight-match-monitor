# -*- coding: utf-8 -*-
"""🔓 اختبارات فكّ اختناق مجمّع ظل xG (تشخيص 2026-08-24).

المشكلة المقاسة: بعد 11 يوماً و62 مباراة، أنتج القياس التنبؤي **صفر** نقاط —
لأن تاريخ الفرق كان يُبنى من المباريات المطابَقة وحدها (5% مما نجلبه)، فلم
يبلغ أي فريق الحد الأدنى. الإصلاح: التاريخ من كل مباراة تعيدها الباقة (صفر
نداءات إضافية)، وحدّ أدنى 2، وقياس لاحق فوري لا ينتظر تاريخاً.

المبدأ الأخطر المحروس هنا: **لا تسريب مستقبل** — ترجيحات اليوم تُحسب قبل
إدخال مبارياته للتاريخ. اختبار واحد يسقط لو انعكس الترتيب يوماً.
صفر شبكة: كل اختبار يعمل ببدائل محلية.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sportmonks_shadow as S


class TestXgLeader(unittest.TestCase):
    """القياس اللاحق: من تفوّق في xG داخل المباراة — ولا ادعاء عند التكافؤ."""

    def test_clear_lead_detected(self):
        self.assertEqual(S.xg_leader(2.5, 0.8), "home")
        self.assertEqual(S.xg_leader(0.4, 1.9), "away")

    def test_close_match_is_no_claim(self):
        self.assertIsNone(S.xg_leader(1.2, 1.0))
        self.assertIsNone(S.xg_leader(1.0, 1.0))

    def test_missing_values_never_crash(self):
        self.assertIsNone(S.xg_leader(None, 1.0))
        self.assertIsNone(S.xg_leader(1.0, None))

    def test_backfill_is_idempotent_and_offline(self):
        fx = [{"xg_home": 2.4, "xg_away": 0.6, "result": "home"},
              {"xg_home": 0.9, "xg_away": 1.1, "result": "draw"}]
        self.assertEqual(S.backfill_leader(fx), 2)
        self.assertEqual(S.backfill_leader(fx), 0)      # لا يكرر
        self.assertEqual(fx[0]["xg_leader"], "home")
        self.assertTrue(fx[0]["xg_leader_correct"])
        self.assertIsNone(fx[1]["xg_leader"])           # تكافؤ
        self.assertIsNone(fx[1]["xg_leader_correct"])

    def test_signal_stats_isolate_disagreements(self):
        """السؤال الحاسم للمرحلة B: عند اختلاف xG مع المحرك، أيّهما أصاب؟"""
        fx = [
            # اتفاق: لا يدخل عدّاد الاختلاف
            {"xg_leader": "home", "xg_leader_correct": True,
             "v2_pick": "home", "v2_correct": True},
            # اختلاف: xG أصاب والمحرك أخطأ
            {"xg_leader": "away", "xg_leader_correct": True,
             "v2_pick": "home", "v2_correct": False},
            # اختلاف: المحرك أصاب وxG أخطأ
            {"xg_leader": "home", "xg_leader_correct": False,
             "v2_pick": "away", "v2_correct": True},
            # تكافؤ xG: خارج اللوحة تماماً (لا ادعاء)
            {"xg_leader": None, "xg_leader_correct": None,
             "v2_pick": "home", "v2_correct": True},
        ]
        st = S.xg_signal_stats(fx)
        self.assertEqual(st["n"], 3)
        self.assertEqual(st["leader_right"], 2)
        self.assertEqual(st["disagree"], 2)
        self.assertEqual(st["xg_right"], 1)
        self.assertEqual(st["v2_right"], 1)


class TestHistoryFromAllFixtures(unittest.TestCase):
    """التاريخ يُبنى من كل مباريات الباقة لا المطابَقة فقط — وبلا تسريب مستقبل."""

    def _run_main(self, days: dict, v2_resolved: list):
        tmp_shadow = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp_v2 = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp_v1 = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp_shadow.write_text("{}", encoding="utf-8")
        tmp_v2.write_text(json.dumps({"resolved": v2_resolved}), encoding="utf-8")
        tmp_v1.write_text("{}", encoding="utf-8")
        orig = (S.SHADOW_FILE, S.V2_FILE, S.V1_FILE, S.KEY,
                S.fetch_day_xg, S.validate)
        S.SHADOW_FILE, S.V2_FILE, S.V1_FILE = tmp_shadow, tmp_v2, tmp_v1
        S.KEY = "مفتاح-وهمي"
        S.fetch_day_xg = lambda day: days.get(day, [])
        S.validate = lambda *a, **k: None            # لا تحقق شبكي في الاختبار
        try:
            S.main()
            return json.loads(tmp_shadow.read_text(encoding="utf-8"))
        finally:
            (S.SHADOW_FILE, S.V2_FILE, S.V1_FILE, S.KEY,
             S.fetch_day_xg, S.validate) = orig
            for t in (tmp_shadow, tmp_v2, tmp_v1):
                t.unlink(missing_ok=True)

    def _day(self, *triples):
        return [{"home": h, "away": a, "xg_home": xh, "xg_away": xa}
                for h, a, xh, xa in triples]

    def test_unmatched_fixtures_still_feed_team_history(self):
        """جوهر الإصلاح: مباراة لم يقيّمها المحرك تبني تاريخ فريقيها رغم ذلك."""
        from datetime import datetime, timedelta, timezone
        y = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        days = {y: self._day(("Alpha", "Beta", 2.0, 0.5),
                             ("Gamma", "Delta", 1.0, 1.4))}
        # المحرك قيّم مباراة واحدة فقط من الاثنتين
        out = self._run_main(days, [{"fid": "1", "date": y, "home": "Alpha",
                                     "away": "Beta", "score": "2-0",
                                     "pick": "home", "correct": True}])
        teams = out["teams"]
        self.assertIn(S._team_key("Gamma"), teams)      # غير مطابَقة — ومع ذلك
        self.assertIn(S._team_key("Delta"), teams)      # دخلت التاريخ
        self.assertEqual(len(out["fixtures"]), 1)       # القياس للمطابَقة وحدها

    def test_history_entries_are_not_duplicated_across_runs(self):
        from datetime import datetime, timedelta, timezone
        y = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        days = {y: self._day(("Alpha", "Beta", 2.0, 0.5))}
        out = self._run_main(days, [])
        self.assertEqual(len(out["teams"][S._team_key("Alpha")]), 1)
        self.assertTrue(out.get("hist_seen"))

    def test_no_future_leak_same_day(self):
        """ترجيح مباراة اليوم لا يرى نتيجة اليوم نفسه — لو انعكس الترتيب سقط."""
        import inspect
        src = inspect.getsource(S.main)
        self.assertLess(src.index("xgform_pick("),
                        src.index("كل مباريات اليوم"),
                        "الترجيح يجب أن يُحسب قبل إدخال مباريات اليوم للتاريخ")

    def test_min_matches_lowered_to_two(self):
        self.assertEqual(S.FORM_MIN_MATCHES, 2)
        h = [{"date": "2026-08-01", "xf": 2.2, "xa": 0.4},
             {"date": "2026-08-08", "xf": 2.0, "xa": 0.6}]
        a = [{"date": "2026-08-01", "xf": 0.5, "xa": 1.8},
             {"date": "2026-08-08", "xf": 0.7, "xa": 2.1}]
        self.assertEqual(S.xgform_pick(h, a), "home")   # كان None قبل الإصلاح
        self.assertIsNone(S.xgform_pick(h[:1], a))      # مباراة واحدة = لا ادعاء


if __name__ == "__main__":
    unittest.main()
