# -*- coding: utf-8 -*-
"""اختبارات بوابة التنبيه الحي (طلب المالك 2026-07-26): صمت تام عند فراغ القائمة.

القاعدة الجديدة: تنبيهات تيليجرام الحية (بداية/هدف/نهاية + تحليل المحرك 2 المباشر
+ النبض) تُرسَل لمباريات قائمة التركيز فقط. إذا كانت القائمة فارغة فلا تنبيه حي
إطلاقاً — لا رجوع للدوريات الكبرى كما كان سابقاً. ملخصات الصباح لا تمرّ بهذه الدالة
وتظل تصل دائماً. كل إصلاح يتحول هنا إلى اختبار دائم (قاعدة SLA).
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import monitor as M

# دوري كبير حقيقي من TOP_LEAGUE_IDS (الدوري الإنجليزي الممتاز) ودوري عادي غير مُدرَج.
TOP_LEAGUE = {"id": 39}
OTHER_LEAGUE = {"id": 99999}


class TestShouldAlert(unittest.TestCase):
    def test_empty_watchlist_silences_top_league(self):
        """الحارس الأساسي: قائمة فارغة → لا تنبيه حتى لمباراة دوري كبير."""
        self.assertFalse(M.should_alert(TOP_LEAGUE, "1", set()))

    def test_empty_watchlist_silences_other_league(self):
        self.assertFalse(M.should_alert(OTHER_LEAGUE, "1", set()))

    def test_watchlist_match_is_alerted(self):
        """مباراة على القائمة تُنبَّه بغض النظر عن دوريها."""
        self.assertTrue(M.should_alert(OTHER_LEAGUE, "1", {"1"}))
        self.assertTrue(M.should_alert(TOP_LEAGUE, "1", {"1"}))

    def test_non_watchlist_match_not_alerted_when_list_nonempty(self):
        """قائمة غير فارغة → مبارياتها فقط؛ دوري كبير خارج القائمة لا يُنبَّه."""
        self.assertFalse(M.should_alert(TOP_LEAGUE, "2", {"1"}))
        self.assertFalse(M.should_alert(OTHER_LEAGUE, "2", {"1"}))

    def test_no_top_league_fallback_remains(self):
        """حارس ضد عودة السلوك القديم: يجب ألا تعتمد الدالة على TOP_LEAGUE_IDS
        حين تكون القائمة فارغة."""
        import inspect
        src = inspect.getsource(M.should_alert)
        # عند القائمة الفارغة يجب أن ترجع الدالة False مباشرةً، لا فحص الدوري.
        self.assertIn("if not watch", src)


if __name__ == "__main__":
    unittest.main()
