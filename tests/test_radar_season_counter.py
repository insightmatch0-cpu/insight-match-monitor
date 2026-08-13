# -*- coding: utf-8 -*-
"""عدّاد موسم الرادار — أمر المالك 2026-08-13.

نفس مصطلح وأسلوب عدّاد الموسم للدقة (compute_stats): كتلة "الموسم" تُشتق
بالترشيح على التاريخ من نفس السجل الكامل — لا حذف ولا تصفير لأي سجل
(قاعدة لا-أسقف-قياس 2026-08-09).

الاختبار الحرج هنا هو (ج): قاعدة الإيقاف REC-005 تبقى مربوطة بالسجل
التراكمي. لو رُبطت بعدّاد الموسم لهبطت كل الأنواع تحت عتبة الـ 30 وعادت
الأنواع المكتومة ترسل تيليجرام من جديد — انحدار خطير يُمنع إلى الأبد.
"""

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import predict_v2 as P

BEFORE = "2026-08-01"    # قبل الموسم
EDGE_OUT = "2026-08-12"  # اليوم السابق لبداية الموسم — خارج العدّاد
EDGE_IN = "2026-08-13"   # يوم البداية نفسه — داخل العدّاد (>=)
AFTER = "2026-08-20"     # داخل الموسم


def _warn(date, level, failed):
    """إنذار مستوى مُقيَّم في سجل الرادار."""
    return {"fid": f"w{date}{level}{failed}", "level": level, "date": date,
            "failed": failed, "graded_on": date}


def _alert(key, date, hit, i=0):
    """تنبيه دراما مُقيَّم في سجل الرادار."""
    return {"fid": f"a{key}{date}{i}", "key": key, "date": date,
            "hit": hit, "graded_on": date}


def _graded(key, hits, total, date):
    """يبني total تنبيهاً مُقيَّماً لنوع key منها hits صحيحة، كلها بتاريخ date."""
    return [_alert(key, date, i < hits, i) for i in range(total)]


class _ResolveHarness(unittest.TestCase):
    """يشغّل التقييم الصباحي على ملف رادار مؤقت ويعيد الملف بعد الكتابة."""

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


class TestSeasonBlockMath(_ResolveHarness):
    """(أ) كتلة الموسم تُحسب صحيحاً من سجلات وهمية مختلطة التواريخ."""

    def _mixed_log(self):
        return {
            "resolved": [
                _warn(BEFORE, "red", True),      # قبل الموسم — خارج العدّاد
                _warn(EDGE_OUT, "red", True),    # 2026-08-12 — خارج العدّاد
                _warn(EDGE_IN, "red", True),     # يوم البداية — داخل
                _warn(AFTER, "red", False),      # داخل الموسم
                _warn(BEFORE, "amber", False),   # خارج
                _warn(AFTER, "amber", True),     # داخل
            ],
            "alerts_resolved": (
                _graded("goal", 1, 4, BEFORE)      # خارج الموسم
                + _graded("goal", 2, 3, AFTER)     # داخل الموسم
                + _graded("flip", 0, 5, EDGE_OUT)  # خارج (2026-08-12)
                + _graded("flip", 1, 1, EDGE_IN)   # داخل (يوم البداية)
            ),
        }

    def test_season_levels_filtered_by_date(self):
        stats = self._run_resolve(self._mixed_log())["meta"]["stats"]
        # أحمر: 4 تراكمياً (3 صادقة) — الموسم 2 فقط (1 صادق)
        self.assertEqual(stats["red"], {"fired": 4, "hit": 3})
        self.assertEqual(stats["season"]["red"], {"fired": 2, "hit": 1})
        # كهرماني: 2 تراكمياً (1 صادق) — الموسم 1 (1 صادق)
        self.assertEqual(stats["amber"], {"fired": 2, "hit": 1})
        self.assertEqual(stats["season"]["amber"], {"fired": 1, "hit": 1})

    def test_season_alerts_per_claim_filtered_by_date(self):
        stats = self._run_resolve(self._mixed_log())["meta"]["stats"]
        self.assertEqual(stats["alerts"]["goal"], {"fired": 7, "hit": 3})
        self.assertEqual(stats["season"]["alerts"]["goal"],
                         {"fired": 3, "hit": 2})
        self.assertEqual(stats["alerts"]["flip"], {"fired": 6, "hit": 1})
        self.assertEqual(stats["season"]["alerts"]["flip"],
                         {"fired": 1, "hit": 1})

    def test_season_start_stamped_like_compute_stats(self):
        """نفس ما تفعله compute_stats: الكتلة تحمل تاريخ بدايتها."""
        stats = self._run_resolve(self._mixed_log())["meta"]["stats"]
        self.assertEqual(stats["season"]["start"], P.SEASON_START)
        self.assertEqual(P.SEASON_START, "2026-08-13")

    def test_season_start_day_is_included(self):
        """الحد الأدنى شامل (>=): سجل يوم 2026-08-13 داخل عدّاد الموسم."""
        stats = self._run_resolve({
            "resolved": [_warn(EDGE_IN, "red", True)],
            "alerts_resolved": _graded("goal", 1, 1, EDGE_IN),
        })["meta"]["stats"]
        self.assertEqual(stats["season"]["red"], {"fired": 1, "hit": 1})
        self.assertEqual(stats["season"]["alerts"]["goal"],
                         {"fired": 1, "hit": 1})

    def test_all_pre_season_gives_zero_season_but_full_cumulative(self):
        """أول يوم موسم: العدّاد صفر والسجل التراكمي كامل — لا تصفير لشيء."""
        stats = self._run_resolve({
            "resolved": [_warn(BEFORE, "red", True)] * 1,
            "alerts_resolved": _graded("goal", 9, 20, BEFORE),
        })["meta"]["stats"]
        self.assertEqual(stats["season"]["red"], {"fired": 0, "hit": 0})
        self.assertEqual(stats["season"]["alerts"], {})
        self.assertEqual(stats["alerts"]["goal"], {"fired": 20, "hit": 9})

    def test_rows_without_date_stay_out_of_season(self):
        """سجل قديم بلا حقل تاريخ لا يدخل الموسم ولا يرمي استثناءً."""
        stats = self._run_resolve({
            "resolved": [{"fid": "old", "level": "red", "failed": True}],
            "alerts_resolved": [{"fid": "oa", "key": "goal", "hit": True}],
        })["meta"]["stats"]
        self.assertEqual(stats["red"], {"fired": 1, "hit": 1})
        self.assertEqual(stats["season"]["red"], {"fired": 0, "hit": 0})
        self.assertEqual(stats["season"]["alerts"], {})


class TestCumulativeUntouched(_ResolveHarness):
    """(ب) السجل التراكمي لا يتغير أبداً — العدّاد ترشيح لا حذف."""

    def test_no_row_dropped_from_either_log(self):
        log_in = {
            "resolved": [_warn(BEFORE, "red", True),
                         _warn(EDGE_OUT, "amber", False),
                         _warn(AFTER, "red", False)],
            "alerts_resolved": (_graded("goal", 3, 40, BEFORE)
                                + _graded("flip", 1, 2, AFTER)),
        }
        before_res = json.loads(json.dumps(log_in["resolved"]))
        before_alerts = json.loads(json.dumps(log_in["alerts_resolved"]))
        log = self._run_resolve(log_in)
        self.assertEqual(log["resolved"], before_res,
                         "سجل الإنذارات التراكمي تغيّر — ممنوع بأمر المالك")
        self.assertEqual(log["alerts_resolved"], before_alerts,
                         "سجل تنبيهات الدراما التراكمي تغيّر — ممنوع")

    def test_cumulative_stats_still_count_everything(self):
        """أرقام التراكمي تبقى على السجل الكامل لا على الموسم."""
        stats = self._run_resolve({
            "resolved": [_warn(BEFORE, "red", True)] * 1,
            "alerts_resolved": (_graded("goal", 3, 40, BEFORE)
                                + _graded("goal", 1, 2, AFTER)),
        })["meta"]["stats"]
        self.assertEqual(stats["alerts"]["goal"], {"fired": 42, "hit": 4})
        self.assertEqual(stats["season"]["alerts"]["goal"],
                         {"fired": 2, "hit": 1})


class TestStopRuleStaysCumulative(_ResolveHarness):
    """(ج) الاختبار الحرج: قاعدة الإيقاف تقرأ التراكمي لا الموسم."""

    def test_silenced_type_stays_silenced_with_clean_small_season(self):
        """نوع مكتوم بسجل تراكمي سيئ يبقى مكتوماً حتى لو كان سجل موسمه
        نظيفاً وصغيراً — لو قرأت القاعدة الموسم لخرج من الصمت وعاد يرسل."""
        log = self._run_resolve({
            "alerts_resolved": (_graded("goal", 5, 30, BEFORE)   # 16.6% تراكمي
                                + _graded("goal", 3, 3, AFTER)), # 100% موسمي
        })
        stats = log["meta"]["stats"]
        # التراكمي 8/33 ≈ 24% → تحت 40% → صامت
        self.assertEqual(stats["alerts"]["goal"], {"fired": 33, "hit": 8})
        # الموسم 3/3 = 100% وحجمه 3 فقط — لو حكم لخرج النوع من القائمتين
        self.assertEqual(stats["season"]["alerts"]["goal"],
                         {"fired": 3, "hit": 3})
        self.assertIn("goal", log["silenced"],
                      "قاعدة الإيقاف تسرّبت إلى عدّاد الموسم — انحدار خطير: "
                      "نوع مكتوم عاد يرسل تيليجرام")
        self.assertNotIn("goal", log["proven"])

    def test_proven_type_stays_proven_with_empty_season(self):
        """المرآة: نوع مُثبَت تراكمياً يبقى مُثبَتاً وعدّاد موسمه صفر."""
        log = self._run_resolve({
            "alerts_resolved": _graded("next_goal", 20, 30, BEFORE)})
        self.assertEqual(log["meta"]["stats"]["season"]["alerts"], {})
        self.assertIn("next_goal", log["proven"])

    def test_season_sized_record_does_not_trigger_a_verdict(self):
        """حجم الموسم وحده لا يصنع حكماً: 3 تنبيهات موسمية سيئة على نوع
        تراكميه تحت الـ 30 تبقى بلا أثر (عتبة الـ 30 تراكمية)."""
        log = self._run_resolve({
            "alerts_resolved": _graded("equalizer", 0, 3, AFTER)})
        self.assertNotIn("equalizer", log.get("silenced", []))
        self.assertNotIn("equalizer", log.get("proven", []))

    def test_stop_rule_loop_reads_astats_in_source(self):
        """حارس مصدري: الحلقة تدور على astats التراكمي لا على أي كتلة موسم."""
        import inspect
        src = inspect.getsource(P.resolve_radar_log)
        loop = re.search(r"for key in sorted\((\w+)\)", src)
        self.assertIsNotNone(loop, "حلقة قاعدة الإيقاف اختفت")
        self.assertEqual(loop.group(1), "astats",
                         "حلقة قاعدة الإيقاف رُبطت بغير السجل التراكمي")
        self.assertIn("لا تربط هذه الحلقة بعدّاد الموسم", src,
                      "تعليق التحذير العربي عند الحلقة غاب")


class TestDigestLineSeasonFirst(unittest.TestCase):
    """سطر النشرة الصباحية: أرقام الموسم أولاً والتراكمي بين قوسين."""

    def _line_for(self, log_content):
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps(log_content, ensure_ascii=False),
                       encoding="utf-8")
        orig = P.RADAR_LOG_FILE
        P.RADAR_LOG_FILE = tmp
        self.addCleanup(lambda: (setattr(P, "RADAR_LOG_FILE", orig),
                                 tmp.unlink(missing_ok=True)))
        return P.drama_scoreboard_line()

    def test_season_numbers_lead_and_cumulative_in_parens(self):
        line = self._line_for({"alerts_resolved":
                               _graded("goal", 1, 4, BEFORE)
                               + _graded("goal", 2, 3, AFTER)})
        self.assertIn("هدف المتأخر: 2/3 (منذ البداية: 3/7)", line)
        self.assertIn("الموسم 2/3 صحيحة (منذ البداية: 3/7)", line)

    def test_zero_season_says_counting_started_today(self):
        line = self._line_for({"alerts_resolved":
                               _graded("flip", 2, 9, BEFORE)})
        self.assertIn("قلب النتيجة: بدأ العدّ اليوم (منذ البداية: 2/9)", line)
        self.assertIn("الموسم بدأ العدّ اليوم", line)

    def test_arabic_claim_names_used(self):
        line = self._line_for({"alerts_resolved":
                               _graded("next_goal", 1, 2, AFTER)})
        self.assertIn(P.DRAMA_CLAIM_AR["next_goal"], line)

    def test_verdict_counter_stays_cumulative(self):
        """عدّاد الـ 30 في السطر يقرأ التراكمي: 7 تراكمياً رغم موسم من 3."""
        line = self._line_for({"alerts_resolved":
                               _graded("goal", 1, 4, BEFORE)
                               + _graded("goal", 2, 3, AFTER)})
        self.assertIn("7/30 نحو الحكم", line)


class TestNoNewMeasurementCaps(unittest.TestCase):
    """(د) لا قصّ [-N:] جديد على أي سجل في مسارات عدّاد الموسم."""

    #  السقف الوحيد المسموح: سقف الطوارئ المعطّل (قيمته 0) خلف شرط if
    ALLOWED = {"[-RADAR_RESOLVED_CAP:]"}
    _SLICE_RE = re.compile(r"\[-(?:\d+|[A-Z_]+)\s*:\s*\]")

    def test_season_paths_have_no_unclassified_slice(self):
        import inspect
        for fn in (P._radar_counts, P.resolve_radar_log,
                   P.drama_scoreboard_line):
            src = inspect.getsource(fn)
            found = {re.sub(r"\s", "", m.group(0))
                     for m in self._SLICE_RE.finditer(src)}
            self.assertEqual(found - self.ALLOWED, set(),
                             f"قصّ قياس جديد في {fn.__name__} — "
                             "قاعدة لا-أسقف-قياس (2026-08-09)")

    def test_emergency_cap_still_disabled(self):
        self.assertEqual(P.RADAR_RESOLVED_CAP, 0)

    def test_season_derived_by_filter_not_by_slicing(self):
        """العدّاد يُشتق بالترشيح على التاريخ — لا بقصّ ولا بتصفير."""
        import inspect
        src = inspect.getsource(P.resolve_radar_log)
        self.assertIn('>= SEASON_START', src)


class TestDashboardSeasonPanel(unittest.TestCase):
    """اللوحة: أرقام الموسم عنوان رئيسي والتراكمي سطر ثانوي أصغر."""

    def setUp(self):
        self.html = (Path(__file__).resolve().parent.parent
                     / "index.html").read_text(encoding="utf-8")

    def test_panel_reads_season_block(self):
        self.assertIn("var season = acc.season || {}", self.html)
        self.assertIn("season.alerts", self.html)

    def test_season_keys_in_both_languages(self):
        for key in ("radarSeasonLbl", "radarFullRec", "radarCountToday"):
            self.assertEqual(self.html.count(key + ':"'), 2,
                             f"المفتاح {key} يجب أن يوجد بالعربية والإنجليزية")

    def test_cumulative_rendered_as_secondary_line(self):
        self.assertIn('class="r-sub"', self.html)
        self.assertIn('class="b-sub"', self.html)
        self.assertIn("السجل الكامل: {v}", self.html)

    def test_verdict_chip_still_reads_cumulative_fired(self):
        """رقاقة الحالة تقرأ التراكمي (o.fired) لا الموسم (s.fired)."""
        self.assertIn('Math.min(o.fired, 30)', self.html)
        self.assertNotIn('Math.min(s.fired, 30)', self.html)

    def test_build_bumped_for_stale_screen_rule(self):
        """قاعدة الشاشة العالقة: كل PR يلمس index.html يرفع IM_BUILD."""
        m = re.search(r"IM_BUILD = (\d+)", self.html)
        self.assertIsNotNone(m)
        self.assertGreaterEqual(int(m.group(1)), 71)


class TestDashboardPassesSeasonThrough(unittest.TestCase):
    """dashboard_update: كتلة الموسم تصل data.json كما بناها التقييم."""

    def test_build_radar_accuracy_keeps_season(self):
        import dashboard_update as D
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps({"meta": {"stats": {
            "red": {"fired": 9, "hit": 4},
            "season": {"red": {"fired": 2, "hit": 1}, "alerts": {},
                       "start": "2026-08-13"}}}}, ensure_ascii=False),
            encoding="utf-8")
        orig = D.RADAR_LOG_FILE
        D.RADAR_LOG_FILE = tmp
        self.addCleanup(lambda: (setattr(D, "RADAR_LOG_FILE", orig),
                                 tmp.unlink(missing_ok=True)))
        acc = D.build_radar_accuracy()
        self.assertEqual(acc["season"]["red"], {"fired": 2, "hit": 1})
        self.assertEqual(acc["season"]["start"], "2026-08-13")
        self.assertEqual(acc["red"], {"fired": 9, "hit": 4})


if __name__ == "__main__":
    unittest.main()
