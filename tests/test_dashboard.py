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


if __name__ == "__main__":
    unittest.main()
