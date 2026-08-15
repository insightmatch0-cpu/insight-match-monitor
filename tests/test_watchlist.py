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


class TestControlChannelFiltering(unittest.TestCase):
    """حادثة «تحقق» من الجهاز الثاني (2026-08-15): الأمر من خارج قناة
    التحكم يُتجاهل بالتصميم — لكن التجاهل يجب أن يُطبع لا أن يصمت،
    والتحديث المتجاهَل يجب أن يقدّم العدّاد كي لا يُعاد قراءته للأبد."""

    def _updates(self, chat_id, text=None, update_id=100):
        u = {"update_id": update_id,
             "message": {"chat": {"id": chat_id}, "text": text}}
        return [u]

    def _run(self, updates):
        import unittest.mock as mock
        resp = mock.Mock()
        resp.json.return_value = {"result": updates}
        resp.raise_for_status.return_value = None
        with mock.patch.object(W.requests, "get", return_value=resp), \
             mock.patch.object(W, "TELEGRAM_CHAT_ID", "111"):
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                items, last_id = W.get_new_messages(0)
        return items, last_id, buf.getvalue()

    def test_owner_text_is_accepted(self):
        items, last_id, out = self._run(self._updates("111", "تحقق"))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["text"], "تحقق")
        self.assertEqual(last_id, 100)

    def test_foreign_chat_is_ignored_but_offset_advances_and_logged(self):
        """جوهر الحادثة: «تحقق» من الجهاز الثاني — يُستهلك ويُتجاهل ويُطبع."""
        items, last_id, out = self._run(self._updates("222", "تحقق"))
        self.assertEqual(items, [])
        self.assertEqual(last_id, 100, "العدّاد يجب أن يتقدم وإلا أُعيدت القراءة للأبد")
        self.assertIn("تجاهلت", out, "التجاهل الصامت هو ما جعل الحادثة غامضة")
        self.assertNotIn("222", out, "لا معرّفات في السجل — المستودع عام (قاعدة 3)")
        self.assertNotIn("تحقق", out.replace("تجاهلت", ""), "لا نصوص رسائل في السجل")

    def test_owner_photo_without_text_is_ignored_and_logged(self):
        items, last_id, out = self._run(self._updates("111", None))
        self.assertEqual(items, [])
        self.assertIn("تجاهلت", out)

    def test_silence_when_nothing_ignored(self):
        _, _, out = self._run(self._updates("111", "مسح"))
        self.assertNotIn("تجاهلت", out)
