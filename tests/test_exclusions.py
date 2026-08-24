# -*- coding: utf-8 -*-
"""اختبارات فلتر البيانات النظيفة (توجيه المالك 2026-07-18).

لا نبني التعلم على بيانات مهملة: دوريات السيدات والفئات السنية والرديف
مستبعدة من التغطية. القاعدة الصلبة رقم 1: القوائم منسوخة في السكربتات
الأربعة ويجب أن تبقى متطابقة حرفياً — هذا الاختبار يمنع أي انحراف.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import monitor
import predict
import predict_v2
import scan


def league(name, country="England"):
    return {"name": name, "country": country, "id": 0}


class TestListsInSync(unittest.TestCase):
    """أي تعديل على الاستبعادات يجب أن يصل الملفات الأربعة معاً."""

    def test_keyword_lists_identical(self):
        self.assertEqual(monitor.EXCLUDED_LEAGUE_KEYWORDS, scan.EXCLUDED_LEAGUE_KEYWORDS)
        self.assertEqual(monitor.EXCLUDED_LEAGUE_KEYWORDS, predict.EXCLUDED_LEAGUE_KEYWORDS)
        self.assertEqual(monitor.EXCLUDED_LEAGUE_KEYWORDS, predict_v2.EXCLUDED_LEAGUE_KEYWORDS)

    def test_country_lists_identical(self):
        self.assertEqual(monitor.EXCLUDED_COUNTRIES, scan.EXCLUDED_COUNTRIES)
        self.assertEqual(monitor.EXCLUDED_COUNTRIES, predict.EXCLUDED_COUNTRIES)
        self.assertEqual(monitor.EXCLUDED_COUNTRIES, predict_v2.EXCLUDED_COUNTRIES)

    def test_top_league_ids_identical(self):
        self.assertEqual(monitor.TOP_LEAGUE_IDS, scan.TOP_LEAGUE_IDS)
        self.assertEqual(monitor.TOP_LEAGUE_IDS, predict.TOP_LEAGUE_IDS)
        self.assertEqual(monitor.TOP_LEAGUE_IDS, predict_v2.TOP_LEAGUE_IDS)

    def test_daily_prediction_cap_in_sync(self):
        """المحركان يتوقعان نفس المباريات — سقف التوقع اليومي يجب أن يتطابق
        (وإلا اختلّت مقارنة الدقة). رُفع لتغطية مباريات المساء (المالك 2026-07-18)."""
        self.assertEqual(predict.MAX_PREDICTIONS_24H, predict_v2.MAX_PREDICTIONS_24H)
        self.assertGreaterEqual(predict_v2.MAX_PREDICTIONS_24H, 150)


class TestQualityFilter(unittest.TestCase):
    """السيدات والفئات السنية والرديف تُستبعد — بأسماء دوريات حقيقية."""

    EXCLUDED = [
        "Premier League Women", "FA WSL", "Serie A Women", "Frauen Bundesliga",
        "2. Frauen Bundesliga", "Primera División Femenina", "Feminine Division 1",
        "U19 Bundesliga", "U18 Premier League - North", "Campionato Primavera - 1",
        "UEFA Youth League", "Professional U21 Development League",
        "Reserve League", "Ural Youth Championship",
        # التسريب الحقيقي المكتشف 2026-08-01: دوريات سيدات بلا كلمة دالة في
        # الاسم — إحداها (WK-League) أفسدت خانة الثقة 70%+ قبل التنظيف
        "WK-League", "Kvindeliga", "Damallsvenskan", "Elitettan",
        "Toppserien", "Northern Super League", "Serie A Femminile",
        "Eredivisie Vrouwen", "Naisten Liiga", "NWSL Women",
    ]
    KEPT = [
        "Premier League", "Championship", "Serie A", "Serie B", "Bundesliga",
        "2. Bundesliga", "La Liga", "Ligue 1", "Ligue 2", "Pro League",
        "Iraqi League", "Eliteserien", "Super Liga", "FAI Cup",
        "UEFA Champions League", "First League", "1. Division",
        "Primera División RFEF - Group 1", "Regionalliga - Bayern",
    ]

    def test_rubbish_excluded_everywhere(self):
        for mod in (monitor, scan, predict, predict_v2):
            for name in self.EXCLUDED:
                self.assertTrue(mod.is_excluded(league(name)),
                                f"{mod.__name__} لم يستبعد: {name}")

    def test_real_leagues_still_covered(self):
        for mod in (monitor, scan, predict, predict_v2):
            for name in self.KEPT:
                self.assertFalse(mod.is_excluded(league(name)),
                                 f"{mod.__name__} استبعد دورياً حقيقياً: {name}")


class TestWomensPatternBackstop(unittest.TestCase):
    """طبقة الأمان النمطية (درس تسريب WK-League — 2026-08-01): القوائم تفشل
    بصمت، فالفريقان بلاحقة W يُستبعدان مهما كان اسم الدوري."""

    def test_helper_exists_in_all_four(self):
        for mod in (monitor, scan, predict, predict_v2):
            self.assertTrue(hasattr(mod, "is_womens_match"), mod.__name__)

    def test_the_exact_leak_caught_by_pattern(self):
        for mod in (monitor, scan, predict, predict_v2):
            self.assertTrue(mod.is_womens_match("Incheon Red Angels W", "Hwacheon KSPO W"),
                            f"{mod.__name__} لم يلتقط مباراة WK-League")

    def test_single_w_team_not_excluded(self):
        # فريق واحد فقط بلاحقة W (اسم رجالي ينتهي بحرف W مصادفة) لا يُستبعد
        for mod in (monitor, scan, predict, predict_v2):
            self.assertFalse(mod.is_womens_match("Wolves W", "Arsenal"))
            self.assertFalse(mod.is_womens_match("Crewe", "Slask Wroclaw"))

    def test_parenthesized_w(self):
        for mod in (monitor, scan, predict, predict_v2):
            self.assertTrue(mod.is_womens_match("Chelsea (W)", "Lyon (W)"))


class TestYouthPatternBackstop(unittest.TestCase):
    """طبقة الأمان النمطية الثانية (تسريب Costa Rica U21 — 2026-08-02):
    فرق الفئات السنية تُلتقط من أسماء الفريقين حتى لو خلا اسم الدوري من أي
    كلمة دالة — نفس عقيدة WK-League: كل قائمة حظر تحتاج نمطاً يسندها."""

    def test_helper_exists_in_all_four(self):
        for mod in (monitor, scan, predict, predict_v2):
            self.assertTrue(hasattr(mod, "is_youth_match"), mod.__name__)

    def test_the_exact_leak_caught_by_pattern(self):
        """الحالة الحقيقية: Costa Rica U21 ظهرت في قائمة اللوحة الحية."""
        self.assertTrue(monitor.is_youth_match("Costa Rica U21", "Guatemala U21"))
        self.assertTrue(monitor.is_youth_match("Spain U19", "France U-19"))
        self.assertTrue(monitor.is_youth_match("Ajax U23", "PSV U23"))

    def test_senior_teams_not_excluded(self):
        self.assertFalse(monitor.is_youth_match("Al Hilal", "Al Nassr"))
        # فريق شباب ضد فريق أول (نادر) — لا استبعاد إلا حين يكون الطرفان فئات
        self.assertFalse(monitor.is_youth_match("Costa Rica U21", "Costa Rica"))
        # أرقام داخل أسماء طبيعية لا تُلتقط (Schalke 04, 1860 Munich)
        self.assertFalse(monitor.is_youth_match("Schalke 04", "1860 Munich"))

    def test_wired_at_the_same_gate_as_womens(self):
        """النمطان يُفحصان معاً عند نفس البوابة في الملفات الأربعة."""
        import inspect
        for mod in (monitor, scan, predict, predict_v2):
            src = inspect.getsource(mod)
            self.assertIn("or is_youth_match(", src, mod.__name__)

    def test_leak_finder_covers_youth(self):
        import inspect
        self.assertIn("is_youth_match", inspect.getsource(predict_v2.find_data_leaks))


class TestPostGradingSentinel(unittest.TestCase):
    """حارس ما بعد التقييم (درس 2026-08-01): التسريب وأخطاء 70%+ تصرخ
    تلقائياً — اكتشافها لا يُترك لحظ المالك."""

    def test_leak_finder_flags_womens_in_pending_and_resolved(self):
        from datetime import timedelta
        today = predict_v2.now_utc().strftime("%Y-%m-%d")
        store = {
            "pending": {"1": {"home": "Seoul W", "away": "Changnyeong W",
                              "league": "WK-League (South-Korea)"}},
            "resolved": [{"date": today, "home": "Malmö FF W", "away": "Piteå W",
                          "league": "Damallsvenskan (Sweden)"}],
        }
        leaks = predict_v2.find_data_leaks(store)
        self.assertEqual(len(leaks), 2)

    def test_leak_finder_clean_store_silent(self):
        today = predict_v2.now_utc().strftime("%Y-%m-%d")
        store = {
            "pending": {"1": {"home": "Al Hilal", "away": "Al Nassr",
                              "league": "Saudi Pro League (Saudi-Arabia)"}},
            "resolved": [{"date": today, "home": "Liverpool", "away": "Wrexham",
                          "league": "Premier League (England)"}],
        }
        self.assertEqual(predict_v2.find_data_leaks(store), [])

    def test_sentinel_wired_into_main(self):
        import inspect
        src = inspect.getsource(predict_v2.main)
        self.assertIn("post_grading_alerts(", src,
                      "الحارس يجب أن يركض في كل تشغيل صباحي")


if __name__ == "__main__":
    unittest.main()


class TestLeakGuardSeesCountry(unittest.TestCase):
    """🔧 REC-016 (قرار المالك 2026-08-24): الحارس كان يمرر country فارغة
    فتعبره دوريات الدول المحظورة إن كان اسمها بريئاً من الكلمات المفتاحية."""

    def test_banned_country_with_innocent_name_is_caught(self):
        import predict_v2 as P
        store = {"pending": {"1": {"home": "A", "away": "B",
                                   "league": "Super Division (India)"}},
                 "resolved": []}
        leaks = P.find_data_leaks(store)
        self.assertEqual(len(leaks), 1)

    def test_clean_league_still_passes(self):
        import predict_v2 as P
        store = {"pending": {"1": {"home": "A", "away": "B",
                                   "league": "Premier League (England)"}},
                 "resolved": []}
        self.assertEqual(P.find_data_leaks(store), [])
