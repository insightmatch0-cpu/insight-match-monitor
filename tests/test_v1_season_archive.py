# -*- coding: utf-8 -*-
"""اختبارات شمول المحرك 1 بأرشيف الموسم — أمر المالك الصريح 2026-08-09.

استثناء موثق لقاعدة تجميد المحرك 1: لا حذف أبداً، وعدّاد موسم من صفر
في 2026-08-13 — مرآة للمحرك 2 حتى تبقى مقارنة المحركين عادلة.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import predict as V1
import predict_v2 as V2


def _row(date, conf, correct, top=False):
    return {"date": date, "confidence": conf, "correct": correct, "top": top,
            "fid": f"{date}-{conf}", "pick": "home",
            "actual": "home" if correct else "away"}


class TestV1NoDeletion(unittest.TestCase):

    def _resolve_with(self, count):
        store = {"pending": {"x": {"date": "2026-08-01"}},
                 "resolved": [_row("2026-08-01", 50, True)] * count}
        orig = V1.api_football
        V1.api_football = lambda q: []
        self.addCleanup(lambda: setattr(V1, "api_football", orig))
        V1.resolve_pending(store)
        return store

    def test_v1_resolved_never_truncated_by_default(self):
        store = self._resolve_with(1200)
        self.assertEqual(len(store["resolved"]), 1200,
                         "سقف 1000 عاد للمحرك 1 — ممنوع بأمر المالك 2026-08-09")

    def test_v1_emergency_cap_switch(self):
        old = V1.RESOLVED_CAP
        try:
            V1.RESOLVED_CAP = 1000
            store = self._resolve_with(1200)
            self.assertEqual(len(store["resolved"]), 1000)
        finally:
            V1.RESOLVED_CAP = old


class TestV1SeasonCounters(unittest.TestCase):

    def test_v1_season_counts_from_aug_13_only(self):
        rows = [_row("2026-08-05", 72, True),
                _row("2026-08-13", 65, True, top=True),
                _row("2026-08-14", 45, False)]
        s = V1.compute_stats(rows)
        self.assertEqual(s["season"]["overall"], {"correct": 1, "total": 2})
        self.assertEqual(s["season"]["start"], "2026-08-13")
        self.assertEqual(s["overall"]["total"], 3)

    def test_v1_season_zeros_before_kickoff(self):
        s = V1.compute_stats([_row("2026-08-05", 72, True)])
        self.assertEqual(s["season"]["overall"], {"correct": 0, "total": 0})

    def test_engines_mirror_each_other(self):
        """عدالة المقارنة: نفس التاريخ ونفس بنية الكتلة في المحركين."""
        self.assertEqual(V1.SEASON_START, V2.SEASON_START)
        self.assertEqual(V1.RESOLVED_CAP, V2.RESOLVED_CAP)
        rows = [_row("2026-08-13", 72, True)]
        self.assertEqual(set(V1.compute_stats(rows)["season"].keys()),
                         set(V2.compute_stats(rows)["season"].keys()))
