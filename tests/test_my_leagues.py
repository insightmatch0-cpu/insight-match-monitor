# -*- coding: utf-8 -*-
"""مفتاح "دورياتي فقط" — REC-010 (قرار المالك 2026-08-13: ب، ب، حد 20).

توصية وضوح لا توصية أداء: لا يتغير أي توقع ولا أي ثقة ولا أي تنبيه.
لذلك أهم اختبار في هذا الملف هو (ب) — أرقام "الكل" مطابقة تماماً لما قبل
التغيير. البقية تحرس ما يمكن أن ينكسر بصمت:

  (أ) أي شريحة مفلترة عددها < 20 لا تعرض نسبة مئوية إطلاقاً.
  (ب) أرقام "الكل" لم تتغير — لا انحدار في العرض القائم.
  (ج) كتلة دوريات المالك تُحسب صحيحاً من صفوف وهمية مختلطة.
  (د) سجل رادار بلا علامة `top` لا يدخل الشريحة (غير مصنّف، لا يُخمَّن).
  (هـ) تكافؤ مفاتيح i18n عربي/إنجليزي.
  (و) لا قصّ [-N:] جديد على أي سجل قياس.

وحارس دائم مستقل: علامة الدوري تُشتق بالمعرف من TOP_LEAGUE_IDS ولا تُطابَق
باسم الدوري نصاً أبداً — نمط "قائمة الحظر التي تفشل مفتوحة" هو بالضبط ما
سبّب حادثة الدوريات النسائية (2026-08-01).
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard_update as D
import monitor as M
import predict_v2 as P

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "index.html").read_text(encoding="utf-8")
SCRIPT = re.search(r"<script>([\s\S]*?)</script>", HTML).group(1)

# لحظة مثبتة حتى تبقى نافذة "آخر 30 يوماً" حتمية مهما تأخر تشغيل الاختبار
FROZEN_NOW = datetime(2026, 8, 14, 6, 0, tzinfo=timezone.utc)


def _row(date, top, correct, conf):
    """صف توقع مُقيَّم بالحد الأدنى من الحقول التي تقرؤها الإحصاءات."""
    return {"date": date, "top": top, "correct": correct, "confidence": conf}


# ستة صفوف مختلطة: دوريات المالك وغيرها، داخل الموسم وخارجه،
# داخل آخر 30 يوماً وخارجها، وكل شريحة ثقة ممثَّلة
MIXED = [
    _row("2026-08-13", True,  True,  72),   # موسم · دورياته · 70+
    _row("2026-08-13", True,  False, 65),   # موسم · دورياته · 60-69
    _row("2026-08-12", True,  True,  55),   # قبل الموسم · دورياته · 50-59
    _row("2026-08-12", False, True,  72),   # قبل الموسم · غيرها · 70+
    _row("2026-07-01", False, False, 45),   # خارج آخر 30 يوماً · غيرها · <50
    _row("2026-08-01", False, True,  62),   # غيرها · 60-69
]

_Z = {"correct": 0, "total": 0}

# 🥇 المرجع الذهبي: شكل شجرة "الكل" كما كانت تُنتَج قبل REC-010 حرفياً،
# مكتوب باليد لا مولَّداً من الكود — فلو انحرف الحساب يوماً كشفه هذا الثابت
GOLDEN_ALL = {
    "overall": {"correct": 4, "total": 6},
    "top_leagues": {"correct": 2, "total": 3},
    "other_leagues": {"correct": 2, "total": 3},
    "by_confidence": {
        "70+": {"correct": 2, "total": 2},
        "60-69": {"correct": 1, "total": 2},
        "50-59": {"correct": 1, "total": 1},
        "<50": {"correct": 0, "total": 1},
    },
    "last30": {"correct": 4, "total": 5},
    "daily": {
        "2026-07-01": {"correct": 0, "total": 1},
        "2026-08-01": {"correct": 1, "total": 1},
        "2026-08-12": {"correct": 2, "total": 2},
        "2026-08-13": {"correct": 1, "total": 2},
    },
    "season": {
        "overall": {"correct": 1, "total": 2},
        "top_leagues": {"correct": 1, "total": 2},
        "other_leagues": dict(_Z),
        "by_confidence": {
            "70+": {"correct": 1, "total": 1},
            "60-69": {"correct": 0, "total": 1},
            "50-59": dict(_Z),
            "<50": dict(_Z),
        },
        "start": "2026-08-13",
    },
}

# 🎛 شريحة دوريات المالك من نفس الصفوف — الثلاثة الحاملة علامة top
GOLDEN_MINE = {
    "overall": {"correct": 2, "total": 3},
    "top_leagues": {"correct": 2, "total": 3},
    "other_leagues": dict(_Z),
    "by_confidence": {
        "70+": {"correct": 1, "total": 1},
        "60-69": {"correct": 0, "total": 1},
        "50-59": {"correct": 1, "total": 1},
        "<50": dict(_Z),
    },
    "last30": {"correct": 2, "total": 3},
    "daily": {
        "2026-08-12": {"correct": 1, "total": 1},
        "2026-08-13": {"correct": 1, "total": 2},
    },
    "season": {
        "overall": {"correct": 1, "total": 2},
        "top_leagues": {"correct": 1, "total": 2},
        "other_leagues": dict(_Z),
        "by_confidence": {
            "70+": {"correct": 1, "total": 1},
            "60-69": {"correct": 0, "total": 1},
            "50-59": dict(_Z),
            "<50": dict(_Z),
        },
        "start": "2026-08-13",
    },
    "market_bench": {"n": 0, "engine_correct": 0, "market_correct": 0,
                     "disagree": 0},
    "min_sample": 20,
}


class _FrozenClock(unittest.TestCase):
    """يثبّت لحظة الحساب حتى لا تتحرك نافذة آخر 30 يوماً تحت الاختبار."""

    def setUp(self):
        orig = P.now_utc
        P.now_utc = lambda: FROZEN_NOW
        self.addCleanup(lambda: setattr(P, "now_utc", orig))


# ============ (ب) أهم اختبار: لا انحدار في أرقام "الكل" ============
class TestAllNumbersUnchanged(_FrozenClock):
    """أرقام "الكل" مطابقة تماماً لما قبل التغيير — العرض القائم لا يتحرك."""

    def test_all_tree_matches_golden(self):
        stats = P.compute_stats(MIXED)
        for key, expected in GOLDEN_ALL.items():
            self.assertEqual(stats[key], expected,
                             f"رقم من أرقام 'الكل' تغيّر: {key}")

    def test_only_new_key_added(self):
        """المفتاح الجديد الوحيد هو top_only — لا مفتاح قائم حُذف أو أُعيد تسميته."""
        stats = P.compute_stats(MIXED)
        self.assertEqual(set(stats) - set(GOLDEN_ALL), {"top_only"})
        self.assertEqual(set(GOLDEN_ALL) - set(stats), set())

    def test_filtered_block_never_feeds_back_into_all(self):
        """وجود الكتلة الموازية لا يغيّر رقماً واحداً في الشجرة الكاملة."""
        full = P.compute_stats(MIXED)
        del full["top_only"]
        self.assertEqual(full, P._stats_tree(MIXED))

    def test_rows_without_top_flag_still_counted_in_all(self):
        """صف قديم بلا حقل top يبقى محسوباً في "الكل" (كـ other_leagues) —
        الفلترة الجديدة لا تُسقط أحداً من العرض القائم."""
        rows = [{"date": "2026-08-13", "correct": True, "confidence": 60}]
        stats = P.compute_stats(rows)
        self.assertEqual(stats["overall"], {"correct": 1, "total": 1})
        self.assertEqual(stats["other_leagues"], {"correct": 1, "total": 1})
        self.assertEqual(stats["top_only"]["overall"], dict(_Z))


# ============ (ج) كتلة دوريات المالك تُحسب صحيحاً ============
class TestMineBlockMath(_FrozenClock):
    """الشجرة الموازية بنفس دوال الشجرة الكاملة على صفوف top وحدها."""

    def test_mine_tree_matches_golden(self):
        self.assertEqual(P.compute_stats(MIXED)["top_only"], GOLDEN_MINE)

    def test_mine_uses_the_same_math_functions(self):
        """نفس _stats_tree حرفياً — لا نسخة ثانية من المنطق تنحرف لاحقاً."""
        mine = P.top_only_stats(MIXED)
        expected = P._stats_tree([r for r in MIXED if r.get("top")])
        for key in expected:
            self.assertEqual(mine[key], expected[key], key)

    def test_market_bench_covered(self):
        """معيار السوق (REC-003) محسوب داخل الشريحة على صفوفها وحدها."""
        rows = [
            dict(_row("2026-08-13", True, True, 70), pick="home",
                 actual="home", mkt_home=50, mkt_draw=30, mkt_away=20),
            dict(_row("2026-08-13", False, False, 70), pick="away",
                 actual="home", mkt_home=60, mkt_draw=25, mkt_away=15),
        ]
        mine = P.top_only_stats(rows)
        # الصف غير التابع لدورياته لا يدخل معيار السوق المفلتر
        self.assertEqual(mine["market_bench"],
                         {"n": 1, "engine_correct": 1, "market_correct": 1,
                          "disagree": 0})

    def test_min_sample_travels_with_the_numbers(self):
        """الحد يسافر مع الأرقام حتى تقرأه اللوحة من مصدره لا من ثابت مكرر."""
        self.assertEqual(P.MIN_FILTERED_SAMPLE, 20)
        self.assertEqual(P.top_only_stats(MIXED)["min_sample"], 20)

    def test_empty_slice_is_zeros_not_crash(self):
        mine = P.top_only_stats([_row("2026-08-13", False, True, 70)])
        self.assertEqual(mine["overall"], dict(_Z))
        self.assertEqual(mine["daily"], {})


# ============ (د) سجل رادار بلا علامة لا يدخل الشريحة ============
class TestRadarSlice(unittest.TestCase):
    """التقييم الصباحي يبني كتلة موازية للرادار من السجلات الموسومة وحدها."""

    def _run_resolve(self, log_content):
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps(log_content, ensure_ascii=False),
                       encoding="utf-8")
        orig = P.RADAR_LOG_FILE
        P.RADAR_LOG_FILE = tmp
        self.addCleanup(lambda: (setattr(P, "RADAR_LOG_FILE", orig),
                                 tmp.unlink(missing_ok=True)))
        P.resolve_radar_log({"resolved": []})
        return json.loads(tmp.read_text(encoding="utf-8"))

    def _mixed_log(self):
        d = "2026-08-13"
        return {
            "resolved": [
                # دورياته
                {"fid": "1", "level": "red", "date": d, "failed": True,
                 "graded_on": d, "top": True},
                {"fid": "2", "level": "amber", "date": d, "failed": False,
                 "graded_on": d, "top": True},
                # غير دورياته (موسومة صراحةً False)
                {"fid": "3", "level": "red", "date": d, "failed": True,
                 "graded_on": d, "top": False},
                # ⛳️ سجل قديم بلا علامة إطلاقاً — "غير مصنّف"
                {"fid": "4", "level": "red", "date": d, "failed": True,
                 "graded_on": d},
            ],
            "alerts_resolved": [
                {"fid": "5", "key": "goal", "date": d, "hit": True,
                 "top": True},
                {"fid": "6", "key": "goal", "date": d, "hit": False,
                 "top": False},
                {"fid": "7", "key": "goal", "date": d, "hit": True},
                {"fid": "8", "key": "flip", "date": d, "hit": True,
                 "top": True},
            ],
        }

    def test_unlabelled_rows_stay_out_of_the_slice(self):
        """السجلات بلا علامة `top` لا تُخمَّن ولا تُحتسب — الفلتر يمتلئ من
        لحظة التنفيذ (منصوص عليه في التوصية ومقبول من المالك)."""
        stats = self._run_resolve(self._mixed_log())["meta"]["stats"]
        # التراكمي يرى الأربعة: 3 حمراء + 1 كهرماني
        self.assertEqual(stats["red"], {"fired": 3, "hit": 3})
        self.assertEqual(stats["amber"], {"fired": 1, "hit": 0})
        # الشريحة ترى الموسومة True وحدها
        self.assertEqual(stats["top_only"]["red"], {"fired": 1, "hit": 1})
        self.assertEqual(stats["top_only"]["amber"], {"fired": 1, "hit": 0})

    def test_slice_covers_every_drama_claim_type(self):
        stats = self._run_resolve(self._mixed_log())["meta"]["stats"]
        self.assertEqual(stats["alerts"]["goal"], {"fired": 3, "hit": 2})
        self.assertEqual(stats["top_only"]["alerts"]["goal"],
                         {"fired": 1, "hit": 1})
        self.assertEqual(stats["top_only"]["alerts"]["flip"],
                         {"fired": 1, "hit": 1})

    def test_slice_has_its_own_season_block(self):
        """نفس أسلوب كتلة الموسم: ترشيح بالتاريخ داخل الشريحة نفسها."""
        log = self._mixed_log()
        log["resolved"].append({"fid": "9", "level": "red", "date": "2026-08-01",
                                "failed": True, "graded_on": "2026-08-01",
                                "top": True})
        stats = self._run_resolve(log)["meta"]["stats"]
        self.assertEqual(stats["top_only"]["red"], {"fired": 2, "hit": 2})
        self.assertEqual(stats["top_only"]["season"]["red"],
                         {"fired": 1, "hit": 1})
        self.assertEqual(stats["top_only"]["season"]["start"], P.SEASON_START)

    def test_stop_rule_never_reads_the_filtered_slice(self):
        """⛔ الانحدار الخطير: قاعدة الإيقاف (REC-005) مربوطة بالسجل التراكمي.
        لو قرأت الشريحة المفلترة لهبطت الأنواع تحت عتبة الـ 30 ولخرجت من
        الصمت فعادت ترسل تيليجرام — نفس فخ عدّاد الموسم بالضبط."""
        d = "2026-08-13"
        log = {
            "resolved": [],
            # 40 تنبيهاً مُقيَّماً بدقة 25% — دون عتبة الإسكات، وكلها ليست
            # من دوريات المالك، فالشريحة المفلترة فارغة تماماً
            "alerts_resolved": [
                {"fid": f"a{i}", "key": "goal", "date": d, "hit": i < 10,
                 "top": False} for i in range(40)
            ],
        }
        out = self._run_resolve(log)
        self.assertEqual(out["silenced"], ["goal"],
                         "قاعدة الإيقاف يجب أن تبقى على السجل التراكمي")
        self.assertEqual(out["meta"]["stats"]["top_only"]["alerts"], {})

    def test_min_sample_stamped_on_the_radar_slice(self):
        stats = self._run_resolve(self._mixed_log())["meta"]["stats"]
        self.assertEqual(stats["top_only"]["min_sample"],
                         P.MIN_FILTERED_SAMPLE)


# ============ علامة الدوري تُكتب بالمعرف لا بالاسم ============
class TestRadarTopStamp(unittest.TestCase):
    """monitor.py يختم علامة الدوري عند الكتابة — بالمعرف، أبداً بالاسم."""

    def test_flag_read_from_prediction_row_not_league_name(self):
        self.assertTrue(M.radar_is_top({"radar": {"top": True}}))
        self.assertFalse(M.radar_is_top({"radar": {"top": False}}))
        # سجل بلا كتلة رادار أو بلا علامة = غير مصنّف (False) لا تخمين
        self.assertFalse(M.radar_is_top({}))
        self.assertFalse(M.radar_is_top({"radar": {}}))
        # اسم دوري كبير في الحقل النصي لا يصنع علامة — لا مطابقة أسماء
        self.assertFalse(M.radar_is_top(
            {"league": "Premier League (England)", "radar": {}}))

    def test_no_league_name_matching_in_the_stamp(self):
        """⛔ حادثة الدوريات النسائية (2026-08-01): مطابقة الاسم تفشل مفتوحة.
        دالة الختم يجب ألا تقارن نصاً إطلاقاً."""
        src = (ROOT / "monitor.py").read_text(encoding="utf-8")
        body = re.search(r"def radar_is_top\(e: dict\) -> bool:([\s\S]*?)\n\n\n",
                         src).group(1)
        for banned in ("league", "startswith", "lower()", "in name", "re."):
            self.assertNotIn(banned, body.split('"""')[-1],
                             "علامة الدوري لا تُشتق من نص")

    def test_every_radar_log_write_stamps_the_flag(self):
        """المواضع الثلاثة (إنذار مستوى + تنبيه دراما + تنبيه طرد) كلها تختم."""
        src = (ROOT / "monitor.py").read_text(encoding="utf-8")
        self.assertEqual(src.count('"top": radar_is_top(e)'), 3)
        # والعلامة تُبنى في مسارَي الرادار (العادي والسريع) من صف التوقع
        self.assertEqual(src.count('bool(p.get("top", radar.get("top")))'), 2)


# ============ (أ) حارس العينة في اللوحة ============
class TestSampleGuardJS(unittest.TestCase):
    """⛔ البند الأخطر: أي عرض مفلتر عدده < 20 لا يعرض نسبة مئوية إطلاقاً."""

    @staticmethod
    def _fn(name):
        m = re.search(r"\nfunction " + name + r"\([\s\S]*?\n\}", SCRIPT)
        return m.group(0)

    @staticmethod
    def _min_sample_decl():
        """يُقتطع إعلان الحد من الصفحة نفسها لا يُعاد تعريفه في الاختبار —
        وإلا مرّ خفضُ الحد (20 ← 0) دون أن يكشفه أحد."""
        return re.search(r"var MIN_FILTERED_SAMPLE = \d+;", SCRIPT).group(0)

    def test_threshold_is_twenty_on_the_page_and_in_the_engine(self):
        """حد العينة قرار مالك (20) — مثبت في الصفحة وفي المحرك معاً."""
        self.assertEqual(self._min_sample_decl(), "var MIN_FILTERED_SAMPLE = 20;")
        self.assertEqual(P.MIN_FILTERED_SAMPLE, 20)

    def _run(self, scope, cases):
        node = shutil.which("node")
        if not node:
            self.skipTest("node غير متوفر")
        js = (
            'var scope = "' + scope + '";\n'
            + self._min_sample_decl() + "\n"
            'function t(k){ return k === "smallSample"'
            ' ? "عينة غير كافية ({n} من {m})" : k; }\n'
            + self._fn("normCount") + self._fn("sampleOf")
            + self._fn("scopedVal") + self._fn("countText")
            + "\nconsole.log(JSON.stringify("
            + json.dumps(cases) + ".map(function(o){"
            "return [scopedVal(o), countText(o)]; })));"
        )
        r = subprocess.run([node, "-e", js], capture_output=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr.decode()[:400])
        return json.loads(r.stdout.decode())

    # عدّادات على الشكلين المستعملين في المشروع: الدقة والرادار
    CASES = [
        {"correct": 0, "total": 3},     # 0% مضلل على 3 مباريات
        {"correct": 2, "total": 2},     # 100% مضلل على مباراتين
        {"correct": 9, "total": 19},    # على حافة الحد من تحت
        {"correct": 10, "total": 20},   # عند الحد بالضبط — مسموح
        {"correct": 39, "total": 104},  # عينة كافية
        {"hit": 1, "fired": 4},         # سجل رادار صغير
        {"hit": 12, "fired": 30},       # سجل رادار كافٍ
    ]

    def test_no_percentage_below_twenty_in_filtered_view(self):
        out = self._run("mine", self.CASES)
        for (val, _cnt), case in zip(out, self.CASES):
            n = case.get("total", case.get("fired"))
            if n < 20:
                self.assertTrue(val["small"], f"عينة {n} يجب أن تُحرس")
                self.assertNotIn("%", val["text"],
                                 f"ظهرت نسبة مئوية على عينة {n}!")
                self.assertIn("عينة غير كافية", val["text"])
                self.assertIn(str(n), val["text"])
                self.assertIn("20", val["text"])
            else:
                self.assertFalse(val["small"])
                self.assertIn("%", val["text"])

    def test_percentages_are_correct_above_the_threshold(self):
        out = self._run("mine", self.CASES)
        self.assertEqual(out[3][0]["text"], "50%")
        self.assertEqual(out[4][0]["text"], "38%")
        self.assertEqual(out[6][0]["text"], "40%")

    def test_all_view_is_never_guarded(self):
        """شريحة "الكل" لا يمسّها الحارس — لا انحدار في العرض القائم."""
        out = self._run("all", self.CASES)
        for val, _cnt in out:
            self.assertFalse(val["small"])
        self.assertEqual(out[0][0]["text"], "0%")
        self.assertEqual(out[1][0]["text"], "100%")

    def test_raw_counts_stay_visible_under_the_guard(self):
        """العدّاد المطلق (صح/مجموع) ليس نسبة — يبقى ظاهراً دائماً."""
        out = self._run("mine", self.CASES)
        self.assertEqual(out[0][1], "0/3")
        self.assertEqual(out[5][1], "1/4")

    def test_daily_trend_bars_are_guarded_too(self):
        """عمود الاتجاه ارتفاعه نسبة مئوية — الأيام تحت الحد تُستبعد (REJ-002)."""
        acc = re.search(r"function renderAccuracy\(acc\)\{[\s\S]*?\n\}", SCRIPT).group(0)
        self.assertIn("sampleOf(daily[d]) >= MIN_FILTERED_SAMPLE", acc)
        self.assertIn("trendSmall", acc)
        panel = re.search(r"function radarAccPanel\(acc\)\{[\s\S]*?\n\}", SCRIPT).group(0)
        self.assertIn("sampleOf(dw[d]) >= MIN_FILTERED_SAMPLE", panel)


# ============ المفتاح نفسه: موضعاه، افتراضه، وذاكرته ============
class TestScopeSwitch(unittest.TestCase):
    """قرار المالك: (ب) الدقة + الرادار · (ب) الافتراضي "دورياتي" · حد 20."""

    def test_switch_rendered_in_both_places(self):
        self.assertIn('id="acc-scope"', HTML)
        self.assertIn('id="radar-scope"', HTML)
        self.assertIn('ids = ["acc-scope", "radar-scope"]', SCRIPT)

    def test_default_is_my_leagues(self):
        self.assertIn('var scope = "mine";', SCRIPT)
        self.assertIn('localStorage.getItem("im-scope") || "mine"', SCRIPT)
        # أي قيمة محفوظة تالفة تعود للافتراضي لا لـ "الكل"
        self.assertIn('if (scope !== "all" && scope !== "mine") scope = "mine";',
                      SCRIPT)

    def test_choice_is_persisted(self):
        self.assertIn('localStorage.setItem("im-scope", scope)', SCRIPT)

    def test_switch_repaints_every_number(self):
        """تبديل المفتاح يعيد الرسم الكامل — لا رقم قديم يبقى على الشاشة."""
        body = re.search(r"function setScope\(s\)\{[\s\S]*?\n\}", SCRIPT).group(0)
        self.assertIn("renderAll()", body)

    def test_both_panels_read_the_filtered_block(self):
        for fn in ("renderAccuracy", "radarAccPanel"):
            body = re.search(r"function " + fn + r"\(acc\)\{[\s\S]*?\n\}",
                             SCRIPT).group(0)
            self.assertIn("acc = acc.top_only;", body,
                          f"{fn} يجب أن يقرأ الشجرة الموازية في شريحة دورياته")

    def test_switch_click_does_not_collapse_the_section(self):
        """🐞 علة رُصدت في المتصفح أثناء التطوير: المفتاح يسكن داخل رأس القسم
        القابل للطي، فكانت كل نقرة تبديل تطوي لوحة الدقة تحت يد المالك.
        طبقتا الحماية: إيقاف صعود الحدث من الزر + فحص الصنف في معالِج الرأس.
        الفحص بالصنف لا بـ closest عمداً — الزر يُعاد رسمه قبل صعود الحدث
        فيصير عنصراً منفصلاً عن الشجرة وترجع closest عندها null."""
        self.assertEqual(SCRIPT.count("event.stopPropagation();setScope("), 2)
        head = re.search(r'head\.addEventListener\("click"[\s\S]*?\n    \}\);',
                         SCRIPT).group(0)
        code = re.sub(r"/\*[\s\S]*?\*/", "", head)   # التعليقات ليست كوداً
        self.assertIn('ev.target.classList.contains("sc-btn")', code)
        self.assertNotIn("closest", code)

    def test_build_bumped(self):
        """قاعدة دائمة: كل PR يمسّ index.html يرفع IM_BUILD (درس 2026-08-02)."""
        self.assertIn("var IM_BUILD = 78;", SCRIPT)


# ============ (هـ) تكافؤ مفاتيح i18n ============
class TestI18nParity(unittest.TestCase):
    """كل مفتاح جديد موجود بالعربية والإنجليزية — لا نص مكسور عند التبديل."""

    NEW_KEYS = ("scopeMine", "scopeAll", "scopeMineNote", "smallSample",
                "mineNoData", "mineNoBlock", "radarMineEmpty", "trendSmall")

    def test_every_new_key_exists_twice(self):
        for key in self.NEW_KEYS:
            self.assertEqual(HTML.count(key + ':"'), 2,
                             f"المفتاح {key} يجب أن يوجد بالعربية والإنجليزية")

    def test_arabic_wording_is_the_owners_text(self):
        self.assertIn('scopeMine:"دورياتي فقط"', HTML)
        self.assertIn('scopeAll:"الكل"', HTML)
        self.assertIn('smallSample:"عينة غير كافية ({n} من {m})"', HTML)

    def test_placeholders_match_across_languages(self):
        """نفس المتغيرات في النصين — وإلا ظهر {n} خاماً للقارئ الإنجليزي."""
        for key in self.NEW_KEYS:
            vals = re.findall(key + r':"([^"]*)"', HTML)
            self.assertEqual(len(vals), 2, key)
            self.assertEqual(sorted(re.findall(r"\{\w+\}", vals[0])),
                             sorted(re.findall(r"\{\w+\}", vals[1])), key)

    def test_numerals_are_latin(self):
        """قاعدة المشروع 2: أرقام لاتينية دائماً، لا هندية."""
        for key in self.NEW_KEYS:
            for val in re.findall(key + r':"([^"]*)"', HTML):
                self.assertFalse(re.search(r"[٠-٩]", val),
                                 f"رقم عربي-هندي في {key}")


# ============ اللوحة: الكتلتان تصلان data.json و data_v2.json ============
class TestDashboardPayload(_FrozenClock):
    """اللوحة تمرر الكتلتين للمحركين — والمحرك 1 مجمّد فتُشتق شريحته هنا."""

    def test_engine1_slice_derived_in_dashboard_not_in_predict(self):
        """قاعدة المشروع 7: المحرك 1 لا يُمسّ — أرقام دورياته تُقرأ هنا."""
        v1_src = (ROOT / "predict.py").read_text(encoding="utf-8")
        self.assertNotIn("top_only", v1_src)
        self.assertNotIn("MIN_FILTERED_SAMPLE", v1_src)

    def test_dashboard_slice_equals_engine2_math(self):
        """مصدر رياضيات واحد للمحركين — لا نسخة ثانية تنحرف بمرور الوقت."""
        self.assertEqual(D.top_only_accuracy(MIXED), P.top_only_stats(MIXED))

    def test_existing_stats_keys_untouched(self):
        """الحقن يضيف top_only فقط — أرقام "الكل" تخرج كما دخلت حرفياً."""
        stats = {"overall": {"correct": 5, "total": 9}, "daily": {"x": 1}}
        out = D._with_top_only(dict(stats), {"resolved": MIXED})
        for key, val in stats.items():
            self.assertEqual(out[key], val)
        self.assertEqual(set(out) - set(stats), {"top_only"})

    def test_engine2_block_passed_through_untouched(self):
        """إن كتبها المحرك 2 بنفسه صباحاً تُمرَّر كما هي بلا إعادة حساب."""
        own = {"overall": {"correct": 1, "total": 1}, "min_sample": 20}
        out = D._with_top_only({"top_only": own}, {"resolved": MIXED})
        self.assertIs(out["top_only"], own)

    def test_radar_slice_daily_trend_excludes_unlabelled(self):
        rows = [{"graded_on": "2026-08-13", "failed": True, "top": True},
                {"graded_on": "2026-08-13", "failed": False, "top": False},
                {"graded_on": "2026-08-13", "failed": True}]
        self.assertEqual(D._daily_warnings(rows),
                         {"2026-08-13": {"hit": 2, "total": 3}})
        self.assertEqual(D._daily_warnings([r for r in rows if r.get("top")]),
                         {"2026-08-13": {"hit": 1, "total": 1}})


# ============ (و) لا قصّ جديد على أي سجل قياس ============
class TestNoNewTruncation(unittest.TestCase):
    """عقيدة لا-أسقف-قياس: الكتل الجديدة ترشّح ولا تقصّ."""

    NEW_FUNCS = (("predict_v2.py", "_stats_tree"),
                 ("predict_v2.py", "top_only_stats"),
                 ("dashboard_update.py", "top_only_accuracy"),
                 ("dashboard_update.py", "_with_top_only"))

    def test_no_new_slice_in_the_filtered_blocks(self):
        for fname, fn in self.NEW_FUNCS:
            src = (ROOT / fname).read_text(encoding="utf-8")
            body = re.search(r"\ndef " + fn + r"\([\s\S]*?\n(?=\n\ndef |\n\n\n)",
                             src).group(0)
            found = re.findall(r"\[-(?:\d+|[A-Z_]+)\s*:\s*\]", body)
            # الاستثناء الوحيد المصنّف سلفاً: شريحة عرض اليوميات (30 يوماً)
            self.assertTrue(all(f == "[-30:]" for f in found),
                            f"قصّ غير مصنّف في {fname}:{fn} — {found}")

    def test_radar_slice_filters_the_full_log_not_a_tail(self):
        """الشريحة تُشتق بالترشيح من السجل الكامل — لا من آخر N سجلاً."""
        src = (ROOT / "predict_v2.py").read_text(encoding="utf-8")
        self.assertIn('top_warns = [x for x in log["resolved"] if x.get("top")]',
                      src)
        self.assertIn(
            'top_alerts = [x for x in log["alerts_resolved"] if x.get("top")]',
            src)


if __name__ == "__main__":
    unittest.main()
