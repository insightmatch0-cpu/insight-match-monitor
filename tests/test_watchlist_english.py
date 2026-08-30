# -*- coding: utf-8 -*-
"""اختبارات انحدار: الأسماء الإنجليزية في رسائل قائمة التركيز (طلب المالك 2026-08-30).

«التلغرام يجب عليه أن يقرأ أسماء الفرق بالإنجليزي في حال (ركز)» — كل رسائل
قائمة التركيز تعرض الاسم الإنجليزي الرسمي بجانب العربي ليتحقق المالك من هوية
الفريق المطابَق، والمفسّر وشبكة الأمان يفهمان الأسماء الإنجليزية في أوامره.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import watchlist as W

SRC = (ROOT / "watchlist.py").read_text(encoding="utf-8")

AR = {"fid": "1", "home": "Barcelona", "away": "Athletic Club",
      "ar_home": "برشلونة", "ar_away": "أتلتيك بلباو",
      "league": "الدوري الإسباني", "kickoff": "2099-01-01T20:00:00+00:00",
      "date": "2099-01-01"}
EN_ONLY = {"fid": "2", "home": "Quepos Cambute", "away": "AD Cofutpa",
           "ar_home": "", "ar_away": "", "league": "", "kickoff": "", "date": ""}


class TestEnglishInFocusMessages(unittest.TestCase):
    """الاسم الإنجليزي يظهر في التسمية — ولا يتكرر إذا كان المعروض إنجليزياً أصلاً."""

    def test_label_carries_english_pair(self):
        label = W.match_label(AR)
        self.assertIn("برشلونة 🆚 أتلتيك بلباو", label)
        self.assertIn("Barcelona × Athletic Club", label)

    def test_label_no_duplicate_when_already_english(self):
        label = W.match_label(EN_ONLY)
        self.assertEqual(label, "Quepos Cambute 🆚 AD Cofutpa")
        self.assertNotIn("(", label)

    def test_english_pair_empty_on_missing_names(self):
        self.assertEqual(W.english_pair({"home": "?", "away": "?"}), "")
        self.assertEqual(W.english_pair({}), "")

    def test_stored_watchlist_label_carries_english(self):
        """التسمية المخزّنة في watchlist.json هي مصدر كل تنبيهات التركيز اللاحقة
        (التقرير، الادعاءات، الحصاد) — لذا يكفي حملها الإنجليزية لتصل للجميع."""
        data = {"matches": {}}
        W.apply_action("set", ["1"], {"1": AR}, data)
        self.assertIn("Barcelona × Athletic Club", data["matches"]["1"]["label"])

    def test_confirmation_message_carries_english(self):
        data = {"matches": {}}
        msg = W.apply_action("set", ["1"], {"1": AR}, data)
        self.assertIn("Barcelona × Athletic Club", msg)

    def test_pick_buttons_source_uses_english_pair(self):
        """رسالة أزرار التوقع تعرض الإنجليزية أيضاً (فحص بنيوي على المصدر)."""
        seg = SRC[SRC.find("def send_pick_buttons"):SRC.find("def candidate_from_watchlist")]
        self.assertIn("english_pair", seg)

    def test_record_user_picks_source_uses_english_pair(self):
        seg = SRC[SRC.find("def record_user_picks"):SRC.find("def cleanup_expired")]
        self.assertIn("english_pair", seg)


class TestEnglishInputUnderstood(unittest.TestCase):
    """المفسّر وشبكة الأمان يقرآن الأسماء الإنجليزية في أوامر «ركز» — تثبيت
    للسلوك القائم حتى لا يُفقد بتعديل لاحق."""

    def test_interpreter_prompt_pins_english_matching(self):
        seg = SRC[SRC.find("def interpret"):SRC.find("FALLBACK_STOPWORDS")]
        self.assertIn("عربي أو إنجليزي", seg)
        # القائمة المرسلة للمفسّر تحمل الاسمين الإنجليزيين
        self.assertIn('"home": c["home"]', seg.replace("'", '"'))

    def test_fallback_match_reads_english_names(self):
        cands = {"1": AR, "2": EN_ONLY}
        self.assertEqual(W.fallback_match("ركز Barcelona", cands), ["1"])
        self.assertEqual(W.fallback_match("focus on cofutpa please", cands), ["2"])


if __name__ == "__main__":
    unittest.main()
