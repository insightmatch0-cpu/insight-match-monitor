# -*- coding: utf-8 -*-
"""🎯 دمج مباريات قائمة التركيز في التمرير العادي (حادثة 2026-09-05).

سبت التوقف الدولي: 129 مباراة حية، التمرير العادي التهم ميزانية الدقائق كاملة،
الرصد السريع لم يعمل ولا مرة، وصافرة نهاية 4 من 5 مفضلات لم تُرَ قط لأن بث
live=all لا يحمل المباراة المنتهية. القاعدة: تنبيهات المفضلة لا تعتمد على
مسار قد يُجوَّع؛ نداء معرّفات واحد في التمرير العادي يضمن رؤية النهاية."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import monitor as M


def fx(fid, status, gh=0, ga=0):
    return {"fixture": {"id": int(fid), "status": {"short": status, "elapsed": 90}},
            "league": {"id": 1, "name": "L", "country": "C"},
            "teams": {"home": {"name": "A"}, "away": {"name": "B"}},
            "goals": {"home": gh, "away": ga}}


class TestMergeFocusFixtures(unittest.TestCase):
    def test_finished_tracked_favorite_is_merged_and_first(self):
        live = [fx(1, "2H"), fx(2, "1H")]
        state = {"9": {"status": "2H", "score": "1-0"}}
        calls = []
        def fetch(path):
            calls.append(path); return [fx(9, "FT", 1, 2)]
        out = M.merge_focus_fixtures(live, {"9"}, state, fetch=fetch)
        self.assertEqual([str(f["fixture"]["id"]) for f in out], ["9", "1", "2"])
        self.assertEqual(calls, ["fixtures?ids=9"])

    def test_finished_untracked_favorite_is_not_merged(self):
        """أُضيفت للقائمة بعد انتهائها — لا تنبيه نهاية وهمي."""
        out = M.merge_focus_fixtures([fx(1, "2H")], {"9"}, {}, fetch=lambda p: [fx(9, "FT")])
        self.assertEqual([str(f["fixture"]["id"]) for f in out], ["1"])

    def test_already_final_in_state_is_not_merged_again(self):
        state = {"9": {"status": "FT"}}
        out = M.merge_focus_fixtures([], {"9"}, state, fetch=lambda p: [fx(9, "FT")])
        self.assertEqual(out, [])

    def test_not_started_is_not_merged_but_live_is(self):
        state = {}
        out = M.merge_focus_fixtures([], {"9", "8"}, state,
                                     fetch=lambda p: [fx(9, "NS"), fx(8, "1H")])
        self.assertEqual([str(f["fixture"]["id"]) for f in out], ["8"])

    def test_favorites_present_in_feed_move_to_front_without_fetch(self):
        live = [fx(1, "2H"), fx(9, "1H"), fx(2, "HT")]
        def fetch(path):
            raise AssertionError("no fetch when nothing is missing")
        out = M.merge_focus_fixtures(live, {"9"}, {}, fetch=fetch)
        self.assertEqual([str(f["fixture"]["id"]) for f in out], ["9", "1", "2"])

    def test_fetch_failure_keeps_feed(self):
        live = [fx(1, "2H")]
        def boom(path):
            raise RuntimeError("api down")
        out = M.merge_focus_fixtures(live, {"9"}, {"9": {"status": "2H"}}, fetch=boom)
        self.assertEqual(out, live)

    def test_kill_switch_and_empty_watch(self):
        live = [fx(1, "2H")]
        self.assertIs(M.merge_focus_fixtures(live, set(), {}, fetch=None), live)
        old = M.FOCUS_IDS_MERGE
        try:
            M.FOCUS_IDS_MERGE = False
            self.assertIs(M.merge_focus_fixtures(live, {"9"}, {}, fetch=None), live)
        finally:
            M.FOCUS_IDS_MERGE = old

    def test_wired_right_after_live_fetch(self):
        src = (ROOT / "monitor.py").read_text(encoding="utf-8")
        body = src[src.index("def main("):]
        i = body.index("fixtures = get_live_fixtures()")
        j = body.index("merge_focus_fixtures(fixtures, watch, state)")
        self.assertLess(i, j)
        self.assertLess(j, body.index("for fx in fixtures:"))


if __name__ == "__main__":
    unittest.main()
