# -*- coding: utf-8 -*-
"""عقيدة "لا أسقف قياس" — المسح الشامل بأمر المالك 2026-08-09.

ثلاث حوادث من نفس الفئة (نافذة الـ 1000، تشبع الرادار 300/300، قص قائمة
الانتظار 400) كلها قصّ `[-N:]` يحذف بيانات قياس بصمت. هذا الملف يقفل
الفئة نهائياً: جرد كامل لكل قصّ في الكود مع تصنيفه — أي قصّ جديد غير
مصنّف يُفشل الاختبارات فيُناقش في الـ PR الذي أضافه، لا بعد حادثة.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import monitor as M
import predict as V1
import predict_v2 as P

ROOT = Path(__file__).resolve().parent.parent

# الجرد المعتمد (2026-08-09): كل قصّ معروف مع تصنيفه —
#   display  = شريحة عرض فقط (البيانات الكاملة محفوظة في مكان آخر)
#   prompt   = نافذة حقن في موجّه (ذاكرة عمل، ليست سجل قياس)
#   working  = ذاكرة عمل تشغيلية محدودة بطبيعتها
#   guarded  = سقف طوارئ خلف شرط if قيمته الافتراضية 0 (معطل)
ALLOWED_TRUNCATIONS = {
    "predict.py": {
        "[-30:]": "display — مخطط اليوميات؛ الأرشيف الدائم يحمل الكامل",
        "[-MAX_RESOLVE_CALLS:]": "working — عدد نداءات API لا تخزين",
        "[-RESOLVED_CAP:]": "guarded — طوارئ خلف if والقيمة 0",
    },
    "predict_v2.py": {
        "[-30:]": "display — مخطط اليوميات؛ الأرشيف الدائم يحمل الكامل",
        "[-MAX_RESOLVE_CALLS:]": "working — عدد نداءات API لا تخزين",
        "[-RESOLVED_CAP:]": "guarded — طوارئ خلف if والقيمة 0",
        "[-MAX_LESSONS_STORED:]": "working — دفتر الدروس (HOLD-001 للمعهد)",
        "[-REFEREE_FIDS_CAP:]": "working — حارس ازدواج الحكام (2000)",
        "[-SCENARIOS_RESOLVED_CAP:]": "guarded — طوارئ خلف if والقيمة 0",
        "[-MAX_LESSONS_IN_PROMPT:]": "prompt — أحدث الدروس المحقونة",
        "[-RADAR_RESOLVED_CAP:]": "guarded — طوارئ خلف if والقيمة 0",
    },
    "monitor.py": {
        "[-15:]": "prompt — أحدث الأحداث/الدروس في سياق التحليل الحي",
        "[-RADAR_SNAPS_KEEP:]": "working — سلسلة اللقطات الحية (زخم 10 دقائق)",
        "[-3:]": "working — حزمة أدلة التنبيه (آخر 3 لقطات تُحفظ معه)",
        "[-RADAR_MAX_WARNINGS:]": "guarded — طوارئ خلف if والقيمة 0",
    },
    "dashboard_update.py": {
        "[-300:]": "display — حوض النتائج الأخيرة للوحة؛ السجل الكامل محفوظ",
        "[-LESSONS_ON_DASHBOARD:]": "display — أحدث الدروس المعروضة",
        "[-SHADOW_LAB_ROWS:]": "display — أحدث بطاقات مختبر الظل",
        "[-30:]": "display — اتجاه دقة الرادار آخر 30 يوماً؛ radar_log كامل",
        "[-40:]": "display — «من هو من»: آخر الإنذارات المُقيَّمة للوحة؛ radar_log كامل",
    },
    "scan.py": {},
    "watchlist.py": {},
    "sportmonks_shadow.py": {
        "[-FORM_WINDOW:]": "working — نافذة حساب فورمة xG؛ تاريخ الفرق الكامل محفوظ",
    },
}

_SLICE_RE = re.compile(r"\[-(?:\d+|[A-Z_]+)\s*:\s*\]")


class TestTruncationInventory(unittest.TestCase):
    """أي قصّ [-N:] جديد في الكود يجب أن يُصنَّف هنا وإلا فشل الاختبار."""

    def test_every_truncation_is_classified(self):
        unknown = []
        for fname, allowed in ALLOWED_TRUNCATIONS.items():
            src = (ROOT / fname).read_text(encoding="utf-8")
            for m in _SLICE_RE.finditer(src):
                token = re.sub(r"\s", "", m.group(0))
                if token not in allowed:
                    line = src.count("\n", 0, m.start()) + 1
                    unknown.append(f"{fname}:{line} {token}")
        self.assertEqual(unknown, [],
                         "قصّ غير مصنّف — صنّفه في ALLOWED_TRUNCATIONS مع "
                         "مبرره في نفس الـ PR (عقيدة لا-أسقف-قياس 2026-08-09): "
                         + "؛ ".join(unknown))


class TestPendingQueuesUncapped(unittest.TestCase):
    """قائمتا انتظار الرادار (إنذارات ودراما) لا تُقصان قبل التقييم أبداً."""

    def test_queue_cap_disabled_by_default(self):
        self.assertEqual(M.RADAR_MAX_WARNINGS, 0,
                         "قص قائمة الانتظار عاد — ثقب قياس ممنوع بأمر المالك")

    def test_all_measurement_caps_are_zero(self):
        self.assertEqual(V1.RESOLVED_CAP, 0)
        self.assertEqual(P.RESOLVED_CAP, 0)
        self.assertEqual(P.RADAR_RESOLVED_CAP, 0)
        self.assertEqual(P.SCENARIOS_RESOLVED_CAP, 0)

    def test_slices_are_guarded_in_source(self):
        import inspect
        src = inspect.getsource(M.maybe_radar_alert)
        self.assertIn("if RADAR_MAX_WARNINGS", src)
        src = inspect.getsource(M.radar_sweep)
        self.assertIn("if RADAR_MAX_WARNINGS", src)


class TestConfigDriftLaw(unittest.TestCase):
    """قانون الحارس 13: إعادة تفعيل أي سقف قياس تُكتشف أول صباح، لا بعد شهر."""

    def _drift_violations(self):
        for name, v in P.integrity_check():
            if "انجراف" in name:
                return v
        self.fail("قانون انجراف الإعدادات غائب عن حارس النزاهة")

    def test_clean_config_passes(self):
        import tempfile, json
        # عزل ملفات الحارس حتى لا يكتب في ملفات المستودع الحقيقية
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text("{}", encoding="utf-8")
        orig = P.HISTORY_FILE
        P.HISTORY_FILE = tmp
        self.addCleanup(lambda: (setattr(P, "HISTORY_FILE", orig),
                                 tmp.unlink(missing_ok=True)))
        self.assertEqual(self._drift_violations(), [])

    def test_reactivated_cap_is_caught_next_morning(self):
        import tempfile
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text("{}", encoding="utf-8")
        orig_hist = P.HISTORY_FILE
        P.HISTORY_FILE = tmp
        old = P.RADAR_RESOLVED_CAP
        try:
            P.RADAR_RESOLVED_CAP = 300
            v = self._drift_violations()
            self.assertTrue(any("RADAR_RESOLVED_CAP=300" in x for x in v))
        finally:
            P.RADAR_RESOLVED_CAP = old
            P.HISTORY_FILE = orig_hist
            tmp.unlink(missing_ok=True)
