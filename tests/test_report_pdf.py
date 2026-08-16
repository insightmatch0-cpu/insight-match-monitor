# -*- coding: utf-8 -*-
"""حارس مسار تقرير PDF (طلب المالك 2026-08-16: «أرسله لتيليجرام أيضاً»).

ثلاث قواعد تُحرس هنا: (1) الوضع الداكن إلزامي — المالك رفض الأبيض صراحةً
والسمة data-theme هي المفتاح الوحيد له؛ (2) الإرسال يمر بقائمة البث
المشتركة لا بـchat_id مفرد، وإلا لن يستقبل الجهاز الثاني صامتاً (نفس ثغرة
2026-08-15)؛ (3) الـworkflow يمرر الأسرار الثلاثة، ولا يُطبع معرّف كامل.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "report"))

import build_pdf  # noqa: E402


class TestBuildPdf(unittest.TestCase):

    def test_dark_theme_is_forced(self):
        """بلا data-theme="dark" يخرج الملف أبيض — وهو ما رفضه المالك."""
        doc = build_pdf.build_document("<style>x</style><main><p>ص</p></main>")
        self.assertIn('data-theme="dark"', doc)

    def test_document_is_complete_and_rtl(self):
        doc = build_pdf.build_document("<style>x</style><main><p>ص</p></main>")
        for needle in ("<!doctype html>", 'dir="rtl"', 'charset="utf-8"',
                       "<body>", "</html>"):
            self.assertIn(needle, doc)

    def test_print_css_unclips_wide_tables(self):
        """الجداول تنزلق أفقياً باللمس؛ لو بقيت كذلك على الورق لاقتُطعت أعمدة."""
        self.assertIn("overflow: visible !important", build_pdf.PRINT_CSS)
        self.assertIn("min-width: 0 !important", build_pdf.PRINT_CSS)

    def test_zero_page_margin_for_edge_to_edge_dark(self):
        """هامش الصفحة يترك إطاراً أبيض حول الخلفية الداكنة — يجب أن يكون صفراً."""
        self.assertIn("margin: 0", build_pdf.PRINT_CSS)
        self.assertIn("print-color-adjust: exact", build_pdf.PRINT_CSS)

    def test_source_page_exists(self):
        self.assertTrue((ROOT / "report" / "control-room.html").exists())


class TestSendPdf(unittest.TestCase):

    def _src(self):
        return (ROOT / "report" / "send_pdf.py").read_text(encoding="utf-8")

    def test_uses_shared_broadcast_list(self):
        self.assertIn("api_guard.broadcast_ids", self._src())

    def test_no_direct_single_chat_send(self):
        """ممنوع الإرسال إلى CHAT_ID مفرداً — يتجاوز قائمة البث."""
        self.assertIsNone(
            re.search(r'"chat_id"\s*:\s*CHAT_ID', self._src()),
            "الإرسال يتجاوز قائمة البث — الجهاز الثاني لن يستقبل",
        )

    def test_ids_are_masked_in_output(self):
        src = self._src()
        self.assertIn("api_guard.mask_id", src)
        # لا سطر طباعة يحمل المعرّف الخام
        self.assertNotIn("print(f\"📄 وصل الملف إلى {cid}", src)

    def test_owner_failure_is_loud(self):
        """فشل جهاز المالك = لا قناة تبليغ → تشغيلة حمراء (درس 14 أغسطس)."""
        self.assertIn("SystemExit", self._src())
        self.assertIn('f.get("owner")', self._src())


class TestWorkflow(unittest.TestCase):

    def _wf(self):
        return (ROOT / ".github" / "workflows" / "report_pdf.yml").read_text(
            encoding="utf-8")

    def test_passes_all_three_secrets(self):
        wf = self._wf()
        for s in ("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_BROADCAST_IDS"):
            with self.subTest(secret=s):
                self.assertIn(s, wf)

    def test_installs_arabic_fonts(self):
        """بلا خط عربي على المنفّذ يخرج الملف مربعات فارغة."""
        self.assertIn("fonts-noto", self._wf())

    def test_has_manual_and_scheduled_paths(self):
        wf = self._wf()
        self.assertIn("workflow_dispatch", wf)
        self.assertIn("schedule", wf)


if __name__ == "__main__":
    unittest.main()
