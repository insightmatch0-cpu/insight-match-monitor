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
        self.assertTrue(R.is_due(p3, _day("2026-08-24")))
        self.assertFalse(R.is_due(p3, _day("2026-08-25")))

    def test_closed_deadline_is_always_silent(self):
        item = {"id": "x", "due": "2026-08-26", "priority": "P1",
                "status": "done"}
        for d in ("2026-08-24", "2026-08-26", "2026-08-28"):
            self.assertFalse(R.is_due(item, _day(d)), d)


class TestRobustness(unittest.TestCase):
    """لا يكسر تشغيلة أبداً — نفس عقيدة api_guard وdeadman."""

    def test_missing_file_returns_empty_without_raising(self):
        gone = Path(tempfile.mkstemp(suffix=".json")[1])
        gone.unlink()
        self.assertEqual(R.load_deadlines(gone), [])
        self.assertEqual(R.reminder_lines(_day("2026-08-23"), gone), "")

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

    def test_no_due_deadlines_yields_empty_block(self):
        f = _file([{"id": "x", "due": "2027-01-01", "priority": "P3"}])
        try:
            self.assertEqual(R.reminder_lines(_day("2026-08-23"), f), "")
        finally:
            f.unlink(missing_ok=True)

    def test_nearest_deadline_comes_first(self):
        f = _file([{"id": "far", "due": "2026-08-26", "priority": "P1"},
                   {"id": "near", "due": "2026-08-24", "priority": "P2"}])
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
            self.assertIsNotNone(R._days_left(it["due"], R.now_utc()), it["id"])
            self.assertTrue(it.get("action"), it["id"])

    def test_trial_expiry_reminds_three_days_ahead(self):
        """طلب المالك الحرفي: قبل الانتهاء بثلاثة أيام."""
        sub = next(i for i in self.items
                   if i["id"] == "sportmonks_trial_expiry")
        self.assertEqual(sub["due"], "2026-08-26")
        self.assertEqual(sub["priority"], "P1")
        self.assertEqual(R._lead(sub), 3)
        self.assertTrue(R.is_due(sub, _day("2026-08-23")))
        self.assertFalse(R.is_due(sub, _day("2026-08-22")))

    def test_verdict_moved_two_weeks_later(self):
        """التمديد أسبوعان: الحكم من 1-3 سبتمبر إلى ~17 سبتمبر."""
        v = next(i for i in self.items if i["id"] == "xg_shadow_verdict")
        self.assertEqual(v["due"], "2026-09-17")

    def test_shadow_window_constant_matches_the_extension(self):
        self.assertEqual(P.XG_SHADOW_DAYS, 35)


if __name__ == "__main__":
    unittest.main()
