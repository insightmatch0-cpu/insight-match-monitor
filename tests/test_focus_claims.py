# -*- coding: utf-8 -*-
"""✅ حارس تتبع الادعاءات الحي للمفضلة (طلب المالك 2026-08-27: تأكيد فوري
لكل ادعاء يتحقق أثناء اللعب + حصاد كامل عند الصافرة).

العقد: ادعاءات حتمية فقط (اتجاه المحرك + أسواق فوق العتبة)، ادعاءات
الوقوع تُؤكَّد لحظة استحالة تراجعها، ادعاءات الغياب لا تُحسم قبل النهاية،
والكتابة للقرص لحظة الإرسال (درس Drukpa/Cracovia)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import monitor as M  # noqa: E402

SRC = (Path(__file__).resolve().parent.parent / "monitor.py").read_text(
    encoding="utf-8")


def _wl(claims):
    return {"matches": {"9": {"label": "برشلونة 🆚 أتلتيك بلباو",
                              "claims": claims}}}


class TestFocusClaimUpdates(unittest.TestCase):
    def setUp(self):
        self.sent = []
        orig = M.send_telegram
        M.send_telegram = lambda t, **k: self.sent.append(t)
        self.addCleanup(lambda: setattr(M, "send_telegram", orig))
        orig_f = M.WATCHLIST_FILE
        M.WATCHLIST_FILE = Path(tempfile.mkdtemp()) / "watchlist.json"
        self.addCleanup(lambda: setattr(M, "WATCHLIST_FILE", orig_f))

    def test_over25_confirms_at_third_goal_immediately(self):
        wl = _wl([{"key": "over25", "text": "فوق 2.5", "status": "pending"}])
        M.focus_claim_updates("9", wl, 2, 1)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("✅ تحقق ادعاء مبكراً", self.sent[0])
        self.assertEqual(wl["matches"]["9"]["claims"][0]["status"], "hit")
        # وكُتب للقرص لحظة الإرسال
        saved = json.loads(M.WATCHLIST_FILE.read_text(encoding="utf-8"))
        self.assertEqual(saved["matches"]["9"]["claims"][0]["status"], "hit")

    def test_btts_yes_confirms_when_both_score(self):
        wl = _wl([{"key": "btts_yes", "text": "كلاهما يسجل",
                   "status": "pending"}])
        M.focus_claim_updates("9", wl, 1, 1)
        self.assertEqual(len(self.sent), 1)

    def test_absence_claims_never_confirm_before_final(self):
        wl = _wl([{"key": "under25", "text": "تحت 2.5", "status": "pending"},
                  {"key": "btts_no", "text": "لن يسجلا", "status": "pending"},
                  {"key": "result", "side": "home", "text": "فوز",
                   "status": "pending"}])
        M.focus_claim_updates("9", wl, 1, 0)
        self.assertEqual(self.sent, [], "لا حسم قبل الصافرة لادعاءات الغياب")
        for c in wl["matches"]["9"]["claims"]:
            self.assertEqual(c["status"], "pending")

    def test_final_settles_everything_and_sends_summary(self):
        wl = _wl([{"key": "result", "side": "home", "text": "فوز برشلونة",
                   "status": "pending"},
                  {"key": "over25", "text": "فوق 2.5", "status": "pending"},
                  {"key": "btts_yes", "text": "كلاهما", "status": "pending"}])
        M.focus_claim_updates("9", wl, 2, 0, final=True)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("🏁 حصاد الادعاءات", self.sent[0])
        self.assertIn("أصاب 1 من 3", self.sent[0])
        self.assertTrue(wl["matches"]["9"].get("claims_settled"))

    def test_no_duplicate_confirmation(self):
        wl = _wl([{"key": "over25", "text": "فوق 2.5", "status": "pending"}])
        M.focus_claim_updates("9", wl, 2, 1)
        M.focus_claim_updates("9", wl, 3, 1)
        self.assertEqual(len(self.sent), 1, "التأكيد مرة واحدة فقط")

    def test_kill_switch(self):
        orig = M.FOCUS_CLAIMS
        M.FOCUS_CLAIMS = False
        self.addCleanup(lambda: setattr(M, "FOCUS_CLAIMS", orig))
        wl = _wl([{"key": "over25", "text": "فوق", "status": "pending"}])
        self.assertFalse(M.focus_claim_updates("9", wl, 3, 0))
        self.assertEqual(self.sent, [])


class TestClaimGeneration(unittest.TestCase):
    def test_market_claims_require_threshold(self):
        def fake_api(path):
            return [{"bookmakers": [{"bets": [
                {"name": "Goals Over/Under", "values": [
                    {"value": "Over 2.5", "odd": "1.40"},
                    {"value": "Under 2.5", "odd": "3.00"}]},
                {"name": "Both Teams Score", "values": [
                    {"value": "Yes", "odd": "2.05"},
                    {"value": "No", "odd": "1.85"}]},
            ]}]}]
        orig = M.api_football
        M.api_football = fake_api
        self.addCleanup(lambda: setattr(M, "api_football", orig))
        v2 = {"pick": "home", "confidence": 75, "ar_home": "برشلونة",
              "ar_away": "بلباو"}
        claims = M.build_focus_claims("9", v2)
        keys = {c["key"] for c in claims}
        self.assertIn("result", keys)
        self.assertIn("over25", keys, "فوق 2.5 ضمنية ~68% تتجاوز العتبة")
        self.assertNotIn("btts_yes", keys, "BTTS ~47% تحت العتبة — لا ادعاء")
        self.assertNotIn("btts_no", keys)

    def test_api_failure_still_yields_result_claim(self):
        orig = M.api_football
        M.api_football = lambda p: (_ for _ in ()).throw(RuntimeError("down"))
        self.addCleanup(lambda: setattr(M, "api_football", orig))
        claims = M.build_focus_claims("9", {"pick": "away", "confidence": 60})
        self.assertEqual([c["key"] for c in claims], ["result"])


class TestStructuralWiring(unittest.TestCase):
    def test_all_four_call_sites_present(self):
        self.assertEqual(SRC.count("focus_claim_updates(fid, wl_data, gh, ga)"),
                         2, "موقعا الهدف: المسار العادي + السريع")
        self.assertEqual(SRC.count("final=True) or wl_dirty)"), 2,
                         "موقعا الصافرة: المسار العادي + السريع")

    def test_claims_generated_with_brief(self):
        self.assertIn('entry["claims"] = build_focus_claims', SRC)


if __name__ == "__main__":
    unittest.main()
