# -*- coding: utf-8 -*-
"""اختبارات معيار السوق — REC-003 (قرار المالك 2026-08-08).

القياس التراكمي "المحرك ضد مرشّح السوق" أداة الحكم على جدوى الإثراء —
أي انكسار فيه يعني قراراً استراتيجياً مبنياً على رقم خاطئ.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import predict_v2 as P


def _row(pick, actual, mkt=None, correct=None):
    r = {"pick": pick, "actual": actual,
         "correct": (pick == actual) if correct is None else correct}
    if mkt:
        r["mkt_home"], r["mkt_draw"], r["mkt_away"] = mkt
    return r


class TestMarketFavorite(unittest.TestCase):
    """مرشّح السوق = أعلى احتمال ضمني، وبحسم حتمي عند التساوي."""

    def test_highest_prob_wins(self):
        self.assertEqual(P.market_favorite(_row("home", "home", (55, 25, 20))), "home")
        self.assertEqual(P.market_favorite(_row("home", "home", (20, 25, 55))), "away")

    def test_tie_is_deterministic_home_then_draw_then_away(self):
        self.assertEqual(P.market_favorite(_row("home", "home", (40, 40, 20))), "home")
        self.assertEqual(P.market_favorite(_row("home", "home", (20, 40, 40))), "draw")

    def test_missing_market_fields_return_empty(self):
        self.assertEqual(P.market_favorite(_row("home", "home")), "")
        self.assertEqual(P.market_favorite({"pick": "home", "actual": "home",
                                            "mkt_home": 50, "mkt_draw": None,
                                            "mkt_away": 20}), "")


class TestMarketBenchStats(unittest.TestCase):
    """الحساب التراكمي: n، إصابات المحرك، إصابات السوق، وعدّاد الاختلاف."""

    def test_counts_on_mixed_sample(self):
        resolved = [
            # اتفاق: كلاهما اختار المضيف وأصاب
            _row("home", "home", (60, 22, 18)),
            # اختلاف: المحرك اختار الضيف وأصاب، السوق رشّح المضيف وأخطأ
            _row("away", "away", (48, 27, 25)),
            # اختلاف: المحرك اختار المضيف وأخطأ، السوق رشّح الضيف وأصاب
            _row("home", "away", (30, 25, 45)),
            # صف بلا أودز — خارج القياس كلياً
            _row("home", "home"),
        ]
        mb = P.market_bench_stats(resolved)
        self.assertEqual(mb["n"], 3)
        self.assertEqual(mb["engine_correct"], 2)
        self.assertEqual(mb["market_correct"], 2)
        self.assertEqual(mb["disagree"], 2)

    def test_empty_record_gives_zero_n(self):
        mb = P.market_bench_stats([])
        self.assertEqual(mb, {"n": 0, "engine_correct": 0,
                              "market_correct": 0, "disagree": 0})

    def test_row_without_actual_is_skipped(self):
        mb = P.market_bench_stats([_row("home", None, (60, 22, 18))])
        self.assertEqual(mb["n"], 0)


class TestDigestLine(unittest.TestCase):
    """سطر النشرة يظهر فقط حين n > 0 — وتحت مفتاح MARKET_BENCH."""

    def _stats(self, bench):
        return {"last30": {"correct": 0, "total": 0},
                "overall": {"correct": 0, "total": 0},
                "market_bench": bench}

    def test_line_present_when_n_positive(self):
        digest = P.build_digest([], self._stats(
            {"n": 12, "engine_correct": 7, "market_correct": 8, "disagree": 3}))
        self.assertIn("⚖️ معيار السوق", digest)
        self.assertIn("7/12", digest)
        self.assertIn("8/12", digest)
        self.assertIn("3 مباراة", digest)

    def test_line_absent_when_n_zero(self):
        digest = P.build_digest([], self._stats(
            {"n": 0, "engine_correct": 0, "market_correct": 0, "disagree": 0}))
        self.assertNotIn("معيار السوق", digest)

    def test_line_absent_when_stats_lack_bench(self):
        digest = P.build_digest([], {"last30": {"correct": 0, "total": 0},
                                     "overall": {"correct": 0, "total": 0}})
        self.assertNotIn("معيار السوق", digest)

    def test_revert_switch_silences_line(self):
        """مفتاح التراجع: MARKET_BENCH=False يخفي السطر حتى لو وُجدت بيانات."""
        old = P.MARKET_BENCH
        try:
            P.MARKET_BENCH = False
            digest = P.build_digest([], self._stats(
                {"n": 12, "engine_correct": 7, "market_correct": 8, "disagree": 3}))
            self.assertNotIn("معيار السوق", digest)
        finally:
            P.MARKET_BENCH = old


class TestPipelineWiring(unittest.TestCase):
    """(أ) حقول السوق تنجو من pending إلى resolved، و(ب) الحساب معلّق في الصباح."""

    def test_resolve_pending_carries_market_fields(self):
        import inspect
        src = inspect.getsource(P.resolve_pending)
        for field in ("mkt_home", "mkt_draw", "mkt_away"):
            self.assertIn(field, src)

    def test_morning_run_attaches_market_bench(self):
        import inspect
        src = inspect.getsource(P.main)
        self.assertIn("market_bench_stats", src,
                      "التقييم الصباحي يجب أن يحسب معيار السوق تراكمياً")
