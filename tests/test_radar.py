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

    def test_early_minutes_not_amber(self):
        """ملاحظة المالك 2026-08-02 (لقطة الشاشة): 0-0 في د5 مع توقع فوز
        ليس "إنذاراً" — كانت المعادلة القديمة تصنع إنذارات ضجيج مبكرة."""
        v = M.danger_score("home", [snap(5)], 5, 0, 0)
        self.assertEqual(v["level"], "green")
        self.assertLess(v["score"], M.RADAR_AMBER)

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

    def test_drama_alerts_graded_by_their_own_rules(self):
        """كل ادعاء يُحاكم بقاعدته: قلب النتيجة = فوز، التعادل = إدراكه فعلاً،
        والإخفاق يُسجل بلا تجميل — لوحة عقل S3."""
        graded, log = self._with_tmp_log(
            {"alerts": [
                {"fid": "1", "date": "2026-08-01", "key": "flip",
                 "side": "away", "score_at": "1-0"},
                {"fid": "2", "date": "2026-08-01", "key": "equalizer",
                 "side": "home", "score_at": "0-1"},
                {"fid": "3", "date": "2026-08-01", "key": "goal",
                 "side": "home", "score_at": "0-1"},
            ]},
            {"resolved": [
                {"fid": "1", "correct": False, "score": "1-2"},   # الضيف قلبها → صح
                {"fid": "2", "correct": True, "score": "1-1"},    # أدرك التعادل → صح
                {"fid": "3", "correct": True, "score": "0-2"},    # لم يسجل → خطأ
            ]})
        self.assertEqual(graded, 3)
        by_fid = {a["fid"]: a for a in log["alerts_resolved"]}
        self.assertTrue(by_fid["1"]["hit"])
        self.assertTrue(by_fid["2"]["hit"])
        self.assertFalse(by_fid["3"]["hit"])
        stats = log["meta"]["stats"]["alerts"]
        self.assertEqual(stats["flip"], {"fired": 1, "hit": 1})
        self.assertEqual(stats["goal"], {"fired": 1, "hit": 0})

    def test_unresolved_drama_alert_waits(self):
        graded, log = self._with_tmp_log(
            {"alerts": [{"fid": "7", "date": P.now_utc().strftime("%Y-%m-%d"),
                         "key": "flip", "side": "home", "score_at": "0-1"}]},
            {"resolved": []})
        self.assertEqual(graded, 0)
        self.assertEqual(len(log["alerts"]), 1)

    def test_drama_scoreboard_line_in_digest(self):
        """الرؤية اليومية الإلزامية (درس 2026-08-02: السجل الأول 1/6 وُجد ولم
        يصل المالك إلا بسؤاله): سطر لوحة الدراما في الملخص الصباحي."""
        import tempfile
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        today = P.now_utc().strftime("%Y-%m-%d")
        tmp.write_text(json.dumps({"alerts_resolved": [
            {"key": "next_goal", "hit": True, "graded_on": today},
            {"key": "equalizer", "hit": False, "graded_on": today},
            {"key": "goal", "hit": False, "graded_on": "2026-08-01"},
        ]}), encoding="utf-8")
        orig = P.RADAR_LOG_FILE
        P.RADAR_LOG_FILE = tmp
        try:
            line = P.drama_scoreboard_line()
        finally:
            P.RADAR_LOG_FILE = orig
            tmp.unlink(missing_ok=True)
        self.assertIn("1/3", line)                 # الإجمالي بصدق
        self.assertIn("1/2", line)                 # حصاد اليوم
        self.assertIn("تجريبية", line)
        # بلا سجل → لا سطر (لا ادعاء بلا قياس)
        tmp2 = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp2.write_text("{}", encoding="utf-8")
        P.RADAR_LOG_FILE = tmp2
        try:
            self.assertEqual(P.drama_scoreboard_line(), "")
        finally:
            P.RADAR_LOG_FILE = orig
            tmp2.unlink(missing_ok=True)
        import inspect
        self.assertIn("drama_scoreboard_line()", inspect.getsource(P.main))

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


class TestDramaAlerts(unittest.TestCase):
    """🚨 عقل S3 (سيناريوهات المالك الحرفية 2026-08-01): تنبيه هدف/تعادل/قلب
    نتيجة من الدقيقة 75 فقط، مرة لكل مباراة، ويُقيَّم صباحاً بلوحته الخاصة."""

    def test_owner_scenario_1_trailing_team_surging(self):
        """سيناريو المالك 1: الهلال متأخر 0-1 ويضغط بقوة حقيقية (موجة تسديد
        وركنيات وضغط وحارس الخصم محاصر) → تنبيه هدف التعادل.
        معايرة 2026-08-02: موجة الضغط وحدها (65) لم تعد تكفي — السجل الأول 1/6."""
        s = [snap(70, {"sog": 2, "cor": 3, "shots": 5}, {"sv": 2}),
             snap(78, {"sog": 4, "cor": 5, "shots": 8}, {"sv": 4})]
        v = M.evaluate_comeback(s, 78, 0, 1)
        self.assertIsNotNone(v)
        self.assertEqual(v["key"], "equalizer")
        self.assertEqual(v["side"], "home")
        self.assertGreaterEqual(v["signal"], M.RADAR_ALERT_SIGNAL_MIN)

    def test_pressure_wave_alone_no_longer_fires(self):
        """درس السجل الأول (2026-08-02، 1/6 صحيحة): الثلاثي العام (تسديد+ركنيات
        +ضغط = 65) يحدث في أواخر أغلب المباريات — لم يعد يطلق تنبيهاً وحده."""
        s = [snap(70, {"sog": 2, "cor": 3, "shots": 5}, {}),
             snap(78, {"sog": 4, "cor": 5, "shots": 8}, {})]
        self.assertIsNone(M.evaluate_comeback(s, 78, 0, 1))

    def test_owner_scenario_2_red_card_flips_expectation(self):
        """سيناريو المالك 2: الهلال 0-1 وطُرد لاعب من النصر مع ضغط → قلب النتيجة."""
        s = [snap(72, {"sog": 2, "cor": 1}, {"rc": 0}),
             snap(80, {"sog": 4, "cor": 3}, {"rc": 1})]
        v = M.evaluate_comeback(s, 80, 0, 1)
        self.assertIsNotNone(v)
        self.assertEqual(v["key"], "flip")
        self.assertTrue(any("نقص عددي" in r for r in v["reasons"]))

    def test_owner_condition_no_alert_before_75(self):
        """شرط المالك الصريح: لا تنبيه قبل الدقيقة 75 مهما اشتد الضغط."""
        s = [snap(60, {"sog": 2, "cor": 3, "shots": 5}, {"sv": 2}),
             snap(70, {"sog": 5, "cor": 6, "shots": 9}, {"sv": 5})]
        self.assertIsNone(M.evaluate_comeback(s, 70, 0, 1))

    def test_no_alert_after_85(self):
        """معايرة 2026-08-02: "هدف قادم" في د90 بلا قيمة (خسائر السجل الأول
        كانت في د88-90) — لا تنبيهات بعد د85."""
        s = [snap(80, {"sog": 2, "cor": 3, "shots": 5}, {"sv": 2}),
             snap(90, {"sog": 4, "cor": 5, "shots": 8}, {"sv": 4})]
        self.assertIsNone(M.evaluate_comeback(s, 90, 0, 1))

    def test_hopeless_margin_silent(self):
        s = [snap(70, {"sog": 2}, {}), snap(80, {"sog": 5}, {})]
        self.assertIsNone(M.evaluate_comeback(s, 80, 0, 3))

    def test_quiet_match_silent(self):
        s = [snap(70, {"sog": 2}, {}), snap(80, {"sog": 2}, {})]
        self.assertIsNone(M.evaluate_comeback(s, 80, 0, 1))

    def test_draw_needs_clear_dominance(self):
        """في التعادل (الادعاء الأكثر ضجيجاً في السجل الأول): عتبة أشد (80)
        وهيمنة أوضح (فارق 30) — وإلا صمت."""
        s = [snap(70, {"sog": 1, "cor": 1, "shots": 3}, {"sv": 1}),
             snap(80, {"sog": 3, "cor": 3, "shots": 6}, {"sv": 3})]
        v = M.evaluate_comeback(s, 80, 1, 1)
        self.assertIsNotNone(v)
        self.assertEqual(v["key"], "next_goal")
        self.assertEqual(v["side"], "home")
        # شد وجذب متكافئ → صمت
        s2 = [snap(70, {"sog": 1}, {"sog": 1}), snap(80, {"sog": 3}, {"sog": 3})]
        self.assertIsNone(M.evaluate_comeback(s2, 80, 1, 1))

    def test_two_goal_deficit_needs_stronger_signal(self):
        s = [snap(70, {"sog": 2, "cor": 3}, {}), snap(80, {"sog": 4, "cor": 5}, {})]
        self.assertIsNone(M.evaluate_comeback(s, 80, 0, 2))   # 50-15=35 لا يكفي
        s_red = [snap(70, {"sog": 2, "cor": 1, "shots": 4}, {"rc": 1}),
                 snap(80, {"sog": 4, "cor": 3, "shots": 7}, {"rc": 1})]
        v = M.evaluate_comeback(s_red, 80, 0, 2)              # 100-15=85 يكفي
        self.assertIsNotNone(v)
        self.assertEqual(v["key"], "goal")

    def _capture_telegram(self):
        sent = []
        orig = M.send_telegram
        M.send_telegram = lambda text: sent.append(text)
        self.addCleanup(lambda: setattr(M, "send_telegram", orig))
        return sent

    def _tmp_radar_file(self):
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text("{}", encoding="utf-8")
        orig = M.RADAR_FILE
        M.RADAR_FILE = tmp
        self.addCleanup(lambda: (setattr(M, "RADAR_FILE", orig),
                                 tmp.unlink(missing_ok=True)))
        return tmp

    def test_alert_sent_once_then_upgraded_only(self):
        """التنبيه مرة واحدة — ولا يتكرر إلا ترقيةً لادعاء أقوى (تعادل → قلب)،
        موسوماً 🧪 في المرحلة التجريبية ومرفقاً بحزمة أدلته الرقمية."""
        sent = self._capture_telegram()
        tmp = self._tmp_radar_file()
        e = {"score": "0-1", "minute": 78, "home": "Al Hilal", "away": "Al Nassr",
             "league": "Saudi Pro League",
             "radar": {"snaps": [snap(70, {"sog": 2, "cor": 3, "shots": 5}, {"sv": 2}),
                                 snap(78, {"sog": 4, "cor": 5, "shots": 8}, {"sv": 4})]}}
        budget = {"used": 0}
        self.assertTrue(M.maybe_radar_alert("9", e, budget))
        self.assertIn("تنبيه دراما", sent[0])
        self.assertIn("🧪", sent[0])            # وسم المرحلة التجريبية (قرار المالك)
        self.assertIn("د78", sent[0])
        # نفس الحالة مرة أخرى → صمت (لا إزعاج)
        self.assertFalse(M.maybe_radar_alert("9", e, budget))
        # طرد يرفع الادعاء إلى قلب النتيجة → ترقية مسموحة
        e["minute"] = 82
        e["radar"]["snaps"] = [snap(74, {"sog": 2, "cor": 1, "shots": 4}, {"rc": 0}),
                               snap(82, {"sog": 4, "cor": 3, "shots": 7}, {"rc": 1})]
        self.assertTrue(M.maybe_radar_alert("9", e, budget))
        self.assertEqual(len(sent), 2)
        log = json.loads(tmp.read_text(encoding="utf-8"))
        self.assertEqual(len(log["alerts"]), 2)
        self.assertEqual(log["alerts"][1]["key"], "flip")
        # حزمة الأدلة: اللقطات التي بُني عليها التنبيه محفوظة معه للتشريح
        self.assertTrue(log["alerts"][0].get("evidence"))

    def test_alert_cap_respected(self):
        sent = self._capture_telegram()
        self._tmp_radar_file()
        e = {"score": "0-1", "minute": 78, "home": "H", "away": "A",
             "radar": {"snaps": [snap(70, {"sog": 2, "cor": 3, "shots": 5}, {"sv": 2}),
                                 snap(78, {"sog": 4, "cor": 5, "shots": 8}, {"sv": 4})]}}
        budget = {"used": M.RADAR_ALERT_CAP_PER_RUN}
        self.assertFalse(M.maybe_radar_alert("9", dict(e), budget))
        self.assertEqual(sent, [])

    def test_wired_into_both_radar_loops(self):
        import inspect
        self.assertIn("maybe_radar_alert(", inspect.getsource(M.radar_sweep))
        self.assertIn("maybe_radar_alert(", inspect.getsource(M.radar_fast_watch))

    def test_drama_signal_exposed_for_funnel(self):
        """قمع الاستباق: الإشارة الخام تُحسب قبل شرط د75 وتصل اللوحة."""
        s = [snap(40, {"sog": 2, "cor": 3}, {}), snap(50, {"sog": 4, "cor": 5}, {})]
        d = M.drama_signal(s, 0, 1)
        self.assertEqual(d["side"], "home")
        self.assertGreaterEqual(d["signal"], 50)   # جاهزة — تنتظر د75 فقط
        import inspect
        for fn in (M.radar_sweep, M.radar_fast_watch, M.radar_live_payload):
            self.assertIn('"drama"', inspect.getsource(fn))

    def test_alerted_flag_survives_sweep_rebuild(self):
        """علم "أُرسل التنبيه" يجب أن ينجو من إعادة بناء الرادار كل دورة —
        وإلا تكرر نفس التنبيه كل 10 دقائق (إزعاج ممنوع)."""
        import inspect
        src = inspect.getsource(M.radar_sweep)
        self.assertIn('"alerted": radar.get("alerted")', src)


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

    def test_scores_map_covers_all_live_matches(self):
        """بلاغ المالك 2026-08-02 (0-3 في الواقع و0-2 على اللوحة): التغذية
        السريعة تحمل نتيجة كل مباراة حية — لا مباريات الرادار فقط."""
        state = {"1": {"status": "1H", "score": "0-3", "minute": 90,
                       "seen": "2026-08-02T00:00:00+00:00"},   # بلا رادار
                 "2": {"status": "FT", "score": "1-0"}}         # منتهية — تُستبعد
        p = M.radar_live_payload(state)
        self.assertIn("scores", p)
        self.assertEqual(p["scores"]["1"]["score"], "0-3")
        self.assertNotIn("2", p["scores"])

    def test_fast_watch_sweeps_all_live_not_only_radar(self):
        """المسار السريع يواصل ما دامت أي مباراة حية (النتائج للجميع)،
        ويستخدم نداء live=all واحداً بدل نداء ids للرادار فقط."""
        import inspect
        src = inspect.getsource(M.radar_fast_watch)
        self.assertIn('fixtures?live=all', src)
        self.assertIn("LIVE_STATUSES for e in state.values()", src)

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

    def test_seen_timestamp_stamped_everywhere(self):
        """بلاغ المالك 2026-08-02: كل كتابة لحالة مباراة حية تحمل لحظة رصدها —
        الدورة الرئيسية، الرصد السريع، والمسار السريع للرادار."""
        import inspect
        for fn in (M.main, M.focus_fast_watch, M.radar_fast_watch):
            self.assertIn('"seen"', inspect.getsource(fn), fn.__name__)
        self.assertIn('"seen"', inspect.getsource(M.radar_live_payload))

    def test_monitor_yml_passes_token(self):
        yml = (Path(__file__).resolve().parent.parent
               / ".github" / "workflows" / "monitor.yml").read_text(encoding="utf-8")
        run_monitor = yml.split("Run monitor")[1].split("run:")[0]
        self.assertIn("GH_TOKEN", run_monitor)


if __name__ == "__main__":
    unittest.main()


class TestSweepAlertRace(unittest.TestCase):
    """سباق الكاتبَين (2026-08-15): radar_sweep يحمل نسخته من السجل في بدايته
    ويحفظها في نهايته، وكان maybe_radar_alert يكتب للقرص نسخة خاصة أثناء
    الجولة — فيدهس الحفظُ الختامي كلَّ تنبيه أُطلق خلالها. تنبيها Mito
    Hollyhock وAlverca وصلا تيليجرام واختفيا من سجل القياس نهائياً.
    العلاج: كاتب واحد — الجولة تمرر نسختها الحية للتنبيه."""

    def _capture_telegram(self):
        sent = []
        orig = M.send_telegram
        M.send_telegram = lambda text: sent.append(text)
        self.addCleanup(lambda: setattr(M, "send_telegram", orig))
        return sent

    def _tmp_radar_file(self):
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text("{}", encoding="utf-8")
        orig = M.RADAR_FILE
        M.RADAR_FILE = tmp
        self.addCleanup(lambda: (setattr(M, "RADAR_FILE", orig),
                                 tmp.unlink(missing_ok=True)))
        return tmp

    def _drama_entry(self):
        return {"score": "0-1", "minute": 78, "home": "H", "away": "A",
                "radar": {"snaps": [snap(70, {"sog": 2, "cor": 3, "shots": 5}, {"sv": 2}),
                                    snap(78, {"sog": 4, "cor": 5, "shots": 8}, {"sv": 4})]}}

    def test_alert_lands_in_shared_log_and_survives_sweep_final_write(self):
        """إعادة تمثيل السباق كاملاً: نسخة الجولة الحية فيها إنذار، التنبيه
        يُلحق بها نفسها، وحفظ الجولة الختامي يحمل الاثنين معاً."""
        self._capture_telegram()
        tmp = self._tmp_radar_file()
        sweep_log = {"warnings": [{"fid": "1", "level": "red"}]}
        ok = M.maybe_radar_alert("9", self._drama_entry(), {"used": 0}, sweep_log)
        self.assertTrue(ok)
        self.assertEqual(len(sweep_log.get("alerts") or []), 1,
                         "التنبيه يجب أن يدخل النسخة الحية نفسها لا نسخة منفصلة")
        # حفظ الجولة الختامي (كما يفعل radar_sweep حين log_dirty)
        M.RADAR_FILE.write_text(json.dumps(sweep_log, ensure_ascii=False),
                                encoding="utf-8")
        saved = json.loads(tmp.read_text(encoding="utf-8"))
        self.assertEqual(len(saved["alerts"]), 1, "التنبيه دُهس — عاد سباق 2026-08-15")
        self.assertEqual(len(saved["warnings"]), 1)

    def test_fast_lane_call_without_log_still_writes_file(self):
        """المسار السريع (بلا نسخة مشتركة) يبقى كما كان: كتابة مباشرة للملف."""
        self._capture_telegram()
        tmp = self._tmp_radar_file()
        ok = M.maybe_radar_alert("9", self._drama_entry(), {"used": 0})
        self.assertTrue(ok)
        saved = json.loads(tmp.read_text(encoding="utf-8"))
        self.assertEqual(len(saved["alerts"]), 1)

    def test_sweep_passes_live_log_to_both_alert_paths(self):
        """بنيوي: يسقط لو عاد نداء التنبيه داخل الجولة بلا النسخة الحية."""
        import inspect
        src = inspect.getsource(M.radar_sweep)
        self.assertIn("maybe_radar_alert(fid, e, alert_budget, log)", src)
        self.assertIn("maybe_red_alert(fid, e, alert_budget, log)", src)


class TestDangerClimb(unittest.TestCase):
    """منحنى تصاعد الخطر (طلب المالك 2026-08-16): البطاقة كانت تعرض الدرجة
    الحالية فقط، فلا يُفرَّق بين 70 هابطة من 90 و70 صاعدة من 40 — وهما
    حالتان متعاكستان. المنحنى يُشتق من نفس اللقطات فيطابقها حتماً."""

    def test_series_length_matches_snaps(self):
        snaps = [snap(60, {"sog": 1}, {"sog": 1}),
                 snap(70, {"sog": 3}, {"sog": 1}),
                 snap(80, {"sog": 5}, {"sog": 1})]
        s = M.danger_series("away", snaps)
        self.assertEqual(len(s), len(snaps))

    def test_escalation_is_visible_as_rising_numbers(self):
        """توقع الضيف والنتيجة تنقلب ضده مع تصاعد الدقيقة → المنحنى يصعد."""
        snaps = [dict(snap(50, {"sog": 1}, {"sog": 1}), gh=0, ga=0),
                 dict(snap(70, {"sog": 3}, {"sog": 1}), gh=1, ga=0),
                 dict(snap(88, {"sog": 6}, {"sog": 1}), gh=1, ga=0)]
        s = M.danger_series("away", snaps)
        self.assertLess(s[0], s[-1], "المنحنى لا يُظهر التصاعد")
        self.assertGreaterEqual(s[-1], M.RADAR_AMBER)

    def test_last_point_equals_current_score(self):
        """آخر نقطة في المنحنى = الدرجة المعروضة كبيرة على البطاقة."""
        snaps = [dict(snap(60, {"sog": 1}, {"sog": 1}), gh=0, ga=0),
                 dict(snap(85, {"sog": 4}, {"sog": 1}), gh=1, ga=0)]
        s = M.danger_series("away", snaps)
        now = M.danger_score("away", snaps, 85, 1, 0)["score"]
        self.assertEqual(s[-1], now)

    def test_empty_and_broken_snaps_are_safe(self):
        self.assertEqual(M.danger_series("home", []), [])
        self.assertEqual(len(M.danger_series("home", [{}, {}])), 2)

    def test_wired_into_payloads_and_card(self):
        import inspect
        from pathlib import Path
        self.assertIn("danger_series", inspect.getsource(M.radar_sweep))
        self.assertIn("danger_series", inspect.getsource(M.radar_fast_watch))
        self.assertIn('"danger"', inspect.getsource(M._radar_trend))
        html = (Path(__file__).resolve().parent.parent / "index.html").read_text(encoding="utf-8")
        self.assertIn("dangerClimb(tr.danger, tr.min)", html)
        dash = (Path(__file__).resolve().parent.parent / "dashboard_update.py").read_text(encoding="utf-8")
        self.assertIn('"danger": radar.get("dscores")', dash)


class TestEarlyRedWarningAlert(unittest.TestCase):
    """🔴 إنذار الرادار الأحمر المبكر إلى تيليجرام (قرار المالك 2026-08-19).

    الجوهر المقاس الذي بُني عليه القرار: 196 من 223 إنذاراً أحمر تُطلق د86+
    وتصف لوحة نتائج مباراة منتهية (دقة 97% بلا قيمة)، بينما شريحة ≤د85
    (27 إنذاراً، 85%) هي الاستباقية الحقيقية. فالسقف الزمني ليس تحفظاً
    بل هو الميزة نفسها — وهذه الاختبارات تحرسه."""

    def setUp(self):
        self.sent = []
        orig = M.send_telegram
        M.send_telegram = lambda t, **k: self.sent.append(t)
        self.addCleanup(lambda: setattr(M, "send_telegram", orig))

    @staticmethod
    def _match(minute=80, score="1-1"):
        return {"home": "Alpha", "away": "Beta", "score": score,
                "minute": minute, "radar": {}}

    RED = {"level": "red", "score": 72,
           "factors": ["ضغط هجومي متصاعد", "حارس الخصم تحت الحصار"]}

    def test_early_red_is_sent(self):
        e = self._match(minute=80)
        ok = M.maybe_red_warning_alert("1", e, self.RED, 80, "home", 61,
                                       {"used": 0})
        self.assertTrue(ok)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("إنذار الرادار", self.sent[0])
        self.assertIn("د80", self.sent[0])
        self.assertIn("Alpha", self.sent[0])

    def test_late_red_is_silent(self):
        """د86+ = قراءة لوحة نتائج لا إنذار — القلب النابض للقرار."""
        for m in (86, 90):
            e = self._match(minute=m)
            ok = M.maybe_red_warning_alert("1", e, self.RED, m, "home", 61,
                                           {"used": 0})
            self.assertFalse(ok, f"د{m} يجب أن يبقى شاشةً فقط")
        self.assertEqual(self.sent, [])

    def test_amber_never_alerts(self):
        e = self._match()
        ok = M.maybe_red_warning_alert(
            "1", e, {"level": "amber", "score": 45, "factors": []}, 80,
            "home", 61, {"used": 0})
        self.assertFalse(ok)
        self.assertEqual(self.sent, [])

    def test_once_per_match(self):
        e = self._match()
        b = {"used": 0}
        self.assertTrue(M.maybe_red_warning_alert("1", e, self.RED, 80,
                                                  "home", 61, b))
        self.assertFalse(M.maybe_red_warning_alert("1", e, self.RED, 82,
                                                   "home", 61, b),
                         "تكرار الإنذار لنفس المباراة")
        self.assertEqual(len(self.sent), 1)

    def test_flag_survives_radar_rebuild(self):
        """الجولة العادية تعيد بناء e['radar'] كاملاً — علم warn_alerted
        يجب أن يُنسخ صراحةً وإلا رنّ الهاتف كل 10 دقائق (درس alerted)."""
        src = Path(M.__file__).read_text(encoding="utf-8")
        self.assertIn('"warn_alerted": radar.get("warn_alerted")', src)

    def test_budget_is_independent_of_drama(self):
        """سقف مستقل: ازدحام الدراما يجب ألا يبتلع إنذارات الرادار."""
        src = Path(M.__file__).read_text(encoding="utf-8")
        self.assertIn("warn_budget", src)
        self.assertNotIn("RED_WARN_ALERT_CAP_PER_RUN", "RADAR_ALERT_CAP_PER_RUN")
        e = self._match()
        self.assertFalse(M.maybe_red_warning_alert(
            "1", e, self.RED, 80, "home", 61,
            {"used": M.RED_WARN_ALERT_CAP_PER_RUN}))

    def test_kill_switch(self):
        orig = M.RED_WARN_ALERT
        M.RED_WARN_ALERT = False
        self.addCleanup(lambda: setattr(M, "RED_WARN_ALERT", orig))
        e = self._match()
        self.assertFalse(M.maybe_red_warning_alert("1", e, self.RED, 80,
                                                   "home", 61, {"used": 0}))
        self.assertEqual(self.sent, [])

    def test_alert_minute_is_recorded_separately(self):
        """حادثة أول ليلة (2026-08-20): صفّان مُرسَلان ظهرا بدقيقة 90 رغم أن
        البوابة ≤د85 — لأن `minute` في الصف يُحدَّث لاحقاً كلما ارتفعت الدرجة
        (نقيس أقصى ما رآه الرادار). فبدت البوابة مخروقة وهي سليمة، واستحال
        تدقيق الشريحة المُرسَلة. الحل: دقيقة الإرسال تُحفظ في حقلها الخاص."""
        src = Path(M.__file__).read_text(encoding="utf-8")
        self.assertIn('"alert_minute": minute if warn_sent else None', src)
        self.assertIn('w["alert_minute"] = minute', src)
        # وترتيب المنطق: الوسم يقع قبل ترقية الصف فلا تُطمس دقيقة الإرسال
        i_create = src.index('"alert_minute": minute if warn_sent else None')
        i_upd = src.index('w["alert_minute"] = minute')
        self.assertLess(i_create, i_upd, "مسارا الوسم انقلبا")

    def test_log_row_blocks_repeat_across_runs(self):
        """حادثة Drukpa (2026-08-21): وصل المالك تنبيهان لنفس المباراة بفارق
        دقيقتين. السبب: `warn_alerted` يعيش في state.json المحفوظ **نهاية**
        التشغيلة، بينما radar_log.json يُحفظ داخلها — فتشغيلة أُجهضت بعد
        الإرسال سلّمت الرسالة وفقدت علمها. السجل هو الدليل الدائم على ما
        أُرسل، فصار بوابة ثانية."""
        e = self._match(minute=80)          # ذاكرة نظيفة: العلم مفقود
        log = {"warnings": [{"fid": "1", "alerted": True, "minute": 78}]}
        ok = M.maybe_red_warning_alert("1", e, self.RED, 80, "home", 61,
                                       {"used": 0}, log)
        self.assertFalse(ok, "تكرر التنبيه رغم أن السجل يقول إنه أُرسل")
        self.assertEqual(self.sent, [])
        self.assertTrue((e.get("radar") or {}).get("warn_alerted"),
                        "العلم المفقود لم يُعَد بناؤه في الذاكرة")

    def test_log_without_alerted_row_still_allows_first_alert(self):
        """البوابة الثانية تمنع التكرار فقط — لا تخنق التنبيه الأول."""
        e = self._match(minute=80)
        log = {"warnings": [{"fid": "1", "minute": 78}]}   # صف بلا alerted
        self.assertTrue(M.maybe_red_warning_alert("1", e, self.RED, 80,
                                                  "home", 61, {"used": 0}, log))
        self.assertEqual(len(self.sent), 1)

    def test_sweep_passes_its_live_log(self):
        """حارس بنيوي (درس سباق 2026-08-15): الجولة تمرّر نسختها الحية."""
        src = Path(M.__file__).read_text(encoding="utf-8")
        self.assertIn("warn_budget, log)", src)
