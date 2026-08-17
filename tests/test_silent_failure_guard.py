# -*- coding: utf-8 -*-
"""اختبارات حارس الأعطال الصامتة — حادثة 2026-08-14.

الحادثة: انتهى اشتراك API-Football فارتدّ الحساب إلى الخطة المجانية، فنفد
الرصيد خلال ساعة. التشغيلة الصباحية فشلت مرتين ثم صمتت، والمالك لم يعلم
19 ساعة حتى اكتشف العطل بنفسه.

كل سبب جذري من الثلاثة له هنا اختبار يثبت أنه لن يتكرر:
(أ) القائمة الفارغة تبقى "يوماً هادئاً" — القاعدة 5 لم تُكسر.
(ب) رفض المزوّد يُرفع استثناءً مصنَّفاً ولا يُبتلع أبداً.
(ج) مانع التكرار يمنع الرسالة الثانية داخل 6 ساعات.
(د) الحارس الخارجي يطلق حين يتأخر history.json ويصمت حين يتقدّم.
(هـ) لا مفتاح في أي رسالة تُرسل (قاعدة الأسرار 3).
"""

import json
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import api_guard as G
import deadman as D
import monitor as M
import predict_v2 as P

FAKE_KEY = "sk-api-football-0123456789abcdef"
FAKE_TOKEN = "1234567890:AAH-fake-telegram-bot-token-xyz"

# ترويسات رصيد نموذجية من API-Football (خطة Pro سليمة)
PRO_HEADERS = {
    "x-ratelimit-requests-remaining": "7100",
    "x-ratelimit-requests-limit": "7500",
}


class FakeResponse:
    """رد HTTP مزيّف — يكفي لما يقرأه guarded_get."""

    def __init__(self, payload, status_code=200, headers=None, text=""):
        self._payload = payload
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("لا JSON")
        return self._payload


class GuardHarness(unittest.TestCase):
    """يعزل الحارس تماماً: state.json مؤقت، لا شبكة، لا حالة مربوطة."""

    def setUp(self):
        G.detach_state()
        self.addCleanup(G.detach_state)

        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text("{}", encoding="utf-8")
        orig_state = G.STATE_FILE
        G.STATE_FILE = tmp
        self.state_file = tmp
        self.addCleanup(lambda: (setattr(G, "STATE_FILE", orig_state),
                                 tmp.unlink(missing_ok=True)))

        # كل رسالة تيليجرام تُلتقط بدل أن تُرسل
        self.sent = []          # (chat_id, text)
        self.responses = []     # ردود API المزيّفة بالترتيب
        self.requested = []

        class FakeRequests:
            @staticmethod
            def get(url, headers=None, timeout=None, params=None):
                self.requested.append(url)
                return self.responses.pop(0)

            @staticmethod
            def post(url, json=None, timeout=None):
                self.sent.append(((json or {}).get("chat_id"), (json or {}).get("text")))
                return FakeResponse({"ok": True})

        orig_req = G.requests
        G.requests = FakeRequests
        self.addCleanup(lambda: setattr(G, "requests", orig_req))

        # مفاتيح مزيّفة في البيئة — تُستعمل لإثبات أنها لا تُطبع أبداً
        import os
        self._env_backup = {}
        for name, val in (("API_FOOTBALL_KEY", FAKE_KEY),
                          ("TELEGRAM_TOKEN", FAKE_TOKEN),
                          ("TELEGRAM_CHAT_ID", "111"),
                          ("TELEGRAM_BROADCAST_IDS", "")):
            self._env_backup[name] = os.environ.get(name)
            os.environ[name] = val
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        import os
        for name, old in self._env_backup.items():
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old

    def call(self, payload, status=200, headers=None, component="اختبار"):
        self.responses.append(FakeResponse(payload, status, headers or PRO_HEADERS))
        return G.guarded_get("https://example.invalid/x", {}, component)

    def read_state(self):
        return json.loads(self.state_file.read_text(encoding="utf-8"))


# ================== (أ) القاعدة 5 محفوظة ==================
class TestEmptyIsNotError(GuardHarness):
    """البيانات الفارغة ليست خطأً — يوم راحة أو خارج الموسم."""

    def test_empty_response_returns_empty_list(self):
        self.assertEqual(self.call({"response": [], "errors": []}), [])

    def test_empty_response_raises_nothing_and_sends_nothing(self):
        self.call({"response": [], "errors": []})
        self.assertEqual(self.sent, [], "يوم هادئ أرسل إنذاراً — القاعدة 5 مكسورة")

    def test_errors_as_empty_dict_is_still_a_quiet_day(self):
        """API-Football يرجع errors كقاموس فارغ أحياناً وكقائمة فارغة أحياناً."""
        self.assertEqual(self.call({"response": [], "errors": {}}), [])
        self.assertEqual(self.sent, [])

    def test_normal_data_passes_through(self):
        rows = [{"fixture": {"id": 1}}]
        self.assertEqual(self.call({"response": rows, "errors": []}), rows)


# ================== (ب) الرفض يصرخ ==================
class TestRefusalRaises(GuardHarness):
    """رفض المزوّد خطأ مصنَّف — لا يُبتلع ولا يُعامل كيوم هادئ."""

    def test_errors_requests_raises_classified(self):
        """الرد الحرفي الذي وصل صباح الحادثة."""
        with self.assertRaises(G.ApiRefused) as ctx:
            self.call({"response": [], "errors": {
                "requests": "You have reached the request limit for the day"}})
        self.assertEqual(ctx.exception.kind, "quota")

    def test_plan_restriction_raises_classified(self):
        with self.assertRaises(G.ApiRefused) as ctx:
            self.call({"response": [], "errors": {
                "plan": "Your subscription does not allow this endpoint"}})
        self.assertEqual(ctx.exception.kind, "plan")

    def test_bad_token_raises_classified(self):
        with self.assertRaises(G.ApiRefused) as ctx:
            self.call({"response": [], "errors": {"token": "invalid api key"}})
        self.assertEqual(ctx.exception.kind, "auth")

    def test_http_429_raises_quota(self):
        with self.assertRaises(G.ApiRefused) as ctx:
            self.call({"message": "too many requests"}, status=429)
        self.assertEqual(ctx.exception.kind, "quota")
        self.assertEqual(ctx.exception.status, 429)

    def test_http_500_raises(self):
        with self.assertRaises(G.ApiRefused) as ctx:
            self.call({"message": "server error"}, status=500)
        self.assertEqual(ctx.exception.kind, "http")

    def test_refusal_sends_immediate_alert_on_first_failure(self):
        """السبب الجذري 2: كان الإنذار ينتظر ثلاث تشغيلات فاشلة. الآن: الأولى."""
        with self.assertRaises(G.ApiRefused):
            self.call({"response": [], "errors": {"requests": "limit reached"}})
        self.assertEqual(len(self.sent), 1, "أول فشل لم يُرسل إنذاراً")

    def test_alert_names_fault_component_and_owner_action(self):
        with self.assertRaises(G.ApiRefused):
            self.call({"response": [], "errors": {"requests": "limit reached"}},
                      component="predict_v2.py (توقعات المحرك 2 الصباحية)")
        text = self.sent[0][1]
        self.assertIn("رصيد", text)                       # نوع العطل
        self.assertIn("predict_v2.py", text)              # المكوّن المتأثر
        self.assertIn("المطلوب منك", text)                # ما المطلوب بالضبط
        self.assertIn("dashboard.api-football.com", text)

    def test_transient_http_error_does_not_spam_telegram(self):
        """خطأ شبكة عابر يُرفع ولا يوقظ المالك — الصراخ لعائلة موت الحساب."""
        with self.assertRaises(G.ApiRefused):
            self.call({"message": "bad gateway"}, status=502)
        self.assertEqual(self.sent, [])

    def test_rollback_key_restores_old_swallowing(self):
        """مفتاح التراجع: API_REFUSAL_STRICT=0 يعيد السلوك القديم حرفياً."""
        import os
        os.environ["API_REFUSAL_STRICT"] = "0"
        self.addCleanup(lambda: os.environ.pop("API_REFUSAL_STRICT", None))
        self.assertEqual(
            self.call({"response": [], "errors": {"requests": "limit reached"}}), [])


class TestEngineWiring(unittest.TestCase):
    """المحركان يمرّان فعلاً عبر الحارس — لا نسخة قديمة باقية."""

    def test_monitor_uses_guard(self):
        self.assertIs(M.ApiRefused, G.ApiRefused)

    def test_predict_v2_uses_guard(self):
        self.assertIs(P.ApiRefused, G.ApiRefused)

    def test_both_engines_call_guarded_get(self):
        calls = []
        orig = G.guarded_get
        G.guarded_get = lambda url, headers, component, timeout=30: calls.append(component) or []
        self.addCleanup(lambda: setattr(G, "guarded_get", orig))
        M.api_football("fixtures?live=all")
        P.api_football("fixtures?date=2026-08-14")
        self.assertEqual(len(calls), 2)
        self.assertTrue(any("monitor" in c for c in calls))
        self.assertTrue(any("predict_v2" in c for c in calls))


# ================== (ج) مانع التكرار ==================
class TestAlertThrottle(GuardHarness):
    """مرة واحدة كل 6 ساعات لكل نوع عطل — إنذار لا إغراق."""

    def _refuse(self):
        with self.assertRaises(G.ApiRefused):
            self.call({"response": [], "errors": {"requests": "limit reached"}})

    def test_second_refusal_within_six_hours_is_silent(self):
        self._refuse()
        self._refuse()
        self._refuse()
        self.assertEqual(len(self.sent), 1,
                         "تكررت الرسالة داخل 6 ساعات — مانع التكرار لا يعمل")

    def test_alert_returns_after_six_hours(self):
        now = G.now_utc()
        self.assertTrue(G.alert_once("quota", "أولى", now=now))
        self.assertFalse(G.alert_once("quota", "ثانية", now=now + timedelta(hours=5, minutes=59)))
        self.assertTrue(G.alert_once("quota", "ثالثة", now=now + timedelta(hours=6, minutes=1)))
        self.assertEqual(len(self.sent), 2)

    def test_throttle_is_per_fault_type(self):
        """عطل من نوع آخر يمرّ فوراً — لا يحجبه صمت النوع الأول."""
        now = G.now_utc()
        self.assertTrue(G.alert_once("quota", "رصيد", now=now))
        self.assertTrue(G.alert_once("auth", "مفتاح", now=now))
        self.assertEqual(len(self.sent), 2)

    def test_throttle_flag_is_persisted_immediately(self):
        """يُحفظ فوراً: لو ماتت التشغيلة بعد الرسالة لتكرر الإنذار كل 10 دقائق."""
        G.alert_once("quota", "رسالة")
        self.assertIn("quota", self.read_state().get("api_alerts", {}))

    def test_bound_state_survives_host_final_save(self):
        """monitor.py يحفظ حالته في نهاية التشغيلة — يجب ألا يمحو علم التهدئة."""
        host_state = {"pulse": {}}
        saves = []
        G.attach_state(host_state, lambda s: saves.append(json.dumps(s)))
        G.alert_once("quota", "رسالة")
        self.assertIn("api_alerts", host_state, "العلم لم يصل إلى حالة المضيف الحية")
        self.assertTrue(saves, "لم يُطلب حفظ فوري من المضيف")


# ================== عدّاد الرصيد ==================
class TestQuotaMeter(GuardHarness):
    """نرى الاختناق قبل أن يقتلنا."""

    def test_headers_are_stored(self):
        self.call({"response": [], "errors": []})
        q = self.read_state().get("api_quota") or {}
        self.assertEqual(q.get("remaining"), 7100)
        self.assertEqual(q.get("limit"), 7500)

    def test_digest_line_format(self):
        self.call({"response": [], "errors": []})
        self.assertEqual(G.quota_line(), "📊 رصيد API: مستهلك 400 من 7500")

    def test_line_uses_latin_digits(self):
        """قاعدة اللغة 2: أرقام لاتينية دائماً، لا أرقام عربية-هندية."""
        self.call({"response": [], "errors": []})
        self.assertFalse(any("٠" <= ch <= "٩" for ch in G.quota_line()))

    def test_no_line_without_a_reading(self):
        self.assertEqual(G.quota_line(), "")

    def test_low_balance_warns_proactively(self):
        """تحت 20% متبقٍ → تحذير استباقي."""
        self.call({"response": [], "errors": []},
                  headers={"x-ratelimit-requests-remaining": "900",
                           "x-ratelimit-requests-limit": "7500"})
        self.assertEqual(len(self.sent), 1)
        self.assertIn("يقترب من النفاد", self.sent[0][1])

    def test_healthy_balance_stays_silent(self):
        self.call({"response": [], "errors": []})
        self.assertEqual(self.sent, [])

    def test_free_plan_ceiling_is_visible(self):
        """جوهر الحادثة: السقف 100 بدل 7,500 — كان يجب أن يُرى قبل الموت."""
        self.call({"response": [], "errors": []},
                  headers={"x-ratelimit-requests-remaining": "12",
                           "x-ratelimit-requests-limit": "100"})
        self.assertIn("من 100", G.quota_line())
        self.assertIn("12 من 100", self.sent[0][1])

    def test_quota_is_read_even_from_a_refusal(self):
        """رد الرفض يحمل الترويسات أيضاً — وهو أهم رد نقرأه."""
        with self.assertRaises(G.ApiRefused):
            self.call({"response": [], "errors": {"requests": "limit reached"}},
                      headers={"x-ratelimit-requests-remaining": "0",
                               "x-ratelimit-requests-limit": "100"})
        self.assertEqual((self.read_state().get("api_quota") or {}).get("remaining"), 0)


# ================== (د) الحارس الخارجي ==================
class TestDeadmanSwitch(unittest.TestCase):
    """حارس يسكن داخل ما يحرسه ليس حارساً — هذا يعيش خارج المحركين."""

    def test_fires_when_history_is_stale(self):
        self.assertTrue(D.should_fire("09:05", "2026-08-14", "2026-08-13", ""))

    def test_silent_when_history_advanced_today(self):
        self.assertFalse(D.should_fire("09:05", "2026-08-14", "2026-08-14", ""))

    def test_silent_before_the_deadline_hour(self):
        """قبل 09:00 UTC لا إنذار — المحرك قد يكون متأخراً لا ميتاً."""
        self.assertFalse(D.should_fire("08:59", "2026-08-14", "2026-08-13", ""))

    def test_fires_at_exactly_nine(self):
        self.assertTrue(D.should_fire("09:00", "2026-08-14", "2026-08-13", ""))

    def test_only_once_per_day(self):
        self.assertFalse(D.should_fire("09:05", "2026-08-14", "2026-08-13", "2026-08-14"))

    def test_yesterdays_flag_does_not_mute_today(self):
        self.assertTrue(D.should_fire("09:05", "2026-08-14", "2026-08-13", "2026-08-13"))

    def test_missing_history_counts_as_stale(self):
        self.assertTrue(D.should_fire("09:05", "2026-08-14", "", ""))

    def test_progress_prefers_the_newer_of_day_key_and_meta(self):
        """مفاتيح الأيام تواريخ مباريات لا تواريخ تشغيل — meta.updated هو
        الدليل القاطع أن التشغيلة جرت، فلا ننذر كاذباً في يوم هادئ."""
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        self.addCleanup(lambda: tmp.unlink(missing_ok=True))
        tmp.write_text(json.dumps({
            "days": {"2026-08-12": {}, "2026-08-13": {}},
            "meta": {"updated": "2026-08-14T04:35:52.610318+00:00"},
        }), encoding="utf-8")
        self.assertEqual(D.history_progress(tmp), "2026-08-14")

    def test_progress_falls_back_to_day_keys(self):
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        self.addCleanup(lambda: tmp.unlink(missing_ok=True))
        tmp.write_text(json.dumps({"days": {"2026-08-13": {}}}), encoding="utf-8")
        self.assertEqual(D.history_progress(tmp), "2026-08-13")

    def test_corrupt_history_is_stale_not_a_crash(self):
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        self.addCleanup(lambda: tmp.unlink(missing_ok=True))
        tmp.write_text("{ليس JSON", encoding="utf-8")
        self.assertEqual(D.history_progress(tmp), "")

    def test_uses_zero_api_calls(self):
        """الشرط الملزم: الحارس لا يلمس API-Football إطلاقاً."""
        source = Path(D.__file__).read_text(encoding="utf-8")
        self.assertNotIn("api_football", source)
        self.assertNotIn("v3.football.api-sports.io", source)

    def test_does_not_import_either_engine(self):
        """الاستقلال التام: لو مات المحرك بقي الحارس حياً."""
        source = Path(D.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import monitor", source)
        self.assertNotIn("import predict", source)

    def test_alert_text_is_the_owners_sentence(self):
        self.assertIn("لم يجرِ التقييم الصباحي اليوم", D.ALERT_TEXT)


class TestDeadmanWorkflowStep(unittest.TestCase):
    """الحارس يجب أن يعيش في monitor.yml — المستقل الوحيد عن المحركين."""

    WF = Path(__file__).resolve().parent.parent / ".github/workflows/monitor.yml"

    def test_step_exists_in_monitor_workflow(self):
        self.assertIn("deadman.py", self.WF.read_text(encoding="utf-8"))

    def test_step_failure_cannot_break_the_run(self):
        text = self.WF.read_text(encoding="utf-8")
        line = [l for l in text.splitlines() if "python deadman.py" in l][0]
        self.assertIn("||", line, "فشل الحارس يجب ألا يُسقط التشغيلة")

    def test_state_is_saved_even_when_the_monitor_fails(self):
        """بلا هذا يضيع علم مانع التكرار فتتكرر الرسالة كل عشر دقائق."""
        text = self.WF.read_text(encoding="utf-8")
        save = text.split("- name: Save state")[1]
        self.assertIn("if: always()", save.split("run:")[0])


# ================== (هـ) لا مفتاح في أي رسالة ==================
class TestNoSecretsLeak(GuardHarness):
    """قاعدة الأسرار 3: ممنوع طباعة أي مفتاح في أي رسالة — تسريب وقع مرة."""

    def test_key_echoed_by_the_provider_is_redacted(self):
        with self.assertRaises(G.ApiRefused):
            self.call({"response": [], "errors": {
                "requests": f"limit reached for key {FAKE_KEY}"}})
        text = self.sent[0][1]
        self.assertNotIn(FAKE_KEY, text)
        self.assertIn(G.REDACTED, text)

    def test_token_never_appears_in_a_message(self):
        with self.assertRaises(G.ApiRefused):
            self.call({"response": [], "errors": {
                "plan": f"upgrade required {FAKE_TOKEN}"}})
        self.assertNotIn(FAKE_TOKEN, self.sent[0][1])

    def test_no_secret_in_any_alert_message(self):
        """مسح شامل: كل رسالة أنتجها الحارس في هذا الاختبار."""
        for payload in ({"errors": {"requests": f"x {FAKE_KEY}"}},
                        {"errors": {"token": f"y {FAKE_KEY}"}}):
            try:
                self.call(dict(payload, response=[]))
            except G.ApiRefused:
                pass
        self.call({"response": [], "errors": []},
                  headers={"x-ratelimit-requests-remaining": "5",
                           "x-ratelimit-requests-limit": "100"})
        self.assertTrue(self.sent, "لم تُرسل أي رسالة — الاختبار بلا معنى")
        for _cid, text in self.sent:
            self.assertNotIn(FAKE_KEY, text)
            self.assertNotIn(FAKE_TOKEN, text)

    def test_exception_message_carries_no_key(self):
        """الاستثناء نفسه يُطبع في سجل التشغيلة — يجب أن يكون نظيفاً أيضاً."""
        with self.assertRaises(G.ApiRefused) as ctx:
            self.call({"response": [], "errors": {"token": f"bad {FAKE_KEY}"}})
        self.assertNotIn(FAKE_KEY, str(ctx.exception))

    def test_redact_ignores_short_values(self):
        """قيمة قصيرة جداً قد تطابق نصاً بريئاً — لا نشوّه الرسائل بها."""
        import os
        os.environ["TELEGRAM_CHAT_ID"] = "111"
        self.assertIn("111", G.redact("المعرّف 111 سليم"))


if __name__ == "__main__":
    unittest.main()


class TestClaudeCreditGuard(unittest.TestCase):
    """💳 فجوة صبيحة 2026-08-17: رصيد Anthropic نفد، 109 مرشحين، 0 توقعات
    محفوظة — والتشغيلة خرجت خضراء بلا إنذار واحد. قاعدتا العلاج من عقيدة
    14 أغسطس نفسها: إنذار فوري من أول رفض (بتهدئة)، وصفر-من-مرشحين =
    تشغيلة حمراء لا خضراء صامتة."""

    def _capture(self, module):
        import unittest.mock as mock
        calls = []
        patcher = mock.patch.object(
            module.api_guard, "alert_once",
            side_effect=lambda kind, text, **k: calls.append((kind, text)) or True)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_v2_credit_refusal_fires_immediate_alert(self):
        import predict_v2 as P2
        calls = self._capture(P2)
        P2.claude_refusal_alert(
            '{"type":"error","error":{"message":"Your credit balance is too low"}}',
            "المحرك 2")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "claude_credit")
        self.assertIn("Plans & Billing", calls[0][1])

    def test_v1_has_same_guard(self):
        import predict as P1
        calls = self._capture(P1)
        P1.claude_refusal_alert("credit balance is too low", "المحرك 1")
        self.assertEqual(calls[0][0], "claude_credit")

    def test_transient_errors_stay_silent(self):
        """مهلة شبكة أو 529 مؤقت ليسا موت حساب — لا إنذار (لا إغراق)."""
        import predict_v2 as P2
        calls = self._capture(P2)
        P2.claude_refusal_alert("Read timed out", "المحرك 2")
        P2.claude_refusal_alert("529 Server Error: overloaded", "المحرك 2")
        self.assertEqual(calls, [])

    def test_zero_predictions_with_candidates_is_loud(self):
        """بنيوي: حارس الصفر موجود في المحركين بعد الحفظ."""
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        for f in ("predict_v2.py", "predict.py"):
            src = (root / f).read_text(encoding="utf-8")
            with self.subTest(engine=f):
                self.assertIn("if upcoming and not new_preds:", src)
                self.assertIn("رفض Claude شامل", src)

    def test_wrapper_calls_the_alert(self):
        """بنيوي: مسار الخطأ في نداء Claude يستدعي الإنذار في المحركين."""
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        for f in ("predict_v2.py", "predict.py"):
            src = (root / f).read_text(encoding="utf-8")
            with self.subTest(engine=f):
                self.assertIn("claude_refusal_alert(", src)
