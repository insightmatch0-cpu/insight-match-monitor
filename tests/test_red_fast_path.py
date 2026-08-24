# -*- coding: utf-8 -*-
"""🟥 اختبارات المسار السريع للطرد — REC-009 (قرار المالك 2026-08-10:
"نفّذ من أي دقيقة").

الادعاء red_advantage: طرد من فريق متعادل أو متقدم بهدف واحد = أفضلية عددية
للخصم — تنبيه فوري من أي دقيقة، بلا شرط د75 وبلا موجات الزخم (استثناء المالك
الصريح لهذا الادعاء وحده). مرة واحدة لكل مباراة بعلم مستقل، مفتاح تراجع
RADAR_RED_FAST_PATH، تقييم صباحي مستقل من score_at، وقاعدة الإيقاف REC-005
تحكمه كأي ادعاء. صفر Claude بالتصميم: كل اختبار يعمل بلا شبكة وبلا مفاتيح.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import monitor as M
import predict_v2 as P


def snap(minute, h=None, a=None):
    return {"minute": minute, "gh": 0, "ga": 0, "h": h or {}, "a": a or {}}


class TestRedAdvantageSignal(unittest.TestCase):
    """شرط الإطلاق: طرد + المطرود منه متعادل أو متقدم بهدف — لا غير."""

    def test_red_on_level_team_benefits_opponent(self):
        v = M.evaluate_red_advantage([snap(30, {"rc": 1}, {})], 0, 0)
        self.assertIsNotNone(v)
        self.assertEqual(v["side"], "away")   # المستفيد خصم المطرود منه

    def test_red_on_team_leading_by_one_benefits_opponent(self):
        v = M.evaluate_red_advantage([snap(60, {}, {"rc": 1})], 0, 1)
        self.assertIsNotNone(v)
        self.assertEqual(v["side"], "home")

    def test_no_fire_when_carded_team_trailing(self):
        """(ب) المطرود منه متأخر أصلاً — تغطيه ادعاءات الدراما القائمة."""
        self.assertIsNone(
            M.evaluate_red_advantage([snap(60, {"rc": 1}, {})], 0, 1))

    def test_no_fire_when_carded_team_leading_by_two(self):
        """(ج) متقدم بهدفين+ — طرده لا يصنع دراما."""
        self.assertIsNone(
            M.evaluate_red_advantage([snap(60, {"rc": 1}, {})], 2, 0))
        self.assertIsNone(
            M.evaluate_red_advantage([snap(60, {"rc": 1}, {})], 3, 0))

    def test_no_fire_without_red_or_with_mutual_reds(self):
        self.assertIsNone(M.evaluate_red_advantage([snap(60)], 0, 0))
        # طرد متبادل يلغي الأفضلية العددية
        self.assertIsNone(
            M.evaluate_red_advantage([snap(60, {"rc": 1}, {"rc": 1})], 0, 0))

    def test_no_snaps_no_crash(self):
        self.assertIsNone(M.evaluate_red_advantage([], 0, 0))


class TestRedAdvantageAlert(unittest.TestCase):
    """التنبيه نفسه: من أي دقيقة، مرة لكل مباراة، مفتاح تراجع، إسكات REC-005."""

    def setUp(self):
        # 📵 بوابة التسعة/المفضلة (قرار المالك 2026-08-24 مساءً) تُفحص في
        # جرزها المخصصة — هنا نعطلها لفحص الآليات الأخرى بمعزل عنها
        _orig_gate = M.DRAMA_MINE_ONLY
        M.DRAMA_MINE_ONLY = False
        self.addCleanup(lambda: setattr(M, "DRAMA_MINE_ONLY", _orig_gate))


    def _capture_telegram(self):
        sent = []
        orig = M.send_telegram
        M.send_telegram = lambda text: sent.append(text)
        self.addCleanup(lambda: setattr(M, "send_telegram", orig))
        return sent

    def _tmp_radar_file(self, content="{}"):
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(content, encoding="utf-8")
        orig = M.RADAR_FILE
        M.RADAR_FILE = tmp
        self.addCleanup(lambda: (setattr(M, "RADAR_FILE", orig),
                                 tmp.unlink(missing_ok=True)))
        return tmp

    def _entry(self, minute=30, score="0-0"):
        return {"score": score, "minute": minute,
                "home": "Al Hilal", "away": "Al Nassr",
                "league": "Saudi Pro League",
                "ar": {"home": "الهلال", "away": "النصر"},
                "radar": {"snaps": [snap(minute - 10, {}, {"rc": 0}),
                                    snap(minute, {}, {"rc": 1})]}}

    def test_fires_at_minute_30_no_minute_condition(self):
        """(أ) قرار المالك الصريح: من أي دقيقة — د30 قبل شرط د75 بكثير،
        وبلا أي موجة زخم في اللقطات. الرسالة عربية بأرقام لاتينية، موسومة
        🧪، وتذكر المستفيد بالاسم العربي والنتيجة والدقيقة."""
        sent = self._capture_telegram()
        tmp = self._tmp_radar_file()
        e = self._entry(minute=30, score="0-0")   # طُرد لاعب النصر والتعادل قائم
        self.assertTrue(M.maybe_red_alert("9", e, {"used": 0}))
        self.assertEqual(len(sent), 1)
        self.assertIn("🟥 أفضلية عددية", sent[0])
        self.assertIn("🧪 تجريبي — قيد المعايرة", sent[0])
        self.assertIn("د30", sent[0])
        self.assertIn("0 - 0", sent[0])           # النتيجة الحالية
        self.assertIn("الهلال", sent[0])          # المستفيد بالاسم العربي
        # السجل: نفس بنية التنبيهات القائمة بمفتاح red_advantage
        log = json.loads(tmp.read_text(encoding="utf-8"))
        a = log["alerts"][0]
        self.assertEqual(a["key"], "red_advantage")
        self.assertEqual(a["side"], "home")
        self.assertEqual(a["score_at"], "0-0")
        self.assertEqual(a["minute"], 30)
        self.assertFalse(a["silenced"])
        self.assertTrue(a.get("evidence"))        # حزمة الأدلة (آخر اللقطات)

    def test_no_fire_when_carded_team_trailing(self):
        """(ب) المطرود منه متأخر → صمت تام (لا رسالة ولا سجل)."""
        sent = self._capture_telegram()
        tmp = self._tmp_radar_file()
        e = self._entry(minute=60, score="1-0")   # النصر مطرود منه ومتأخر
        self.assertFalse(M.maybe_red_alert("9", e, {"used": 0}))
        self.assertEqual(sent, [])
        self.assertNotIn("alerts", json.loads(tmp.read_text(encoding="utf-8")))

    def test_no_fire_when_carded_team_leading_by_two(self):
        """(ج) المطرود منه متقدم بهدفين+ → صمت."""
        sent = self._capture_telegram()
        self._tmp_radar_file()
        e = self._entry(minute=60, score="0-2")   # النصر مطرود منه ومتقدم 2-0
        self.assertFalse(M.maybe_red_alert("9", e, {"used": 0}))
        self.assertEqual(sent, [])

    def test_once_per_match(self):
        """(د) مرة واحدة لكل مباراة — العلم red_alerted يمنع التكرار."""
        sent = self._capture_telegram()
        self._tmp_radar_file()
        e = self._entry()
        budget = {"used": 0}
        self.assertTrue(M.maybe_red_alert("9", e, budget))
        self.assertFalse(M.maybe_red_alert("9", e, budget))
        self.assertEqual(len(sent), 1)
        self.assertTrue(e["radar"]["red_alerted"])

    def test_kill_switch_disables_entirely(self):
        """(هـ) RADAR_RED_FAST_PATH=False = تعطيل كامل — لا رسالة ولا سجل."""
        sent = self._capture_telegram()
        tmp = self._tmp_radar_file()
        orig = M.RADAR_RED_FAST_PATH
        M.RADAR_RED_FAST_PATH = False
        try:
            self.assertFalse(M.maybe_red_alert("9", self._entry(), {"used": 0}))
        finally:
            M.RADAR_RED_FAST_PATH = orig
        self.assertEqual(sent, [])
        self.assertNotIn("alerts", json.loads(tmp.read_text(encoding="utf-8")))

    def test_silenced_claim_logged_without_telegram(self):
        """(و) قاعدة الإيقاف REC-005: النوع المُسكَت يُسجَّل للتقييم الصباحي
        بلا إرسال — فيستمر قياسه ويستطيع الخروج من الصمت."""
        sent = self._capture_telegram()
        tmp = self._tmp_radar_file(json.dumps({"silenced": ["red_advantage"]}))
        e = self._entry()
        self.assertTrue(M.maybe_red_alert("9", e, {"used": 0}))
        self.assertEqual(sent, [])
        log = json.loads(tmp.read_text(encoding="utf-8"))
        self.assertTrue(log["alerts"][0]["silenced"])
        self.assertTrue(e["radar"]["red_alerted"])

    def test_independent_of_drama_ladder_flag(self):
        """العلم مستقل: تنبيه دراما سابق (alerted) لا يحجب مسار الطرد،
        ومسار الطرد لا يلمس alerted."""
        self._capture_telegram()
        self._tmp_radar_file()
        e = self._entry()
        e["radar"]["alerted"] = "equalizer"   # سلم الدراما أطلق ادعاءه سابقاً
        self.assertTrue(M.maybe_red_alert("9", e, {"used": 0}))
        self.assertEqual(e["radar"]["alerted"], "equalizer")   # لم يُمس

    def test_alert_cap_respected(self):
        """سقف تنبيهات التشغيلة القائم يشمله — وبلا علم فيُعاد في دورة تالية."""
        sent = self._capture_telegram()
        self._tmp_radar_file()
        e = self._entry()
        self.assertFalse(M.maybe_red_alert("9", e, {"used": M.RADAR_ALERT_CAP_PER_RUN}))
        self.assertEqual(sent, [])
        self.assertFalse((e["radar"] or {}).get("red_alerted"))

    def test_wired_into_both_radar_loops(self):
        import inspect
        self.assertIn("maybe_red_alert(", inspect.getsource(M.radar_sweep))
        self.assertIn("maybe_red_alert(", inspect.getsource(M.radar_fast_watch))

    def test_red_flag_survives_sweep_rebuild(self):
        """العلم يجب أن ينجو من إعادة بناء الرادار كل دورة — وإلا تكرر
        التنبيه كل 10 دقائق (نفس درس علم alerted)."""
        import inspect
        self.assertIn('"red_alerted": radar.get("red_alerted")',
                      inspect.getsource(M.radar_sweep))


class TestRedAdvantageGrading(unittest.TestCase):
    """(ز) التقييم الصباحي: إصابة إن سجّل المستفيد بعد لحظة التنبيه —
    مقارنة النتيجة النهائية بـ score_at، وعدّاد مستقل في meta.stats.alerts."""

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

    def test_hit_when_beneficiary_scored_after_alert(self):
        graded, log = self._with_tmp_log(
            {"alerts": [
                {"fid": "1", "date": "2026-08-09", "key": "red_advantage",
                 "side": "home", "score_at": "1-1"},
                {"fid": "2", "date": "2026-08-09", "key": "red_advantage",
                 "side": "away", "score_at": "0-0"},
            ]},
            {"resolved": [
                {"fid": "1", "correct": True, "score": "2-1"},   # المستفيد سجّل → صح
                {"fid": "2", "correct": True, "score": "1-0"},   # لم يسجّل → خطأ
            ]})
        self.assertEqual(graded, 2)
        by_fid = {a["fid"]: a for a in log["alerts_resolved"]}
        self.assertTrue(by_fid["1"]["hit"])
        self.assertFalse(by_fid["2"]["hit"])
        # (7) عدّاد مستقل — لوحة منفصلة لكل ادعاء، لا خلط
        self.assertEqual(log["meta"]["stats"]["alerts"]["red_advantage"],
                         {"fired": 2, "hit": 1})

    def test_miss_even_if_beneficiary_conceded(self):
        """المستفيد لم يسجّل — إخفاق حتى لو خسر الطرف المطرود منه شيئاً آخر."""
        graded, log = self._with_tmp_log(
            {"alerts": [{"fid": "3", "date": "2026-08-09",
                         "key": "red_advantage", "side": "home",
                         "score_at": "1-1"}]},
            {"resolved": [{"fid": "3", "correct": False, "score": "1-2"}]})
        self.assertEqual(graded, 1)
        self.assertFalse(log["alerts_resolved"][0]["hit"])

    def test_counter_separate_from_other_claims(self):
        graded, log = self._with_tmp_log(
            {"alerts": [
                {"fid": "1", "date": "2026-08-09", "key": "red_advantage",
                 "side": "home", "score_at": "0-0"},
                {"fid": "2", "date": "2026-08-09", "key": "flip",
                 "side": "away", "score_at": "1-0"},
            ]},
            {"resolved": [
                {"fid": "1", "correct": True, "score": "1-0"},
                {"fid": "2", "correct": False, "score": "1-2"},
            ]})
        stats = log["meta"]["stats"]["alerts"]
        self.assertEqual(stats["red_advantage"], {"fired": 1, "hit": 1})
        self.assertEqual(stats["flip"], {"fired": 1, "hit": 1})

    def test_scoreboard_line_covers_red_advantage(self):
        """(8) سطر النشرة الصباحية المفصّل يعرض الادعاء الجديد تلقائياً —
        DRAMA_CLAIM_AR يحمل اسمه العربي فيدخل التفصيل كغيره."""
        self.assertIn("red_advantage", P.DRAMA_CLAIM_AR)
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        today = P.now_utc().strftime("%Y-%m-%d")
        tmp.write_text(json.dumps({"alerts_resolved": [
            {"key": "red_advantage", "hit": True, "graded_on": today},
        ]}), encoding="utf-8")
        orig = P.RADAR_LOG_FILE
        P.RADAR_LOG_FILE = tmp
        try:
            line = P.drama_scoreboard_line()
        finally:
            P.RADAR_LOG_FILE = orig
            tmp.unlink(missing_ok=True)
        self.assertIn("أفضلية عددية", line)
        self.assertIn("1/1", line)


class TestDrawBranchRedFix(unittest.TestCase):
    """(ح) الإصلاح المرافق: فرع التعادل في drama_signal كان يثبّت red=False
    نصاً — الآن تُحسب فعلياً من لقطة خصم الطرف المهيمن كفرع التأخر."""

    def test_draw_branch_computes_red_from_opponent_snapshot(self):
        s = [snap(60, {"sog": 1}, {"rc": 0}),
             snap(70, {"sog": 4, "cor": 3}, {"rc": 1})]
        d = M.drama_signal(s, 1, 1)
        self.assertEqual(d["side"], "home")   # المهيمن زخماً (والطرد عند خصمه)
        self.assertTrue(d["red"])

    def test_draw_branch_red_false_without_card(self):
        s = [snap(60, {"sog": 1}, {}), snap(70, {"sog": 4, "cor": 3}, {})]
        d = M.drama_signal(s, 1, 1)
        self.assertFalse(d["red"])

    def test_trailing_branch_unchanged(self):
        """شروط بقية الادعاءات لم تُمس — فرع التأخر يحسبها كما كان."""
        s = [snap(70, {"sog": 2}, {"rc": 1})]
        d = M.drama_signal(s, 0, 1)
        self.assertTrue(d["red"])


if __name__ == "__main__":
    unittest.main()
