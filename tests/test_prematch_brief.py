# -*- coding: utf-8 -*-
"""⭐ حارس موجز ما قبل المباراة (طلب المالك 2026-08-27: موجز أدلة جميل
رفيع المستوى قبل كل مباراة مفضلة — «always keep it in this way»).

العقد: حتمي بالكامل (صفر Claude)، يسبق تقرير السيناريوهات، علمه الخاص
يُحفظ لحظة الإرسال (درس Drukpa/Cracovia)، وفشله الجزئي لا يحجبه."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import monitor as M  # noqa: E402

SRC = (Path(__file__).resolve().parent.parent / "monitor.py").read_text(
    encoding="utf-8")


class TestPrematchBrief(unittest.TestCase):
    def setUp(self):
        # لا نداءات API حقيقية في الاختبار
        self._orig_api = M.api_football
        M.api_football = lambda *a, **k: []
        self.addCleanup(lambda: setattr(M, "api_football", self._orig_api))

    def _pend(self):
        v2 = {"home": "Barcelona", "away": "Athletic Club",
              "ar_home": "برشلونة", "ar_away": "أتلتيك بلباو",
              "ar_league": "الدوري الإسباني", "pick": "home",
              "confidence": 75, "prob_home": 75, "prob_draw": 15,
              "prob_away": 10, "reason": "سبب اختباري"}
        v1 = {"pick": "home", "confidence": 70}
        return v2, v1

    def test_brief_contains_both_engines_and_reason(self):
        v2, v1 = self._pend()
        txt = M.prematch_brief("1", {"label": "برشلونة 🆚 أتلتيك بلباو"},
                               v2, v1, 44)
        self.assertIn("⭐ موجز ما قبل المباراة", txt)
        self.assertIn("المحرك 2: فوز برشلونة — 75%", txt)
        self.assertIn("المحرك 1: فوز برشلونة — 70%", txt)
        self.assertIn("قراءة المحرك: سبب اختباري", txt)

    def test_market_line_from_odds_with_overround_removed(self):
        v2, v1 = self._pend()
        def fake_api(path):
            if path.startswith("odds"):
                return [{"bookmakers": [{"bets": [{"name": "Match Winner",
                        "values": [{"value": "Home", "odd": "1.30"},
                                   {"value": "Draw", "odd": "5.50"},
                                   {"value": "Away", "odd": "9.00"}]}]}]}]
            return []
        M.api_football = fake_api
        txt = M.prematch_brief("1", {}, v2, v1, 40)
        self.assertIn("السوق: برشلونة", txt)
        self.assertIn("%", txt)

    def test_partial_failure_never_blocks_brief(self):
        v2, v1 = self._pend()
        def boom(path):
            raise RuntimeError("api down")
        M.api_football = boom
        txt = M.prematch_brief("1", {}, v2, v1, 40)
        self.assertIn("⭐ موجز ما قبل المباراة", txt)

    def test_brief_is_deterministic_no_claude(self):
        import inspect
        src = inspect.getsource(M.prematch_brief)
        self.assertNotIn("analyze_with_claude", src)
        self.assertNotIn("CLAUDE", src)

    def test_structural_sent_before_claude_with_own_durable_flag(self):
        body = SRC.split("def prematch_reports(")[1]
        i_brief = body.find("prematch_brief(fid")
        i_claude = body.find("analyze_with_claude(")
        self.assertTrue(0 < i_brief < i_claude,
                        "الموجز يُرسل قبل نداء Claude — يصل ولو فشل التقرير")
        gate = body[:i_claude]
        self.assertIn('entry.get("brief_sent")', gate)
        self.assertIn('entry["brief_sent"] = True', gate)
        # الحفظ الفوري لحظة الإرسال (درس المخزنين 2026-08-21)
        seg = body[i_brief:i_claude]
        self.assertIn("WATCHLIST_FILE.write_text", seg)

    def test_kill_switch_exists(self):
        self.assertTrue(hasattr(M, "PREMATCH_BRIEF"))
        self.assertIn("if PREMATCH_BRIEF and", SRC)


if __name__ == "__main__":
    unittest.main()
