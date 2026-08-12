# -*- coding: utf-8 -*-
"""اختبارات مولّد بيانات اللوحة — تحرس إصلاحات العرض من العودة."""

import sys
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard_update as D


def live_entry(**kw):
    base = {"status": "1H", "home": "H", "away": "A", "score": "0-0", "minute": 10}
    base.update(kw)
    return base


class TestBuildLive(unittest.TestCase):
    """بطاقات المباريات الحية تحمل توقع كل محرك (طلب المالك 2026-07-18)."""

    def test_attaches_both_engine_predictions(self):
        state = {"111": live_entry()}
        v1 = {"pending": {"111": {"pick": "home", "confidence": 55}}}
        v2 = {"pending": {"111": {"pick": "draw", "confidence": 40}}}
        live = D.build_live(state, v1, v2)
        self.assertEqual(live[0]["pred_v1"], {"pick": "home", "confidence": 55})
        self.assertEqual(live[0]["pred_v2"], {"pick": "draw", "confidence": 40})

    def test_no_prediction_no_field(self):
        live = D.build_live({"3": live_entry()}, {}, {})
        self.assertNotIn("pred_v1", live[0])
        self.assertNotIn("pred_v2", live[0])

    def test_finished_matches_excluded(self):
        live = D.build_live({"4": live_entry(status="FT")}, {}, {})
        self.assertEqual(live, [])

    def test_english_names_carried_everywhere(self):
        """طلب المالك 2026-08-02: الاسم الإنجليزي يرافق العربي في كل حمولة —
        الحية، النتائج، مختبر الظل — ليختار العرض بلغة الواجهة."""
        live = D.build_live({"9": live_entry(ar={"home": "الهلال", "away": "النصر"},
                                             home="Al Hilal", away="Al Nassr")}, {}, {})
        self.assertEqual(live[0]["home"], "الهلال")
        self.assertEqual(live[0]["home_en"], "Al Hilal")
        res = D.build_recent_results({"resolved": [
            {"home": "Kerry", "away": "Shelbourne", "ar_home": "كيري",
             "date": "2026-07-17", "pick": "away", "confidence": 72,
             "score": "2-2", "actual": "draw", "correct": False}]})
        self.assertEqual(res[0]["home"], "كيري")
        self.assertEqual(res[0]["home_en"], "Kerry")

    def test_seen_timestamp_carried(self):
        """بلاغ المالك 2026-08-02 (65 على اللوحة و84 في الواقع): لحظة رصد
        الدقيقة يجب أن تصل اللوحة حتى تُقدّم العدّاد بما مضى منذها."""
        state = {"7": live_entry(seen="2026-08-02T20:00:00+00:00")}
        live = D.build_live(state, {}, {})
        self.assertEqual(live[0]["seen"], "2026-08-02T20:00:00+00:00")


class TestRecentResults(unittest.TestCase):
    """إصلاح 2026-07-17: الأحدث أولاً والدوريات الكبرى في المقدمة، نافذة 50."""

    def _mk(self, n, top, date):
        return {"home": f"h{n}", "away": f"a{n}", "date": date,
                "top": top, "pick": "home", "confidence": 50,
                "score": "1-0", "actual": "home", "correct": True}

    def test_top_first_within_same_date(self):
        store = {"resolved": [self._mk(1, False, "2026-07-17"),
                              self._mk(2, True, "2026-07-17")]}
        out = D.build_recent_results(store)
        self.assertEqual(out[0]["home"], "h2", "الدوري الكبير يتقدم في نفس اليوم")

    def test_window_is_50(self):
        store = {"resolved": [self._mk(i, False, "2026-07-10") for i in range(80)]}
        self.assertEqual(len(D.build_recent_results(store)), D.RECENT_RESULTS_SHOWN)
        self.assertEqual(D.RECENT_RESULTS_SHOWN, 50)

    def test_reason_carried_when_present(self):
        """سطر "لماذا" (طلب المالك 2026-08-01): سبب التوقع يصل صف النتيجة."""
        e = self._mk(1, True, "2026-08-01")
        e["reason"] = "أفضلية أرض وضغط هجومي"
        out = D.build_recent_results({"resolved": [e]})
        self.assertEqual(out[0]["reason"], "أفضلية أرض وضغط هجومي")

    def test_no_reason_no_field(self):
        """إدخالات المحرك 1 (بلا reason) تمر بلا حقل — لا فراغات على اللوحة."""
        out = D.build_recent_results({"resolved": [self._mk(1, True, "2026-08-01")]})
        self.assertNotIn("reason", out[0])


class TestUpcoming(unittest.TestCase):
    def test_probabilities_carried_when_present(self):
        kick = (D.now_utc() + timedelta(hours=3)).isoformat()
        store = {"pending": {"9": {
            "kickoff": kick, "home": "X", "away": "Y", "league": "L", "top": True,
            "pick": "home", "confidence": 60,
            "prob_home": 60, "prob_draw": 25, "prob_away": 15}}}
        out = D.build_upcoming(store)
        self.assertEqual(out[0]["prob_home"], 60)

    def test_old_matches_dropped(self):
        kick = (D.now_utc() - timedelta(hours=5)).isoformat()
        store = {"pending": {"9": {"kickoff": kick, "home": "X", "away": "Y",
                                   "league": "L", "top": False,
                                   "pick": "home", "confidence": 60}}}
        self.assertEqual(D.build_upcoming(store), [])


class TestUpdateLine(unittest.TestCase):
    """خط التحديث (طلب المالك 2026-08-02): لحظة آخر تشغيل توقعات لكل محرك
    تصل اللوحة — منها يُرسم الشريط الزمني (آخر تحديث ← الآن ← القادم)."""

    def test_pred_updated_in_both_payload_builders(self):
        import inspect
        self.assertIn('"pred_updated"', inspect.getsource(D.main))
        self.assertIn('"pred_updated"', inspect.getsource(D.build_data_v2))


class TestFreshnessSentinel(unittest.TestCase):
    """حارس الطزاجة (بلاغ المالك 2026-08-02 — اللوحة 9 والواقع 18): بناء
    اللوحة يصرخ في السجل حين تكون لقطات المباريات الحية قديمة أو بلا طابع."""

    def test_missing_seen_flagged(self):
        warns = D.freshness_warnings({"1": live_entry()})
        self.assertEqual(len(warns), 1)
        self.assertIn("بلا طابع رصد", warns[0])

    def test_fresh_seen_silent(self):
        fresh = D.now_utc().isoformat()
        self.assertEqual(D.freshness_warnings({"1": live_entry(seen=fresh)}), [])

    def test_old_seen_flagged(self):
        old = (D.now_utc() - timedelta(minutes=45)).isoformat()
        warns = D.freshness_warnings({"1": live_entry(seen=old)})
        self.assertEqual(len(warns), 1)
        self.assertIn("دقيقة", warns[0])

    def test_wired_into_main(self):
        import inspect
        self.assertIn("freshness_warnings(", inspect.getsource(D.main))


class TestShadowLab(unittest.TestCase):
    """🔬 مختبر الظل (طلب المالك 2026-08-01): بطاقات تقييم التقارير تصل اللوحة
    من scenarios_v2.json — الأحدث أولاً، مع عدّادَي الإجمالي والانتظار."""

    def _with_tmp_scenarios(self, payload):
        import json
        import tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        orig = D.SCENARIOS_V2_FILE
        D.SCENARIOS_V2_FILE = tmp
        try:
            return D.build_shadow_lab()
        finally:
            D.SCENARIOS_V2_FILE = orig
            tmp.unlink(missing_ok=True)

    def test_rows_newest_first_with_counts(self):
        lab = self._with_tmp_scenarios({
            "pending": {"p1": {}},
            "resolved": [
                {"graded_on": "2026-07-30", "ar_home": "أ", "ar_away": "ب",
                 "league": "دوري", "shadow": True, "correct": 3, "total": 8},
                {"graded_on": "2026-07-31", "ar_home": "ج", "ar_away": "د",
                 "league": "دوري", "shadow": False, "correct": 5, "total": 7},
            ],
        })
        self.assertEqual(lab["graded_total"], 2)
        self.assertEqual(lab["pending"], 1)
        self.assertEqual(lab["reports"][0]["home"], "ج")   # الأحدث أولاً
        self.assertFalse(lab["reports"][0]["shadow"])
        self.assertTrue(lab["reports"][1]["shadow"])
        self.assertEqual(lab["reports"][1]["correct"], 3)

    def test_match_date_not_grading_date(self):
        """بلاغ المالك 2026-08-02: تقرير مباراة كأس قديمة ظهر بتاريخ صباح
        تقييمه — البطاقة يجب أن تعرض تاريخ المباراة نفسها."""
        lab = self._with_tmp_scenarios({
            "pending": {},
            "resolved": [{"date": "2026-07-14", "graded_on": "2026-08-01",
                          "home": "Spain", "away": "Argentina",
                          "shadow": False, "correct": 1, "total": 9}],
        })
        self.assertEqual(lab["reports"][0]["date"], "2026-07-14")

    def test_empty_store_safe(self):
        lab = self._with_tmp_scenarios({})
        self.assertEqual(lab["reports"], [])
        self.assertEqual(lab["graded_total"], 0)
        self.assertEqual(lab["pending"], 0)

    def test_row_cap(self):
        many = [{"graded_on": f"2026-07-{i:02d}", "home": "H", "away": "A",
                 "shadow": True, "correct": 1, "total": 2} for i in range(1, 21)]
        lab = self._with_tmp_scenarios({"pending": {}, "resolved": many})
        self.assertEqual(len(lab["reports"]), D.SHADOW_LAB_ROWS)
        self.assertEqual(lab["graded_total"], 20)

    def test_v2_waiting_and_per_type_accuracy(self):
        """v2 (طلب المالك 2026-08-01): المنتظرة تظهر ببياناتها، والدقة
        تُحسب لكل نوع على السجل الكامل، والتقرير الأصلي يصل اللوحة."""
        lab = self._with_tmp_scenarios({
            "pending": {"p1": {"date": "2026-08-01", "kickoff": "2026-08-01T18:00:00+00:00",
                               "ar_home": "الهلال", "ar_away": "النصر",
                               "league": "دوري روشن", "shadow": False, "report": "نص"}},
            "resolved": [
                {"graded_on": "2026-07-30", "home": "A", "away": "B",
                 "shadow": True, "correct": 3, "total": 8, "report": "تقرير ظل"},
                {"graded_on": "2026-07-31", "home": "C", "away": "D",
                 "shadow": False, "correct": 5, "total": 7, "report": "تقرير قائمة"},
            ],
        })
        self.assertEqual(len(lab["waiting"]), 1)
        self.assertEqual(lab["waiting"][0]["home"], "الهلال")
        self.assertIn("kickoff", lab["waiting"][0])
        self.assertEqual(lab["shadow_acc"], {"correct": 3, "total": 8, "reports": 1})
        self.assertEqual(lab["watch_acc"], {"correct": 5, "total": 7, "reports": 1})
        self.assertEqual(lab["reports"][0]["report"], "تقرير قائمة")


class TestActiveExperiments(unittest.TestCase):
    """🧪 صف التجارب النشطة (قاعدة الحوكمة هـ): تجربة ظل نشطة تظهر في مختبر
    الظل يومياً بلا سؤال — وتختفي كلياً حين لا توجد تجربة."""

    def _with_tmp_shadow(self, payload):
        import json
        import tempfile
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        if payload is None:
            tmp.unlink()   # لا ملف = لا تجربة بعد
        else:
            tmp.write_text(json.dumps(payload), encoding="utf-8")
        orig = D.SPORTMONKS_SHADOW_FILE
        D.SPORTMONKS_SHADOW_FILE = tmp
        try:
            return D.build_active_experiments()
        finally:
            D.SPORTMONKS_SHADOW_FILE = orig
            tmp.unlink(missing_ok=True)

    def test_row_appears_when_collector_active(self):
        exps = self._with_tmp_shadow({"meta": {
            "started": "2026-08-13", "total": 24,
            "last_day_matched": 9, "last_day_unmatched": 2,
            "xgform": {"n": 10, "correct": 6}}})
        self.assertEqual(len(exps), 1)
        x = exps[0]
        self.assertEqual(x["key"], "xg_shadow")
        self.assertIn("xG", x["name"])
        self.assertEqual(x["yday_matched"], 9)
        self.assertEqual(x["total"], 24)
        self.assertEqual(x["form_n"], 10)
        self.assertEqual(x["form_correct"], 6)
        self.assertEqual(x["days_total"], D.XG_SHADOW_TOTAL_DAYS)

    def test_no_row_before_first_collection(self):
        self.assertEqual(self._with_tmp_shadow(None), [])
        self.assertEqual(self._with_tmp_shadow({}), [])

    def test_wired_into_shadow_lab_payload(self):
        """الصف يصل اللوحة عبر shadow_lab في data_v2.json."""
        import inspect
        src = inspect.getsource(D.build_shadow_lab)
        self.assertIn("build_active_experiments()", src)

    def test_page_renders_experiments_strip(self):
        """الواجهة ترسم الشريط وتُبقي القسم ظاهراً ولو بلا تقارير مُقيَّمة."""
        html = (Path(__file__).resolve().parent.parent / "index.html").read_text(
            encoding="utf-8")
        self.assertIn("labExperimentsStrip", html)
        self.assertIn("lab.experiments", html)
        # مفاتيح الترجمة موجودة (توازن العربية/الإنجليزية يحرسه اختبار الواجهة)
        self.assertIn("labExpDay", html)


if __name__ == "__main__":
    unittest.main()
