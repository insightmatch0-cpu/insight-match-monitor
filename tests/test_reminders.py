# -*- coding: utf-8 -*-
"""📅 اختبارات سجل المواعيد والتذكيرات (قرار المالك 2026-08-14).

المبادئ المحروسة: مهلة 3-5 أيام حسب الشريحة، تكرار يومي بلا إغراق، رسالة
واحدة لكل موعد في اليوم مهما تعدّدت مواضع النداء، صمت بعد الإغلاق، وصفر
قدرة على كسر أي تشغيلة. صفر شبكة بالتصميم: لا نداء خارجي في أي اختبار.
"""

import inspect
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import deadman as D
import predict_v2 as P
import reminders as R


def _day(s):
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _file(deadlines):
    tmp = Path(tempfile.mkstemp(suffix=".json")[1])
    tmp.write_text(json.dumps({"deadlines": deadlines}, ensure_ascii=False),
                   encoding="utf-8")
    return tmp


class TestLeadWindows(unittest.TestCase):
    """المهلة: 3-5 أيام حسب الشريحة كما اشترط المالك، والنصّ يتقدّم الافتراضي."""

    def test_priority_defaults_stay_inside_owner_range(self):
        for tier, lead in R.LEAD_DAYS.items():
            self.assertGreaterEqual(lead, 3, tier)
            self.assertLessEqual(lead, 5, tier)

    def test_each_tier_opens_on_its_own_lead_day(self):
        for tier, lead in R.LEAD_DAYS.items():
            item = {"id": "x", "due": "2026-09-10", "priority": tier}
            # يوم قبل فتح النافذة: صامت
            self.assertFalse(R.is_due(item, _day("2026-09-10")
                                      .replace(day=10 - lead - 1)), tier)
            # أول يوم في النافذة: يستحق
            self.assertTrue(R.is_due(item, _day("2026-09-10")
                                     .replace(day=10 - lead)), tier)

    def test_explicit_lead_days_overrides_tier_default(self):
        """طلب المالك الصريح: تذكير انتهاء التجربة قبل 3 أيام لا 5."""
        item = {"id": "x", "due": "2026-08-26", "priority": "P1",
                "lead_days": 3}
        self.assertFalse(R.is_due(item, _day("2026-08-22")))
        self.assertTrue(R.is_due(item, _day("2026-08-23")))

    def test_repeats_every_day_until_the_due_date(self):
        """«لأفعلها قبل يوم» يتحقق بالتكرار اليومي لا بتذكير واحد."""
        item = {"id": "x", "due": "2026-08-26", "priority": "P1",
                "lead_days": 3}
        for d in ("2026-08-23", "2026-08-24", "2026-08-25", "2026-08-26"):
            self.assertTrue(R.is_due(item, _day(d)), d)


class TestGracePeriods(unittest.TestCase):
    """بعد الموعد: P1 أسبوع، P2 يوم، P3 صفر — محدود عمداً ضد إرهاق الإنذار."""

    def test_p1_stays_loud_for_a_week_then_stops(self):
        item = {"id": "x", "due": "2026-08-26", "priority": "P1"}
        self.assertTrue(R.is_due(item, _day("2026-09-02")))    # +7
        self.assertFalse(R.is_due(item, _day("2026-09-03")))   # +8

    def test_p2_and_p3_fade_quickly(self):
        p2 = {"id": "a", "due": "2026-08-24", "priority": "P2"}
        p3 = {"id": "b", "due": "2026-08-24", "priority": "P3"}
        self.assertTrue(R.is_due(p2, _day("2026-08-25")))
        self.assertFalse(R.is_due(p2, _day("2026-08-26")))
        # P3 افتراضاً على 3 و2 كبقية الشرائح: صامت يوم الموعد وبعده
        self.assertTrue(R.is_due(p3, _day("2026-08-22")))
        self.assertFalse(R.is_due(p3, _day("2026-08-24")))
        self.assertFalse(R.is_due(p3, _day("2026-08-25")))

    def test_closed_deadline_is_always_silent(self):
        item = {"id": "x", "due": "2026-08-26", "priority": "P1",
                "status": "done"}
        for d in ("2026-08-24", "2026-08-26", "2026-08-28"):
            self.assertFalse(R.is_due(item, _day(d)), d)


class TestRobustness(unittest.TestCase):
    """لا يكسر تشغيلة أبداً — نفس عقيدة api_guard وdeadman."""

    def test_missing_file_shouts_instead_of_raising_or_going_silent(self):
        """سجل مفقود حالة عطل لا يوم هادئ: لا يرفع استثناءً، ولا يصمت."""
        gone = Path(tempfile.mkstemp(suffix=".json")[1])
        gone.unlink()
        self.assertEqual(R.load_deadlines(gone), [])      # لا استثناء
        line = R.reminder_lines(_day("2026-08-23"), gone)
        self.assertIn("لا موعد قادم في السجل", line)

    def test_malformed_entries_are_skipped_not_fatal(self):
        f = _file([{"id": "ok", "due": "2026-08-26", "priority": "P1"},
                   {"due": "2026-08-26"},              # بلا معرّف
                   {"id": "bad", "due": "ليس تاريخاً"},
                   "نص لا قاموس"])
        try:
            rows = R.due_reminders(_day("2026-08-23"), f)
            self.assertEqual([r["id"] for r in rows], ["ok"])
        finally:
            f.unlink(missing_ok=True)

    def test_quiet_day_still_speaks(self):
        """انحدار: كان يرجع فراغاً حين لا استحقاق — وهو عيب «اختفاء السطر»
        نفسه الذي أخفى عطل تجربة xG. حارس لا يُسمع صوته لا يُعرف أنه حي."""
        f = _file([{"id": "x", "service": "خدمة ما", "price": "€5",
                    "due": "2027-01-01", "priority": "P3"}])
        try:
            line = R.reminder_lines(_day("2026-08-23"), f)
            self.assertNotEqual(line, "")
            self.assertIn("حارس المواعيد", line)
            self.assertIn("لا استحقاق", line)
            self.assertIn("خدمة ما", line)      # أقرب موعد يُسمّى
            self.assertIn("2027-01-01", line)
        finally:
            f.unlink(missing_ok=True)

    def test_register_with_no_future_deadline_is_flagged(self):
        """سجل بلا موعد قادم واحد = حالة عطل، لا يوم هادئ."""
        f = _file([{"id": "x", "service": "س", "due": "2020-01-01",
                    "priority": "P1", "status": "done"}])
        try:
            line = R.reminder_lines(_day("2026-08-23"), f)
            self.assertIn("لا موعد قادم في السجل", line)
        finally:
            f.unlink(missing_ok=True)

    def test_watch_line_appears_every_single_day_of_a_quiet_month(self):
        """لا يوم واحد بلا سطر — هذا هو الفرق بين مدير تجديد وسجل نائم."""
        f = _file([{"id": "x", "service": "خدمة", "due": "2026-12-01",
                    "priority": "P1"}])
        try:
            for d in range(1, 29):
                line = R.reminder_lines(_day(f"2026-09-{d:02d}"), f)
                self.assertTrue(line.strip(), f"يوم صامت: 2026-09-{d:02d}")
        finally:
            f.unlink(missing_ok=True)

    def test_nearest_deadline_comes_first(self):
        f = _file([{"id": "far", "due": "2026-08-26", "priority": "P1"},
                   {"id": "near", "due": "2026-08-25", "priority": "P2"}])
        try:
            rows = R.due_reminders(_day("2026-08-23"), f)
            self.assertEqual([r["id"] for r in rows], ["near", "far"])
        finally:
            f.unlink(missing_ok=True)


class TestTelegramFiring(unittest.TestCase):
    """رسالة واحدة لكل موعد في اليوم مهما تعدّدت مواضع النداء."""

    class _Guard:
        def __init__(self, boom=False):
            self.sent, self.seen, self.boom = [], set(), boom

        def alert_once(self, kind, text):
            if self.boom:
                raise RuntimeError("انفجار متعمد")
            if kind in self.seen:      # يحاكي مانع التكرار الحقيقي
                return False
            self.seen.add(kind)
            self.sent.append((kind, text))
            return True

    def _fire(self, guard, day, path, times=1):
        orig = R.api_guard
        R.api_guard = guard
        try:
            return [R.fire(_day(day), path) for _ in range(times)]
        finally:
            R.api_guard = orig

    def test_double_call_same_day_sends_once(self):
        """النشرة والحارس الخارجي ينادياننا معاً — ولا يصل المالك رسالتان."""
        f = _file([{"id": "sub", "due": "2026-08-26", "priority": "P1",
                    "lead_days": 3}])
        guard = self._Guard()
        try:
            counts = self._fire(guard, "2026-08-23", f, times=3)
            self.assertEqual(counts, [1, 0, 0])
            self.assertEqual(len(guard.sent), 1)
            self.assertIn("2026-08-23", guard.sent[0][0])   # المفتاح مؤرَّخ
        finally:
            f.unlink(missing_ok=True)

    def test_new_day_opens_a_new_message(self):
        f = _file([{"id": "sub", "due": "2026-08-26", "priority": "P1",
                    "lead_days": 3}])
        guard = self._Guard()
        try:
            self._fire(guard, "2026-08-23", f)
            self._fire(guard, "2026-08-24", f)
            self.assertEqual(len(guard.sent), 2)
        finally:
            f.unlink(missing_ok=True)

    def test_firing_failure_never_breaks_the_run(self):
        f = _file([{"id": "sub", "due": "2026-08-26", "priority": "P1"}])
        try:
            self.assertEqual(self._fire(self._Guard(boom=True),
                                        "2026-08-23", f), [0])
        finally:
            f.unlink(missing_ok=True)

    def test_message_carries_action_and_no_secret(self):
        f = _file([{"id": "sub", "due": "2026-08-26", "priority": "P1",
                    "lead_days": 3, "action": "جدّد قبل 25 أغسطس"}])
        guard = self._Guard()
        try:
            self._fire(guard, "2026-08-23", f)
            text = guard.sent[0][1]
            self.assertIn("جدّد قبل 25 أغسطس", text)
            self.assertIn("P1", text)
            for word in ("TELEGRAM_TOKEN", "SPORTMONKS_KEY", "Authorization"):
                self.assertNotIn(word, text)
        finally:
            f.unlink(missing_ok=True)


class TestWiring(unittest.TestCase):
    """التذكير يصل تيليجرام من مسارين: النشرة، والحارس الذي ينجو من موتها."""

    def test_wired_into_morning_digest(self):
        self.assertIn("reminders.reminder_lines()",
                      inspect.getsource(P.main))

    def test_wired_into_independent_deadman(self):
        src = inspect.getsource(D.main)
        self.assertIn("reminders.fire(", src)

    def test_reminders_module_makes_no_api_calls(self):
        """صفر نداءات API — يقرأ قرصاً فقط (نفس عقيدة api_guard)."""
        src = Path(R.__file__).read_text(encoding="utf-8")
        for banned in ("requests.get", "requests.post", "urlopen"):
            self.assertNotIn(banned, src)


class TestRegisteredDeadlines(unittest.TestCase):
    """السجل الحقيقي: مواعيد المالك المسجَّلة اليوم سليمة وقابلة للتنفيذ."""

    def setUp(self):
        self.items = R.load_deadlines(
            Path(__file__).resolve().parent.parent / "reminders.json")

    def test_register_is_valid(self):
        self.assertTrue(self.items)
        ids = [i["id"] for i in self.items]
        self.assertEqual(len(ids), len(set(ids)), "معرّف مكرر")
        for it in self.items:
            self.assertIn(it.get("priority"), R.LEAD_DAYS, it["id"])
            self.assertTrue(it.get("action"), it["id"])
            self.assertTrue(it.get("service"), it["id"])
            if it.get("due"):        # الصفوف بلا تاريخ مقصودة (بيانات ناقصة)
                self.assertIsNotNone(
                    R._days_left(it["due"], R.now_utc()), it["id"])

    def test_trial_expiry_closed_by_owner_decision(self):
        """قرار المالك 2026-08-24 («keep for another two weeks»): الصف أُقفل
        بقراره فلا يذكّر بعد الآن — والمراجعة التالية صف مستقل مفتوح."""
        sub = next(i for i in self.items
                   if i["id"] == "sportmonks_trial_expiry")
        self.assertEqual(sub["due"], "2026-08-26")
        self.assertEqual(sub["priority"], "P1")
        self.assertEqual(sub["status"], "done")
        self.assertIn("قرار المالك 2026-08-24", sub.get("resolution", ""))
        self.assertFalse(R.is_due(sub, _day("2026-08-23")))
        self.assertFalse(R.is_due(sub, _day("2026-08-26")))

    def test_two_week_review_reminds_three_days_ahead(self):
        """المراجعة الجديدة (استحقاق 2026-09-07) ترث قاعدة T-3 حرفياً."""
        sub = next(i for i in self.items
                   if i["id"] == "sportmonks_two_week_review")
        self.assertEqual(sub["due"], "2026-09-07")
        self.assertEqual(sub["priority"], "P1")
        self.assertEqual(R._lead(sub), 3)
        self.assertTrue(R.is_due(sub, _day("2026-09-04")))
        self.assertFalse(R.is_due(sub, _day("2026-09-03")))

    def test_verdict_moved_two_weeks_later(self):
        """التمديد أسبوعان: الحكم من 1-3 سبتمبر إلى ~17 سبتمبر."""
        v = next(i for i in self.items if i["id"] == "xg_shadow_verdict")
        self.assertEqual(v["due"], "2026-09-17")

    def test_shadow_window_constant_matches_the_extension(self):
        self.assertEqual(P.XG_SHADOW_DAYS, 35)


class TestGenericSubscriptionRegister(unittest.TestCase):
    """أمر المالك: النظام لكل واجهة نستعملها، لا لـ Sportmonks وحدها."""

    def setUp(self):
        self.path = Path(__file__).resolve().parent.parent / "reminders.json"
        self.items = R.load_deadlines(self.path)

    def test_every_paid_dependency_has_a_row(self):
        services = " ".join(i.get("service", "") for i in self.items)
        for dep in ("Sportmonks", "API-Football", "Claude"):
            self.assertIn(dep, services, f"واجهة بلا صف في السجل: {dep}")

    def test_subscriptions_alert_three_then_two_days_ahead(self):
        """جدول المالك الحرفي للاشتراكات: قبل 3 أيام، ثم قبل يومين."""
        for it in self.items:
            if not it.get("billable") or not it.get("due"):
                continue
            # remind_at صريح = تجاوز موثّق (تسليم لا اشتراك)؛ الباقي على 3 و2
            expected = (tuple(sorted(set(it["remind_at"]), reverse=True))
                        if it.get("remind_at") else (3, 2))
            self.assertEqual(R._offsets(it), expected, it["id"])

    def test_every_reminder_carries_service_name_and_price(self):
        """«with the price with the name of the application» — في كل رسالة."""
        for it in self.items:
            if not it.get("due"):
                continue
            row = dict(it, days_left=3)
            line = R.reminder_line(row)
            self.assertIn(it["service"], line, it["id"])
            if it.get("price"):
                self.assertIn(it["price"], line, it["id"])

    def test_unknown_date_or_price_is_asked_never_invented(self):
        """اختلاق تاريخ تجديد أو سعر أسوأ من الفراغ — يُسأل عنه ولا يُخمَّن."""
        # آلية الفجوات تبقى محروسة بملف مصطنع — السجل الحقيقي أُغلقت فجواته
        # بقرار المالك 2026-08-15 (إرجاء اشتراك Claude وسعر الصرف)
        f = _file([{"id": "x", "service": "خدمة", "billable": True,
                    "priority": "P1"}])
        try:
            pending = {r["id"]: r["missing"] for r in R.pending_input(f)}
            self.assertIn("x", pending)
            self.assertTrue(pending["x"])
            self.assertIn("لا أخمّن", R.pending_lines(f))
        finally:
            f.unlink(missing_ok=True)

    def test_owner_deferred_items_stay_recorded_but_silent(self):
        """قرار المالك 2026-08-15: اشتراك Claude «للاحقاً» — صف موثَّق بلا إلحاح."""
        row = next(i for i in self.items
                   if i["id"] == "claude_subscription_renewal")
        self.assertEqual(row["status"], "deferred")     # موجود، غير محذوف
        self.assertNotIn("claude_subscription_renewal",
                         {r["id"] for r in R.pending_input(self.path)})

    def test_api_football_date_and_price_are_now_known(self):
        """جُدِّد 2026-08-15 إلى Pro — فخرج من كتلة الناقص إلى المراقبة."""
        row = next(i for i in self.items if i["id"] == "api_football_renewal")
        self.assertEqual(row["due"], "2026-09-15")
        self.assertIn("$", row["price"])        # بالدولار كما أمر المالك
        self.assertNotIn("api_football_renewal",
                         {r["id"] for r in R.pending_input(self.path)})

    def test_pending_block_disappears_once_filled(self):
        f = _file([{"id": "x", "service": "خدمة", "price": "€1",
                    "billable": True, "due": "2026-12-01", "priority": "P1"}])
        try:
            self.assertEqual(R.pending_input(f), [])
            self.assertEqual(R.pending_lines(f), "")
        finally:
            f.unlink(missing_ok=True)

    def test_arabic_number_agreement(self):
        """النشرة تُقرأ على الهاتف — «2 أيام» ليست عربية."""
        self.assertEqual(R._arabic_days(2), "يومين")
        self.assertEqual(R._arabic_days(3), "3 أيام")
        self.assertEqual(R._arabic_days(11), "11 يوماً")
        row = {"service": "س", "title": "ت", "due": "2026-08-26",
               "priority": "P1", "days_left": 2}
        self.assertIn("بعد يومين", R.reminder_line(row))


class TestCurrencyDisplay(unittest.TestCase):
    """أمر المالك 2026-08-15: الأسعار بالدولار — وبلا اختراع سعر صرف."""

    def test_no_recorded_rate_means_no_invented_dollar_figure(self):
        f = _file([{"id": "x", "service": "خدمة", "price": "€571/شهر",
                    "amount_eur": 571, "due": "2026-12-01", "priority": "P1"}])
        try:
            self.assertIsNone(R.fx_rate(f))
            line = R.reminder_line(R.due_reminders(_day("2026-11-28"), f)[0],
                                   R.fx_rate(f))
            self.assertIn("€571", line)
            self.assertNotIn("$", line)      # لا رقم دولاري مخترَع
        finally:
            f.unlink(missing_ok=True)

    def test_recorded_rate_adds_a_dollar_estimate(self):
        f = Path(tempfile.mkstemp(suffix=".json")[1])
        f.write_text(json.dumps({
            "fx": {"eur_usd": 1.10, "as_of": "2026-08-15"},
            "deadlines": [{"id": "x", "service": "خدمة", "price": "€571/شهر",
                           "amount_eur": 571, "due": "2026-12-01",
                           "priority": "P1"}]}), encoding="utf-8")
        try:
            self.assertEqual(R.fx_rate(f), 1.10)
            row = R.due_reminders(_day("2026-11-28"), f)[0]
            self.assertIn("$628", R.reminder_line(row, R.fx_rate(f)))
        finally:
            f.unlink(missing_ok=True)

    def test_missing_rate_is_surfaced_unless_owner_defers(self):
        """فجوة سعر الصرف تُرفع مثل أي بيان ناقص — إلا حين يرجئها المالك صراحةً."""
        f = Path(tempfile.mkstemp(suffix=".json")[1])
        rows = [{"id": "x", "service": "س", "price": "€10", "amount_eur": 10,
                 "due": "2026-12-01", "priority": "P1"}]
        f.write_text(json.dumps({"deadlines": rows}), encoding="utf-8")
        try:
            self.assertIn("سعر صرف", R.pending_lines(f))       # بلا إرجاء: تُرفع
            f.write_text(json.dumps({"fx": {"deferred": True},
                                     "deadlines": rows}), encoding="utf-8")
            self.assertNotIn("سعر صرف", R.pending_lines(f))    # أرجأها: تصمت
        finally:
            f.unlink(missing_ok=True)
        # السجل الحقيقي: المالك أرجأها 2026-08-15 — فالنشرة بلا سطر فجوة صرف
        self.assertNotIn("سعر صرف", R.pending_lines(self.__class__._register()))

    @staticmethod
    def _register():
        return Path(__file__).resolve().parent.parent / "reminders.json"

    def test_natively_usd_prices_need_no_conversion(self):
        reg = R.load_deadlines(self._register())
        api = next(i for i in reg if i["id"] == "api_football_renewal")
        self.assertNotIn("amount_eur", api)   # ليست باليورو أصلاً
        self.assertIn("$", api["price"])


if __name__ == "__main__":
    unittest.main()
