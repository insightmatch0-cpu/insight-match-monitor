# -*- coding: utf-8 -*-
"""اختبارات الرادار (طلب المالك 2026-08-01): إنذار مبكر رياضي بحت قبل سقوط
التوقع — درجة الخطر، اختيار المباريات، تقييم صدق الإنذارات، ووصولها اللوحة.
صفر Claude بالتصميم: أي اختبار هنا يعمل بلا شبكة وبلا مفاتيح.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard_update as D
import monitor as M
import predict_v2 as P


def snap(minute, h=None, a=None):
    return {"minute": minute, "gh": 0, "ga": 0, "h": h or {}, "a": a or {}}


class TestDangerScore(unittest.TestCase):
    """درجة الخطر: شفافة، محددة، ولا تتطلب أي نداء خارجي."""

    def test_losing_late_is_red_from_scoreboard_alone(self):
        """توقع خاسر في د85 = أحمر حتى لو هدأت الأرقام تماماً."""
        v = M.danger_score("home", [snap(85)], 85, 0, 1)
        self.assertEqual(v["level"], "red")
        self.assertTrue(any("تُسقط التوقع" in f for f in v["factors"]))

    def test_comfortable_lead_quiet_is_green(self):
        """تقدم 2-0 هادئ = أخضر — لا إنذارات زائفة بلا سبب."""
        s = [snap(60, {"sog": 3}, {"sog": 1}), snap(70, {"sog": 3}, {"sog": 1})]
        v = M.danger_score("home", s, 70, 2, 0)
        self.assertEqual(v["level"], "green")
        self.assertEqual(v["score"], 0)

    def test_fragile_lead_with_surge_escalates(self):
        """تقدم بهدف + موجة تسديد وركنيات من الضيف = إنذار كهرماني على الأقل."""
        s = [snap(60, {"sog": 2}, {"sog": 2, "cor": 3, "shots": 5}),
             snap(70, {"sog": 2}, {"sog": 5, "cor": 6, "shots": 9})]
        v = M.danger_score("home", s, 70, 1, 0)
        self.assertGreaterEqual(v["score"], M.RADAR_AMBER)
        self.assertTrue(any("تسديد" in f for f in v["factors"]))

    def test_red_card_against_picked_side(self):
        s = [snap(50, {"rc": 1}, {})]
        v = M.danger_score("home", s, 50, 1, 0)
        self.assertTrue(any("نقص عددي" in f for f in v["factors"]))

    def test_draw_pick_threatened_by_either_side(self):
        """توقع تعادل: زخم أي طرف يهدده — يؤخذ الأعلى."""
        s = [snap(60, {"sog": 1}, {"sog": 1}),
             snap(70, {"sog": 4}, {"sog": 1})]
        v = M.danger_score("draw", s, 70, 0, 0)
        self.assertGreaterEqual(v["score"], 10 + 12)

    def test_single_snapshot_no_momentum_no_crash(self):
        v = M.danger_score("away", [snap(10)], 10, 0, 0)
        self.assertIn("level", v)

    def test_score_clamped_0_100(self):
        s = [snap(80, {"rc": 1, "sv": 0}, {"sog": 0, "cor": 0, "shots": 0}),
             snap(89, {"rc": 1, "sv": 9}, {"sog": 9, "cor": 9, "shots": 20})]
        v = M.danger_score("home", s, 89, 0, 1)
        self.assertLessEqual(v["score"], 100)


class TestRadarSelection(unittest.TestCase):
    """اختيار مباريات الرادار: القائمة أولاً ثم الكبرى ثم الأعلى ثقة، وسقف صارم."""

    def test_priority_watchlist_then_top_then_confidence(self):
        state = {"1": {"status": "1H"}, "2": {"status": "2H"},
                 "3": {"status": "NS"}, "4": {"status": "1H"}}
        v2p = {"1": {"top": False, "confidence": 80},
               "2": {"top": True, "confidence": 50},
               "3": {"top": True, "confidence": 90},
               "4": {"top": False, "confidence": 60}}
        order = M.select_radar_fixtures(state, v2p, {"4"})
        self.assertEqual(order, ["4", "2", "1"])   # "3" ليست حية — تُستبعد

    def test_no_prediction_no_radar(self):
        state = {"9": {"status": "1H"}}
        self.assertEqual(M.select_radar_fixtures(state, {}, set()), [])

    def test_cap_respected(self):
        state = {str(i): {"status": "1H"} for i in range(30)}
        v2p = {str(i): {"top": False, "confidence": 50} for i in range(30)}
        out = M.select_radar_fixtures(state, v2p, set())
        self.assertEqual(len(out), M.RADAR_STATS_CAP)


class TestRadarGrading(unittest.TestCase):
    """لوحة صدق الرادار: الإنذار يُقيَّم صباحاً على النتيجة الحقيقية — صفر نداءات."""

    def _with_tmp_log(self, payload, store):
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        orig = P.RADAR_LOG_FILE
        P.RADAR_LOG_FILE = tmp
        try:
            graded = P.resolve_radar_log(store)
            return graded, json.loads(tmp.read_text(encoding="utf-8"))
        finally:
            P.RADAR_LOG_FILE = orig
            tmp.unlink(missing_ok=True)

    def test_true_warning_counted_as_hit(self):
        graded, log = self._with_tmp_log(
            {"warnings": [{"fid": "9", "date": "2026-08-01",
                           "level": "red", "score": 70}]},
            {"resolved": [{"fid": "9", "correct": False, "score": "0-1"}]})
        self.assertEqual(graded, 1)
        self.assertTrue(log["resolved"][0]["failed"])
        self.assertEqual(log["meta"]["stats"]["red"], {"fired": 1, "hit": 1})

    def test_false_alarm_counted_honestly(self):
        """إنذار لم يتحقق يُسجل كإنذار كاذب — الصدق قبل التجميل."""
        graded, log = self._with_tmp_log(
            {"warnings": [{"fid": "5", "date": "2026-08-01",
                           "level": "amber", "score": 45}]},
            {"resolved": [{"fid": "5", "correct": True, "score": "2-0"}]})
        self.assertEqual(graded, 1)
        self.assertFalse(log["resolved"][0]["failed"])
        self.assertEqual(log["meta"]["stats"]["amber"], {"fired": 1, "hit": 0})

    def test_unresolved_warning_waits(self):
        """مباراة بلا نتيجة بعد — الإنذار ينتظر صباحاً آخر ولا يُفقد."""
        graded, log = self._with_tmp_log(
            {"warnings": [
                {"fid": "7", "date": P.now_utc().strftime("%Y-%m-%d"),
                 "level": "red", "score": 70},
                {"fid": "8", "date": "2026-08-01", "level": "red", "score": 70},
            ]},
            {"resolved": [{"fid": "8", "correct": False, "score": "0-2"}]})
        self.assertEqual(graded, 1)
        self.assertEqual([w["fid"] for w in log["warnings"]], ["7"])


class TestRadarDashboard(unittest.TestCase):
    """بيانات الرادار تصل اللوحة مع البطاقة الحية — درجة وعوامل واتجاهات."""

    def test_radar_attached_with_trend(self):
        state = {"5": {"status": "1H", "home": "H", "away": "A", "score": "1-0",
                       "minute": 30,
                       "radar": {"snaps": [{"minute": 20, "h": {"sog": 1, "cor": 2},
                                            "a": {"sog": 0, "cor": 1}},
                                           {"minute": 30, "h": {"sog": 2, "cor": 2},
                                            "a": {"sog": 2, "cor": 3}}],
                                 "score": 55, "level": "amber",
                                 "factors": ["x"], "pick": "home", "confidence": 70}}}
        live = D.build_live(state, {}, {})
        r = live[0]["radar"]
        self.assertEqual(r["score"], 55)
        self.assertEqual(r["level"], "amber")
        self.assertEqual(r["trend"]["h_sog"], [1, 2])
        self.assertEqual(r["trend"]["a_cor"], [1, 3])

    def test_no_radar_no_field(self):
        state = {"6": {"status": "1H", "home": "H", "away": "A",
                       "score": "0-0", "minute": 10}}
        self.assertNotIn("radar", D.build_live(state, {}, {})[0])

    def test_radar_sweep_wired_into_monitor_main(self):
        """حارس: الرادار يعمل ضمن الدورة الرئيسية ولا يستطيع إسقاطها."""
        import inspect
        src = inspect.getsource(M.main)
        self.assertIn("radar_sweep(", src)

    def test_radar_grading_wired_into_predict_v2_main(self):
        import inspect
        src = inspect.getsource(P.main)
        self.assertIn("resolve_radar_log(", src)

    def test_radar_survives_entry_rebuild(self):
        """ذاكرة الرادار (اللقطات) يجب أن تنجو من إعادة بناء الحالة كل دورة."""
        import inspect
        src = inspect.getsource(M.main)
        self.assertIn('prev.get("radar")', src)


class TestRadarFastLane(unittest.TestCase):
    """⚡ المسار السريع (طلب المالك 2026-08-01 — "الأسرع"): تحديث ~90 ثانية
    ونشر مباشر إلى فرع radar-live — بلا مفاتيح مكشوفة وبلا إعادة بناء Pages."""

    def test_fast_snap_replaces_within_window(self):
        """لقطات الـ 90 ثانية تستبدل الأخيرة — الزخم يبقى مقاساً على ~10 دقائق."""
        snaps = [{"minute": 40, "h": {"sog": 1}, "a": {}},
                 {"minute": 50, "h": {"sog": 2}, "a": {}}]
        out = M.merge_fast_snap(snaps, {"minute": 52, "h": {"sog": 3}, "a": {}})
        self.assertEqual(len(out), 2)
        self.assertEqual(out[-1]["minute"], 52)
        self.assertEqual(out[0]["minute"], 40)

    def test_snap_appends_after_gap(self):
        snaps = [{"minute": 40, "h": {}, "a": {}}]
        out = M.merge_fast_snap(snaps, {"minute": 50, "h": {}, "a": {}})
        self.assertEqual(len(out), 2)

    def test_snaps_capped(self):
        snaps = [{"minute": m, "h": {}, "a": {}} for m in range(0, 120, 10)]
        out = M.merge_fast_snap(snaps, {"minute": 130, "h": {}, "a": {}})
        self.assertLessEqual(len(out), M.RADAR_SNAPS_KEEP)

    def test_live_payload_shape(self):
        state = {"1": {"status": "1H", "home": "H", "away": "A", "league": "L",
                       "score": "1-0", "minute": 30,
                       "radar": {"snaps": [{"minute": 30, "h": {"sog": 2}, "a": {"sog": 1}}],
                                 "score": 20, "level": "green", "factors": [],
                                 "pick": "home", "confidence": 60}},
                 "2": {"status": "FT", "radar": {"score": 50}},   # منتهية — تُستبعد
                 "3": {"status": "1H"}}                            # بلا رادار — تُستبعد
        p = M.radar_live_payload(state)
        self.assertIn("updated", p)
        self.assertEqual(len(p["matches"]), 1)
        self.assertEqual(p["matches"][0]["radar"]["trend"]["h_sog"], [2])

    def test_publish_silent_without_token(self):
        """بلا GH_TOKEN (محلياً/بيئة ناقصة) — لا استثناء ولا نشر، صمت آمن."""
        orig = M.GH_TOKEN
        M.GH_TOKEN = ""
        try:
            self.assertFalse(M.publish_radar_live({}))
        finally:
            M.GH_TOKEN = orig

    def test_fast_watch_respects_deadline(self):
        """ميزانية منتهية = خروج فوري بلا أي نداء أو نوم — تسليم نظيف للدورة التالية."""
        import time as _t
        state = {"1": {"status": "1H", "radar": {"score": 50}}}
        t0 = _t.monotonic()
        self.assertEqual(M.radar_fast_watch(state, set(), _t.monotonic() - 1), 0)
        self.assertLess(_t.monotonic() - t0, 2)

    def test_wired_into_main(self):
        import inspect
        src = inspect.getsource(M.main)
        self.assertIn("radar_fast_watch(", src)
        self.assertIn("publish_radar_live(", src)

    def test_monitor_yml_passes_token(self):
        yml = (Path(__file__).resolve().parent.parent
               / ".github" / "workflows" / "monitor.yml").read_text(encoding="utf-8")
        run_monitor = yml.split("Run monitor")[1].split("run:")[0]
        self.assertIn("GH_TOKEN", run_monitor)


if __name__ == "__main__":
    unittest.main()
