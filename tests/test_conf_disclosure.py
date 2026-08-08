# -*- coding: utf-8 -*-
"""اختبارات إفصاح الثقة — REC-004 (قرار المالك 2026-08-08).

فتح الإفصاح فوق 65 يجب ألا يمسّ حارس الكؤوس ولا يغيّر أي اختيار —
هذان خطان أحمران منصوص عليهما في التوصية نفسها.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import predict_v2 as P


def _reply(fid, ph, pd, pa):
    return (f'[{{"id":"{fid}","prob_home":{ph},"prob_draw":{pd},'
            f'"prob_away":{pa},"reason":"x"}}]')


class TestDisclosureAbove65(unittest.TestCase):
    """غير الكأس يتجاوز 65 الآن — هذا جوهر التوصية."""

    def test_non_cup_confidence_can_exceed_65(self):
        out = P.parse_predictions_json(_reply("1", 72, 18, 10))
        self.assertEqual(out["1"]["confidence"], 72)
        self.assertGreater(out["1"]["confidence"], 65)

    def test_confidence_clipped_at_disclosure_cap(self):
        """القص عند 80: قناعة 88% تُعلن 80 — الاحتمالات نفسها لا تُمسّ."""
        out = P.parse_predictions_json(_reply("2", 88, 7, 5))
        self.assertEqual(out["2"]["confidence"], P.CONF_DISCLOSURE_CAP)
        self.assertEqual(out["2"]["prob_home"], 88)

    def test_pick_is_still_highest_probability(self):
        """الاختيار ثابت: القص يمسّ الثقة المعلنة فقط."""
        out = P.parse_predictions_json(_reply("3", 88, 7, 5))
        self.assertEqual(out["3"]["pick"], "home")
        out = P.parse_predictions_json(_reply("4", 10, 18, 72))
        self.assertEqual(out["4"]["pick"], "away")

    def test_prompt_carries_the_disclosure_instruction(self):
        """التعليمة (سجل ~96% عند ≥65 + الإذن بتجاوز 65) داخل موجّه التوقع."""
        import inspect
        src = inspect.getsource(P.claude_predict_batch)
        self.assertIn("96%", src)
        self.assertIn("أعلى من 65", src)
        self.assertIn("CONF_DISCLOSURE", src)

    def test_revert_switch_restores_old_cap(self):
        """مفتاح التراجع: CONF_DISCLOSURE=False يعيد سقف المحلل القديم 85."""
        old = P.CONF_DISCLOSURE
        try:
            P.CONF_DISCLOSURE = False
            out = P.parse_predictions_json(_reply("5", 88, 7, 5))
            self.assertEqual(out["5"]["confidence"], 85)
        finally:
            P.CONF_DISCLOSURE = old


class TestCupGuardrailUntouched(unittest.TestCase):
    """الخط الأحمر: الكأس تبقى مقصوصة عند 65 مع أرضية التعادل — بلا أي مساس."""

    def test_cup_still_capped_at_65(self):
        e = {"is_cup": True, "pick": "home", "confidence": 80,
             "prob_home": 80, "prob_draw": 12, "prob_away": 8}
        P.apply_cup_guardrail(e)
        self.assertLessEqual(e["confidence"], P.CUP_CONF_CAP)
        self.assertEqual(e["pick"], "home")
        self.assertGreaterEqual(e["prob_draw"], P.CUP_MIN_DRAW)

    def test_cup_cap_constant_is_still_65(self):
        """CUP_CONF_CAP=65 نصاً — أي تغيير له قرار مالك منفصل، لا أثر جانبي."""
        self.assertEqual(P.CUP_CONF_CAP, 65)

    def test_disclosure_cap_is_80(self):
        self.assertEqual(P.CONF_DISCLOSURE_CAP, 80)

    def test_cup_entry_from_parser_then_guardrail_lands_at_65(self):
        """المسار الكامل: محلل يفصح 78 ثم حارس الكأس يقصّه إلى 65."""
        out = P.parse_predictions_json(_reply("6", 78, 12, 10))
        e = dict(out["6"], is_cup=True)
        P.apply_cup_guardrail(e)
        self.assertLessEqual(e["confidence"], 65)
        self.assertEqual(e["pick"], "home")
