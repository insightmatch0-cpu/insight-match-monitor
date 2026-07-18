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
