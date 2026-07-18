# -*- coding: utf-8 -*-
"""اختبار قائمة سيناريوهات المحرك 2 — تقارير ما قبل المباراة والتحليل الحي."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import monitor as M


class TestScenarioMenu(unittest.TestCase):
    """درس كيري×شيلبورن (2026-07-18): مباراة كأس ذهبت لركلات الترجيح ولم يكن
    في القائمة أي سيناريو للأشواط الإضافية/الترجيح — أُضيف ويجب ألا يُحذف."""

    def test_penalty_shootout_scenario_present(self):
        self.assertIn("ركلات الترجيح", M.SCENARIO_MENU_V2)
        self.assertIn("الإضافية", M.SCENARIO_MENU_V2)

    def test_menu_injected_into_both_prompts(self):
        """القائمة مشتركة بين التحليل الحي وتقرير ما قبل المباراة."""
        self.assertIn(M.SCENARIO_MENU_V2, M.SYSTEM_PROMPT_LIVE_V2)
        self.assertIn(M.SCENARIO_MENU_V2, M.SYSTEM_PROMPT_PREMATCH)

    def test_core_scenarios_still_present(self):
        for kw in ("الهدف القادم", "الركنيات", "البطاقات", "الكرات الثابتة"):
            self.assertIn(kw, M.SCENARIO_MENU_V2)


if __name__ == "__main__":
    unittest.main()
