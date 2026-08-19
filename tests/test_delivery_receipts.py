# -*- coding: utf-8 -*-
"""اختبارات تأكيد التسليم الذاتي — طلب المالك 2026-08-15.

الثغرة التي تغلقها هذه الطبقة: البث لجهازين كان يعمل، لكن النظام **يرمي رد
تيليجرام** على كل إرسال. فلو حظر أحد الأجهزة البوت غداً، أو حُذف، أو صار
المعرّف خاطئاً، لصمت النظام في اتجاه ذلك الجهاز **بلا أن يدري أحد** — نفس
فئة العطل الصامت التي كلّفت 19 ساعة في 14 أغسطس، مطبَّقة على قناة التسليم
بدل قناة البيانات.

ما يحرسه هذا الملف (المطلوب حرفياً من المالك):
(أ) رد ok:false يُسجَّل فشلاً ولا يكسر بقية المستقبِلين ولا التشغيلة.
(ب) فشل مستقبِل يولّد إنذاراً للمالك **مرة واحدة** خلال 6 ساعات لكل معرّف.
(ج) فشل معرّف المالك يطبع بوضوح ويخرج بحالة فشل (أحمر في صفحة Actions).
(د) سطر نبض التسليم يظهر التحذير حين X<Y ويظهر ✅ حين X=Y.
(هـ) أمر /تحقق يبث ويرد بالتفصيل ولا يستدعي Claude إطلاقاً.
(و) بلا سرّ البث يبقى السلوك مستقبِلاً واحداً — أي السلوك القديم حرفياً.

وحدّ صادق يُحرَس هنا أيضاً: تيليجرام يؤكد **التسليم** لا **القراءة**. لا
يجوز لأي رسالة أن تدّعي أن المستقبِل «رأى» أو «قرأ».
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import api_guard as G
import watchlist as W

OWNER = "111111"
DEVICE_2 = "222222"
DEVICE_3 = "333333"

FAKE_TOKEN = "1234567890:AAH-fake-telegram-bot-token-xyz"

# ردود الفشل الحقيقية من Telegram Bot API
BLOCKED = (403, "Forbidden: bot was blocked by the user")
NOT_FOUND = (400, "Bad Request: chat not found")
DEACTIVATED = (403, "Forbidden: user is deactivated")


class FakeTelegramResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if self._payload is None:
            raise ValueError("لا JSON في الرد")
        return self._payload


class DeliveryHarness(unittest.TestCase):
    """يعزل الطبقة تماماً: state.json مؤقت، لا شبكة، أعلام مصفَّرة."""

    def setUp(self):
        G.detach_state()
        self.addCleanup(G.detach_state)
        G.reset_delivery_flags()
        self.addCleanup(G.reset_delivery_flags)

        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text("{}", encoding="utf-8")
        orig_state = G.STATE_FILE
        G.STATE_FILE = tmp
        self.state_file = tmp
        self.addCleanup(lambda: (setattr(G, "STATE_FILE", orig_state),
                                 tmp.unlink(missing_ok=True)))

        self.sent = []          # (chat_id, text) — كل محاولة إرسال
        self.refuse = {}        # {chat_id: (error_code, description)}
        self.raise_for = set()  # {chat_id} → استثناء شبكة

        harness = self

        class FakeRequests:
            @staticmethod
            def post(url, json=None, timeout=None):
                cid = (json or {}).get("chat_id")
                harness.sent.append((cid, (json or {}).get("text")))
                if cid in harness.raise_for:
                    raise RuntimeError(f"شبكة مقطوعة نحو {cid}")
                if cid in harness.refuse:
                    code, desc = harness.refuse[cid]
                    return FakeTelegramResponse(
                        {"ok": False, "error_code": code, "description": desc},
                        status_code=code,
                    )
                return FakeTelegramResponse({"ok": True, "result": {}})

        orig_req = G.requests
        G.requests = FakeRequests
        self.addCleanup(lambda: setattr(G, "requests", orig_req))

        self._env_backup = {}
        for name, val in (("TELEGRAM_TOKEN", FAKE_TOKEN),
                          ("TELEGRAM_CHAT_ID", OWNER),
                          ("TELEGRAM_BROADCAST_IDS", "")):
            self._env_backup[name] = os.environ.get(name)
            os.environ[name] = val
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for name, old in self._env_backup.items():
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old

    def read_state(self) -> dict:
        return json.loads(self.state_file.read_text(encoding="utf-8"))

    def broadcast(self, raw=f"{DEVICE_2},{DEVICE_3}", text="تنبيه", **kw):
        return G.send_telegram_multi(FAKE_TOKEN, OWNER, raw, text, **kw)

    def alerts_to_owner(self):
        """رسائل إنذار فشل التسليم التي وصلت المالك."""
        return [t for cid, t in self.sent
                if cid == OWNER and t and t.startswith("📡 رسالة لم تصل")]


# ========== (أ) رد ok:false يُسجَّل فشلاً ولا يكسر البقية ==========
class TestFailureIsRead(DeliveryHarness):

    def test_ok_false_is_recorded_as_failure(self):
        """الثغرة الأصلية: هذا الرد كان يُرمى، فيُحسب الجهاز مستلماً وهو صامت."""
        self.refuse = {DEVICE_2: BLOCKED}
        r = self.broadcast()
        self.assertEqual(r["delivered"], 2)
        self.assertEqual(r["total"], 3)
        self.assertEqual([f["id"] for f in r["failed"]], [G.mask_id(DEVICE_2)])

    def test_failure_reason_is_human_arabic(self):
        """السبب بلغة يفهمها المالك، لا نص إنجليزي من الخادم."""
        self.refuse = {DEVICE_2: BLOCKED}
        self.assertEqual(self.broadcast()["failed"][0]["reason"], "الجهاز حظر البوت")

    def test_reason_distinguishes_fault_types(self):
        for code_desc, expected in ((BLOCKED, "الجهاز حظر البوت"),
                                    (NOT_FOUND, "معرّف غير موجود"),
                                    (DEACTIVATED, "الحساب معطّل")):
            with self.subTest(reason=expected):
                self.setUp()
                self.refuse = {DEVICE_2: code_desc}
                self.assertEqual(self.broadcast()["failed"][0]["reason"], expected)

    def test_failure_carries_an_action(self):
        """لا نخبره بالعطل فقط — نخبره بما يفعل."""
        self.refuse = {DEVICE_2: NOT_FOUND}
        self.assertTrue(self.broadcast()["failed"][0]["action"].strip())

    def test_one_failure_does_not_stop_the_rest(self):
        """الضمانة القائمة: البقية تصل مهما فشل واحد."""
        self.refuse = {DEVICE_2: BLOCKED}
        self.broadcast()
        delivered = [cid for cid, _t in self.sent if cid != DEVICE_2]
        self.assertIn(OWNER, delivered)
        self.assertIn(DEVICE_3, delivered)

    def test_failure_never_raises(self):
        """التنبيه خدمة مساعدة — لا يجوز أن يُسقط المحرك أبداً."""
        self.refuse = {OWNER: BLOCKED, DEVICE_2: NOT_FOUND, DEVICE_3: DEACTIVATED}
        try:
            with redirect_stdout(io.StringIO()):
                self.broadcast()
        except Exception as e:
            self.fail(f"فشل التسليم كسر التشغيلة: {e}")

    def test_network_exception_is_also_a_failure(self):
        self.raise_for = {DEVICE_2}
        r = self.broadcast()
        self.assertEqual([f["id"] for f in r["failed"]], [G.mask_id(DEVICE_2)])

    def test_network_error_text_never_leaks_the_full_id(self):
        """نص الاستثناء قد يحمل المعرّف الكامل — يجب أن يخرج مقنَّعاً."""
        self.raise_for = {DEVICE_2}
        buf = io.StringIO()
        with redirect_stdout(buf):
            r = self.broadcast()
        self.assertNotIn(DEVICE_2, r["failed"][0]["reason"])
        self.assertNotIn(DEVICE_2, buf.getvalue())

    def test_unreadable_body_counts_as_delivered(self):
        """غياب الدليل ليس دليل غياب: رد بلا جسم مقروء ولا كود خطأ = السلوك
        القديم (وصلت). العكس كان سينتج إنذارات كاذبة وخروجاً أحمر كاذباً."""
        harness = self

        class BodylessRequests:
            @staticmethod
            def post(url, json=None, timeout=None):
                harness.sent.append(((json or {}).get("chat_id"), None))
                return FakeTelegramResponse(None)

        G.requests = BodylessRequests
        r = self.broadcast()
        self.assertEqual(r["failed"], [])
        self.assertEqual(r["delivered"], 3)

    def test_ids_in_result_are_masked(self):
        """قاعدة الأسرار 3: سجل Actions و state.json كلاهما عام."""
        self.refuse = {DEVICE_2: BLOCKED}
        r = self.broadcast()
        blob = json.dumps(r, ensure_ascii=False)
        self.assertNotIn(OWNER, blob)
        self.assertNotIn(DEVICE_2, blob)

    def test_no_token_is_ever_printed(self):
        self.refuse = {DEVICE_2: BLOCKED}
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.broadcast()
        self.assertNotIn(FAKE_TOKEN, buf.getvalue())
        self.assertNotIn(DEVICE_2, buf.getvalue())


# ========== (ب) إنذار المالك مرة واحدة كل 6 ساعات لكل معرّف ==========
class TestFailureAlert(DeliveryHarness):

    def test_failed_recipient_alerts_the_owner(self):
        self.refuse = {DEVICE_2: BLOCKED}
        self.broadcast()
        alerts = self.alerts_to_owner()
        self.assertEqual(len(alerts), 1)
        self.assertIn(G.mask_id(DEVICE_2), alerts[0])
        self.assertIn("الجهاز حظر البوت", alerts[0])
        self.assertIn("المطلوب منك", alerts[0])

    def test_alert_goes_to_the_owner_alone(self):
        """الإنذار عن جهاز لا يُبث إلى الأجهزة — أحدها موضوع الرسالة أصلاً."""
        self.refuse = {DEVICE_2: BLOCKED}
        self.broadcast()
        alert_recipients = {cid for cid, t in self.sent
                            if t and t.startswith("📡 رسالة لم تصل")}
        self.assertEqual(alert_recipients, {OWNER})

    def test_second_failure_within_six_hours_is_silent(self):
        """إنذار لا إغراق: تشغيلة كل 10 دقائق كانت ستُرسل 36 رسالة في 6 ساعات."""
        self.refuse = {DEVICE_2: BLOCKED}
        self.broadcast()
        self.broadcast()
        self.broadcast()
        self.assertEqual(len(self.alerts_to_owner()), 1)

    def test_alert_returns_after_six_hours(self):
        self.refuse = {DEVICE_2: BLOCKED}
        self.broadcast()
        self.assertEqual(len(self.alerts_to_owner()), 1)
        # نُرجع ختم آخر إنذار 6 ساعات ودقيقة إلى الوراء
        state = self.read_state()
        kind = f"delivery_{G.mask_id(DEVICE_2)}"
        slot = state["api_alerts"][kind]
        slot["last"] = (G.now_utc() - timedelta(hours=6, minutes=1)).isoformat()
        self.state_file.write_text(json.dumps(state, ensure_ascii=False),
                                   encoding="utf-8")
        self.broadcast()
        self.assertEqual(len(self.alerts_to_owner()), 2)

    def test_throttle_is_per_recipient(self):
        """صمت جهاز لا يحجب إنذار جهاز آخر — لكل معرّف مهلته."""
        self.refuse = {DEVICE_2: BLOCKED, DEVICE_3: NOT_FOUND}
        self.broadcast()
        self.assertEqual(len(self.alerts_to_owner()), 2)

    def test_throttle_flag_is_persisted_immediately(self):
        """لو ماتت التشغيلة بعد الإنذار مباشرة لتكرر كل 10 دقائق."""
        self.refuse = {DEVICE_2: BLOCKED}
        self.broadcast()
        self.assertIn(f"delivery_{G.mask_id(DEVICE_2)}",
                      self.read_state().get("api_alerts", {}))

    def test_alert_never_claims_the_message_was_read(self):
        """حدّ صادق: تيليجرام يؤكد التسليم لا القراءة."""
        self.refuse = {DEVICE_2: BLOCKED}
        self.broadcast()
        for word in ("رآها", "قرأها", "شاهد"):
            self.assertNotIn(word, self.alerts_to_owner()[0])

    def test_rollback_key_silences_the_alert(self):
        os.environ["DELIVERY_ALERTS_ENABLED"] = "0"
        self.addCleanup(lambda: os.environ.pop("DELIVERY_ALERTS_ENABLED", None))
        self.refuse = {DEVICE_2: BLOCKED}
        self.broadcast()
        self.assertEqual(self.alerts_to_owner(), [])

    def test_alerting_does_not_recurse_when_the_alert_itself_fails(self):
        """الإنذار يُرسل بنفس طبقة الإرسال — لولا حارس التكرار لدار بلا نهاية."""
        self.refuse = {DEVICE_2: BLOCKED, OWNER: BLOCKED}
        with redirect_stdout(io.StringIO()):
            self.broadcast()
        self.assertLess(len(self.sent), 20, "حلقة إنذار لا نهائية")


# ========== (ج) فشل معرّف المالك: يطبع ويخرج بحالة فشل ==========
class TestOwnerUnreachable(DeliveryHarness):

    def _fail_owner(self):
        self.refuse = {OWNER: BLOCKED}
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.broadcast()
        return buf.getvalue()

    def test_owner_failure_prints_loudly(self):
        out = self._fail_owner()
        self.assertIn("🚨", out)
        self.assertIn("المالك", out)
        self.assertIn("الجهاز حظر البوت", out)
        self.assertIn(G.mask_id(OWNER), out)

    def test_owner_failure_raises_the_flag(self):
        self._fail_owner()
        self.assertTrue(G.owner_unreachable())

    def test_run_exits_with_failure_status(self):
        """الفشل الصاخب أفضل من الصمت: يجب أن تظهر التشغيلة حمراء."""
        self._fail_owner()
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                G.exit_if_owner_unreachable()
        self.assertEqual(cm.exception.code, 1)

    def test_healthy_run_does_not_exit(self):
        self.broadcast()
        self.assertFalse(G.owner_unreachable())
        G.exit_if_owner_unreachable()   # يجب ألا يرمي شيئاً

    def test_owner_failure_sends_no_telegram_alert(self):
        """لا سبيل للتبليغ عبر تيليجرام حين تكون قناة المالك نفسها مقطوعة."""
        self._fail_owner()
        self.assertEqual(self.alerts_to_owner(), [])

    def test_other_recipients_still_receive_when_owner_fails(self):
        self.refuse = {OWNER: BLOCKED}
        with redirect_stdout(io.StringIO()):
            self.broadcast()
        self.assertIn(DEVICE_3, [cid for cid, _t in self.sent])

    def test_rollback_key_prevents_the_red_exit(self):
        self._fail_owner()
        os.environ["DELIVERY_ALERTS_ENABLED"] = "0"
        self.addCleanup(lambda: os.environ.pop("DELIVERY_ALERTS_ENABLED", None))
        with redirect_stdout(io.StringIO()):
            G.exit_if_owner_unreachable()   # لا SystemExit

    def test_senders_call_the_exit_guard(self):
        """حارس بنيوي: كل مرسِل يلتزم بالخروج الأحمر — لو حُذف النداء يسقط."""
        root = Path(__file__).resolve().parent.parent
        for script in ("monitor.py", "predict.py", "predict_v2.py", "scan.py"):
            with self.subTest(script=script):
                self.assertIn("exit_if_owner_unreachable",
                              (root / script).read_text(encoding="utf-8"))

    def test_predict_workflows_save_data_even_when_red(self):
        """الخروج الأحمر يجب ألا يبتلع بيانات اليوم — نفس إصلاح 14 أغسطس."""
        root = Path(__file__).resolve().parent.parent / ".github" / "workflows"
        for wf in ("monitor.yml", "predict.yml", "predict_v2.yml"):
            with self.subTest(workflow=wf):
                text = (root / wf).read_text(encoding="utf-8")
                save = text[text.index("Save "):]
                head = save[:save.index("run:")]
                self.assertIn("if: always()", head,
                              f"{wf}: خطوة الحفظ بلا if: always() — "
                              "تشغيلة حمراء ستضيّع بيانات اليوم")


# ========== (د) نبض التسليم في النشرة الصباحية ==========
class TestDeliveryPulse(DeliveryHarness):

    def test_full_delivery_shows_a_tick(self):
        self.broadcast()
        self.assertEqual(G.delivery_line(), "📡 التسليم: 3 من 3 ✅")

    def test_partial_delivery_shows_a_visible_warning(self):
        self.refuse = {DEVICE_3: BLOCKED}
        self.broadcast(raw=DEVICE_3)
        self.assertEqual(G.delivery_line(), "⚠️ التسليم: 1 من 2 — جهاز لم يستلم")

    def test_two_missing_devices_are_pluralised(self):
        self.refuse = {DEVICE_2: BLOCKED, DEVICE_3: NOT_FOUND}
        self.broadcast()
        self.assertEqual(G.delivery_line(), "⚠️ التسليم: 1 من 3 — 2 أجهزة لم تستلم")

    def test_line_uses_latin_digits(self):
        """قاعدة اللغة 2: أرقام لاتينية دائماً."""
        self.broadcast()
        self.assertFalse(set(G.delivery_line()) & set("٠١٢٣٤٥٦٧٨٩"))

    def test_line_is_empty_without_any_record(self):
        """لا سطر مضلل قبل أول بث."""
        self.assertEqual(G.delivery_line(), "")

    def test_result_is_stored_under_its_own_state_key(self):
        """مفتاح مستقل: لا يمحو مانع التكرار ولا عدّاد الرصيد."""
        self.refuse = {DEVICE_2: BLOCKED}
        self.broadcast()
        d = self.read_state().get("delivery") or {}
        self.assertEqual(d["delivered"], 2)
        self.assertEqual(d["total"], 3)
        self.assertEqual([f["id"] for f in d["failed"]], [G.mask_id(DEVICE_2)])
        self.assertTrue(d.get("at"))

    def test_state_never_stores_a_full_chat_id(self):
        self.refuse = {DEVICE_2: BLOCKED}
        self.broadcast()
        blob = self.state_file.read_text(encoding="utf-8")
        self.assertNotIn(DEVICE_2, blob)
        self.assertNotIn(FAKE_TOKEN, blob)

    def test_delivery_record_survives_the_alert_write(self):
        """الإنذار يكتب api_alerts بعد الإرسال — يجب ألا يمحو مفتاح delivery."""
        self.refuse = {DEVICE_2: BLOCKED}
        self.broadcast()
        state = self.read_state()
        self.assertIn("delivery", state)
        self.assertIn("api_alerts", state)

    def test_rollback_key_removes_the_line(self):
        os.environ["DELIVERY_LINE"] = "0"
        self.addCleanup(lambda: os.environ.pop("DELIVERY_LINE", None))
        self.broadcast()
        self.assertEqual(G.delivery_line(), "")

    def test_digest_carries_the_pulse_line(self):
        """حارس بنيوي: السطر موصول فعلاً بنشرة المحرك 2 الصباحية."""
        src = (Path(__file__).resolve().parent.parent / "predict_v2.py"
               ).read_text(encoding="utf-8")
        self.assertIn("delivery_line", src)


# ========== (هـ) أمر /تحقق ==========
class TestVerifyCommand(DeliveryHarness):
    """يبث رسالة اختبار لكل المستقبِلين ثم يرد على المالك بالتفصيل."""

    def setUp(self):
        super().setUp()
        os.environ["TELEGRAM_BROADCAST_IDS"] = f"{DEVICE_2},{DEVICE_3}"

        self.owner_replies = []
        self.claude_calls = []
        self.updates = []
        harness = self

        class FakeResponse:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"result": harness.updates}

        class WatchlistRequests:
            @staticmethod
            def get(url, params=None, timeout=None):
                return FakeResponse

            @staticmethod
            def post(url, json=None, timeout=None):
                harness.owner_replies.append((json or {}).get("chat_id"))
                harness.owner_texts.append((json or {}).get("text"))
                return None

        self.owner_texts = []
        orig = (W.requests, W.TELEGRAM_CHAT_ID, W.TELEGRAM_TOKEN,
                W.WATCHLIST_FILE, W.interpret)
        W.requests = WatchlistRequests
        W.TELEGRAM_CHAT_ID = OWNER
        W.TELEGRAM_TOKEN = FAKE_TOKEN
        wl = Path(tempfile.mkstemp(suffix=".json")[1])
        W.WATCHLIST_FILE = wl

        def no_claude(message, candidates):
            harness.claude_calls.append(message)
            return {"action": "none", "fids": [], "picks": []}

        W.interpret = no_claude
        self.addCleanup(lambda: (
            setattr(W, "requests", orig[0]),
            setattr(W, "TELEGRAM_CHAT_ID", orig[1]),
            setattr(W, "TELEGRAM_TOKEN", orig[2]),
            setattr(W, "WATCHLIST_FILE", orig[3]),
            setattr(W, "interpret", orig[4]),
            wl.unlink(missing_ok=True),
        ))

    def _send_command(self, text="تحقق"):
        self.updates = [{"update_id": 1,
                         "message": {"chat": {"id": int(OWNER)}, "text": text}}]
        with redirect_stdout(io.StringIO()):
            W.main()

    def test_command_broadcasts_to_every_recipient(self):
        self._send_command()
        self.assertEqual([cid for cid, _t in self.sent],
                         [OWNER, DEVICE_2, DEVICE_3])

    def test_command_never_calls_claude(self):
        """أمر مباشر مثل «مسح» — بلا أي نداء Claude."""
        self._send_command()
        self.assertEqual(self.claude_calls, [])

    def test_reply_goes_to_the_owner_and_lists_every_recipient(self):
        self._send_command()
        self.assertEqual(set(self.owner_replies), {OWNER})
        reply = "\n".join(self.owner_texts)
        for cid in (OWNER, DEVICE_2, DEVICE_3):
            self.assertIn(G.mask_id(cid), reply)

    def test_reply_names_the_failure_and_its_reason(self):
        self.refuse = {DEVICE_2: NOT_FOUND}
        self._send_command()
        reply = "\n".join(self.owner_texts)
        self.assertIn("⚠️", reply)
        self.assertIn(G.mask_id(DEVICE_2), reply)
        self.assertIn("معرّف غير موجود", reply)
        self.assertIn("2 من 3", reply)

    def test_reply_confirms_success_when_all_arrive(self):
        self._send_command()
        reply = "\n".join(self.owner_texts)
        self.assertIn("✅", reply)
        self.assertIn("3 من 3", reply)

    def test_reply_states_the_honest_limit(self):
        """لا يدّعي القراءة أبداً — يقول «وصلت إلى الجهاز»."""
        self._send_command()
        reply = "\n".join(self.owner_texts)
        self.assertIn("وصلت إلى الجهاز", reply)
        for word in ("رآها", "قرأها"):
            self.assertNotIn(word, reply)

    def test_slash_form_works_too(self):
        self._send_command("/تحقق")
        self.assertEqual([cid for cid, _t in self.sent],
                         [OWNER, DEVICE_2, DEVICE_3])

    def test_ordinary_sentence_containing_the_word_is_not_the_command(self):
        """مطابقة تامة: «تحقق من مباراة الريال» رسالة تركيز لا أمر فحص.
        (منذ التشغيل المشترك 2026-08-19 يُبثّ تأكيد القائمة للجهازين، فالدليل
        على أن الأمر لم يُبتلع هو غياب بطاقة الفحص لا غياب الإرسال.)"""
        self._send_command("تحقق من مباراة الريال")
        self.assertEqual(len(self.claude_calls), 1, "لم يُفسَّر كرسالة تركيز")
        for _cid, t in self.sent:
            self.assertNotIn("التسليم", t or "", "الأمر ابتلع رسالة عادية")

    def test_verify_sends_no_duplicate_alert(self):
        """المالك طلب الفحص وسيصله التقرير — إنذار إضافي ضجيج مكرَّر."""
        self.refuse = {DEVICE_2: BLOCKED}
        self._send_command()
        self.assertEqual(self.alerts_to_owner(), [])

    def test_second_owner_device_may_run_the_command(self):
        """التشغيل المشترك (قرار المالك 2026-08-19): جهاز المالك الثاني يأمر
        مثل الأساسي — «يعملان كجهاز واحد». البطاقة نفسها تبقى للأساسي."""
        self.updates = [{"update_id": 1,
                         "message": {"chat": {"id": int(DEVICE_2)},
                                     "text": "تحقق"}}]
        with redirect_stdout(io.StringIO()):
            W.main()
        self.assertEqual([cid for cid, _t in self.sent],
                         [OWNER, DEVICE_2, DEVICE_3],
                         "أمر الجهاز الثاني لم يُنفَّذ")

    def test_command_is_documented_in_the_reply_menu(self):
        """قائمة الأوامر المفهومة تذكر الأمر الجديد."""
        src = Path(W.__file__).read_text(encoding="utf-8")
        self.assertIn("تحقق", src.split("لم أتعرف على مباريات")[1][:400])


# ========== (و) بلا سرّ البث: مستقبِل واحد — السلوك القديم حرفياً ==========
class TestNoSecretIsOldBehaviour(DeliveryHarness):

    def test_absent_secret_means_one_recipient(self):
        r = self.broadcast(raw="")
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["delivered"], 1)
        self.assertEqual([cid for cid, _t in self.sent], [OWNER])

    def test_empty_secret_means_one_recipient(self):
        self.assertEqual(self.broadcast(raw="   ")["total"], 1)
        self.assertEqual(self.broadcast(raw=None)["total"], 1)

    def test_pulse_line_reads_one_of_one(self):
        self.broadcast(raw="")
        self.assertEqual(G.delivery_line(), "📡 التسليم: 1 من 1 ✅")

    def test_no_alert_is_possible_without_extra_devices(self):
        """بلا أجهزة إضافية لا يوجد مستقبِل يفشل غير المالك — ولا إغراق."""
        self.broadcast(raw="")
        self.assertEqual(self.alerts_to_owner(), [])

    def test_verify_without_secret_checks_the_owner_alone(self):
        os.environ["TELEGRAM_BROADCAST_IDS"] = ""
        r = G.verify_delivery()
        self.assertEqual(r["total"], 1)
        self.assertIn("1 من 1", G.verify_report(r))


# ========== عزل الاختبارات عن ملفات الإنتاج ==========
class TestSuiteDoesNotDirtyProductionState(unittest.TestCase):
    """حارس دائم (ثغرة اكتُشفت أثناء بناء هذه الطبقة 2026-08-15).

    الإرسال صار يكتب نتيجة التسليم في state.json، فأي اختبار لا يعزل الملف
    كان يكتب في ملف الإنتاج المُلتزَم — ثم يبتلعه `git add -A` في التشغيلة
    التالية فيلوّث حالة حقيقية ببيانات وهمية. الملف مكتوب بالبوت ومُلتزَم
    تلقائياً، فلا أحد كان سيلاحظ. هذا الاختبار يمنع عودة الفئة كلها.
    """

    CHILD_ENV = "IM_STATE_GUARD_CHILD"

    def test_broadcast_tests_leave_the_real_state_json_untouched(self):
        # حارس التفريخ: التشغيلة الابن تشمل هذا الملف نفسه، فبلا هذا الشرط
        # لتفرّخت الاختبارات بلا نهاية
        if os.environ.get(self.CHILD_ENV):
            self.skipTest("تشغيلة ابن — لا تفريخ متداخل")

        import subprocess
        root = Path(__file__).resolve().parent.parent
        state = root / "state.json"
        before = state.read_text(encoding="utf-8") if state.exists() else None

        proc = subprocess.run(
            [sys.executable, "-m", "unittest",
             "tests.test_telegram_broadcast", "tests.test_delivery_receipts"],
            cwd=str(root), capture_output=True, timeout=300,
            env={**os.environ, self.CHILD_ENV: "1"},
        )
        after = state.read_text(encoding="utf-8") if state.exists() else None
        self.assertEqual(proc.returncode, 0, "التشغيلة الابن نفسها فشلت")
        self.assertEqual(before, after,
                         "اختبار كتب في state.json الحقيقي — اعزل G.STATE_FILE")


# ========== الحدّ الصادق موثَّق في الكود والتوثيق ==========
class TestHonestLimitIsDocumented(unittest.TestCase):
    """تيليجرام يؤكد التسليم لا القراءة — يجب أن يُكتب صراحةً، لا يُفترض."""

    ROOT = Path(__file__).resolve().parent.parent

    def test_guard_documents_the_limit_in_arabic(self):
        src = (self.ROOT / "api_guard.py").read_text(encoding="utf-8")
        self.assertIn("التسليم", src)
        self.assertIn("لا يوجد إيصال قراءة للبوتات", src)

    def test_claude_md_documents_the_limit(self):
        doc = (self.ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("تيليجرام يؤكد التسليم لا القراءة", doc)


if __name__ == "__main__":
    unittest.main()
