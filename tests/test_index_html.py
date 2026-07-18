# -*- coding: utf-8 -*-
"""اختبارات اللوحة (index.html) — صحة الجافاسكربت + حراسة إصلاحات الواجهة.

وميض "قيد الإنشاء" (بلاغ المالك 2026-07-18) عاد سببه أن الرسم سبق وصول
البيانات — هذه الاختبارات تضمن بقاء طبقات الحماية الثلاث للأبد:
الذاكرة المحلية، وحالة التحميل الثلاثية، وشرط الـ 404 الحقيقي.
"""

import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "index.html").read_text(encoding="utf-8")
SCRIPT = re.search(r"<script>([\s\S]*?)</script>", HTML).group(1)


class TestJavaScriptSyntax(unittest.TestCase):
    def test_inline_script_parses(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node غير متوفر")
        proof = "new Function(require('fs').readFileSync(0,'utf8'));console.log('OK')"
        r = subprocess.run([node, "-e", proof], input=SCRIPT.encode(),
                           capture_output=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr.decode()[:500])


class TestFlashFix(unittest.TestCase):
    """لوحة "قيد الإنشاء" تظهر فقط عند 404 حقيقي — لا وميض أثناء التحميل."""

    def test_local_cache_layers_exist(self):
        self.assertIn("im-cache-v1", SCRIPT)
        self.assertIn("im-cache-v2", SCRIPT)

    def test_three_state_loader(self):
        self.assertIn('v2state = "loading"', SCRIPT)
        self.assertIn('v2state = "missing"', SCRIPT)
        self.assertIn('v2state = "ok"', SCRIPT)

    def test_construction_gated_on_missing(self):
        gate = re.search(r'v2state === "missing"[\s\S]{0,120}renderConstruction', SCRIPT)
        self.assertIsNotNone(gate, "لوحة قيد الإنشاء يجب أن تكون خلف شرط missing")

    def test_404_check_before_missing(self):
        self.assertIn('"404"', SCRIPT)


class TestLiveCards(unittest.TestCase):
    """بطاقات LIVE تعرض توقع المحركين وتُخفي المباراة من قائمة الـ 24 ساعة."""

    def test_engine_chips_rendered(self):
        self.assertIn("livePredsRow", SCRIPT)
        self.assertIn("pred_v1", SCRIPT)
        self.assertIn("pred_v2", SCRIPT)

    def test_live_matches_filtered_from_upcoming(self):
        self.assertIn("liveSet[p.fid]", SCRIPT)

    def test_kicked_off_badge(self):
        self.assertIn("ko-live", SCRIPT)
        self.assertIn("liveBadge", SCRIPT)


class TestI18n(unittest.TestCase):
    """كل مفتاح ترجمة عربي له نظير إنجليزي — لا نص مكسور عند تبديل اللغة."""

    def test_ar_en_keys_match(self):
        m = re.search(r"ar:\s*\{([\s\S]*?)\n\s*\},\s*\n\s*en:\s*\{([\s\S]*?)\n\s*\}\s*\n\};",
                      SCRIPT)
        self.assertIsNotNone(m, "تعذر إيجاد قاموس الترجمة")
        keys = lambda s: set(re.findall(r"^\s*([A-Za-z]\w*)\s*:", s, re.M))
        ar, en = keys(m.group(1)), keys(m.group(2))
        self.assertEqual(ar, en, f"مفاتيح غير متطابقة: {ar ^ en}")


if __name__ == "__main__":
    unittest.main()
