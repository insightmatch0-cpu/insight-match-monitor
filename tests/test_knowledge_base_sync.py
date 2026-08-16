# -*- coding: utf-8 -*-
"""🛡 حارس مواءمة قاعدة المعرفة — الامتثال كاختبار (أمر المالك 2026-08-16).

«كل تغيير يجب أن يحدّث قاعدة المعرفة افتراضياً — أحد يحرسها ويراقبها
ويجعل كل شيء متوائماً، مثل الامتثال.» — هذا الملف هو ذلك الأحد.

الآلية بنفس عقيدة المستودع (كل قاعدة = اختبار يمنع الدمج): جدول حقائق
مُثبَّت يربط **ثوابت الكود الحية** بعبارات **صفحة غرفة التحكم**
(report/control-room.html — مصدر الصفحة المنشورة والـPDF اليومي معاً).
من غيّر ثابتاً في الكود دون تحديث الصفحة في نفس الـPR، احمرّ الفحص
ووقف الدمج — فتبقى قاعدة المعرفة صادقة **بالإجبار لا بالتذكر**.

القيمة تُقرأ من الكود لحظة الفحص (لا تُنسخ هنا): تغيير RADAR_RED من 65
إلى 70 مثلاً يجعل النمط المطلوب «أحمر 70» فيسقط الفحص حتى تقول الصفحة
70 أيضاً. وحقائق الميزات تربط وجود الميزة في اللوحة بوجود شرحها في الدليل.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import monitor as M
import predict_v2 as P

PAGE = (ROOT / "report" / "control-room.html").read_text(encoding="utf-8")
PORTAL = (ROOT / "index.html").read_text(encoding="utf-8")


def _fact_table():
    """جدول الحقائق: (اسم الحقيقة، القيمة الحية من الكود، الأنماط المطلوبة
    في الصفحة — كل نمط يُبنى من القيمة نفسها فيتبعها إن تغيّرت)."""
    return [
        ("عتبة الكهرماني", M.RADAR_AMBER,
         [f"كهرماني ≥{M.RADAR_AMBER}", f"كهرماني {M.RADAR_AMBER}-"]),
        ("عتبة الأحمر", M.RADAR_RED,
         [f"أحمر {M.RADAR_RED} فأعلى", f"أحمر ≥{M.RADAR_RED}"]),
        ("بداية نافذة التنبيه", M.RADAR_ALERT_MIN,
         [f"د{M.RADAR_ALERT_MIN}"]),
        ("نهاية نافذة التنبيه", M.RADAR_ALERT_MAX,
         [f"د{M.RADAR_ALERT_MAX}"]),
        ("إيقاع المسار السريع", M.FOCUS_SWEEP_SECONDS,
         [f"{M.FOCUS_SWEEP_SECONDS}</span> ثانية",
          f"{M.FOCUS_SWEEP_SECONDS} ثانية"]),
        ("حصة تقارير الظل", M.SHADOW_REPORTS_PER_DAY,
         [f"الـ<span class=\"num\">{M.SHADOW_REPORTS_PER_DAY}</span> كلها",
          f"حصص التقارير الـ{M.SHADOW_REPORTS_PER_DAY}"]),
        ("حارس العينة المفلترة", P.MIN_FILTERED_SAMPLE,
         [f"{P.MIN_FILTERED_SAMPLE}</span> عيّنة", f"{P.MIN_FILTERED_SAMPLE} عيّنة",
          f"من {P.MIN_FILTERED_SAMPLE}"]),
    ]


class TestNumericFactsAligned(unittest.TestCase):
    """كل ثابت مُثبَّت في الجدول يجب أن تقوله الصفحة بقيمته الحالية."""

    def test_every_pinned_fact_appears_with_current_value(self):
        missing = []
        for name, value, patterns in _fact_table():
            if not any(pat in PAGE for pat in patterns):
                missing.append(f"{name} (القيمة الحية {value})")
        self.assertEqual(missing, [],
                         "قاعدة المعرفة انحرفت عن الكود — حدّث "
                         "report/control-room.html في نفس الـ PR: "
                         + "؛ ".join(missing))


class TestFeatureDocsAligned(unittest.TestCase):
    """كل ميزة معروضة على اللوحة يجب أن يشرحها الدليل — والعكس ممنوع:
    لا يشرح الدليل ميزة أزيلت من اللوحة."""

    FEATURES = [
        # (اسم الميزة، دليل وجودها في index.html، العبارة المطلوبة في الصفحة)
        ("منحنى تصاعد الخطر", "dangerClimb(", "تصاعد الخطر"),
        ("سطر الخطر لصالح", "radarThreat", "الخطر لصالح"),
        ("قائمة منطقة التنبيه", "radarRecent", "منطقة التنبيه (د75+)"),
        ("مفتاح دورياتي/الكل", "im-scope", "دورياتي"),
    ]

    def test_portal_features_are_documented(self):
        problems = []
        for name, portal_token, page_phrase in self.FEATURES:
            in_portal = portal_token in PORTAL
            in_page = page_phrase in PAGE
            if in_portal and not in_page:
                problems.append(f"{name}: موجودة في اللوحة وغائبة عن الدليل")
            if not in_portal and in_page:
                problems.append(f"{name}: أزيلت من اللوحة وما زال الدليل يشرحها")
        self.assertEqual(problems, [],
                         "مواءمة الميزات انكسرت — أصلح الدليل في نفس الـ PR: "
                         + "؛ ".join(problems))


class TestListSemanticsMatch(unittest.TestCase):
    """دلالة قائمة منطقة التنبيه واحدة في اللوحة والدليل (أمر «لا فرق»)."""

    def test_alert_zone_window_stated_identically(self):
        # اللوحة تعلن د75+ وآخر 24 ساعة في عنوانها — الدليل يقولهما أيضاً
        self.assertIn("منطقة التنبيه (د75+)", PORTAL)
        self.assertIn("آخر 24 ساعة", PAGE)
        self.assertIn("منطقة التنبيه (د75+)", PAGE)


if __name__ == "__main__":
    unittest.main()
