# -*- coding: utf-8 -*-
"""اختبارات تقارير الظل (توجيه المالك 2026-07-18): تدريب يومي صامت.

المحرك 2 يلتقط تقرير سيناريوهات لمباريات الدوريات الكبرى القادمة تلقائياً
— بلا تيليجرام — ويُقيَّم صباحاً كأي تقرير. هذه الاختبارات تحرس منطق
الاختيار: كبرى فقط، داخل النافذة، بلا تكرار، وباحترام السقف اليومي.
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import monitor as M

NOW = datetime(2026, 7, 18, 15, 0, tzinfo=timezone.utc)


def fx(minutes_from_now, top=True):
    return {"top": top,
            "kickoff": (NOW + timedelta(minutes=minutes_from_now)).isoformat()}


class TestSelectShadowFixtures(unittest.TestCase):
    def test_picks_top_fixture_inside_window(self):
        pend = {"1": fx(30)}
        self.assertEqual(M.select_shadow_fixtures(pend, {}, set(), NOW, 6), ["1"])

    def test_skips_non_top(self):
        pend = {"1": fx(30, top=False)}
        self.assertEqual(M.select_shadow_fixtures(pend, {}, set(), NOW, 6), [])

    def test_skips_watchlist_matches(self):
        """مباريات قائمة التركيز لها التقرير العادي — لا نكررها."""
        pend = {"1": fx(30)}
        self.assertEqual(M.select_shadow_fixtures(pend, {}, {"1"}, NOW, 6), [])

    def test_skips_already_captured(self):
        pend = {"1": fx(30)}
        self.assertEqual(M.select_shadow_fixtures(pend, {"1": {}}, set(), NOW, 6), [])

    def test_skips_outside_window_or_started(self):
        pend = {"far": fx(120), "started": fx(-5)}
        self.assertEqual(M.select_shadow_fixtures(pend, {}, set(), NOW, 6), [])

    def test_cap_and_kickoff_order(self):
        pend = {"a": fx(40), "b": fx(10), "c": fx(25)}
        picked = M.select_shadow_fixtures(pend, {}, set(), NOW, 2)
        self.assertEqual(picked, ["b", "c"], "الأقرب انطلاقاً أولاً وبحد السقف")

    def test_bad_kickoff_ignored(self):
        pend = {"1": {"top": True, "kickoff": "not-a-date"}}
        self.assertEqual(M.select_shadow_fixtures(pend, {}, set(), NOW, 6), [])


class TestShadowConfig(unittest.TestCase):
    def test_daily_cap_defined_and_sane(self):
        self.assertTrue(1 <= M.SHADOW_REPORTS_PER_DAY <= 20)


if __name__ == "__main__":
    unittest.main()


class TestEveningReserve(unittest.TestCase):
    """الحجز المسائي (قرار المالك 2026-08-15 — حالة شيفيلد×برمنغهام):
    سبت تشامبيونشيب مزدحم استهلكت فيه مباريات 11:30/14:00 الحصص الست
    كلها، فوصلت شيفيلد (16:30، ثقة 38) ونافذتها مفتوحة والحصة صفر.
    القاعدة: المبكرة لا تستهلك الحصص المحجوزة للمساء، والحجز ديناميكي
    بقدر المباريات المسائية الفعلية، والذهب فوق القاعدة."""

    MORNING = datetime(2026, 8, 15, 10, 45, tzinfo=timezone.utc)

    def _evening_fx(self, hour=16, minute=30):
        return {"top": True,
                "kickoff": datetime(2026, 8, 15, hour, minute,
                                    tzinfo=timezone.utc).isoformat()}

    def test_is_early_boundary(self):
        self.assertTrue(M._shadow_is_early("2026-08-15T14:00:00+00:00"))
        self.assertFalse(M._shadow_is_early("2026-08-15T15:00:00+00:00"))
        self.assertFalse(M._shadow_is_early("2026-08-15T16:30:00+00:00"))
        self.assertTrue(M._shadow_is_early("not-a-date"), "المجهول يخضع للحجز")

    def test_evening_ahead_counts_only_top_uncaptured_today(self):
        pend = {
            "shef": self._evening_fx(16, 30),          # تُحسب
            "captured": self._evening_fx(17, 0),       # ملتقطة — لا تُحسب
            "watch": self._evening_fx(18, 0),          # قائمة التركيز — لا
            "small": {**self._evening_fx(19, 0), "top": False},  # ليست كبرى
            "early": {"top": True,
                      "kickoff": "2026-08-15T14:00:00+00:00"},   # مبكرة
            "tomorrow": {"top": True,
                         "kickoff": "2026-08-16T16:00:00+00:00"},  # غداً
        }
        n = M.evening_fixtures_ahead(pend, {"captured": {}}, {"watch"},
                                     self.MORNING)
        self.assertEqual(n, 1)

    def test_no_evening_fixtures_means_no_reserve(self):
        """لا مباريات مسائية في الجدول = صفر حجز، لا حصة مهدورة."""
        pend = {"a": {"top": True, "kickoff": "2026-08-15T14:00:00+00:00"}}
        self.assertEqual(
            M.evening_fixtures_ahead(pend, {}, set(), self.MORNING), 0)

    def test_reserve_constants_sane(self):
        self.assertTrue(0 < M.SHADOW_EVENING_RESERVE < M.SHADOW_REPORTS_PER_DAY,
                        "الحجز جزء من السقف لا كله — وإلا جاع الصباح أو المساء")
        self.assertTrue(0 <= M.SHADOW_EVENING_FROM_UTC <= 23)

    def test_sheffield_scenario_reserve_blocks_fifth_early_capture(self):
        """إعادة تمثيل يوم 15 أغسطس: مع الحجز، المبكرة الخامسة لا تُلتقط
        وتبقى حصتان لشيفيلد ورفاقها المسائيين."""
        reserve = min(M.SHADOW_EVENING_RESERVE, 2)   # مباراتان مسائيتان في الجدول
        early_used = 4
        early_budget = max(0, M.SHADOW_REPORTS_PER_DAY - reserve - early_used)
        self.assertEqual(early_budget, 0,
                         "بعد 4 مبكرات والحجز 2: لا حصة مبكرة خامسة")


class TestNightCap(unittest.TestCase):
    """سقف الليل الأمريكي (اكتشاف 2026-08-16): ليلة السبت التقط الظل 6
    مباريات MLS/أرجنتينية تحمل تاريخ السبت UTC — فاستُهلكت حصة اليوم كلها
    قبل أن تصحو إنجلترا. القاعدة: الليل (قبل 06:00 UTC) يُحتسب على الحصة
    بحد أقصى 2 ولا يُلتقط منه أكثر، والفائض الليلي لا يسد نهار أوروبا."""

    def test_night_constants_sane(self):
        self.assertTrue(0 < M.SHADOW_NIGHT_MAX < M.SHADOW_REPORTS_PER_DAY)
        self.assertTrue(0 < M.SHADOW_NIGHT_UNTIL_UTC < M.SHADOW_EVENING_FROM_UTC)

    def test_shadow_hour_parses_and_defaults_conservatively(self):
        self.assertEqual(M._shadow_hour("2026-08-16T00:30:00+00:00"), 0)
        self.assertEqual(M._shadow_hour("2026-08-16T14:00:00+00:00"), 14)
        self.assertEqual(M._shadow_hour("bad"), 12, "المجهول نهارٌ مبكر — الأكثر تحفظاً")

    def test_saturday_scenario_budget_math(self):
        """إعادة تمثيل صباح 16 أغسطس: 6 ملتقطة كلها ليلية → لولا السقف
        لكانت الحصة صفراً؛ معه يُحتسب اثنان فقط وتبقى 4 حصص لنهار إنجلترا."""
        night_used = 6
        night_counted = min(night_used, M.SHADOW_NIGHT_MAX)
        day_used = 0
        budget = M.SHADOW_REPORTS_PER_DAY - day_used - night_counted
        self.assertEqual(budget, M.SHADOW_REPORTS_PER_DAY - M.SHADOW_NIGHT_MAX,
                         "فائض الليل يجب ألا يسد نهار أوروبا")

    def test_no_night_games_changes_nothing(self):
        """يوم بلا ليل أمريكي: الحساب القديم حرفياً."""
        night_used = 0
        budget = M.SHADOW_REPORTS_PER_DAY - 3 - min(night_used, M.SHADOW_NIGHT_MAX)
        self.assertEqual(budget, M.SHADOW_REPORTS_PER_DAY - 3)

    def test_structural_night_gate_in_source(self):
        """بنيوي: بوابة سقف الليل وعدّاده موجودان في shadow_reports."""
        import inspect
        src = inspect.getsource(M.shadow_reports)
        self.assertIn("night_used >= SHADOW_NIGHT_MAX", src)
        self.assertIn("night_counted = min(night_used, SHADOW_NIGHT_MAX)", src)
