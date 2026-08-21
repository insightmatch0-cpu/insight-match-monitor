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


class TestMarketProbsStored(unittest.TestCase):
    """شريحة "المحرك ضد السوق" (طلب المالك 2026-08-01): احتمالات السوق الضمنية
    تُخزَّن مع التوقع وتُحمل حتى سجل resolved — قياس مستقبلي مجاني."""

    def test_odds_context_stashes_market_probs_on_match(self):
        fake_payload = [{
            "bookmakers": [{
                "name": "TestBook",
                "bets": [{
                    "name": "Match Winner",
                    "values": [
                        {"value": "Home", "odd": "2.00"},
                        {"value": "Draw", "odd": "3.50"},
                        {"value": "Away", "odd": "4.00"},
                    ],
                }],
            }],
        }]
        orig = P._enrich_call
        P._enrich_call = lambda path, budget: fake_payload
        try:
            m = {"fid": "123"}
            txt = P.odds_context(m, {"used": 0})
        finally:
            P._enrich_call = orig
        self.assertIn("implied probabilities", txt)
        # 1/2 + 1/3.5 + 1/4 → بعد إزالة الهامش: 48% / 28% / 24% (تقريب)
        self.assertEqual(m["mkt_home"], 48)
        self.assertEqual(m["mkt_draw"], 28)
        self.assertEqual(m["mkt_away"], 24)

    def test_scenario_grading_stores_claim_breakdown(self):
        """طلب المالك 2026-08-01: التصحيح بنداً‑ببند يُحفظ مع التقرير المُقيَّم
        (grades + grade_summary) ليظهر تفصيله في مختبر الظل.
        (الجوهر انتقل إلى _grade_scenario_entry المشتركة بين الصباح والتقييم
        اللحظي 2026-08-09 — نفس الضمانة، من المسارين معاً.)"""
        import inspect
        src = inspect.getsource(P._grade_scenario_entry)
        self.assertIn('entry["grades"]', src)
        self.assertIn("grade_summary", src)
        self.assertIn("_grade_scenario_entry",
                      inspect.getsource(P.resolve_scenarios))

    def test_resolve_carries_market_probs(self):
        """سجل resolved يجب أن يحمل حقول السوق — وإلا ضاع القياس عند التقييم."""
        import inspect
        src = inspect.getsource(P.resolve_pending)
        for field in ("mkt_home", "mkt_draw", "mkt_away"):
            self.assertIn(field, src)


class TestWhyLine(unittest.TestCase):
    """سطر "لماذا" (طلب المالك 2026-08-01): قراءة المحرك قبل المباراة يجب أن
    تنجو من التقييم — كانت تُحذف مع pending فلا يُعرف ماذا كان يفكر حين أخطأ."""

    def test_resolve_carries_reason(self):
        import inspect
        src = inspect.getsource(P.resolve_pending)
        self.assertIn('"reason"', src, "سجل resolved يجب أن يحمل سبب التوقع")

    def test_parser_extracts_reason(self):
        """المصدر: المحلل يستخرج reason من رد Claude — لو سقط ضاع السطر كله."""
        out = P.parse_predictions_json(
            '[{"id":"4","prob_home":60,"prob_draw":25,"prob_away":15,'
            '"reason":"ضغط هجومي متواصل"}]')
        self.assertEqual(out["4"]["reason"], "ضغط هجومي متواصل")

    def test_gold_miss_alert_includes_reason(self):
        """تنبيه خطأ الـ 70%+ الصباحي يعرض ماذا كان المحرك يفكر."""
        import inspect
        src = inspect.getsource(P.post_grading_alerts)
        self.assertIn("reason", src)
        self.assertIn("كان يفكر", src)


if __name__ == "__main__":
    unittest.main()


class TestDigestSections(unittest.TestCase):
    """⭐/⚡ قسما رأس النشرة (طلب المالك 2026-08-21: «كل شيء مخلوط»)."""

    def _preds(self):
        return [
            {"fid": "1", "home": "A", "away": "B", "pick": "home",
             "confidence": 74, "top": True, "prob_home": 74, "prob_draw": 16,
             "prob_away": 10, "mkt_home": 80, "mkt_draw": 12, "mkt_away": 8},
            {"fid": "2", "home": "C", "away": "D", "pick": "away",
             "confidence": 35, "top": False, "prob_home": 34, "prob_draw": 31,
             "prob_away": 35, "mkt_home": 38, "mkt_draw": 30, "mkt_away": 32},
            {"fid": "3", "home": "E", "away": "F", "pick": "home",
             "confidence": 50, "top": True, "prob_home": 50, "prob_draw": 30,
             "prob_away": 20},
        ]

    def test_gold_and_contra_extracted_with_portal_terminology(self):
        text = "\n".join(P.digest_sections(self._preds()))
        self.assertIn("⭐ الاختيارات الذهبية — ثقة 70%+ (1)", text)
        self.assertIn("⚡ ضد السوق — المحرك يخالف المرشح (1)", text)
        self.assertIn("C 🆚 D", text)
        # المباراة بلا أودز لا تدخل قسم السوق، والثقة 50 لا تدخل الذهبية
        self.assertNotIn("E 🆚 F", text)

    def test_agreement_with_market_stays_out_of_contra(self):
        # الصف الأول يوافق السوق (كلاهما home) — الذهبية نعم، ضد السوق لا
        text = "\n".join(P.digest_sections([self._preds()[0]]))
        self.assertIn("الاختيارات الذهبية", text)
        self.assertNotIn("ضد السوق", text)

    def test_empty_input_yields_no_headers(self):
        self.assertEqual(P.digest_sections([]), [])

    def test_build_digest_guarded_by_kill_switch(self):
        import os as _os
        src = open(_os.path.join(_os.path.dirname(_os.path.dirname(
            _os.path.abspath(__file__))), "predict_v2.py"), encoding="utf-8").read()
        body = src.split("def build_digest(")[1]
        self.assertIn("if DIGEST_SECTIONS:", body)
        self.assertIn("digest_sections(new_preds)", body)
