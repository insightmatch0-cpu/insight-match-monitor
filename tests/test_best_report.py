# -*- coding: utf-8 -*-
"""🏆 حارس تقرير «أين نتفوق؟» الأسبوعي (طلب المالك 2026-08-24).

الغرض: كل رقم محسوب من ملفات القياس لحظة التشغيل (قاعدة الأرقام
المجمّدة)، حدود العينات تُحترم، ولا نداء API أو Claude في المسار كله.
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "report"))

import best_report as B  # noqa: E402


class TestComputedNotHardcoded(unittest.TestCase):
    def test_collect_reads_live_files_and_render_uses_them(self):
        d = B.collect()
        html = B.render(d)
        # نسبة المحرك 2 الإجمالية المحسوبة تظهر في الصفحة حرفياً
        self.assertIn(f">{d['s1']['v2'][2]}%<", html)
        self.assertIn(f"{d['s1']['v2'][0]}/{d['s1']['v2'][1]}", html)
        # تاريخ الإصدار هو تاريخ اليوم لا تاريخاً مكتوباً
        self.assertIn(d["date"], html)

    def test_sample_guards_enforced(self):
        d = B.collect()
        for pct, c, n, name in d["s1_top"] + d["s1_bottom"]:
            self.assertGreaterEqual(n, B.S1_MIN_LEAGUE, name)
        for pct, c, n, name in d["s3_leagues"]:
            self.assertGreaterEqual(n, B.S3_MIN_LEAGUE, name)
        for pct, c, n, name in d["s2_cats"]:
            self.assertGreaterEqual(n, 20, name)

    def test_every_percentage_row_carries_raw_counts(self):
        d = B.collect()
        html = B.render(d)
        # أول صف دوريات: النسبة والعداد الخام معاً
        pct, c, n, name = d["s1_top"][0]
        self.assertIn(f"{c}/{n}", html)


class TestNoExternalCalls(unittest.TestCase):
    def test_script_touches_no_api_or_claude(self):
        src = (ROOT / "report" / "best_report.py").read_text(encoding="utf-8")
        for banned in ("api-sports.io", "anthropic", "API_FOOTBALL_KEY",
                       "ANTHROPIC_API_KEY"):
            self.assertNotIn(banned, src)

    def test_workflow_is_weekly_and_reuses_send_pdf(self):
        wf = (ROOT / ".github" / "workflows" / "best_report.yml").read_text(
            encoding="utf-8")
        self.assertIn('cron: "50 5 * * 1"', wf)      # أسبوعي — الاثنين
        self.assertIn("report/send_pdf.py", wf)       # نفس مسار الإيصالات
        self.assertIn("workflow_dispatch", wf)        # زر يدوي للمالك
        self.assertNotIn("git push", wf)              # لا يلتزم بشيء


if __name__ == "__main__":
    unittest.main()
