"""حارس المفاتيح المحجوزة في state.json (عطل 2026-08-15).

حلقة تنظيف الذاكرة في monitor.py كانت تحذف كل مفتاح ليس مباراة حية —
فمحت كل دورة: `delivery` (نبض التسليم — سُجّل «2/2» في 15:35 و16:10
ومُحي في الدورة التالية، مثبت من تاريخ الكوميتات)، و`api_alerts`
(تهدئة الـ6 ساعات — محوها يعيد إغراق الرسائل الذي بُنيت لمنعه)،
و`deadman.alerted_for` (محوه يجعل إنذار «لم يجرِ التقييم» يتكرر كل
10 دقائق بدل مرة يومياً).

نفس فئة درس WK-League: قائمة سوداء تفشل مفتوحة. العلاج المحروس هنا:
الحذف بالنمط الرقمي فقط (مفاتيح المباريات كلها أرقام)، فأي مفتاح
محجوز — حاضر أو مستقبلي — يفشل مغلقاً أي ينجو تلقائياً.
"""

import re
import unittest
from pathlib import Path

import monitor

ROOT = Path(__file__).resolve().parent.parent

# المفاتيح المحجوزة المعروفة اليوم — القائمة للتوثيق والاختبار، لا يعتمد
# عليها الكود الإنتاجي (الحماية بالنمط الرقمي كي ينجو أي مفتاح مستقبلي)
RESERVED_TODAY = ["api_alerts", "api_quota", "deadman", "delivery", "xg_live"]


class TestPruneDeadMatches(unittest.TestCase):

    def _state(self):
        s = {
            "1001": {"score": "1-0"},   # حية
            "1002": {"score": "2-2"},   # انتهت — تُحذف
            1003: {"score": "0-0"},     # مفتاح int (قبل أول حفظ JSON) — يُحذف أيضاً
        }
        for k in RESERVED_TODAY:
            s[k] = {"marker": k}
        return s

    def test_dead_fixtures_are_pruned_and_live_kept(self):
        state = self._state()
        monitor.prune_dead_matches(state, {"1001"})
        self.assertIn("1001", state)
        self.assertNotIn("1002", state)
        self.assertNotIn(1003, state)

    def test_reserved_keys_survive_pruning(self):
        """جوهر العطل: المفاتيح المحجوزة كانت تُمحى كل دورة."""
        state = self._state()
        monitor.prune_dead_matches(state, {"1001"})
        for k in RESERVED_TODAY:
            with self.subTest(key=k):
                self.assertIn(k, state, f"المفتاح المحجوز {k} مُحي — عاد عطل 2026-08-15")

    def test_future_reserved_key_survives_without_listing(self):
        """يفشل مغلقاً: مفتاح محجوز جديد لم يُدرج في أي قائمة ينجو تلقائياً."""
        state = {"9999": {}, "future_guard_key": {"x": 1}}
        monitor.prune_dead_matches(state, set())
        self.assertIn("future_guard_key", state)
        self.assertNotIn("9999", state)

    def test_empty_live_ids_clears_all_fixtures_only(self):
        """ليلة بلا مباريات: تُمسح المباريات كلها وتبقى ذاكرة الحارس كاملة."""
        state = self._state()
        monitor.prune_dead_matches(state, set())
        self.assertEqual(sorted(k for k in state), sorted(RESERVED_TODAY))

    def test_main_uses_prune_not_inline_delete(self):
        """بنيوي: main() يستدعي الدالة، ولا حلقة حذف عارية بلا حارس النمط."""
        src = (ROOT / "monitor.py").read_text(encoding="utf-8")
        self.assertIn("prune_dead_matches(state, live_ids)", src)
        # الحلقة القديمة: del state[fid] داخل شرط لا يفحص isdigit
        bare = re.compile(r"if fid not in live_ids:\s*\n\s*del state\[fid\]")
        self.assertIsNone(
            bare.search(src),
            "عادت حلقة الحذف العارية — كل مفتاح محجوز سيُمحى كل دورة",
        )


if __name__ == "__main__":
    unittest.main()
