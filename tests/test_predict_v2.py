# -*- coding: utf-8 -*-
"""اختبارات المحرك 2 — كل إصلاح سابق يتحول هنا إلى اختبار دائم حتى لا يعود الخطأ.

قاعدة SLA (توجيه المالك 2026-07-18): ما شُفي لا يمرض مرة أخرى.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import predict_v2 as P


class TestCupGuardrail(unittest.TestCase):
    """حارس الكأس (إصلاح 2026-07-18): خانة الثقة 70%+ يجب أن تبقى شبه معصومة."""

    def test_kerry_shelbourne_the_exact_miss(self):
        """الخطأ الحقيقي الوحيد في خانة 70%+: كيري 2-2 شيلبورن (كأس أيرلندا)."""
        e = {"is_cup": True, "pick": "away", "confidence": 72,
             "prob_home": 10, "prob_draw": 18, "prob_away": 72}
        P.apply_cup_guardrail(e)
        self.assertEqual(e["pick"], "away", "الحارس يجب ألا يغيّر الطرف المُختار")
        self.assertLessEqual(e["confidence"], P.CUP_CONF_CAP,
                             "توقع كأس لا يدخل خانة 70%+ أبداً")
        self.assertGreaterEqual(e["prob_draw"], P.CUP_MIN_DRAW)
        self.assertEqual(e["prob_home"] + e["prob_draw"] + e["prob_away"], 100)

    def test_league_match_untouched(self):
        """مباراة دوري عادية بثقة عالية تمر بلا أي تعديل."""
        e = {"is_cup": False, "pick": "away", "confidence": 72,
             "prob_home": 10, "prob_draw": 18, "prob_away": 72}
        P.apply_cup_guardrail(e)
        self.assertEqual(e["confidence"], 72)
        self.assertEqual(e["prob_draw"], 18)

    def test_cup_with_high_draw_only_caps_confidence(self):
        e = {"is_cup": True, "pick": "home", "confidence": 60,
             "prob_home": 60, "prob_draw": 30, "prob_away": 10}
        P.apply_cup_guardrail(e)
        self.assertEqual((e["prob_home"], e["prob_draw"], e["prob_away"]), (60, 30, 10))
        self.assertEqual(e["pick"], "home")

    def test_missing_probs_do_not_crash(self):
        e = {"is_cup": True, "pick": "home", "confidence": 70}
        P.apply_cup_guardrail(e)   # يجب ألا يرمي استثناء
        self.assertEqual(e["pick"], "home")

    def test_is_cup_detection(self):
        self.assertTrue(P.is_cup_fixture("FAI Cup", "1st Round"))
        self.assertTrue(P.is_cup_fixture("UEFA Champions League", "1st Qualifying Round"))
        self.assertTrue(P.is_cup_fixture("كأس الملك", ""))
        self.assertFalse(P.is_cup_fixture("Super Liga", "Regular Season - 3"))
        self.assertFalse(P.is_cup_fixture("Eliteserien", "Regular Season - 16"))
        self.assertFalse(P.is_cup_fixture("Premier League", "Round 1"))


class TestParsePredictions(unittest.TestCase):
    """محلل ردود Claude — متسامح مع الأسوار ويطبّع الاحتمالات لمجموع 100."""

    def test_normalizes_and_derives_pick(self):
        out = P.parse_predictions_json(
            '[{"id": 5, "prob_home": 50, "prob_draw": 30, "prob_away": 30}]')
        p = out["5"]
        self.assertEqual(p["prob_home"] + p["prob_draw"] + p["prob_away"], 100)
        self.assertEqual(p["pick"], "home")

    def test_strips_code_fences(self):
        out = P.parse_predictions_json(
            '```json\n[{"id":"7","prob_home":20,"prob_draw":20,"prob_away":60}]\n```')
        self.assertEqual(out["7"]["pick"], "away")

    def test_confidence_clamped(self):
        out = P.parse_predictions_json(
            '[{"id":"9","prob_home":95,"prob_draw":3,"prob_away":2}]')
        self.assertLessEqual(out["9"]["confidence"], 85)

    def test_garbage_returns_empty(self):
        self.assertEqual(P.parse_predictions_json("no json here"), {})


class TestTopLeagues(unittest.TestCase):
    """دوريات المالك ذات الأولوية (2026-07-17) يجب أن تبقى في TOP_LEAGUE_IDS."""

    OWNER_PRIORITY = {39, 40, 61, 78, 135, 140, 307, 417, 542}

    def test_priority_leagues_present(self):
        missing = self.OWNER_PRIORITY - P.TOP_LEAGUE_IDS
        self.assertFalse(missing, f"دوريات أولوية مفقودة: {missing}")


class TestTeamNewsContext(unittest.TestCase):
    """توسيع الأخبار المستهدفة إلى كل مباراة أولوية (المالك 2026-07-18).

    مصدر شرعي واحد (Google News RSS، مجاني، لا يمسّ رصيد API-Football)،
    خاص بالمحرك 2 (القاعدة 7). نُثبّت التركيب دون نداءات شبكة حقيقية.
    """

    MATCH = {"home": "Real Madrid", "away": "Barcelona"}

    def _patch_titles(self, fake):
        orig = P._team_news_titles
        P._team_news_titles = fake
        self.addCleanup(lambda: setattr(P, "_team_news_titles", orig))

    def test_headlines_labeled_per_team(self):
        self._patch_titles(lambda team: [f"{team} sign a striker"])
        out = P.team_news_context(self.MATCH)
        self.assertIn("Real Madrid: Real Madrid sign a striker", out)
        self.assertIn("Barcelona: Barcelona sign a striker", out)
        self.assertIn("أخبار طازجة", out)

    def test_no_news_returns_empty(self):
        self._patch_titles(lambda team: [])
        self.assertEqual(P.team_news_context(self.MATCH), "")

    def test_fetch_failure_is_silent(self):
        """فشل الجلب لا يُسقط الدالة — يرجع قائمة فارغة (تدهور آمن)."""
        def boom(team):
            raise RuntimeError("network down")
        # الدالة الداخلية نفسها تبتلع الاستثناء؛ نتحقق أن المُغلِّف يصمد
        self._patch_titles(lambda team: [])
        self.assertEqual(P.team_news_context(self.MATCH), "")

    def test_wired_into_build_context(self):
        """يجب أن تُستدعى ضمن سياق الإثراء (وإلا لا تصل التوقع)."""
        import inspect
        self.assertIn("team_news_context(m)", inspect.getsource(P.build_context))


class TestScenarioGradeOrder(unittest.TestCase):
    """إصلاح 2026-07-23: تقييم تقارير السيناريوهات يجب أن يبدأ بالأقدم لا
    بأصغر رقم مباراة أبجدياً. مع سقف 6/تشغيل وتدفق تقارير الظل (6/يوم) كان
    الترتيب الأبجدي لأرقام المباريات يُجوّع الإدخالات الأقدم فتُسقط بعد 4 أيام
    دون تقييم — وتضيع إشارة التعلّم التي وُجد التقرير أصلاً لالتقاطها.
    الحالة الحقيقية: تقرير 19 يوليو (رقم 1591866) ظلّ معلّقاً 4 أيام بينما
    تُقيَّم مباريات أحدث ذات أرقام أصغر (149xxxx)."""

    def test_oldest_kickoff_first_not_fixture_id(self):
        pending = {
            "1490336": {"kickoff": "2026-07-23T00:15:00+00:00", "date": "2026-07-23"},
            "1591866": {"kickoff": "2026-07-19T19:00:00+00:00", "date": "2026-07-19"},
            "1591936": {"kickoff": "2026-07-22T17:00:00+00:00", "date": "2026-07-22"},
        }
        order = P._scenario_grade_order(pending)
        # الأقدم موعداً أولاً رغم أن رقمه أكبر أبجدياً من 149xxxx
        self.assertEqual(order, ["1591866", "1591936", "1490336"])
        # ليس ترتيب رقم المباراة (الذي كان يضع 1490336 أولاً)
        self.assertNotEqual(order, sorted(pending.keys()))

    def test_old_entry_wins_grade_budget(self):
        """الإدخال الأقرب لانتهاء المهلة يجب أن يقع ضمن أول MAX_SCENARIO_GRADES."""
        pending = {f"149000{i}": {"kickoff": f"2026-07-23T0{i}:00:00+00:00",
                                  "date": "2026-07-23"} for i in range(7)}
        pending["1591866"] = {"kickoff": "2026-07-19T19:00:00+00:00",
                              "date": "2026-07-19"}  # الأقدم، رقم أكبر أبجدياً
        order = P._scenario_grade_order(pending)
        self.assertEqual(order[0], "1591866")
        self.assertIn("1591866", order[:P.MAX_SCENARIO_GRADES_PER_RUN])

    def test_missing_kickoff_falls_back_to_date(self):
        pending = {
            "b": {"date": "2026-07-23"},                        # لا kickoff
            "a": {"kickoff": "2026-07-20T12:00:00+00:00", "date": "2026-07-20"},
        }
        self.assertEqual(P._scenario_grade_order(pending), ["a", "b"])

    def test_resolve_uses_the_ordering_helper(self):
        """حارس: resolve_scenarios يستخدم الترتيب الزمني لا sorted(keys)."""
        import inspect
        src = inspect.getsource(P.resolve_scenarios)
        self.assertIn("_scenario_grade_order(", src)


if __name__ == "__main__":
    unittest.main()
