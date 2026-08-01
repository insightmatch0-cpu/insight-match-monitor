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


class TestGoldPicks(unittest.TestCase):
    """قسم الاختيارات الذهبية (ثقة 70%+) + تمييز شريحة الـ 70%+ في سجل الدقة
    (طلب المالك 2026-08-01 — رؤية أفضل لطبقة القناعة العالية للمحرك 2)."""

    def test_gold_section_exists(self):
        self.assertIn('id="gold-sec"', HTML)
        self.assertIn('id="gold-list"', HTML)
        self.assertIn('id="gold-count"', HTML)

    def test_gold_v2_only_and_70_threshold(self):
        gate = re.search(r'function renderGold[\s\S]{0,200}engine === "v2"', SCRIPT)
        self.assertIsNotNone(gate, "القسم الذهبي يجب أن يقتصر على تبويب المحرك 2")
        self.assertIn(">= 70", SCRIPT)

    def test_gold_hidden_in_construction_and_loading(self):
        # حالتا "قيد الإنشاء" و"التحميل" لا تمران عبر renderUpcoming — يجب إخفاء القسم فيهما
        for fn in ("renderConstruction", "renderLoadingState"):
            body = re.search(fn + r"\(\)\{([\s\S]*?)\n\}", SCRIPT)
            self.assertIsNotNone(body, fn)
            self.assertIn("gold-sec", body.group(1), f"{fn} يجب أن يخفي القسم الذهبي")

    def test_70_bucket_gold_highlight(self):
        self.assertIn('"70+"?" gold"', SCRIPT.replace("'", '"'))

    def test_shared_card_builder(self):
        # بطاقة واحدة مشتركة بين القائمتين — لا نسختين تتباعدان مع الزمن
        self.assertIn("function predCard", SCRIPT)
        self.assertTrue(SCRIPT.count("predCard(") >= 3)


class TestMarketChip(unittest.TestCase):
    """⚖️ شريحة "المحرك ضد السوق" (طلب المالك 2026-08-01): تظهر فقط عند توفر
    احتمالات السوق، وتتوهج حين يخالف المحرك مرشح السوق."""

    def test_mkt_row_exists_and_used_in_card(self):
        self.assertIn("function mktRow", SCRIPT)
        gate = re.search(r"probRow\(p\)\s*\+\s*mktRow\(p\)", SCRIPT)
        self.assertIsNotNone(gate, "البطاقة يجب أن تعرض شريحة السوق بعد الاحتمالات")

    def test_hidden_without_market_data(self):
        body = re.search(r"function mktRow\(p\)\{([\s\S]*?)\n\}", SCRIPT)
        self.assertIsNotNone(body)
        self.assertIn('p.mkt_home == null) return ""', body.group(1))

    def test_disagreement_highlight(self):
        self.assertIn('fav !== p.pick', SCRIPT)
        self.assertIn('"mkt', SCRIPT.replace("'", '"'))
        self.assertIn("mktDiff", SCRIPT)


if __name__ == "__main__":
    unittest.main()
