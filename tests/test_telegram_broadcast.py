# -*- coding: utf-8 -*-
"""اختبارات بث تيليجرام متعدد المستقبِلين — طلب المالك 2026-08-14.

التصميم المحروس هنا هو **فصل البث عن التحكم**:
- الخارج (بث): التنبيهات تصل إلى المالك + 2-3 أجهزة/أشخاص إضافيين عبر
  السرّ الاختياري TELEGRAM_BROADCAST_IDS.
- الداخل (تحكم): watchlist.py يبقى يقرأ الأوامر من TELEGRAM_CHAT_ID حصراً —
  لا أحد غير المالك يغيّر قائمة التركيز أو يسجّل توقعاً باسمه. سجل توقعات
  المالك في predictions_user.json يجب ألا يتلوث، وأزرار التوقع له وحده.

الاختبار الحاسم: توسيع البث يجب ألا يفتح قناة التحكم ولو بمقدار معرّف واحد.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import api_guard as G
import monitor as M
import predict as V1
import predict_v2 as P
import watchlist as W

OWNER = "111111"
DEVICE_2 = "222222"
DEVICE_3 = "333333"
STRANGER = "999999"


class FakeTelegramResponse:
    """رد تيليجرام مزيّف — البنية الحقيقية: {"ok":true} أو
    {"ok":false,"error_code":N,"description":"..."}."""

    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if self._payload is None:
            raise ValueError("لا JSON في الرد")
        return self._payload


class BroadcastHarness(unittest.TestCase):
    """يلتقط كل نداء sendMessage بدل إرساله.

    ثلاث طرق لتزييف الفشل، وكلها واقعية:
    - fail_for      : استثناء شبكة (لا رد أصلاً)
    - refuse_for    : رد HTTP بجسم {"ok": false, ...} — الحالة الأشيع فعلياً
    - bodyless      : رد بلا JSON صالح (وسيط غريب) — يجب أن يُعدّ تسليماً
    """

    def setUp(self):
        self.sent = []          # (chat_id, text)
        self.fail_for = set()   # {chat_id} → استثناء شبكة
        self.refuse_for = {}    # {chat_id: (error_code, description)}
        self.bodyless = set()   # {chat_id} → رد بلا جسم مقروء
        G.reset_delivery_flags()
        self.addCleanup(G.reset_delivery_flags)

        # عزل state.json إلزامي منذ 2026-08-15: الإرسال صار يسجّل نتيجة
        # التسليم في الحالة، فبلا هذا العزل تكتب الاختبارات في ملف الإنتاج
        # المُلتزَم — وتلوّثه التشغيلة التالية بـ git add -A.
        G.detach_state()
        self.addCleanup(G.detach_state)
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text("{}", encoding="utf-8")
        orig_state = G.STATE_FILE
        G.STATE_FILE = tmp
        self.addCleanup(lambda: (setattr(G, "STATE_FILE", orig_state),
                                 tmp.unlink(missing_ok=True)))

        harness = self

        class FakeRequests:
            @staticmethod
            def post(url, json=None, timeout=None):
                cid = (json or {}).get("chat_id")
                if cid in harness.fail_for:
                    raise RuntimeError(f"المستقبِل {cid} حظر البوت")
                if cid in harness.refuse_for:
                    code, desc = harness.refuse_for[cid]
                    return FakeTelegramResponse(
                        {"ok": False, "error_code": code, "description": desc},
                        status_code=code,
                    )
                harness.sent.append((cid, (json or {}).get("text")))
                if cid in harness.bodyless:
                    return FakeTelegramResponse(None)
                return FakeTelegramResponse({"ok": True, "result": {}})

        orig = G.requests
        G.requests = FakeRequests
        self.addCleanup(lambda: setattr(G, "requests", orig))

    def recipients(self):
        """المستقبِلون الذين وصلهم **نص البث** نفسه.

        نستبعد رسائل إنذار فشل التسليم (2026-08-15): هي رسالة إدارية تذهب
        إلى المالك وحده بعد الفشل، وليست جزءاً من البث الذي تقيسه هذه
        الاختبارات.
        """
        return [cid for cid, t in self.sent
                if not str(t or "").startswith("📡 رسالة لم تصل")]


# ================== قائمة المستقبِلين ==================
class TestRecipientList(unittest.TestCase):

    def test_absent_secret_means_owner_only(self):
        """الغياب = السلوك القديم تماماً."""
        self.assertEqual(G.broadcast_ids(OWNER, None), [OWNER])

    def test_empty_secret_means_owner_only(self):
        self.assertEqual(G.broadcast_ids(OWNER, ""), [OWNER])
        self.assertEqual(G.broadcast_ids(OWNER, "   "), [OWNER])

    def test_multiple_ids_are_parsed(self):
        self.assertEqual(G.broadcast_ids(OWNER, f"{DEVICE_2},{DEVICE_3}"),
                         [OWNER, DEVICE_2, DEVICE_3])

    def test_whitespace_and_trailing_commas_tolerated(self):
        """السرّ يُلصق يدوياً — نتحمل الفراغات والفواصل الزائدة والأسطر."""
        self.assertEqual(G.broadcast_ids(OWNER, f" {DEVICE_2} , ,{DEVICE_3},\n"),
                         [OWNER, DEVICE_2, DEVICE_3])

    def test_owner_is_always_first_and_never_duplicated(self):
        ids = G.broadcast_ids(OWNER, f"{DEVICE_2},{OWNER},{DEVICE_2}")
        self.assertEqual(ids, [OWNER, DEVICE_2])

    def test_owner_receives_even_if_secret_lists_only_others(self):
        """المالك لا يُستبدل أبداً — البث إضافة لا إحلال."""
        self.assertIn(OWNER, G.broadcast_ids(OWNER, DEVICE_2))


# ================== سلوك الإرسال ==================
class TestBroadcastDelivery(BroadcastHarness):

    def test_broadcasts_to_every_id(self):
        G.send_telegram_multi("tok", OWNER, f"{DEVICE_2},{DEVICE_3}", "تنبيه")
        self.assertEqual(self.recipients(), [OWNER, DEVICE_2, DEVICE_3])

    def test_absent_secret_reproduces_old_behaviour_exactly(self):
        G.send_telegram_multi("tok", OWNER, "", "تنبيه")
        self.assertEqual(self.sent, [(OWNER, "تنبيه")])

    def test_one_failing_recipient_does_not_stop_the_rest(self):
        """جهاز حظر البوت أو معرّف خاطئ — البقية يجب أن تصل."""
        self.fail_for = {DEVICE_2}
        result = G.send_telegram_multi("tok", OWNER, f"{DEVICE_2},{DEVICE_3}", "تنبيه")
        self.assertEqual(self.recipients(), [OWNER, DEVICE_3])
        # العقد الجديد: نتيجة منظّمة بدل العدد المجرد — نفس القوة، تفصيل أكثر
        self.assertEqual(result["delivered"], 2)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["sent"], [G.mask_id(OWNER), G.mask_id(DEVICE_3)])
        self.assertEqual([f["id"] for f in result["failed"]], [G.mask_id(DEVICE_2)])

    def test_failing_recipient_never_raises(self):
        """التنبيه خدمة مساعدة — لا يجوز أن يُسقط التشغيلة."""
        self.fail_for = {OWNER, DEVICE_2}
        try:
            G.send_telegram_multi("tok", OWNER, DEVICE_2, "تنبيه")
        except Exception as e:
            self.fail(f"فشل الإرسال كسر التشغيلة: {e}")

    def test_same_text_reaches_everyone(self):
        G.send_telegram_multi("tok", OWNER, DEVICE_2, "نص واحد")
        self.assertEqual({text for _cid, text in self.sent}, {"نص واحد"})


class TestEnginesBroadcast(BroadcastHarness):
    """المحركان يمرّان فعلاً بالبث — لا نسخة قديمة تُرسل للمالك وحده."""

    def _patch(self, module, raw):
        orig = module.TELEGRAM_BROADCAST_IDS, module.TELEGRAM_CHAT_ID
        module.TELEGRAM_BROADCAST_IDS = raw
        module.TELEGRAM_CHAT_ID = OWNER
        self.addCleanup(lambda: (
            setattr(module, "TELEGRAM_BROADCAST_IDS", orig[0]),
            setattr(module, "TELEGRAM_CHAT_ID", orig[1])))

    def test_monitor_broadcasts(self):
        self._patch(M, f"{DEVICE_2},{DEVICE_3}")
        M.send_telegram("تنبيه هدف")
        self.assertEqual(self.recipients(), [OWNER, DEVICE_2, DEVICE_3])

    def test_predict_v2_broadcasts(self):
        self._patch(P, DEVICE_2)
        P.send_telegram("نشرة الصباح")
        self.assertEqual(self.recipients(), [OWNER, DEVICE_2])

    def test_predict_v1_broadcasts(self):
        """حادثة 2026-08-17: predict.py استعمل TELEGRAM_BROADCAST_IDS في
        الإرسال دون تعريفه (NameError) فسقطت نشرة المحرك 1 ثلاثة أيام بصمت
        بينما التوقعات تُحفظ — هذا الاختبار يستدعي مسار V1 فعلياً فيمسك
        أي اسم غير معرّف قبل الدمج."""
        self._patch(V1, DEVICE_2)
        V1.send_telegram("نشرة المحرك 1")
        self.assertEqual(self.recipients(), [OWNER, DEVICE_2])

    def test_predict_v1_long_digest_broadcasts(self):
        """النشرة الحقيقية تمر عبر send_telegram_long — نغطي نفس المسار
        الذي انهار في التشغيلة 2026-08-17."""
        self._patch(V1, DEVICE_2)
        V1.send_telegram_long("\n".join(f"سطر {i} " + "x" * 200 for i in range(40)))
        self.assertGreater(len(self.sent), 2, "لم يقع تقسيم — الاختبار بلا معنى")
        self.assertEqual(set(self.recipients()), {OWNER, DEVICE_2})

    def test_every_sender_module_defines_the_broadcast_secret(self):
        """حارس بنيوي: أي وحدة تمرر TELEGRAM_BROADCAST_IDS إلى البث يجب أن
        تقرأه من البيئة عند الاستيراد — النسيان في وحدة واحدة هو جوهر الحادثة."""
        for module in (M, V1, P):
            src = Path(module.__file__).read_text(encoding="utf-8")
            if "TELEGRAM_BROADCAST_IDS," in src or "TELEGRAM_BROADCAST_IDS)" in src:
                self.assertIn(
                    'os.environ.get("TELEGRAM_BROADCAST_IDS"', src,
                    f"{Path(module.__file__).name} يستعمل سرّ البث دون تعريفه")

    def test_monitor_without_secret_is_owner_only(self):
        self._patch(M, "")
        M.send_telegram("تنبيه")
        self.assertEqual(self.recipients(), [OWNER])

    def test_long_digest_broadcasts_every_chunk(self):
        """send_telegram_long يقسم ثم يبث — كل جزء يصل كل المستقبِلين."""
        self._patch(P, DEVICE_2)
        P.send_telegram_long("\n".join(f"سطر {i} " + "x" * 200 for i in range(40)))
        self.assertGreater(len(self.sent), 2, "لم يقع تقسيم — الاختبار بلا معنى")
        self.assertEqual(set(self.recipients()), {OWNER, DEVICE_2})


# ================== التحكم يبقى للمالك حصراً ==================
class TestControlStaysOwnerOnly(unittest.TestCase):
    """فصل البث عن التحكم: البث اتسع، والتحكم لم يتسع معه."""

    def setUp(self):
        self.updates = []
        harness = self

        class FakeResponse:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"result": harness.updates}

        class FakeRequests:
            @staticmethod
            def get(url, params=None, timeout=None):
                return FakeResponse

        orig_req, orig_chat = W.requests, W.TELEGRAM_CHAT_ID
        W.requests = FakeRequests
        W.TELEGRAM_CHAT_ID = OWNER
        self.addCleanup(lambda: (setattr(W, "requests", orig_req),
                                 setattr(W, "TELEGRAM_CHAT_ID", orig_chat)))

    @staticmethod
    def _msg(uid, chat_id, text):
        return {"update_id": uid, "message": {"chat": {"id": chat_id}, "text": text}}

    @staticmethod
    def _callback(uid, chat_id, data):
        return {"update_id": uid,
                "callback_query": {"id": "cb1", "data": data,
                                   "message": {"chat": {"id": chat_id}}}}

    def test_owner_command_is_accepted(self):
        self.updates = [self._msg(1, int(OWNER), "الريال وبرشلونة")]
        items, _ = W.get_new_messages(0)
        self.assertEqual(len(items), 1)

    def test_stranger_command_is_rejected(self):
        self.updates = [self._msg(1, int(STRANGER), "امسح القائمة")]
        items, _ = W.get_new_messages(0)
        self.assertEqual(items, [], "غير المالك غيّر قائمة التركيز")

    def test_broadcast_recipient_cannot_command(self):
        """جهاز البث يستقبل التنبيهات ولا يملك حق الأمر — جوهر الفصل."""
        self.updates = [self._msg(1, int(DEVICE_2), "امسح القائمة")]
        items, _ = W.get_new_messages(0)
        self.assertEqual(items, [])

    def test_broadcast_recipient_cannot_press_prediction_buttons(self):
        """أزرار التوقع للمالك وحده — predictions_user.json يجب ألا يتلوث."""
        self.updates = [self._callback(1, int(DEVICE_2), "p|123|home")]
        items, _ = W.get_new_messages(0)
        self.assertEqual(items, [])

    def test_owner_button_still_works(self):
        self.updates = [self._callback(1, int(OWNER), "p|123|home")]
        items, _ = W.get_new_messages(0)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "callback")

    def test_mixed_batch_keeps_only_the_owner(self):
        self.updates = [
            self._msg(1, int(STRANGER), "امسح القائمة"),
            self._msg(2, int(DEVICE_2), "سجل لي فوز الريال"),
            self._msg(3, int(OWNER), "الريال وبرشلونة"),
        ]
        items, last = W.get_new_messages(0)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["text"], "الريال وبرشلونة")
        self.assertEqual(last, 3, "offset يجب أن يتقدم فوق رسائل الغرباء أيضاً")

    def test_watchlist_does_not_read_the_broadcast_secret(self):
        """حارس بنيوي: لو أضاف أحد البث إلى قناة التحكم يسقط هذا الاختبار."""
        source = Path(W.__file__).read_text(encoding="utf-8")
        self.assertNotIn("TELEGRAM_BROADCAST_IDS", source)


if __name__ == "__main__":
    unittest.main()
