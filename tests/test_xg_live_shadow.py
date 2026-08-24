# -*- coding: utf-8 -*-
"""🔬 اختبارات xG الحي في الظل — الحد الذي لا يُتجاوز مكتوبٌ هنا كي لا يُنسى.

التجربة كلها مبنية على وعد واحد: xG **لا يؤثر على أي تنبيه يُرسل للمالك**.
لو انكسر هذا الوعد فقدنا القدرة على معرفة هل حسّن أم أضرّ، وصارت كل أرقامنا
بلا معنى. الاختبارات الخمسة أدناه (أ-هـ) هي حرّاس ذلك الوعد.

صفر شبكة وصفر مفاتيح: كل شيء هنا يعمل على بيانات مُصطنعة.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import monitor as M
import predict_v2 as P
import sportmonks_shadow as S


def snap(minute, h=None, a=None, xg=None, gh=0, ga=0):
    s = {"minute": minute, "gh": gh, "ga": ga, "h": h or {}, "a": a or {}}
    if xg:
        s["xg_h"], s["xg_a"] = xg
    return s


class TestExistingScoreFrozen(unittest.TestCase):
    """(أ) اختبار تثبيت صريح: الدرجة الحالية لم تتغيّر قيمتها على نفس المدخلات.

    هذا أهم اختبار في الملف. التجربة كلها تفترض أن الدرجة الحالية ثابتة
    كخط أساس؛ لو انزاحت قيمتها ولو نقطة واحدة صارت المقارنة بين شيئين
    مختلفين وفقدت التجربة معناها بالكامل.
    """

    # قيم مرصودة من danger_score قبل إضافة الطبقة الموازية — أي تغيّر هنا
    # يعني انحداراً في الجزء المثبت (89.5% للأحمر) لا "تحديث توقعات"
    CASES = [
        # (pick, minute, gh, ga, لقطات, الدرجة المتوقعة)
        ("home", 85, 0, 1, [snap(75), snap(85)], 66),
        # 0-0 في د5 مع ترجيح المضيف: النتيجة الحالية تُسقط التوقع تقنياً، لكن
        # 18 نقطة تبقى أخضر — هذا بالضبط علاج ضجيج البدايات (المالك 2026-08-02)
        ("home", 5, 0, 0, [snap(-5), snap(5)], 18),
        ("away", 90, 1, 0, [snap(80), snap(90)], 69),
        ("draw", 70, 0, 0, [snap(60), snap(70)], 10),
        ("home", 60, 2, 1, [snap(50), snap(60)], 15),
    ]

    def test_scores_are_bit_for_bit_unchanged(self):
        for pick, minute, gh, ga, snaps, expected in self.CASES:
            with self.subTest(pick=pick, minute=minute):
                v = M.danger_score(pick, snaps, minute, gh, ga)
                self.assertEqual(v["score"], expected)

    def test_xg_in_snapshot_cannot_move_the_existing_score(self):
        """وجود xG في اللقطة لا يغيّر الدرجة الحالية ولو نقطة — الفصل تام."""
        for pick, minute, gh, ga, snaps, expected in self.CASES:
            loaded = [dict(s, xg_h=3.4, xg_a=0.1) for s in snaps]
            with self.subTest(pick=pick):
                self.assertEqual(
                    M.danger_score(pick, loaded, minute, gh, ga)["score"],
                    expected)


class TestMissingXgBreaksNothing(unittest.TestCase):
    """(ب) غياب xG لا يكسر شيئاً ويترك score_xg فارغاً."""

    def test_no_xg_means_has_xg_false(self):
        v = M.danger_score_xg("home", [snap(85), snap(90)], 90, 0, 1)
        self.assertFalse(v["has_xg"])
        self.assertIsInstance(v["score"], int)      # لا استثناء، لا None

    def test_empty_snaps_do_not_raise(self):
        self.assertFalse(M.danger_score_xg("home", [], 10, 0, 0)["has_xg"])
        self.assertEqual(M._last_xg([]), (None, None))

    def test_lookup_survives_missing_module_and_empty_map(self):
        self.assertIsNone(M.xg_lookup({}, {"home": "A", "away": "B"}))
        self.assertIsNone(M.xg_lookup(None, {"home": "A", "away": "B"}))

    def test_live_map_is_silent_without_key(self):
        """بلا مفتاح: خريطة فارغة وملاحظة، لا استثناء ولا نداء شبكة."""
        orig = S.KEY
        S.KEY = ""
        try:
            xg_map, remaining, note = S.live_xg_map()
        finally:
            S.KEY = orig
        self.assertEqual(xg_map, {})
        self.assertIn("مطفأ", note)


class TestTwoScoreboardsNeverMix(unittest.TestCase):
    """(ج) الكتلتان منفصلتان ولا تختلطان — قاعدة الحوكمة (ج)."""

    def _stats(self, rows):
        log = {"warnings": [], "resolved": rows, "alerts": [],
               "alerts_resolved": [{"fid": "1", "key": "goal", "hit": True,
                                    "date": "2026-08-15"}]}
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps(log), encoding="utf-8")
        orig = P.RADAR_LOG_FILE
        P.RADAR_LOG_FILE = tmp
        try:
            P.resolve_radar_log({"resolved": []})
            return json.loads(tmp.read_text(encoding="utf-8"))
        finally:
            P.RADAR_LOG_FILE = orig
            tmp.unlink(missing_ok=True)

    ROWS = [
        # الحالي أحمر ومصيب، والموازي كهرماني على نفس المباراة
        {"fid": "1", "date": "2026-08-15", "level": "red", "score": 70,
         "level_xg": "amber", "score_xg": 45, "failed": True},
        # الحالي كهرماني ومخطئ، والموازي أحمر ومصيب
        {"fid": "2", "date": "2026-08-15", "level": "amber", "score": 45,
         "level_xg": "red", "score_xg": 72, "failed": False},
        # صف بلا xG إطلاقاً — يجب أن يبقى خارج شريحة المقارنة
        {"fid": "3", "date": "2026-08-15", "level": "red", "score": 80,
         "failed": True},
    ]

    def test_xg_block_exists_beside_the_current_one_not_inside_it(self):
        stats = self._stats(list(self.ROWS))["meta"]["stats"]
        self.assertIn("xg", stats)
        self.assertIn("red", stats)              # الكتلة الحالية في مكانها
        # الكتلة الحالية تعدّ الصفوف الثلاثة؛ كتلة xG تعدّ اثنين فقط
        self.assertEqual(stats["red"]["fired"], 2)
        self.assertEqual(stats["xg"]["n"], 2)

    def test_comparison_runs_on_the_same_matches_for_both_scores(self):
        """الشرط الجوهري: الأساس يُقاس على شريحة xG نفسها لا على كل السجل."""
        xg = self._stats(list(self.ROWS))["meta"]["stats"]["xg"]
        # على الصفّين اللذين لهما xG: الحالي أحمر مرة، والموازي أحمر مرة
        self.assertEqual(xg["base"]["red"]["fired"], 1)
        self.assertEqual(xg["xg"]["red"]["fired"], 1)
        # والنتيجتان معكوستان — دليل أن العدّادين مستقلان فعلاً لا منسوخان
        self.assertEqual(xg["base"]["red"]["hit"], 1)
        self.assertEqual(xg["xg"]["red"]["hit"], 0)

    def test_rows_without_xg_never_enter_the_slice(self):
        xg = self._stats(list(self.ROWS))["meta"]["stats"]["xg"]
        self.assertEqual(xg["n"], 2)             # الصف الثالث خارجها

    def test_xg_only_trigger_is_measured_but_does_not_pollute_current_board(self):
        """الحالة التي تُجرى التجربة لأجلها: xG ينذر ولوحة النتائج مطمئنة.

        يجب أن تُقاس (تدخل كتلة xG) وألا تُحتسب إطلاقاً في عدّادات المالك
        الحالية — الصف درجته الحالية green فلا هو أحمر ولا كهرماني.
        """
        rows = list(self.ROWS) + [
            {"fid": "4", "date": "2026-08-15", "level": "green", "score": 15,
             "level_xg": "amber", "score_xg": 41, "failed": True}]
        stats = self._stats(rows)["meta"]["stats"]
        self.assertEqual(stats["xg"]["n"], 3)            # دخلت القياس
        self.assertEqual(stats["xg"]["xg"]["amber"]["fired"], 2)
        # ولم تُزحزح عدّادات المالك الحالية عمّا كانت عليه قبلها
        self.assertEqual(stats["red"]["fired"], 2)
        self.assertEqual(stats["amber"]["fired"], 1)

    def test_digest_line_hides_percentages_under_the_sample_guard(self):
        """حارس العينة نفسه المطبَّق في REC-010 — لا نسبة تحت الحد."""
        log = {"meta": {"stats": {"xg": {"n": 4,
                                         "xg": {"red": {"fired": 2, "hit": 2}},
                                         "base": {"red": {"fired": 2, "hit": 1}}}}}}
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps(log), encoding="utf-8")
        orig = P.RADAR_LOG_FILE
        P.RADAR_LOG_FILE = tmp
        try:
            line = P.xg_radar_line()
        finally:
            P.RADAR_LOG_FILE = orig
            tmp.unlink(missing_ok=True)
        self.assertIn("عينة غير كافية", line)
        self.assertNotIn("%", line)


class TestNoAlertReadsXg(unittest.TestCase):
    """(د) لا تنبيه يقرأ score_xg — الحد الذي لا يُتجاوز، مثبتاً بنيوياً."""

    def setUp(self):
        # 📵 بوابة التسعة/المفضلة (قرار المالك 2026-08-24 مساءً) تُفحص في
        # جرزها المخصصة — هنا نعطلها لفحص الآليات الأخرى بمعزل عنها
        _orig_gate = M.DRAMA_MINE_ONLY
        M.DRAMA_MINE_ONLY = False
        self.addCleanup(lambda: setattr(M, "DRAMA_MINE_ONLY", _orig_gate))


    ALERT_FUNCS = ("maybe_radar_alert", "maybe_red_alert",
                   "evaluate_comeback", "drama_signal", "should_alert")

    def test_alert_functions_never_mention_the_xg_fields(self):
        """فحص بنيوي على المصدر: أي ذكر لحقول xG داخل دالة تنبيه = فشل."""
        import inspect
        for name in self.ALERT_FUNCS:
            fn = getattr(M, name, None)
            if fn is None:
                continue
            src = inspect.getsource(fn)
            for field in ("score_xg", "level_xg", "xg_h", "xg_a",
                          "danger_score_xg"):
                with self.subTest(func=name, field=field):
                    self.assertNotIn(field, src)

    def test_drama_signal_identical_with_and_without_xg(self):
        """نفس اللقطات مع xG وبدونه → نفس إشارة الدراما بايتاً ببايت."""
        base = [snap(80, {"sog": 1}, {"sog": 5}), snap(88, {"sog": 1}, {"sog": 9})]
        loaded = [dict(s, xg_h=0.2, xg_a=3.9) for s in base]
        self.assertEqual(M.drama_signal(base, 1, 0),
                         M.drama_signal(loaded, 1, 0))

    def test_alert_payload_carries_no_xg_field(self):
        """حتى لو حُسبت الدرجة الموازية، لا تتسرب إلى رسالة أو سجل تنبيه."""
        import inspect
        src = inspect.getsource(M.radar_sweep)
        # تُحسب قبل maybe_radar_alert، لكن لا تُمرَّر إليه — النداء يحمل
        # النسخة الحية من السجل فقط (إصلاح سباق 2026-08-15)، لا أي حقل xG
        self.assertIn("verdict_xg", src)
        self.assertIn("maybe_radar_alert(fid, e, alert_budget, log, watch=watch)", src)
        # watch وسيط بوابة هاتف (قرار 2026-08-24) لا حقل xG — المحظور هو xG
        call = src.split("maybe_radar_alert(fid, e", 1)[1].split(")")[0]
        self.assertNotIn("xg", call.lower())


class TestKillSwitch(unittest.TestCase):
    """(هـ) XG_LIVE_SHADOW=False يعطّل كل شيء فوراً."""

    def test_false_disables_fetching_entirely(self):
        orig_flag, orig_key = S.XG_LIVE_SHADOW, S.KEY
        S.XG_LIVE_SHADOW, S.KEY = False, "مفتاح-وهمي"

        def boom(path, params):
            raise AssertionError("لا يجوز أي نداء والمفتاح مطفأ")
        orig_req = S._request
        S._request = boom
        try:
            xg_map, remaining, note = S.live_xg_map()
        finally:
            S.XG_LIVE_SHADOW, S.KEY, S._request = orig_flag, orig_key, orig_req
        self.assertEqual(xg_map, {})
        self.assertIn("مطفأ", note)

    def test_flag_is_independent_of_the_morning_collector(self):
        """المجمّع الصباحي تجربة جارية — لا يجوز أن يطفئه مفتاح الطبقة الحية."""
        src = Path(S.__file__).read_text(encoding="utf-8")
        self.assertIn("XG_SHADOW = True", src)
        self.assertIn("XG_LIVE_SHADOW = True", src)
        self.assertIn("XG_LIVE_SHADOW", ",".join(
            [l for l in src.splitlines() if "def live_xg_map" in l
             or "XG_LIVE_SHADOW" in l]))


class TestSelfThrottle(unittest.TestCase):
    """الكبح الذاتي: ما يجعل البناء ممكناً قبل معرفة سقف النداءات."""

    def test_stops_fetching_below_the_reserve(self):
        """متبقٍ تحت الحجز = لا نداء إطلاقاً، فلا تُجوَّع التجربة الصباحية."""
        orig_key, orig_req = S.KEY, S._request

        def boom(path, params):
            raise AssertionError("لا يجوز النداء والرصيد تحت الحجز")
        S.KEY, S._request = "مفتاح-وهمي", boom
        try:
            xg_map, remaining, note = S.live_xg_map(
                last_remaining=10, reserve=300)
        finally:
            S.KEY, S._request = orig_key, orig_req
        self.assertEqual(xg_map, {})
        self.assertIn("كبح ذاتي", note)

    def test_reads_remaining_from_the_response_and_returns_it(self):
        """المتبقي يُقرأ من كل رد ويُعاد للمنادي كي ينجو بين التشغيلات."""
        orig_key, orig_req = S.KEY, S._request
        body = {"data": [{"name": "Arsenal vs Fulham",
                          "xgfixture": [
                              {"type_id": S.XG_TYPE_ID, "location": "home",
                               "data": {"value": 2.4}},
                              {"type_id": S.XG_TYPE_ID, "location": "away",
                               "data": {"value": 0.6}}]}],
                "rate_limit": {"remaining": 1817, "resets_in_seconds": 2900,
                               "requested_entity": "Fixture"}}
        S.KEY, S._request = "مفتاح-وهمي", lambda p, q: (200, body)
        try:
            xg_map, remaining, note = S.live_xg_map(last_remaining=None)
        finally:
            S.KEY, S._request = orig_key, orig_req
        self.assertEqual(remaining, 1817)
        self.assertEqual(xg_map[("Arsenal", "Fulham")], (2.4, 0.6))

    def test_one_call_per_cycle_regardless_of_match_count(self):
        """نقطة التصميم: نداء واحد يغطي كل المباريات الجارية، لا نداء لكل مباراة."""
        orig_key, orig_req = S.KEY, S._request
        calls = []
        many = [{"name": f"H{i} vs A{i}",
                 "xgfixture": [{"type_id": S.XG_TYPE_ID, "location": "home",
                                "data": {"value": 1.0}},
                               {"type_id": S.XG_TYPE_ID, "location": "away",
                                "data": {"value": 0.5}}]} for i in range(40)]

        def stub(path, params):
            calls.append(path)
            return 200, {"data": many}
        S.KEY, S._request = "مفتاح-وهمي", stub
        try:
            xg_map, _, _ = S.live_xg_map()
        finally:
            S.KEY, S._request = orig_key, orig_req
        self.assertEqual(len(calls), 1)          # 40 مباراة، نداء واحد
        self.assertEqual(len(xg_map), 40)

    def test_matching_stays_conservative(self):
        """مطابقة الأسماء هنا هي نفسها المحافظة في المجمّع الصباحي."""
        m = {("Arsenal", "Fulham"): (2.4, 0.6)}
        self.assertEqual(S.live_xg_for(m, "Arsenal FC", "Fulham"), (2.4, 0.6))
        self.assertIsNone(S.live_xg_for(m, "Arsenal", "Chelsea"))


class TestFbrefComparisonTable(unittest.TestCase):
    """📋 جدول المقارنة مع FBref: التحقق الوحيد الممكن قبل انتهاء التجربة.

    التحقق الأصلي (OPTA_SAMPLE، موسم 2022-23) رجع بصفر عينة لأن الباقة بلا
    تغطية تاريخية — فالبديل مباريات حالية في الدوريات المغطاة.
    """

    def _table(self, body, day="2026-08-15"):
        import contextlib
        import io as _io
        orig_key, orig_req = S.KEY, S._request
        S.KEY, S._request = "مفتاح-وهمي", lambda p, q: (200, body)
        buf = _io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rows = S.xg_table(day)
        finally:
            S.KEY, S._request = orig_key, orig_req
        return buf.getvalue(), rows

    @staticmethod
    def _fx(home, away, xh, xa, league_id):
        return {"name": f"{home} vs {away}", "league_id": league_id,
                "xgfixture": [
                    {"type_id": S.XG_TYPE_ID, "location": "home",
                     "data": {"value": xh}},
                    {"type_id": S.XG_TYPE_ID, "location": "away",
                     "data": {"value": xa}}]}

    def test_marks_owner_covered_leagues_by_name(self):
        """الدوريات المغطاة تُسمّى بالعربية لا برقمها — يقرؤها المالك لا مبرمج."""
        body = {"data": [self._fx("Al Hilal", "Al Faisaly", 2.4, 0.6, 944),
                         self._fx("Wolves", "Blackburn", 1.8, 1.1, 9),
                         self._fx("Naft", "Al Karkh", 0.9, 0.4, 911)],
                "pagination": {"has_more": False}}
        out, rows = self._table(body)
        self.assertEqual(len(rows), 3)
        self.assertIn("الدوري السعودي", out)
        self.assertIn("التشامبيونشيب", out)
        self.assertIn("دوري 911", out)          # غير مغطى: يُعرض برقمه بصدق
        self.assertIn("منها 2", out)            # اثنتان في دوريات المالك

    def test_points_at_fbref_only_when_there_is_something_to_compare(self):
        body = {"data": [self._fx("Naft", "Al Karkh", 0.9, 0.4, 911)],
                "pagination": {"has_more": False}}
        out, _ = self._table(body)
        self.assertNotIn("fbref.com", out)
        self.assertIn("لا مباراة في الدوريات المغطاة", out)

    def test_offers_fbref_when_a_covered_match_exists(self):
        body = {"data": [self._fx("Al Hilal", "Al Faisaly", 2.4, 0.6, 944)],
                "pagination": {"has_more": False}}
        out, _ = self._table(body)
        self.assertIn("fbref.com", out)
        self.assertIn("0.35", out)              # حد التوافق المعلن

    def test_never_prints_the_key(self):
        body = {"data": [self._fx("Al Hilal", "Al Faisaly", 2.4, 0.6, 944)],
                "pagination": {"has_more": False}}
        out, _ = self._table(body)
        self.assertNotIn("مفتاح-وهمي", out)

    def test_covered_list_is_reference_not_a_gate(self):
        """⛔ القائمة للعرض فقط: مباراة خارجها تُجمع ولا تُحجب.

        لو صارت بوابةً لأصبحت قائمة حظر تفشل مفتوحة — نمط حادثة الدوريات
        النسائية بعينه. مطابقة الاسم هي البوابة الحقيقية.
        """
        body = {"data": [self._fx("Some Team", "Other Team", 1.0, 2.0, 12345)],
                "pagination": {"has_more": False}}
        _, rows = self._table(body)
        self.assertEqual(len(rows), 1)          # جُمعت رغم أنها خارج القائمة

    def test_table_mode_is_wired_into_cli_and_workflow(self):
        src = Path(S.__file__).read_text(encoding="utf-8")
        self.assertIn('"--table" in sys.argv', src)
        yml = (Path(__file__).resolve().parent.parent
               / ".github" / "workflows" / "xg_probe.yml").read_text(encoding="utf-8")
        self.assertIn("--table", yml)
        self.assertIn("contents: read", yml)     # ما زال قراءة محضة


class TestXgScoreBehaviour(unittest.TestCase):
    """الدرجة الموازية: تستبدل طبقة الزخم بفارق xG وتبقي المثبت كما هو."""

    def test_scoreboard_component_matches_the_current_score_exactly(self):
        """بلا xG وبلا زخم: الدرجتان متطابقتان — الجزء المثبت لم يُمسّ."""
        for pick, minute, gh, ga in (("home", 85, 0, 1), ("draw", 70, 0, 0),
                                     ("home", 60, 2, 1), ("away", 90, 1, 0)):
            snaps = [snap(minute - 10), snap(minute)]
            with self.subTest(pick=pick):
                self.assertEqual(
                    M.danger_score(pick, snaps, minute, gh, ga)["score"],
                    M.danger_score_xg(pick, snaps, minute, gh, ga)["score"])

    def test_xg_edge_against_the_pick_raises_danger(self):
        """أفضلية xG للطرف الذي يهدد التوقع ترفع الدرجة الموازية."""
        snaps = [snap(70, xg=(0.3, 2.5)), snap(75, xg=(0.3, 2.6))]
        v = M.danger_score_xg("home", snaps, 75, 1, 0)
        base = M.danger_score_xg("home", [snap(70), snap(75)], 75, 1, 0)
        self.assertGreater(v["score"], base["score"])
        self.assertTrue(v["has_xg"])
        self.assertTrue(any("xG" in f for f in v["factors"]))

    def test_xg_edge_favouring_the_pick_adds_nothing(self):
        """أفضلية xG لصالح الطرف المُختار لا تضيف خطراً — الاتجاه محترم."""
        snaps = [snap(70, xg=(2.5, 0.3)), snap(75, xg=(2.6, 0.3))]
        v = M.danger_score_xg("home", snaps, 75, 1, 0)
        self.assertEqual(v["score"],
                         M.danger_score("home", [snap(70), snap(75)], 75, 1, 0)["score"])

    def test_last_known_xg_is_used_when_newest_snap_lacks_it(self):
        """لقطة سريعة بلا xG لا تمحو آخر قراءة معروفة."""
        self.assertEqual(M._last_xg([snap(70, xg=(1.1, 2.2)), snap(75)]),
                         (1.1, 2.2))


if __name__ == "__main__":
    unittest.main()
