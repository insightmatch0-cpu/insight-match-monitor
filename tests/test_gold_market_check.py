# -*- coding: utf-8 -*-
"""اختبارات انحدار: التحقق السوقي قبل الذهبية — HOLD-016 أ (قرار المالك 2026-09-09).

RND-027: الذهبية المُخصَّبة 31/34 = 91% مقابل الخفيفة (بلا أودز) 27/39 = 69%.
القاعدة: توقع خفيف بثقة ≥70 يستدعي نداء أودز واحداً؛ لا سوق أو سوق < 70 للاختيار
⇒ الثقة تُخفَّض إلى 69 — الاختيار والاحتمالات لا تُمس، والمُخصَّب لا يُلمس.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import predict_v2 as P

SRC = (ROOT / "predict_v2.py").read_text(encoding="utf-8")


def odds_rows(home, draw, away):
    return [{"bookmakers": [{"name": "Bet365", "bets": [{"name": "Match Winner", "values": [
        {"value": "Home", "odd": str(home)}, {"value": "Draw", "odd": str(draw)},
        {"value": "Away", "odd": str(away)}]}]}]}]


def lean_gold(conf=72, pick="home"):
    return {"fid": "1", "pick": pick, "confidence": conf,
            "prob_home": conf, "prob_draw": 100 - conf - 10, "prob_away": 10}


class TestGoldMarketCheck(unittest.TestCase):
    def setUp(self):
        self.stats = {"checked": 0, "kept": 0, "capped": 0, "no_market": 0}
        self.calls = []

    def fetch(self, rows):
        def _f(fid):
            self.calls.append(fid)
            return rows
        return _f

    def test_market_agrees_keeps_gold_and_stores_market(self):
        e = lean_gold(72)
        tag = P.apply_gold_market_check(e, False, {"used": 0},
                                        fetch=self.fetch(odds_rows(1.25, 6.0, 11.0)), stats=self.stats)
        self.assertEqual(tag, "kept")
        self.assertEqual(e["confidence"], 72)
        self.assertEqual(e["pick"], "home")
        self.assertGreaterEqual(e["mkt_home"], P.GOLD_MARKET_MIN)
        self.assertEqual(self.stats["kept"], 1)
        self.assertEqual(self.calls, ["1"])

    def test_market_disagrees_caps_but_never_changes_pick(self):
        """الحالة التي بُني الحارس لها: ثقة 72 بلا مرساة والسوق يرى 55%."""
        e = lean_gold(72)
        tag = P.apply_gold_market_check(e, False, {"used": 0},
                                        fetch=self.fetch(odds_rows(1.8, 3.6, 4.5)), stats=self.stats)
        self.assertTrue(tag.startswith("capped:market_"))
        self.assertEqual(e["confidence"], P.GOLD_CHECK_CAP)
        self.assertLess(e["confidence"], 70, "لا تدخل خانة 70%+")
        self.assertEqual(e["pick"], "home")
        self.assertEqual(e["prob_home"], 72, "الاحتمالات لا تُمس — المعايرة تتعلم منها")
        self.assertEqual(e["gold_check"], tag)

    def test_no_market_means_no_anchor_and_caps(self):
        e = lean_gold(78)
        tag = P.apply_gold_market_check(e, False, {"used": 0}, fetch=self.fetch([]), stats=self.stats)
        self.assertEqual(tag, "capped:no_market")
        self.assertEqual(e["confidence"], P.GOLD_CHECK_CAP)
        self.assertEqual(self.stats["no_market"], 1)

    def test_enriched_prediction_is_never_touched_or_called(self):
        e = lean_gold(78)
        tag = P.apply_gold_market_check(e, True, {"used": 0}, fetch=self.fetch([]), stats=self.stats)
        self.assertEqual(tag, "")
        self.assertEqual(e["confidence"], 78)
        self.assertEqual(self.calls, [])

    def test_below_70_is_never_called(self):
        e = lean_gold(69)
        tag = P.apply_gold_market_check(e, False, {"used": 0}, fetch=self.fetch([]), stats=self.stats)
        self.assertEqual(tag, "")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.stats["checked"], 0)

    def test_kill_switch(self):
        e = lean_gold(78)
        orig = P.GOLD_MARKET_CHECK
        P.GOLD_MARKET_CHECK = False
        try:
            tag = P.apply_gold_market_check(e, False, {"used": 0}, fetch=self.fetch([]), stats=self.stats)
        finally:
            P.GOLD_MARKET_CHECK = orig
        self.assertEqual(tag, "")
        self.assertEqual(e["confidence"], 78)

    def test_call_budget_caps_without_fetching(self):
        self.stats["checked"] = P.GOLD_CHECK_MAX_CALLS
        e = lean_gold(75)
        tag = P.apply_gold_market_check(e, False, {"used": 0}, fetch=self.fetch(odds_rows(1.2, 6, 12)), stats=self.stats)
        self.assertEqual(tag, "capped:budget")
        self.assertEqual(self.calls, [])
        self.assertEqual(e["confidence"], P.GOLD_CHECK_CAP)

    def test_fetch_failure_never_kills_the_run(self):
        def boom(fid):
            raise RuntimeError("network")
        e = lean_gold(75)
        tag = P.apply_gold_market_check(e, False, {"used": 0}, fetch=boom, stats=self.stats)
        self.assertEqual(tag, "capped:no_market")

    def test_constants_pinned(self):
        self.assertTrue(P.GOLD_MARKET_CHECK)
        self.assertEqual(P.GOLD_CHECK_CAP, 69)
        self.assertEqual(P.GOLD_MARKET_MIN, 70)

    def test_wired_after_cup_guardrail_in_batch_loop(self):
        """فحص بنيوي: الحارس يُستدعى في حلقة الدفعات بعد حارس الكأس وقبل الحفظ."""
        i_cup = SRC.find("apply_cup_guardrail(entry)")
        i_gold = SRC.find("apply_gold_market_check(entry, is_enriched, budget)")
        i_store = SRC.find('store["pending"][m["fid"]] = entry')
        self.assertTrue(0 < i_cup < i_gold < i_store)

    def test_digest_line(self):
        st = P.GOLD_CHECK_STATS
        saved = dict(st)
        try:
            st.update({"checked": 3, "kept": 1, "capped": 2, "no_market": 1})
            line = P.gold_check_line()
            self.assertIn("3 ذهبية خفيفة", line)
            self.assertIn("أُبقيت 1", line)
            self.assertIn("خُفِّضت 2", line)
            st.update({"checked": 0})
            self.assertEqual(P.gold_check_line(), "")
        finally:
            st.update(saved)
        self.assertIn("gold_check_line()", SRC[SRC.find("cost = claude_cost_line()"):][:600])


class TestParseMarketRefactor(unittest.TestCase):
    """odds_context ما زال يخزّن احتمالات السوق على المباراة كما قبل التفكيك."""

    def test_parse_market_overround_removed(self):
        ph, pd, pa, *_ = P.parse_market(odds_rows(2.0, 3.5, 3.5))
        self.assertTrue(99 <= ph + pd + pa <= 101)   # تقريب كل احتمال على حدة (السلوك الأصلي)
        self.assertGreater(ph, pd)

    def test_odds_context_stores_market_fields(self):
        m = {"fid": "9"}
        orig = P._enrich_call
        P._enrich_call = lambda path, budget: odds_rows(1.5, 4.0, 6.0)
        try:
            txt = P.odds_context(m, {"used": 0})
        finally:
            P._enrich_call = orig
        self.assertIn("implied probabilities", txt)
        self.assertTrue(99 <= m["mkt_home"] + m["mkt_draw"] + m["mkt_away"] <= 101)


if __name__ == "__main__":
    unittest.main()


class TestGoldCheckTagSurvivesResolution(unittest.TestCase):
    """2026-09-12: بعد ثلاثة أيام من التحقق السوقي لم يحمل أي صف في resolved
    وسم gold_check — resolve_pending كان ينسخ قائمة حقول ثابتة تُسقطه، فتصبح
    إعادة القياس المسجَّلة (بعد ≥30 ذهبية خفيفة) مستحيلة. الوسم يجب أن ينجو."""

    def _finished(self, fid, date):
        return [{"fixture": {"id": int(fid), "status": {"short": "FT"}},
                 "goals": {"home": 2, "away": 0},
                 "score": {"fulltime": {"home": 2, "away": 0}},
                 "teams": {"home": {"logo": ""}, "away": {"logo": ""}},
                 "league": {"logo": ""}}]

    def test_tag_copied_to_resolved(self):
        orig = P.api_football
        P.api_football = lambda path: self._finished("777001", "2026-09-11")
        try:
            store = {"pending": {"777001": {
                "fid": "777001", "date": "2026-09-11", "home": "A", "away": "B",
                "league": "L", "pick": "home", "confidence": 69,
                "prob_home": 72, "prob_draw": 16, "prob_away": 12,
                "gold_check": "capped:market_66", "mkt_home": 66}}, "resolved": []}
            P.resolve_pending(store)
        finally:
            P.api_football = orig
        self.assertEqual(len(store["resolved"]), 1)
        self.assertEqual(store["resolved"][0].get("gold_check"), "capped:market_66")
        self.assertTrue(store["resolved"][0]["correct"])

    def test_untagged_row_stays_none(self):
        """صف بلا فحص (مُخصَّب أو دون 70) يحمل None لا نصاً مُخترَعاً."""
        orig = P.api_football
        P.api_football = lambda path: self._finished("777002", "2026-09-11")
        try:
            store = {"pending": {"777002": {"fid": "777002", "date": "2026-09-11",
                     "home": "A", "away": "B", "pick": "away", "confidence": 55}},
                     "resolved": []}
            P.resolve_pending(store)
        finally:
            P.api_football = orig
        self.assertIn("gold_check", store["resolved"][0])
        self.assertIsNone(store["resolved"][0]["gold_check"])

    def test_structural(self):
        import inspect
        self.assertIn('"gold_check": p.get("gold_check")', inspect.getsource(P.resolve_pending))

