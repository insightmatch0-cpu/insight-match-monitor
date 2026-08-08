# -*- coding: utf-8 -*-
"""اختبارات REC-006 (قرار المالك 2026-08-08): حقن كشف الحساب ومعدلات الأساس
في سياق تقرير ما قبل المباراة.

تحرس ثلاثة أشياء: (1) الدالتان تحسبان أرقاماً صحيحة من بيانات معروفة،
(2) الحقن يختفي كلياً عند ضبط مفتاح التراجع PREMATCH_CALIBRATION_CTX = False،
(3) الملفات الناقصة/الفاسدة لا تكسر التقرير أبداً (فشل صامت).
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import monitor as M


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def scenarios_fixture():
    """تقريران مُقيَّمان بأنواع ادعاء ونتائج معروفة مسبقاً."""
    return {"pending": {}, "resolved": [
        {"grades": [
            {"claim": "فوز المضيف بهامش هدفين", "result": "صح"},
            {"claim": "كلا الفريقين يسجلان: نعم (60%)", "result": "خطأ"},
            {"claim": "إجمالي الأهداف فوق 2.5 (55%)", "result": "صح"},
            {"claim": "الركنيات: تفوق المضيف", "result": "جزئي"},
            {"claim": "البطاقات: 4-6 إنذارات", "result": "خطأ"},
        ]},
        {"grades": [
            {"claim": "هدف من كرة ثابتة (40%)", "result": "خطأ"},
            {"claim": "الشوط الثاني أغزر أهدافاً", "result": "صح"},
            {"claim": "المسجل المحتمل: فلان", "result": "جزئي"},
            {"claim": "كلا الفريقين يسجلان: لا", "result": "صح"},
        ]},
        {"note": "تقرير بلا grades — يُتجاهل"},
    ]}


def predictions_fixture():
    """8 مباريات مُقيَّمة في L1: BTTS في 4/8، فوق 2.5 في 3/8.
    ودوري واحد يملك 16 مباراة (فوق العتبة) بمعدلات مختلفة عن الإجمالي."""
    resolved = [
        {"score": "1-1", "league": "L1"}, {"score": "2-0", "league": "L1"},
        {"score": "0-0", "league": "L1"}, {"score": "2-1", "league": "L1"},
        {"score": "1-0", "league": "L1"}, {"score": "0-1", "league": "L1"},
        {"score": "3-2", "league": "L1"}, {"score": "1-2", "league": "L1"},
    ]
    # دوري "Big League": 16 مباراة كلها 1-1 → BTTS 100% وفوق 2.5 = 0%
    resolved += [{"score": "1-1", "league": "Big League"} for _ in range(16)]
    return {"pending": {}, "resolved": resolved}


class PrematchCalibrationBase(unittest.TestCase):
    """تهيئة مشتركة: ملفات مؤقتة بدل ملفات المستودع الحقيقية."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.orig = (M.SCENARIOS_FILE, M.RADAR_PREDICTIONS_FILE,
                     M.LESSONS_FILE, M.PREMATCH_CALIBRATION_CTX)
        M.SCENARIOS_FILE = root / "scenarios_v2.json"
        M.RADAR_PREDICTIONS_FILE = root / "predictions_v2.json"
        M.LESSONS_FILE = root / "lessons_v2.json"

        def restore():
            (M.SCENARIOS_FILE, M.RADAR_PREDICTIONS_FILE,
             M.LESSONS_FILE, M.PREMATCH_CALIBRATION_CTX) = self.orig
        self.addCleanup(restore)


class TestClaimType(unittest.TestCase):
    def test_types_from_real_claim_shapes(self):
        """عينات بصيغة البنود الحقيقية في scenarios_v2.json."""
        cases = [
            ("فوز روساريو سنترال بهامش هدف إلى هدفين", "النتيجة"),
            ("كلا الفريقين يسجلان: لا (60%)", "كلا الفريقين يسجلان"),
            ("إجمالي الأهداف تحت 2.5 (58%)", "إجمالي الأهداف"),
            ("المسجل المحتمل: دي ماريا أو فيليس", "المسجل"),
            ("الركنيات: إجمالي 9-11 مع تفوق روساريو", "الركنيات"),
            ("البطاقات: 4-6 إنذارات أغلبها لألدوسيفي", "البطاقات"),
            ("هدف من كرة ثابتة للأرض (40%)", "الكرات الثابتة"),
            ("الشوط الثاني أغزر أهدافاً", "الأشواط"),
        ]
        for claim, expected in cases:
            self.assertEqual(M.claim_type(claim), expected, claim)

    def test_unknown_claim_is_other(self):
        self.assertEqual(M.claim_type("بند غامض بلا كلمات معروفة"), "أخرى")


class TestScenarioScorecard(PrematchCalibrationBase):
    def test_counts_per_claim_type(self):
        write_json(M.SCENARIOS_FILE, scenarios_fixture())
        text = M.scenario_scorecard_text()
        self.assertIn("سجل أدائك الفعلي", text)
        self.assertIn("2 تقريراً / 9 بنداً", text)
        self.assertIn("- كلا الفريقين يسجلان: صح 1 / جزئي 0 / خطأ 1 (من 2)", text)
        self.assertIn("- النتيجة: صح 1 / جزئي 0 / خطأ 0 (من 1)", text)
        self.assertIn("- الركنيات: صح 0 / جزئي 1 / خطأ 0 (من 1)", text)
        self.assertIn("- البطاقات: صح 0 / جزئي 0 / خطأ 1 (من 1)", text)
        self.assertIn("- الكرات الثابتة: صح 0 / جزئي 0 / خطأ 1 (من 1)", text)

    def test_stated_percentage_gap(self):
        """4 بنود أعلنت نسبة: (60 خطأ، 55 صح، 40 خطأ) + لا نسبة في البقية.
        المتوسط المعلن (60+55+40)/3 ≈ 52% والمتحقق 1/3 ≈ 33%."""
        write_json(M.SCENARIOS_FILE, scenarios_fixture())
        text = M.scenario_scorecard_text()
        self.assertIn("متوسطها 52%", text)
        self.assertIn("تحقق منها فعلياً 33%", text)

    def test_missing_file_silent(self):
        self.assertEqual(M.scenario_scorecard_text(), "")

    def test_corrupt_file_silent(self):
        M.SCENARIOS_FILE.write_text("{ليس json", encoding="utf-8")
        self.assertEqual(M.scenario_scorecard_text(), "")

    def test_empty_resolved_silent(self):
        write_json(M.SCENARIOS_FILE, {"pending": {}, "resolved": []})
        self.assertEqual(M.scenario_scorecard_text(), "")


class TestBaseRates(PrematchCalibrationBase):
    def test_overall_rates(self):
        write_json(M.RADAR_PREDICTIONS_FILE, predictions_fixture())
        text = M.base_rates_text("")
        # الإجمالي 24 مباراة: BTTS = (4+16)/24 = 83%، فوق 2.5 = 3/24 = 12%
        self.assertIn("24 مباراة", text)
        self.assertIn("83%", text)
        self.assertIn("12%", text)
        self.assertIn("سياق لا أمر", text)

    def test_league_rates_shown_when_sample_enough(self):
        write_json(M.RADAR_PREDICTIONS_FILE, predictions_fixture())
        text = M.base_rates_text("Big League")
        self.assertIn("وفي هذا الدوري تحديداً (16 مباراة)", text)
        self.assertIn("100%", text)   # BTTS في الدوري الكبير
        self.assertIn("0%", text)     # فوق 2.5 في الدوري الكبير

    def test_league_rates_hidden_when_sample_small(self):
        """دوري L1 عنده 8 مباريات فقط — أقل من BASE_RATE_MIN_LEAGUE."""
        write_json(M.RADAR_PREDICTIONS_FILE, predictions_fixture())
        self.assertNotIn("وفي هذا الدوري تحديداً", M.base_rates_text("L1"))

    def test_missing_file_silent(self):
        self.assertEqual(M.base_rates_text("L1"), "")

    def test_bad_scores_ignored(self):
        write_json(M.RADAR_PREDICTIONS_FILE, {"resolved": [
            {"score": "", "league": "L1"}, {"score": None, "league": "L1"},
            {"league": "L1"}, {"score": "2-1", "league": "L1"},
        ]})
        text = M.base_rates_text("")
        self.assertIn("1 مباراة", text)


class TestInjectionSwitch(PrematchCalibrationBase):
    """مفتاح التراجع: True = الحقن موجود، False = يختفي كلياً من السياق."""

    def setUp(self):
        super().setUp()
        write_json(M.SCENARIOS_FILE, scenarios_fixture())
        write_json(M.RADAR_PREDICTIONS_FILE, predictions_fixture())
        # نعزل السياق عن الشبكة: صفر نداءات API وصفر أخبار في الاختبار
        self._api, self._news = M.api_football, M.team_news_headlines
        M.api_football = lambda path: []
        M.team_news_headlines = lambda team: []

        def restore():
            M.api_football, M.team_news_headlines = self._api, self._news
        self.addCleanup(restore)
        self.v2p = {"home": "A", "away": "B", "league": "Big League",
                    "pick": "home", "prob_home": 50, "prob_draw": 30,
                    "prob_away": 20, "reason": ""}

    def test_injected_when_enabled(self):
        M.PREMATCH_CALIBRATION_CTX = True
        ctx = M.build_prematch_context("1", self.v2p, None, None)
        self.assertIn("سجل أدائك الفعلي", ctx)
        self.assertIn("معدلات الأساس الفعلية", ctx)
        self.assertIn("وفي هذا الدوري تحديداً", ctx)

    def test_absent_when_disabled(self):
        M.PREMATCH_CALIBRATION_CTX = False
        ctx = M.build_prematch_context("1", self.v2p, None, None)
        self.assertNotIn("سجل أدائك الفعلي", ctx)
        self.assertNotIn("معدلات الأساس", ctx)

    def test_report_survives_missing_data_files(self):
        """ملفا البيانات غير موجودين إطلاقاً — التقرير يُبنى بلا انهيار."""
        M.PREMATCH_CALIBRATION_CTX = True
        M.SCENARIOS_FILE.unlink()
        M.RADAR_PREDICTIONS_FILE.unlink()
        ctx = M.build_prematch_context("1", self.v2p, None, None)
        self.assertIn("مباراة تنطلق قريباً", ctx)
        self.assertNotIn("سجل أدائك الفعلي", ctx)


if __name__ == "__main__":
    unittest.main()
