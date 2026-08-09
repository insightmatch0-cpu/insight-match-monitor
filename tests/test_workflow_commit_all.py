# -*- coding: utf-8 -*-
"""اختبارات خطوة الحفظ في مسارات العمل (إصلاح 2026-08-09 — قاعدة SLA).

العطل الذي حدث فعلاً: خطوة "Save state" في monitor.yml كانت تُدرِج قائمة ملفات
مكتوبة باليد، ثم تعمل `git pull --rebase` قبل الدفع. أي ملف متتبَّع تعدّله
التشغيلة ولم يرد في القائمة يبقى معدَّلاً في شجرة العمل، فيرفض git الـ rebase:

    error: cannot pull with rebase: You have unstaged changes.

الخمس محاولات كلها تفشل، تنتهي التشغيلة بالخطأ، ويُهدَم الرَنَر ومعه كل ما
حسبته التشغيلة: تجمّدت data.json على main مساء 2026-08-09 (تشغيلتان متتاليتان
21:00 و21:10 UTC) — وهو تجمّد دائم لا يُشفى ذاتياً لأن كل تشغيلة تالية تفشل مثله.

الإصلاح المحروس هنا: `git add -A` — تُدرَج كل مخرجات التشغيلة، فتبقى شجرة العمل
نظيفة قبل الـ rebase مهما أضيف من ملفات جديدة لاحقاً. القاعدة الدائمة: قائمة
إدراج يدوية قبل rebase هي عطل ينتظر أول ملف جديد.
"""

import re
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
SAVING = ("monitor.yml", "predict.yml", "predict_v2.yml")


def _save_step(text: str) -> str:
    """نص خطوة الحفظ (من git config حتى نهاية حلقة الدفع)."""
    start = text.find('git config user.name')
    assert start != -1, "لا توجد خطوة حفظ"
    return text[start:]


class TestWorkflowsStageEverything(unittest.TestCase):
    def test_saving_workflows_stage_all_before_rebase(self):
        """كل مسار يدفع بيانات يجب أن يُدرِج الكل، لا قائمة يدوية."""
        for name in SAVING:
            with self.subTest(workflow=name):
                step = _save_step((WORKFLOWS / name).read_text(encoding="utf-8"))
                self.assertIn("git add -A", step,
                              f"{name}: يجب إدراج كل المخرجات قبل الـ rebase")

    def test_no_hand_written_file_list(self):
        """لا عودة للقائمة اليدوية: `git add <ملف>.json` يعيد العطل حرفياً."""
        pattern = re.compile(r"git add\s+(?!-A\b)[^\s]*\.json")
        for name in SAVING:
            with self.subTest(workflow=name):
                step = _save_step((WORKFLOWS / name).read_text(encoding="utf-8"))
                self.assertIsNone(
                    pattern.search(step),
                    f"{name}: إدراج ملفات مسمّاة يترك غيرها معدَّلاً فيفشل الـ rebase")

    def test_rebase_still_guarded_by_retry_and_hard_failure(self):
        """الإصلاح لا يجوز أن يُضعف الحماية القائمة: إعادة محاولة + فشل صريح."""
        for name in SAVING:
            with self.subTest(workflow=name):
                step = _save_step((WORKFLOWS / name).read_text(encoding="utf-8"))
                self.assertIn("git pull --rebase", step)
                self.assertIn('[ "$ok" = "1" ]', step,
                              f"{name}: يجب أن تفشل الخطوة بوضوح إذا لم يتم الدفع")

    def test_pycache_excluded_so_add_all_is_safe(self):
        """`git add -A` آمن فقط ما دام مخلّفات بايثون مستبعدة."""
        ignored = (WORKFLOWS.parent.parent / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("__pycache__", ignored)


if __name__ == "__main__":
    unittest.main()
