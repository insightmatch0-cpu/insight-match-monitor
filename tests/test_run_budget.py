# -*- coding: utf-8 -*-
"""اختبارات سقف زمن التشغيلة (إصلاح ازدحام 2026-08-06 — قاعدة SLA).

العطل الذي حدث فعلاً: في أمسية مزدحمة استغرقت الجولة العادية 6-7 دقائق، ثم بدأ
الرصد السريع ميزانيته الكاملة (8 دقائق) من *نهاية* الجولة العادية — فصار طول
التشغيلة "مهما استغرقت الجولة + 8 دقائق"، بلا سقف. تجاوزت التشغيلات خانة الـ10
دقائق، تكوّن طابور، وكل تشغيلة جديدة قتلت السابقة قبل خطوة الحفظ: 7 ساعات و23
دقيقة بلا كتابة بيانات (15:40 → 23:10 UTC) واللوحة مجمّدة على 15:59.

الإصلاح المحروس هنا: fast_watch_deadline() تُثبَّت على بداية التشغيلة، فلا يمكن
لأي تشغيلة أن تتجاوز خانتها الزمنية مهما طالت الجولة العادية.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import monitor as M


class TestRunWallClockBudget(unittest.TestCase):
    def test_quiet_night_keeps_full_fast_watch_budget(self):
        """ليلة هادئة: الجولة العادية سريعة → الرصد السريع يأخذ ميزانيته كاملة."""
        with mock.patch.object(M, "RUN_START", 1000.0), \
             mock.patch.object(M.time, "monotonic", return_value=1030.0):
            # مرّت 30 ثانية فقط منذ البدء؛ 30+480=510 أصغر من 1000+420=1420-1000
            remaining = M.fast_watch_deadline() - 1030.0
        self.assertEqual(remaining, M.FOCUS_LOOP_BUDGET_SECONDS,
                         "الليلة الهادئة يجب ألا تفقد أي ثانية من الرصد السريع")

    def test_busy_night_is_capped_by_run_start(self):
        """أمسية مزدحمة: الجولة العادية أخذت 6 دقائق → المهلة تُقصّ من بداية التشغيلة."""
        start, now = 1000.0, 1000.0 + 6 * 60
        with mock.patch.object(M, "RUN_START", start), \
             mock.patch.object(M.time, "monotonic", return_value=now):
            deadline = M.fast_watch_deadline()
        # لولا الإصلاح لكانت المهلة now+480 = 1840 (أي 14 دقيقة من بدء التشغيلة)
        self.assertLess(deadline, now + M.FOCUS_LOOP_BUDGET_SECONDS,
                        "المهلة يجب أن تُقصّ في الأمسية المزدحمة")
        self.assertEqual(deadline, start + M.RUN_WALL_CLOCK_BUDGET_SECONDS)

    def test_overrunning_pass_disables_fast_watch_entirely(self):
        """الجولة العادية تجاوزت السقف → مهلة منتهية، فتخرج الحلقتان فوراً."""
        start, now = 1000.0, 1000.0 + M.RUN_WALL_CLOCK_BUDGET_SECONDS + 90
        with mock.patch.object(M, "RUN_START", start), \
             mock.patch.object(M.time, "monotonic", return_value=now):
            deadline = M.fast_watch_deadline()
        self.assertLessEqual(deadline, now,
                             "التشغيلة المتأخرة يجب أن تُسلّم فوراً لا أن تمدد نفسها")

    def test_run_can_never_outlast_its_ten_minute_slot(self):
        """الحارس الجوهري: أياً كان طول الجولة العادية، لا تتجاوز التشغيلة خانتها.

        السقف + وقت الإعداد/اللوحة/الحفظ يجب أن يبقى تحت الـ10 دقائق التي تفصل
        بين تشغيلتين، وإلا عاد الطابور الذي سبّب فقدان البيانات."""
        self.assertLess(M.RUN_WALL_CLOCK_BUDGET_SECONDS, 10 * 60,
                        "سقف التشغيلة يجب أن يبقى تحت فاصل الـ10 دقائق")
        start = 1000.0
        for elapsed in (0, 60, 300, 420, 600, 1800):
            with mock.patch.object(M, "RUN_START", start), \
                 mock.patch.object(M.time, "monotonic", return_value=start + elapsed):
                deadline = M.fast_watch_deadline()
            self.assertLessEqual(
                deadline, start + M.RUN_WALL_CLOCK_BUDGET_SECONDS,
                f"المهلة تجاوزت سقف التشغيلة بعد {elapsed}s من الجولة العادية")

    def test_both_fast_lanes_share_the_same_anchored_deadline(self):
        """الرصد السريع للقائمة والرادار السريع يشتركان في المهلة نفسها."""
        import inspect
        src = inspect.getsource(M.focus_fast_watch)
        self.assertIn("fast_watch_deadline()", src,
                      "focus_fast_watch يجب أن يستخدم المهلة المثبّتة لا مهلة محلية")
        main_src = inspect.getsource(M.main) if hasattr(M, "main") else ""
        if main_src:
            self.assertNotIn("time.monotonic() + FOCUS_LOOP_BUDGET_SECONDS", main_src,
                             "لا مهلة غير مثبّتة في نقطة استدعاء المسار السريع")


if __name__ == "__main__":
    unittest.main()
