# -*- coding: utf-8 -*-
"""اختبار انحدار: التقرير العالق في pending — علة 2026-08-09.

تقرير Lech Poznan × Aarhus (انطلاق 29 يوليو) بقي "منتظراً" 11 يوماً:
بياناته النهائية موجودة لكن تقييمه يفشل كل صباح، ومهلة الإسقاط كانت
تُطبق على مسار غياب البيانات فقط. ما شُفي لا يمرض مرة أخرى.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import predict_v2 as P


def _entry(date, kickoff):
    return {"fid": "1", "date": date, "kickoff": kickoff,
            "home": "Lech Poznan", "away": "Aarhus",
            "report": "تقرير", "shadow": True}


class TestStuckPendingDropped(unittest.TestCase):

    def _run(self, pending):
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps({"pending": pending, "resolved": []},
                                  ensure_ascii=False), encoding="utf-8")
        orig_file = P.SCENARIOS_FILE
        P.SCENARIOS_FILE = tmp
        # بيانات نهائية موجودة لكن التقييم يفشل — سيناريو العلة بالضبط
        orig_actual = P.actual_match_data
        orig_grade = P.grade_scenario_report
        P.actual_match_data = lambda fid: ("النتيجة 1-0", {"referee": ""})
        P.grade_scenario_report = lambda entry, actual: None
        self.addCleanup(lambda: (setattr(P, "SCENARIOS_FILE", orig_file),
                                 setattr(P, "actual_match_data", orig_actual),
                                 setattr(P, "grade_scenario_report", orig_grade),
                                 tmp.unlink(missing_ok=True)))
        P.resolve_scenarios()
        return json.loads(tmp.read_text(encoding="utf-8"))

    def test_old_entry_with_failing_grade_is_dropped(self):
        """تقرير تجاوز مهلة الإسقاط وتقييمه يفشل → يُسقط لا يُعاد للأبد."""
        scen = self._run({"1": _entry("2026-07-29", "2026-07-29T17:00:00+00:00")})
        self.assertEqual(scen["pending"], {},
                         "التقرير العالق يجب أن يُسقط بعد المهلة")

    def test_fresh_entry_with_failing_grade_is_retried(self):
        """تقرير حديث تقييمه يفشل → يبقى منتظراً (إعادة غداً كالمعتاد)."""
        fresh = (P.now_utc()).strftime("%Y-%m-%d")
        kick = (P.now_utc() - P.timedelta(hours=6)).isoformat()
        scen = self._run({"1": _entry(fresh, kick)})
        self.assertIn("1", scen["pending"],
                      "الفشل الحديث يعاد غداً — لا إسقاط مبكراً")
