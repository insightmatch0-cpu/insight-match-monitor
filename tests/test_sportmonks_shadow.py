# -*- coding: utf-8 -*-
"""🔬 اختبارات مجمّع ظل xG (المرحلة أ — خطة 2026-07-31، انطلقت 2026-08-12).

المبادئ المحروسة: صفر تأثير على المحركات، فشل صامت بلا مفتاح، مطابقة أسماء
محافظة بين المزودين، ترجيح فورمة xG من التاريخ السابق فقط (لا تسريب مستقبل)،
سجل قياس بلا قص، وسطر الرؤية اليومية في النشرة (قاعدة المالك هـ).
صفر شبكة بالتصميم: كل اختبار يعمل بلا مفاتيح وبلا نداء خارجي.
"""

import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import predict_v2 as P
import sportmonks_shadow as S


class TestNameMatching(unittest.TestCase):
    """مطابقة أسماء الفرق بين API-Football وSportmonks — محافظة لا متساهلة."""

    def test_exact_and_decorated_names_match(self):
        self.assertTrue(S.names_match("Al Hilal", "Al-Hilal"))
        self.assertTrue(S.names_match("Manchester United", "Manchester United FC"))
        self.assertTrue(S.names_match("Bayern München", "Bayern Munchen"))

    def test_subset_names_match(self):
        self.assertTrue(S.names_match("Barcelona", "FC Barcelona"))

    def test_different_teams_do_not_match(self):
        self.assertFalse(S.names_match("Manchester United", "Manchester City"))
        self.assertFalse(S.names_match("Al Hilal", "Al Nassr"))

    def test_empty_names_never_match(self):
        self.assertFalse(S.names_match("", "Arsenal"))
        self.assertFalse(S.names_match("FC", "SC"))   # لواحق فقط = لا هوية


class TestXgFormPick(unittest.TestCase):
    """ترجيح فورمة xG: من التاريخ فقط، ولا ادعاء قبل عينة كافية."""

    def _hist(self, *pairs):
        return [{"date": "2026-08-01", "xf": f, "xa": a} for f, a in pairs]

    def test_no_pick_before_min_matches(self):
        """لا ترجيح تحت الحد الأدنى — الحد نفسه يُقرأ من الثابت لا يُثبَّت رقماً
        (تحديث 2026-08-24: خُفض 3 → 2 بعد أن أنتج القياس صفراً 11 يوماً؛
        الحارس هو أن الترجيح يمتنع تحت الحد أياً كان)."""
        short = self._hist(*([(2, 1)] * (S.FORM_MIN_MATCHES - 1)))
        enough = self._hist(*([(1, 1)] * S.FORM_MIN_MATCHES))
        self.assertIsNone(S.xgform_pick(short, enough))
        self.assertIsNone(S.xgform_pick(enough, short))

    def test_clear_edge_picks_stronger_side(self):
        h = self._hist((2.5, 0.5), (2.0, 0.8), (1.9, 0.6))
        a = self._hist((0.8, 1.5), (0.6, 2.0), (1.0, 1.8))
        self.assertEqual(S.xgform_pick(h, a), "home")
        self.assertEqual(S.xgform_pick(a, h), "away")

    def test_tiny_gap_is_draw(self):
        h = self._hist((1.2, 1.0), (1.1, 1.0), (1.0, 1.0))
        a = self._hist((1.1, 1.0), (1.0, 1.0), (1.1, 1.1))
        self.assertEqual(S.xgform_pick(h, a), "draw")

    def test_form_uses_window_only(self):
        """القديم خارج النافذة لا يؤثر — لكن التاريخ الكامل يبقى محفوظاً."""
        old_bad = [(0.1, 3.0)] * 10
        recent_good = [(2.0, 0.5)] * S.FORM_WINDOW
        h = self._hist(*(old_bad + recent_good))
        a = self._hist(*([(1.0, 1.0)] * 5))
        self.assertEqual(S.xgform_pick(h, a), "home")

    def test_outcome_parsing(self):
        self.assertEqual(S._outcome("2-1"), "home")
        self.assertEqual(S._outcome("0-0"), "draw")
        self.assertEqual(S._outcome("1-3"), "away")
        self.assertEqual(S._outcome("سيئ"), "")


class TestSilentFailZeroImpact(unittest.TestCase):
    """عقيدة الظل: بلا مفتاح = تخطٍ نظيف؛ وفشل المجمّع لا يمس أي محرك."""

    def test_no_key_exits_cleanly_without_writing(self):
        orig_key, orig_file = S.KEY, S.SHADOW_FILE
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.unlink()   # يجب ألا يُنشأ
        S.KEY = ""
        S.SHADOW_FILE = tmp
        try:
            S.main()   # لا استثناء
            self.assertFalse(tmp.exists())
        finally:
            S.KEY, S.SHADOW_FILE = orig_key, orig_file

    def test_workflow_step_cannot_fail_the_run(self):
        yml = (Path(__file__).resolve().parent.parent
               / ".github" / "workflows" / "predict_v2.yml").read_text(encoding="utf-8")
        self.assertIn("sportmonks_shadow.py ||", yml)
        self.assertIn("SPORTMONKS_KEY", yml)

    def test_collector_never_writes_engine_memories(self):
        """المجمّع يقرأ ذاكرتي المحركين ولا يكتب فيهما أبداً."""
        src = inspect.getsource(S)
        self.assertNotIn("V2_FILE.write", src)
        self.assertNotIn("V1_FILE.write", src)
        # الكتابة الوحيدة في المجمّع كله هي ملف الظل
        self.assertEqual(src.count(".write_text("), 1)
        self.assertIn("SHADOW_FILE.write_text(", src)


class TestMeasurementIntegrity(unittest.TestCase):
    """سجل الظل سجل قياس: لا قص، والترجيح لا يرى مستقبله."""

    def test_no_truncation_of_measurement_lists(self):
        """القص الوحيد المسموح: نافذة حساب الفورمة — التاريخ نفسه لا يُقص."""
        src = inspect.getsource(S)
        import re
        slices = re.findall(r"\[-(?:\d+|[A-Z_]+)\s*:\s*\]", src)
        self.assertEqual(set(slices), {"[-FORM_WINDOW:]"},
                         "قص جديد في المجمّع — صنّفه (عقيدة لا-أسقف-قياس)")

    def test_pick_computed_before_history_append(self):
        """الترجيح يُحسب قبل إلحاق مباريات اليوم — وإلا تسرّب المستقبل للقياس.
        (أُعيد ربطه ببنية 2026-08-24: التاريخ صار يُبنى من كل مباريات اليوم
        في كتلة تالية للترجيحات — نفس الحارس، بنفس القوة، على المرساة الجديدة.)"""
        src = inspect.getsource(S.main)
        self.assertLess(src.index("xgform_pick("),
                        src.index('shadow["teams"].setdefault(_team_key(sm["home"])'),
                        "إدخال مباريات اليوم للتاريخ سبق حساب الترجيح — تسريب مستقبل")


class TestKillSwitch(unittest.TestCase):
    """مفتاح التراجع XG_SHADOW: إطفاؤه = تعطيل فوري بلا حذف أي كود."""

    def test_switch_off_skips_cleanly_without_writing(self):
        orig_flag, orig_key, orig_file = S.XG_SHADOW, S.KEY, S.SHADOW_FILE
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.unlink()   # يجب ألا يُنشأ
        S.XG_SHADOW, S.KEY, S.SHADOW_FILE = False, "مفتاح-وهمي", tmp
        try:
            S.main()   # لا استثناء ولا كتابة ولا نداء شبكة (يخرج قبل كل شيء)
            self.assertFalse(tmp.exists())
        finally:
            S.XG_SHADOW, S.KEY, S.SHADOW_FILE = orig_flag, orig_key, orig_file


def _sm_fixture(home, away, xh, xa):
    """جسم مباراة بصيغة Sportmonks المؤكدة من المسبار — للمحاكاة فقط."""
    return {"name": f"{home} vs {away}", "xgfixture": [
        {"type_id": S.XG_TYPE_ID, "location": "home", "data": {"value": xh}},
        {"type_id": S.XG_TYPE_ID, "location": "away", "data": {"value": xa}},
    ]}


class TestValidateMode(unittest.TestCase):
    """وضع التحقق مقابل Opta (--validate): جدول فروق صادق، وفشل HTTP لا يقتل."""

    def _run_validate(self, api_stub, persist=False):
        import contextlib
        import io
        orig_api, orig_key = S._api, S.KEY
        S._api, S.KEY = api_stub, "مفتاح-وهمي"
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                summary = S.validate(persist=persist)
        finally:
            S._api, S.KEY = orig_api, orig_key
        return summary, buf.getvalue()

    def test_prints_diff_table_and_summary(self):
        """عينة مُطابقة → صف فرق لكل مباراة مغطاة وخلاصة بمتوسط الفرق."""
        sample = S.OPTA_SAMPLE[0]
        def api_stub(path, params):
            if sample["date"] in path:
                return {"data": [_sm_fixture(sample["home"], sample["away"],
                                             sample["opta_home"] + 0.1,
                                             sample["opta_away"])],
                        "pagination": {"has_more": False}}
            return {"data": [], "pagination": {"has_more": False}}
        summary, out = self._run_validate(api_stub)
        self.assertEqual(summary["n"], 1)
        self.assertEqual(summary["no_coverage"], len(S.OPTA_SAMPLE) - 1)
        self.assertAlmostEqual(summary["mean_abs_diff"], 0.05)
        self.assertIn("توافق جيد", summary["verdict"])
        self.assertIn("لا تغطية", out)      # غير المُغطى يُسجَّل، لا يُخمَّن
        self.assertIn("Opta", out)

    def test_http_failure_never_raises(self):
        """كل النداءات تفشل → خلاصة «لا تغطية» صادقة، صفر استثناءات."""
        summary, out = self._run_validate(lambda path, params: None)
        self.assertEqual(summary["n"], 0)
        self.assertIsNone(summary["mean_abs_diff"])
        self.assertIn("لا تغطية", summary["verdict"])

    def test_no_key_skips_silently(self):
        orig_key = S.KEY
        S.KEY = ""
        try:
            self.assertEqual(S.validate(persist=False), {})
        finally:
            S.KEY = orig_key

    def test_persist_writes_summary_into_shadow_meta(self):
        """نتيجة التحقق تُحفظ في السجل ليقرأها تقرير 24 أغسطس المرحلي."""
        orig_file = S.SHADOW_FILE
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps({"fixtures": [], "meta": {"total": 3}}),
                       encoding="utf-8")
        S.SHADOW_FILE = tmp
        try:
            self._run_validate(lambda path, params: None, persist=True)
            saved = json.loads(tmp.read_text(encoding="utf-8"))
            self.assertIn("opta_validation", saved["meta"])
            self.assertEqual(saved["meta"]["total"], 3)   # لا يمس بقية السجل
        finally:
            S.SHADOW_FILE = orig_file
            tmp.unlink(missing_ok=True)

    def test_first_run_triggers_validation_once(self):
        """أول تشغيلة تُشغّل التحقق تلقائياً؛ وجود الخلاصة يمنع تكراره يومياً."""
        src = inspect.getsource(S.main)
        self.assertIn('"opta_validation" not in meta', src)
        self.assertIn("validate()", src)

    def test_opta_sample_is_sane(self):
        """عينة المرجع: مباريات إنجليزية بموسم سابق وأرقام موجبة معقولة."""
        self.assertGreaterEqual(len(S.OPTA_SAMPLE), 6)
        for s in S.OPTA_SAMPLE:
            self.assertTrue(s["date"].startswith("2022-"))
            self.assertGreaterEqual(s["opta_home"], 0)
            self.assertGreaterEqual(s["opta_away"], 0)
            self.assertLess(s["opta_home"] + s["opta_away"], 8)


class TestDigestVisibility(unittest.TestCase):
    """قاعدة المالك (هـ): تجربة نشطة = سطر يومي في النشرة بلا سؤال."""

    def test_line_appears_with_data(self):
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps({"meta": {
            "started": "2026-08-13", "total": 24,
            "last_day_matched": 9, "last_day_unmatched": 2,
            "xgform": {"n": 10, "correct": 6}}}), encoding="utf-8")
        src = inspect.getsource(P.sportmonks_shadow_line)
        self.assertIn('sportmonks_shadow.json', src)
        orig = P.load_json
        P.load_json = lambda path, default: json.loads(tmp.read_text(encoding="utf-8"))
        try:
            line = P.sportmonks_shadow_line()
        finally:
            P.load_json = orig
            tmp.unlink(missing_ok=True)
        self.assertIn("ظل xG", line)
        self.assertIn("9", line)
        self.assertIn("6/10", line)

    def test_silent_without_data(self):
        orig = P.load_json
        P.load_json = lambda path, default: {}
        try:
            self.assertEqual(P.sportmonks_shadow_line(), "")
        finally:
            P.load_json = orig

    def test_wired_into_digest(self):
        src = inspect.getsource(P.main)
        self.assertIn("sportmonks_shadow_line()", src)


# ============ اختبارات الانحدار: إنقاذ تجربة xG (14 أغسطس 2026) ============
# العطل: المجمّع عمل من 13 أغسطس وجمع صفر مباراة (295 مُفلتة، 0 مطابقة) بينما
# سطر النشرة يختفي تماماً — عطل صامت كامل. هذه الاختبارات تحرس الطبقات الثلاث
# التي منعت رؤيته: التشخيص، والإنذار، والرؤية اليومية.

class TestProbeMode(unittest.TestCase):
    """وضع --probe: يقول الحقيقة كاملة في سجل Actions، وبلا مفتاح في المخرَج."""

    FAKE_KEY = "sk-سرّ-تجريبي-لا-يجوز-أن-يُطبع-9f3a2b"

    def _run_probe(self, request_stub, day="2026-08-11", ours=None, key=None):
        """يشغّل المسبار بردّ مُحاكى ويرجع (المخرَج، هل كُتب ملف الظل)."""
        import contextlib
        import io as _io
        orig = (S._request, S.KEY, S.V2_FILE, S.SHADOW_FILE)
        tmp_v2 = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp_v2.write_text(json.dumps({"resolved": ours or []}), encoding="utf-8")
        tmp_shadow = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp_shadow.unlink()          # يجب ألا يُنشأ: المسبار قراءة محضة
        S._request = request_stub
        S.KEY = self.FAKE_KEY if key is None else key
        S.V2_FILE, S.SHADOW_FILE = tmp_v2, tmp_shadow
        buf = _io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                S.probe(day)
        finally:
            S._request, S.KEY, S.V2_FILE, S.SHADOW_FILE = orig
            tmp_v2.unlink(missing_ok=True)
            wrote = tmp_shadow.exists()
            tmp_shadow.unlink(missing_ok=True)
        return buf.getvalue(), wrote

    @staticmethod
    def _body(fixtures, has_more=False, message=None):
        return {"data": fixtures,
                "pagination": {"has_more": has_more, "count": len(fixtures)},
                "message": message,
                "subscription": [{"plans": [{"plan": "Growth - Trialing",
                                             "sport": "Football",
                                             "category": "Advanced"}],
                                  "bundles": [{"bundle": "Pressure Index & xG",
                                               "category": "Pressure"}]}]}

    @staticmethod
    def _fx(home, away, xh=None, xa=None, league_id=8):
        """مباراة سبورتمونكس؛ بلا xG حين لا تُمرَّر القيم (حالة العطل الحقيقية)."""
        xg = []
        if xh is not None:
            xg = [{"type_id": S.XG_TYPE_ID, "location": "home",
                   "data": {"value": xh}},
                  {"type_id": S.XG_TYPE_ID, "location": "away",
                   "data": {"value": xa}}]
        return {"name": f"{home} vs {away}", "league_id": league_id,
                "xgfixture": xg}

    def test_prints_status_counts_and_xg_split_per_page(self):
        """لكل صفحة: رمز HTTP، عدد المباريات، وكم منها بـxG وكم بلا."""
        page1 = [self._fx("Arsenal", "Fulham", 2.6, 0.8),
                 self._fx("Kairat", "Levski Sofia")]
        def stub(path, params):
            return (200, self._body(page1)) if params.get("page") == 1 \
                else (200, self._body([]))
        out, wrote = self._run_probe(stub)
        self.assertIn("صفحة 1", out)
        self.assertIn("HTTP 200", out)
        self.assertIn("بـxG 1", out)
        self.assertIn("بلا xG 1", out)
        self.assertIn("📊 الإجمالي", out)
        self.assertFalse(wrote)      # قراءة محضة: صفر كتابة

    def test_never_prints_the_key(self):
        """قاعدة الأسرار 3: لا المفتاح ولا أي جزء منه في أي مخرَج، مهما حدث."""
        def stub(path, params):
            return 403, {"message": "Forbidden"}
        out, _ = self._run_probe(stub)
        self.assertNotIn(self.FAKE_KEY, out)
        for chunk in (self.FAKE_KEY[:8], self.FAKE_KEY[8:16], "9f3a2b"):
            self.assertNotIn(chunk, out)
        self.assertIn("HTTP 403", out)          # الحالة تُقال، السرّ لا

    def test_network_exception_prints_type_only(self):
        """استثناء الشبكة: نوعه فقط — نصّه قد يحمل ما لا نريد تسريبه."""
        def stub(path, params):
            return None, {"_exception": "ConnectionError"}
        out, _ = self._run_probe(stub)
        self.assertIn("ConnectionError", out)
        self.assertNotIn(self.FAKE_KEY, out)

    def test_prints_sample_names_from_both_sides(self):
        """10 أسماء من كل جانب لفحص المطابقة بالعين."""
        ours = [{"date": "2026-08-11", "score": "1-0", "home": "Kairat Almaty",
                 "away": "Levski Sofia", "league": "UEFA Champions League"}]
        def stub(path, params):
            return (200, self._body([self._fx("Kairat", "Levski Sofia")])) \
                if params.get("page") == 1 else (200, self._body([]))
        out, _ = self._run_probe(stub, ours=ours)
        self.assertIn("عيّنة أسماء سبورتمونكس", out)
        self.assertIn("عيّنة أسماء محركاتنا", out)
        self.assertIn("Kairat Almaty", out)          # جانبنا
        self.assertIn("UEFA Champions League", out)

    def test_separates_coverage_failure_from_matching_failure(self):
        """التشخيص الحاسم: نظير بالاسم موجود لكن بلا xG ⇐ تغطية لا مطابقة."""
        ours = [{"date": "2026-08-11", "score": "1-0", "home": "Kairat Almaty",
                 "away": "Levski Sofia", "league": "UEFA Champions League"}]
        def stub(path, params):
            return (200, self._body([self._fx("Kairat", "Levski Sofia")])) \
                if params.get("page") == 1 else (200, self._body([]))
        out, _ = self._run_probe(stub, ours=ours)
        self.assertIn("1 لها نظير بالاسم", out)
        self.assertIn("0 منها يحمل xG", out)
        self.assertIn("بلا xG", out)
        self.assertIn("لا المطابقة", out)

    def test_reports_subscription_bundles(self):
        """ملخص الاشتراك يفصل «الباقة بلا xG» عن «دورياتنا خارج الباقة»."""
        def stub(path, params):
            return 200, self._body([self._fx("Arsenal", "Fulham", 2.6, 0.8)])
        out, _ = self._run_probe(stub)
        self.assertIn("Growth", out)
        self.assertIn("Pressure Index & xG", out)

    def test_no_key_skips_cleanly(self):
        def stub(path, params):
            raise AssertionError("لا يجوز نداء الشبكة بلا مفتاح")
        out, wrote = self._run_probe(stub, key="")
        self.assertIn("لا مفتاح", out)
        self.assertFalse(wrote)

    def test_probe_wired_into_cli_and_workflow(self):
        """المسبار قابل للتشغيل من Actions — وإلا بقيت الحقيقة بعيدة عنا."""
        src = Path(S.__file__).read_text(encoding="utf-8")
        self.assertIn('"--probe" in sys.argv', src)
        yml = (Path(__file__).resolve().parent.parent
               / ".github" / "workflows" / "xg_probe.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch", yml)
        self.assertIn("sportmonks_shadow.py --probe", yml)
        self.assertIn("SPORTMONKS_KEY", yml)
        self.assertIn("contents: read", yml)     # لا كتابة في المستودع

    def test_probe_prints_rate_limit_from_body(self):
        """الحاجز القاطع: المسبار يقرأ حالة الحد من جسم الرد ويطبعها."""
        body = self._body([self._fx("Arsenal", "Fulham", 2.6, 0.8)])
        body["rate_limit"] = {"remaining": 2487, "resets_in_seconds": 3210,
                              "requested_entity": "Fixture"}
        out, _ = self._run_probe(lambda p, q: (200, body))
        self.assertIn("سقف النداءات", out)
        self.assertIn("2487", out)               # المتبقي كما رجع
        self.assertIn("3210", out)               # نافذة التصفير كما رجعت
        self.assertIn("Fixture", out)            # الحد لكل كيان لا لكل حساب

    def test_probe_measures_cost_per_call_not_assumes_it(self):
        """كلفة النداء تُقاس من فرق المتبقي بين صفحتين — لا تُفترض."""
        pages = {1: 2500, 2: 2498}
        def stub(path, params):
            page = params.get("page")
            b = self._body([self._fx("A", "B", 1.0, 1.0)], has_more=(page == 1))
            b["rate_limit"] = {"remaining": pages.get(page, 2496),
                               "resets_in_seconds": 3000,
                               "requested_entity": "Fixture"}
            return 200, b
        out, _ = self._run_probe(stub)
        self.assertIn("كلفة مقاسة", out)
        self.assertIn("2 من الرصيد", out)        # 2500 → 2498 عبر نداء واحد

    def test_probe_refuses_to_invent_a_cap_when_provider_is_silent(self):
        """لا بيانات حد = قول ذلك صراحةً. رقم مُختلَق هنا أسوأ من لا رقم."""
        out, _ = self._run_probe(
            lambda p, q: (200, self._body([self._fx("A", "B", 1.0, 1.0)])))
        self.assertIn("لا تفترض السقف", out)
        for bad in ("2500", "3000", "7500"):     # لا سقف مُلفَّق في المخرَج
            self.assertNotIn(bad, out)

    def test_rate_limit_falls_back_to_headers(self):
        """لو نقل المزوّد الحد إلى ترويسة، القراءة لا تعمى."""
        s = S._rate_sample({}, {"x-ratelimit-limit": "2500",
                                "x-ratelimit-remaining": "1999",
                                "x-ratelimit-reset": "600"})
        self.assertEqual(s["remaining"], 1999)
        self.assertEqual(s["limit_header"], 2500)
        self.assertEqual(s["source"], "ترويسة")

    def test_window_label_does_not_overclaim_from_one_reading(self):
        """عدّاد تنازلي واحد لا يثبت طول النافذة — الوصف يقول «يتسق مع»."""
        self.assertIn("تتسق مع", S._window_label(1200))
        self.assertIn("غير معروفة", S._window_label(None))
        self.assertIn("أطول من يوم", S._window_label(200000))

    def test_rate_limit_output_never_leaks_the_key(self):
        """قاعدة الأسرار 3 تشمل مسار الحد الجديد كما تشمل ما قبله."""
        body = self._body([self._fx("A", "B", 1.0, 1.0)])
        body["rate_limit"] = {"remaining": 10, "resets_in_seconds": 60,
                              "requested_entity": "Fixture"}
        out, _ = self._run_probe(lambda p, q: (200, body))
        self.assertNotIn(self.FAKE_KEY, out)
        self.assertNotIn(self.FAKE_KEY[:12], out)


class TestZeroCollectionAlarm(unittest.TestCase):
    """الدرس المعمّم من عطل 14 أغسطس: صفر مدخلات يومين متتاليين = صراخ."""

    class _Guard:
        """بديل api_guard يسجّل ما أُرسل بدل إرساله."""
        def __init__(self, boom=False):
            self.sent, self.boom = [], boom

        def alert_once(self, kind, text):
            if self.boom:
                raise RuntimeError("انفجار متعمد")
            self.sent.append((kind, text))
            return True

    def _with_guard(self, guard, fn):
        orig = S.api_guard
        S.api_guard = guard
        try:
            return fn()
        finally:
            S.api_guard = orig

    def test_one_zero_day_stays_quiet(self):
        """يوم واحد بصفر ليس عطلاً — الإنذار المبكر إنذار كاذب."""
        guard = self._Guard()
        meta = {}
        sent = self._with_guard(
            guard, lambda: S._maybe_alert_zero(meta, 295, 0, "2026-08-13"))
        self.assertFalse(sent)
        self.assertEqual(meta["zero_streak"], 1)
        self.assertEqual(guard.sent, [])

    def test_two_consecutive_zero_days_alert(self):
        guard = self._Guard()
        meta = {"total": 0}
        self._with_guard(
            guard, lambda: S._maybe_alert_zero(meta, 295, 0, "2026-08-13"))
        sent = self._with_guard(
            guard, lambda: S._maybe_alert_zero(meta, 288, 0, "2026-08-14"))
        self.assertTrue(sent)
        self.assertEqual(meta["zero_streak"], S.ZERO_STREAK_ALERT)
        self.assertEqual(len(guard.sent), 1)
        kind, text = guard.sent[0]
        self.assertEqual(kind, "xg_shadow_zero")
        self.assertIn("صفر", text)
        self.assertIn("288", text)          # كم عُرض على المطابقة
        self.assertIn("--probe", text)      # الخطوة التالية صريحة

    def test_a_matched_day_resets_the_streak(self):
        guard = self._Guard()
        meta = {}
        self._with_guard(
            guard, lambda: S._maybe_alert_zero(meta, 295, 0, "2026-08-13"))
        self._with_guard(
            guard, lambda: S._maybe_alert_zero(meta, 120, 9, "2026-08-14"))
        self.assertEqual(meta["zero_streak"], 0)
        self.assertEqual(guard.sent, [])

    def test_idle_day_never_counts_as_zero(self):
        """يوم بلا مباريات مقيَّمة أصلاً يوم هادئ لا عطل (القاعدة 5-أ)."""
        guard = self._Guard()
        meta = {}
        self._with_guard(
            guard, lambda: S._maybe_alert_zero(meta, 0, 0, "2026-08-13"))
        self.assertEqual(meta.get("zero_streak", 0), 0)
        self.assertNotIn("zero_last_day", meta)

    def test_same_day_rerun_does_not_double_count(self):
        """التشغيلة الاحتياطية لا ترفع العدّاد مرتين في اليوم نفسه."""
        guard = self._Guard()
        meta = {}
        for _ in range(3):
            self._with_guard(
                guard, lambda: S._maybe_alert_zero(meta, 295, 0, "2026-08-13"))
        self.assertEqual(meta["zero_streak"], 1)
        self.assertEqual(guard.sent, [])

    def test_alert_failure_never_breaks_the_collector(self):
        """فشل الإنذار لا يجوز أن يصير عطلاً ثانياً."""
        meta = {}
        for day in ("2026-08-13", "2026-08-14"):
            sent = self._with_guard(
                self._Guard(boom=True),
                lambda: S._maybe_alert_zero(meta, 295, 0, day))
        self.assertFalse(sent)              # لا استثناء يخرج

    def test_missing_guard_module_is_tolerated(self):
        """غياب api_guard لا يسقط المجمّع — الاستيراد محروس."""
        meta = {}
        for day in ("2026-08-13", "2026-08-14"):
            sent = self._with_guard(
                None, lambda: S._maybe_alert_zero(meta, 295, 0, day))
        self.assertFalse(sent)
        self.assertEqual(meta["zero_streak"], 2)

    def test_alert_text_carries_no_secret(self):
        meta = {"zero_streak": 2, "zero_last_day": "2026-08-14", "total": 0}
        text = S.zero_alert_text(meta, 295)
        self.assertNotIn(S.KEY or "«لا مفتاح»", text)
        self.assertNotIn("Authorization", text)

    def test_alarm_wired_into_main(self):
        src = inspect.getsource(S.main)
        self.assertIn("_maybe_alert_zero(", src)


class TestZeroVisibility(unittest.TestCase):
    """**اختفاء السطر هو ما أخفى العطل** — تجربة نشطة بصفر تقول «0» بصوت."""

    def _line(self, meta):
        orig = P.load_json
        P.load_json = lambda path, default: {"meta": meta}
        try:
            return P.sportmonks_shadow_line()
        finally:
            P.load_json = orig

    def test_zero_collection_still_shows_a_line(self):
        """الانحدار الحقيقي: total=0 كان يُخفي السطر تماماً طوال 13 أغسطس."""
        line = self._line({"started": "2026-08-13", "total": 0,
                           "last_day_matched": 0, "last_day_unmatched": 295,
                           "zero_streak": 2})
        self.assertNotEqual(line, "")
        self.assertIn("ظل xG", line)
        self.assertIn("0 مباراة", line)
        self.assertIn("295", line)
        self.assertIn("صفر جمع", line)
        self.assertIn("probe", line)
        self.assertIn("2 يوم متتالٍ", line)

    def test_silent_only_before_the_experiment_started(self):
        """الصمت مسموح في حالة واحدة: تجربة لم تبدأ أصلاً."""
        self.assertEqual(self._line({}), "")


class TestLatinFolding(unittest.TestCase):
    """طيّ الحروف التي لا تفكّكها NFKD — تصحيح رمز، لا توسيع مطابقة.

    دليل مقاس (مسبار 14 أغسطس على الرد الحقيقي ليوم 11 أغسطس): من 3 مباريات
    مشمولة بالباقة، سقطت 2 على طبقة المطابقة — إحداهما «Bodø / Glimt» لأن
    الـ ø كانت تُحذف صامتة. الحاجز الثاني بعد التغطية، ولا بد أن يسقط قبل أن
    تبدأ الدوريات المشمولة (الدوري السعودي 13 أغسطس، الإنجليزي 21 أغسطس).
    """

    def test_undecomposable_letters_no_longer_vanish(self):
        self.assertEqual(sorted(S._norm_tokens("Bodø / Glimt")),
                         ["bodo", "glimt"])
        self.assertTrue(S.names_match("Bodo/Glimt", "Bodø / Glimt"))

    def test_common_european_spellings_match(self):
        for ours, theirs in [("Zaglebie Lubin", "Zagłębie Lubin"),
                             ("Preussen", "Preußen"),
                             ("Djurgardens IF", "Djurgårdens IF"),
                             ("Odense Boldklub", "Ødense Boldklub")]:
            self.assertTrue(S.names_match(ours, theirs), f"{ours} ↔ {theirs}")

    def test_known_remaining_gap_is_documented_not_papered_over(self):
        """حدّ معروف باقٍ عمداً: أعراف النقل (Å↔aa، ü↔ue) وألقاب الأندية
        (AGF↔Aarhus). علاجها يحتاج جدول أسماء مرادفة لا مطابقة أفضل —
        وتوسيع المطابقة لالتقاطها كان سيولّد أزواجاً خاطئة. يُقاس ولا يُرقّع."""
        self.assertFalse(S.names_match("Aalborg BK", "Ålborg BK"))
        self.assertFalse(S.names_match("Aarhus", "AGF"))

    def test_folding_never_creates_false_pairs(self):
        """الشرط الحاكم: بيانات خاطئة أسوأ من لا بيانات — لا زوج خاطئ جديد."""
        for a, b in [("Manchester United", "Manchester City"),
                     ("Al Hilal", "Al Nassr"),
                     ("Aarhus", "AGF"),          # لقب مختلف: يبقى بلا مطابقة
                     ("Real Madrid", "Real Sociedad"),
                     ("Bodø / Glimt", "Bodø / Draugen")]:
            self.assertFalse(S.names_match(a, b), f"{a} ↔ {b}")

    def test_existing_umlaut_convention_untouched(self):
        """لم نمسّ ä/ö/ü: NFKD يكفيها، وتغيير عرفها كان سيكسر مطابقة قائمة."""
        self.assertTrue(S.names_match("Bayern München", "Bayern Munchen"))
        self.assertTrue(S.names_match("Malmo FF", "Malmö FF"))

    def test_empty_and_suffix_only_still_never_match(self):
        self.assertFalse(S.names_match("", "Arsenal"))
        self.assertFalse(S.names_match("FC", "SC"))


if __name__ == "__main__":
    unittest.main()
