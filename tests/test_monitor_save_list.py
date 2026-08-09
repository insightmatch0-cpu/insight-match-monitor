# -*- coding: utf-8 -*-
"""حارس قائمة الحفظ في monitor.yml (علة 2026-08-09).

العلة: التقييم اللحظي (PR #81) جعل المراقب يكتب lessons_v2.json أثناء
التشغيل، لكن خطوة Save state لم تكن تضمه إلى git add — فبقي تعديلاً غير
محفوظ، ورفض git pull --rebase العمل ("You have unstaged changes")، وفشلت
ثلاث تشغيلات متتالية وتجمدت بيانات اللوحة.

القاعدة الدائمة: كل ملف متتبَّع يمكن أن يكتبه أي سكربت في مسار المراقب
(watchlist → monitor → dashboard_update → watchdog، بما فيه ما يستدعيه
المراقب من predict_v2 كالتقييم اللحظي) يجب أن يظهر في قائمة git add
بخطوة Save state — وإلا عادت العلة نفسها.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MONITOR_YML = ROOT / ".github" / "workflows" / "monitor.yml"

# كل الملفات المتتبَّعة التي يكتبها مسار المراقب اليوم — أي ملف جديد يكتبه
# المسار مستقبلاً يجب إضافته هنا وفي monitor.yml معاً
MONITOR_WRITTEN_FILES = [
    "state.json",             # monitor.py — ذاكرة المباريات الحية
    "data.json",              # dashboard_update.py
    "data_v2.json",           # dashboard_update.py
    "news.json",              # dashboard_update.py
    "watchlist.json",         # watchlist.py + monitor.py
    "predictions_user.json",  # watchlist.py — توقعات المالك
    "scenarios_v2.json",      # monitor.py + التقييم اللحظي (predict_v2)
    "lessons_v2.json",        # التقييم اللحظي يضيف دروساً (علة 2026-08-09)
    "referees.json",          # record_referee أثناء التقييم اللحظي
    "radar_log.json",         # الرادار — إنذارات وتنبيهات
]


class TestMonitorSaveList(unittest.TestCase):
    def setUp(self):
        self.yml = MONITOR_YML.read_text(encoding="utf-8")

    def test_every_written_file_is_saved(self):
        """كل ملف يكتبه مسار المراقب موجود في git add بخطوة Save state."""
        for fname in MONITOR_WRITTEN_FILES:
            pattern = r"git add [^\n]*\b" + re.escape(fname) + r"\b"
            self.assertRegex(
                self.yml, pattern,
                msg=(f"{fname} يكتبه مسار المراقب لكنه ليس في قائمة git add "
                     "بخطوة Save state في monitor.yml — التعديل غير المحفوظ "
                     "سيُفشل git pull --rebase وتفشل التشغيلة كلها "
                     "(العلة الأصلية 2026-08-09)"))

    def test_lessons_regression(self):
        """العلة الأصلية تحديداً: lessons_v2.json محمي بسطر add شرطي."""
        self.assertIn("if [ -f lessons_v2.json ]; then git add lessons_v2.json; fi",
                      self.yml)
