# -*- coding: utf-8 -*-
"""اختبارات المحرك 2 — كل إصلاح سابق يتحول هنا إلى اختبار دائم حتى لا يعود الخطأ.

قاعدة SLA (توجيه المالك 2026-07-18): ما شُفي لا يمرض مرة أخرى.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import predict_v2 as P


class TestCupGuardrail(unittest.TestCase):
    """حارس الكأس (إصلاح 2026-07-18): خانة الثقة 70%+ يجب أن تبقى شبه معصومة."""

    def test_kerry_shelbourne_the_exact_miss(self):
        """الخطأ الحقيقي الوحيد في خانة 70%+: كيري 2-2 شيلبورن (كأس أيرلندا)."""
        e = {"is_cup": True, "pick": "away", "confidence": 72,
             "prob_home": 10, "prob_draw": 18, "prob_away": 72}
        P.apply_cup_guardrail(e)
        self.assertEqual(e["pick"], "away", "الحارس يجب ألا يغيّر الطرف المُختار")
        self.assertLessEqual(e["confidence"], P.CUP_CONF_CAP,
                             "توقع كأس لا يدخل خانة 70%+ أبداً")
        self.assertGreaterEqual(e["prob_draw"], P.CUP_MIN_DRAW)
        self.assertEqual(e["prob_home"] + e["prob_draw"] + e["prob_away"], 100)

    def test_league_match_untouched(self):
        """مباراة دوري عادية بثقة عالية تمر بلا أي تعديل."""
        e = {"is_cup": False, "pick": "away", "confidence": 72,
             "prob_home": 10, "prob_draw": 18, "prob_away": 72}
        P.apply_cup_guardrail(e)
        self.assertEqual(e["confidence"], 72)
        self.assertEqual(e["prob_draw"], 18)

    def test_cup_with_high_draw_only_caps_confidence(self):
        e = {"is_cup": True, "pick": "home", "confidence": 60,
             "prob_home": 60, "prob_draw": 30, "prob_away": 10}
        P.apply_cup_guardrail(e)
        self.assertEqual((e["prob_home"], e["prob_draw"], e["prob_away"]), (60, 30, 10))
        self.assertEqual(e["pick"], "home")

    def test_missing_probs_do_not_crash(self):
        e = {"is_cup": True, "pick": "home", "confidence": 70}
        P.apply_cup_guardrail(e)   # يجب ألا يرمي استثناء
        self.assertEqual(e["pick"], "home")

    def test_is_cup_detection(self):
        self.assertTrue(P.is_cup_fixture("FAI Cup", "1st Round"))
        self.assertTrue(P.is_cup_fixture("UEFA Champions League", "1st Qualifying Round"))
        self.assertTrue(P.is_cup_fixture("كأس الملك", ""))
        self.assertFalse(P.is_cup_fixture("Super Liga", "Regular Season - 3"))
        self.assertFalse(P.is_cup_fixture("Eliteserien", "Regular Season - 16"))
        self.assertFalse(P.is_cup_fixture("Premier League", "Round 1"))


class TestParsePredictions(unittest.TestCase):
    """محلل ردود Claude — متسامح مع الأسوار ويطبّع الاحتمالات لمجموع 100."""

    def test_normalizes_and_derives_pick(self):
        out = P.parse_predictions_json(
            '[{"id": 5, "prob_home": 50, "prob_draw": 30, "prob_away": 30}]')
        p = out["5"]
        self.assertEqual(p["prob_home"] + p["prob_draw"] + p["prob_away"], 100)
        self.assertEqual(p["pick"], "home")

    def test_strips_code_fences(self):
        out = P.parse_predictions_json(
            '```json\n[{"id":"7","prob_home":20,"prob_draw":20,"prob_away":60}]\n```')
        self.assertEqual(out["7"]["pick"], "away")

    def test_confidence_clamped(self):
        out = P.parse_predictions_json(
            '[{"id":"9","prob_home":95,"prob_draw":3,"prob_away":2}]')
        self.assertLessEqual(out["9"]["confidence"], 85)

    def test_garbage_returns_empty(self):
        self.assertEqual(P.parse_predictions_json("no json here"), {})


class TestTopLeagues(unittest.TestCase):
    """دوريات المالك ذات الأولوية (2026-07-17) يجب أن تبقى في TOP_LEAGUE_IDS."""

    OWNER_PRIORITY = {39, 40, 61, 78, 135, 140, 307, 417, 542}

    def test_priority_leagues_present(self):
        missing = self.OWNER_PRIORITY - P.TOP_LEAGUE_IDS
        self.assertFalse(missing, f"دوريات أولوية مفقودة: {missing}")


if __name__ == "__main__":
    unittest.main()
