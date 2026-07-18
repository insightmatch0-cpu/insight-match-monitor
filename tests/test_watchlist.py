# -*- coding: utf-8 -*-
"""اختبارات قائمة التركيز — التعرف الاحتياطي على أسماء الفرق (إصلاح 2026-07-15)."""

import sys
import unittest
from datetime import timedelta
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


class TestPickCallback(unittest.TestCase):
    """زر التوقع (سباق الدقة الثلاثي) — إصلاح فقدان التصويت الصامت 2026-07-18.

    البلاغ: أُضيفت فرنسا للقائمة، وصلت الأزرار، لكن الضغط لم يُسجّل ولم يردّ.
    """

    CANDS = {"1591865": {"fid": "1591865", "home": "France", "away": "England",
                         "ar_home": "فرنسا", "ar_away": "إنجلترا", "league": "WC",
                         "kickoff": (W.now_utc() + timedelta(hours=4)).isoformat(),
                         "date": "2026-07-18"}}

    def test_valid_tap_records_and_replies(self):
        saved = {}
        orig = W.save_json
        W.save_json = lambda path, data: saved.update({str(path): data})
        try:
            reply = W.handle_pick_callback("pick|1591865|home", self.CANDS)
        finally:
            W.save_json = orig
        self.assertTrue(reply, "الضغطة الصحيحة يجب أن تُرجع تأكيداً غير فارغ")
        user = saved.get("predictions_user.json") or {}
        self.assertIn("1591865", user.get("pending", {}))
        self.assertEqual(user["pending"]["1591865"]["pick"], "home")

    def test_unknown_fid_returns_empty_not_crash(self):
        """fid غير معروف → "" (فيرسل main رسالة خطأ واضحة، لا صمت)."""
        self.assertEqual(W.handle_pick_callback("pick|999999|home", self.CANDS), "")

    def test_malformed_payload_returns_empty(self):
        for bad in ("garbage", "pick|1591865", "pick|1591865|sideways", "x|1591865|home"):
            self.assertEqual(W.handle_pick_callback(bad, self.CANDS), "")

    def test_watchlist_fallback_rescues_missing_match(self):
        """مباراة في القائمة لكن غابت عن توقعات الـ24 ساعة → تبقى قابلة للتصويت."""
        wl = {"matches": {"1591865": {"home": "France", "away": "England",
                                      "date": "2026-07-18",
                                      "kickoff": (W.now_utc()+timedelta(hours=4)).isoformat()}}}
        cands = W.candidate_from_watchlist(wl)
        self.assertIn("1591865", cands)
        self.assertEqual(cands["1591865"]["home"], "France")


if __name__ == "__main__":
    unittest.main()
