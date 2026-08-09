# -*- coding: utf-8 -*-
"""اختبارات أولوية الذهب في تقارير الظل — أمر المالك 2026-08-09.

حادثة 8 أغسطس: حصص الظل الست استهلكتها مباريات أبكر انطلاقاً، فبقيت
مباراتا 70%+ (PSV 78 وEstrela 73) بلا أي تقرير. الذهب لا يُترك بعد اليوم.
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import monitor as M

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


def fx(minutes, top=True, conf=None):
    e = {"top": top,
         "kickoff": (NOW + timedelta(minutes=minutes)).isoformat()}
    if conf is not None:
        e["confidence"] = conf
    return e


class TestGoldPriority(unittest.TestCase):
    """مباريات الثقة ≥70 تتقدم الجميع في الاختيار مهما كان انطلاقها أبعد."""

    def test_gold_beats_nearer_kickoff(self):
        pend = {"near": fx(10, conf=55), "gold": fx(40, conf=78)}
        picked = M.select_shadow_fixtures(pend, {}, set(), NOW, 1)
        self.assertEqual(picked, ["gold"],
                         "مباراة 70%+ تتقدم الأقرب انطلاقاً — درس PSV")

    def test_within_same_tier_nearest_first(self):
        pend = {"a": fx(40, conf=55), "b": fx(10, conf=50), "c": fx(25, conf=60)}
        self.assertEqual(M.select_shadow_fixtures(pend, {}, set(), NOW, 2),
                         ["b", "c"])
        pend2 = {"g1": fx(40, conf=72), "g2": fx(10, conf=75)}
        self.assertEqual(M.select_shadow_fixtures(pend2, {}, set(), NOW, 2),
                         ["g2", "g1"])

    def test_threshold_is_70(self):
        pend = {"n69": fx(10, conf=69), "g70": fx(40, conf=70)}
        self.assertEqual(M.select_shadow_fixtures(pend, {}, set(), NOW, 1),
                         ["g70"])

    def test_no_confidence_treated_as_normal(self):
        pend = {"x": fx(10), "g": fx(40, conf=71)}
        self.assertEqual(M.select_shadow_fixtures(pend, {}, set(), NOW, 2),
                         ["g", "x"])

    def test_revert_switch_restores_kickoff_order(self):
        old = M.SHADOW_GOLD_PRIORITY
        try:
            M.SHADOW_GOLD_PRIORITY = False
            pend = {"near": fx(10, conf=55), "gold": fx(40, conf=78)}
            self.assertEqual(M.select_shadow_fixtures(pend, {}, set(), NOW, 1),
                             ["near"])
        finally:
            M.SHADOW_GOLD_PRIORITY = old


class TestGoldExtraBudget(unittest.TestCase):
    """بعد نفاد الحصة اليومية: الذهب وحده يملك حصة إضافية مسقوفة."""

    def test_extra_budget_logic_wired(self):
        """المنطق موصول في shadow_reports: حصة ذهب منفصلة وعلامة gold تُحفظ."""
        import inspect
        src = inspect.getsource(M.shadow_reports)
        self.assertIn("gold_budget", src)
        self.assertIn("GOLD_SHADOW_EXTRA_PER_DAY", src)
        self.assertIn('"gold": gold', src)

    def test_caps_are_sane(self):
        self.assertEqual(M.GOLD_SHADOW_MIN_CONF, 70)
        self.assertGreater(M.GOLD_SHADOW_EXTRA_PER_DAY, 0)
        self.assertLessEqual(M.GOLD_SHADOW_EXTRA_PER_DAY, 6,
                             "الحصة الإضافية مسقوفة — حماية التكلفة")
