# -*- coding: utf-8 -*-
"""اختبارات REC-002 (قرار المالك 2026-08-08): إكمال سجل المعايرة المحقون.

العيبان المعالجان: (1) الشريحة تحت 50 — أغلبية التوقعات — كانت غائبة كلياً
عن الموجّه، (2) شريحة بعينة 5 مباريات كانت تُعرض كحقيقة ("70%+: 100% (5/5)")
فتصنع غروراً مبنياً على لا شيء. قاعدة SLA: ما شُفي لا يمرض مرة أخرى.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import predict_v2 as P


def make_stats(b70=(0, 0), b60=(0, 0), b50=(0, 0), blow=(0, 0)):
    """يبني قاموس إحصاءات كامل بشرائح ثقة محددة (صحيح، إجمالي)."""
    def d(pair):
        return {"correct": pair[0], "total": pair[1]}
    total = sum(x[1] for x in (b70, b60, b50, blow))
    correct = sum(x[0] for x in (b70, b60, b50, blow))
    return {
        "overall": {"correct": correct, "total": total},
        "last30": {"correct": correct, "total": total},
        "top_leagues": {"correct": 0, "total": 0},
        "other_leagues": {"correct": correct, "total": total},
        "by_confidence": {
            "70+": d(b70), "60-69": d(b60), "50-59": d(b50), "<50": d(blow),
        },
        "daily": {},
    }


class TestCalibrationText(unittest.TestCase):
    def test_low_bucket_present(self):
        """جوهر REC-002 (أ): شريحة الثقة تحت 50 تظهر في السجل المحقون."""
        text = P.calibration_text(
            make_stats(b70=(20, 25), b60=(30, 50), b50=(40, 90), blow=(90, 200)))
        self.assertIn("عندما كانت ثقتك تحت 50%", text)
        self.assertIn("45% (90/200)", text)

    def test_small_sample_flagged(self):
        """جوهر REC-002 (ب): السطر المضلل '70%+: 100% (5/5)' يُوسم صراحة."""
        text = P.calibration_text(
            make_stats(b70=(5, 5), b60=(30, 50), b50=(40, 90), blow=(90, 200)))
        line70 = next(l for l in text.split("\n") if "70%+" in l)
        self.assertIn("100% (5/5)", line70)
        self.assertIn("عينة غير كافية — لا تعتمد عليها", line70)

    def test_large_sample_not_flagged(self):
        stats = make_stats(b70=(20, 25), b60=(30, 50), b50=(40, 90),
                           blow=(90, 200))
        for line in P.calibration_text(stats).split("\n"):
            if "عندما كانت ثقتك" in line:
                self.assertNotIn("عينة غير كافية", line, line)

    def test_flag_threshold_boundary(self):
        """19 = توسم، 20 = لا توسم (الحد CALIBRATION_MIN_BUCKET)."""
        below = P._confidence_line("70%+", {"correct": 10, "total": 19})
        at = P._confidence_line("70%+", {"correct": 10, "total": 20})
        self.assertIn("عينة غير كافية", below)
        self.assertNotIn("عينة غير كافية", at)

    def test_empty_bucket_not_flagged(self):
        """شريحة فارغة تعرض 'لا يوجد سجل بعد' بلا وسم عينة."""
        line = P._confidence_line("70%+", {"correct": 0, "total": 0})
        self.assertIn("لا يوجد سجل بعد", line)
        self.assertNotIn("عينة غير كافية", line)

    def test_no_history_message_unchanged(self):
        text = P.calibration_text(make_stats())
        self.assertIn("لا يوجد سجل تاريخي بعد", text)

    def test_all_four_buckets_listed(self):
        """السجل يعرض الشرائح الأربع كلها — لا شريحة غائبة بعد اليوم."""
        text = P.calibration_text(
            make_stats(b70=(20, 25), b60=(30, 50), b50=(40, 90), blow=(90, 200)))
        for label in ("70%+", "60-69%", "50-59%", "تحت 50%"):
            self.assertIn(f"عندما كانت ثقتك {label}", text)


if __name__ == "__main__":
    unittest.main()
