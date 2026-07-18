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


class TestQualityFilter(unittest.TestCase):
    """السيدات والفئات السنية والرديف تُستبعد — بأسماء دوريات حقيقية."""

    EXCLUDED = [
        "Premier League Women", "FA WSL", "Serie A Women", "Frauen Bundesliga",
        "2. Frauen Bundesliga", "Primera División Femenina", "Feminine Division 1",
        "U19 Bundesliga", "U18 Premier League - North", "Campionato Primavera - 1",
        "UEFA Youth League", "Professional U21 Development League",
        "Reserve League", "Ural Youth Championship",
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


if __name__ == "__main__":
    unittest.main()
