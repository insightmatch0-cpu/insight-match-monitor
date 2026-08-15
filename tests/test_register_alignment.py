# -*- coding: utf-8 -*-
"""🔗 حارس تطابق السجلات — سجل المواعيد (reminders.json) مقابل سجل
الاستمرارية في CLAUDE.md.

سبب الوجود (طلب المالك 2026-08-15: «تأكد أن كل شيء متطابق مع كل الغرف»):
المشروع يُدار من غرف عمليات متعددة تعمل بالتوازي، وكل غرفة تكتب في السجلات.
وقع الانحراف فعلاً: سعر Sportmonks بقي €29 في CLAUDE.md بينما الفاتورة
الحقيقية €571 — خطأ بـ20 ضعفاً عاش في العقيدة حتى اكتُشف بالمصادفة، وكل
مفاضلة تكلفة كُتبت قبل اكتشافه بُنيت عليه.

الدرس المعمَّم من ذلك: **التطابق الذي يُفحص شهرياً بالعين ينحرف بين الفحصين.**
التدقيق الشهري يبقى، لكن هذا الاختبار يجعل الانحراف يسقط في الـPR الذي
يُحدثه لا بعد شهر — نفس منطق قانون انحراف الإعدادات في حارس النزاهة.

صفر شبكة وصفر مفاتيح: يقرأ ملفين على القرص فقط.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
REMINDERS = ROOT / "reminders.json"
CLAUDE_MD = ROOT / "CLAUDE.md"


def _deadlines():
    d = json.loads(REMINDERS.read_text(encoding="utf-8"))
    return d.get("deadlines") or d.get("items") or []


class TestRegisterAlignment(unittest.TestCase):

    def setUp(self):
        self.md = CLAUDE_MD.read_text(encoding="utf-8")
        self.items = _deadlines()

    def test_reminders_file_is_not_empty(self):
        """سجل مواعيد فارغ = صمت كامل عن كل اشتراك. لا يمرّ بصمت."""
        self.assertGreater(len(self.items), 0)

    def test_every_billable_price_appears_in_the_doctrine(self):
        """كل سعر يُدفع فعلاً موجود في CLAUDE.md — لا سعر يعيش في ملف واحد.

        هذا هو الاختبار الذي كان سيمسك خطأ الـ€29 يوم كُتب.
        """
        for item in self.items:
            price = (item.get("price") or "").strip()
            if not price or item.get("status") in ("done", "deferred"):
                continue
            # نأخذ الجزء الرقمي من السعر (مثل «€571/شهر» → «571»)
            digits = "".join(c for c in price if c.isdigit())
            if not digits:
                continue          # «مجاني» وما شابه — لا رقم يُطابَق
            with self.subTest(service=item.get("service")):
                self.assertIn(digits, self.md,
                              f"السعر {price} للخدمة {item.get('service')} "
                              f"غير مذكور في CLAUDE.md — السجلان انحرفا")

    def test_no_stale_sportmonks_price_presented_as_current(self):
        """€29 لا يجوز أن يظهر كسعر **حالي** — بقاؤه كسرد تاريخي مقصود.

        الشرط: كل ذكر لـ€29 يجب أن يجاوره تصحيح صريح في السطر نفسه.
        """
        for line in self.md.split("\n"):
            if "€29" not in line:
                continue
            with self.subTest(line=line[:60]):
                self.assertTrue(
                    "€571" in line,
                    "ذُكر €29 بلا تصحيح €571 في السطر نفسه — "
                    "قارئ عابر سيأخذه سعراً حالياً")

    def test_active_deadlines_have_a_date(self):
        """موعد مفتوح بلا تاريخ لا يُنبِّه أبداً — يجب أن يكون مُرجأً صراحةً."""
        for item in self.items:
            if item.get("status") in ("done", "deferred"):
                continue
            with self.subTest(id=item.get("id")):
                self.assertTrue(item.get("due"),
                                f"{item.get('id')} مفتوح بلا تاريخ — "
                                f"إما تاريخ أو status=deferred صراحةً")

    def test_reminders_module_is_registered_in_the_file_table(self):
        """قاعدة العقيدة: كل ملف جديد يأخذ صفاً في جدول الملفات بنفس الـPR."""
        self.assertIn("`reminders.py`", self.md)
        self.assertIn("reminders.json", self.md)

    def test_xg_window_length_agrees_between_digest_and_dashboard(self):
        """نافذة الظل رقم واحد في كل واجهة عرض — لا «يوم 3 من 21» هنا
        و«من 35» هناك (انحراف حقيقي أمسكه فحص 15 أغسطس: اللوحة بقيت
        على 21 بعد تمديد المالك)."""
        import dashboard_update as D
        import predict_v2 as P
        self.assertEqual(D.XG_SHADOW_TOTAL_DAYS, P.XG_SHADOW_DAYS)

    def test_xg_verdict_date_is_consistent_across_code_and_doctrine(self):
        """نافذة الظل مُدِّدت إلى 35 يوماً — لا يبقى تاريخ حكم قديم فاعلاً."""
        import predict_v2 as P
        self.assertEqual(P.XG_SHADOW_DAYS, 35)
        verdict = next((i for i in self.items
                        if i.get("id") == "xg_shadow_verdict"), None)
        self.assertIsNotNone(verdict, "حكم تجربة xG غير مسجَّل في المواعيد")
        self.assertEqual(verdict.get("due"), "2026-09-17")
        self.assertIn("17 سبتمبر", self.md)


if __name__ == "__main__":
    unittest.main()
