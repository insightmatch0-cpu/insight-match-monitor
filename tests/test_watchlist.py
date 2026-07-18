# -*- coding: utf-8 -*-
"""اختبارات قائمة التركيز — التعرف الاحتياطي على أسماء الفرق (إصلاح 2026-07-15)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchlist as W

FIXTURES = {
    "1": {"home": "Universitatea Craiova", "away": "FC Sarajevo",
          "ar_home": "يونيفرسيتاتيا كرايوفا", "ar_away": "سراييفو"},
    "2": {"home": "Kairat Almaty", "away": "KF Milano",
          "ar_home": "كايرات ألماتي", "ar_away": "ميلانو"},
    "3": {"home": "Real Madrid", "away": "Barcelona",
          "ar_home": "ريال مدريد", "ar_away": "برشلونة"},
}


class TestFallbackMatch(unittest.TestCase):
    """البلاغ الأصلي: "U.craiova + kairat" لم يتعرف عليهما المفسر."""

    def test_original_report_case(self):
        fids = W.fallback_match("U.craiova + kairat", FIXTURES)
        self.assertEqual(set(fids), {"1", "2"})

    def test_arabic_waw_prefix(self):
        fids = W.fallback_match("كرايوفا وكايرات", FIXTURES)
        self.assertEqual(set(fids), {"1", "2"})

    def test_no_match_returns_empty(self):
        self.assertEqual(W.fallback_match("xyzabc", FIXTURES), [])


if __name__ == "__main__":
    unittest.main()
