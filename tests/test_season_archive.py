# -*- coding: utf-8 -*-
"""اختبارات أرشيف الموسم الكامل — أمر المالك 2026-08-09.

حادثة "أرقام 70%+ تتغير يومياً": سقف الـ 1000 حوّل اللوحة إلى نافذة متحركة
~7 أيام في حجم الموسم. القرار: لا حذف أبداً، وعدّاد الموسم يبدأ من صفر
في 2026-08-13 — هذه الاختبارات تمنع عودة النافذة الصامتة إلى الأبد.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import predict_v2 as P


def _row(date, conf, correct, top=False):
    return {"date": date, "confidence": conf, "correct": correct, "top": top,
            "fid": f"{date}-{conf}-{correct}", "pick": "home",
            "actual": "home" if correct else "away"}


class TestNoDeletion(unittest.TestCase):
    """لا يُحذف سجل مُقيَّم أبداً — إلا إن أعاد المالك سقفاً للطوارئ."""

    def _resolve_with(self, resolved_count):
        store = {"pending": {"x": {"date": "2026-08-01"}},
                 "resolved": [_row("2026-08-01", 50, True)] * resolved_count}
        orig = P.api_football
        P.api_football = lambda q: []
        self.addCleanup(lambda: setattr(P, "api_football", orig))
        P.resolve_pending(store)
        return store

    def test_resolved_never_truncated_by_default(self):
        store = self._resolve_with(1200)
        self.assertEqual(len(store["resolved"]), 1200,
                         "سقف الـ 1000 القديم عاد — ممنوع بأمر المالك 2026-08-09")

    def test_emergency_cap_switch_still_works(self):
        old = P.RESOLVED_CAP
        try:
            P.RESOLVED_CAP = 1000
            store = self._resolve_with(1200)
            self.assertEqual(len(store["resolved"]), 1000)
        finally:
            P.RESOLVED_CAP = old

    def test_default_cap_is_disabled(self):
        self.assertEqual(P.RESOLVED_CAP, 0)


class TestSeasonCounters(unittest.TestCase):
    """عدّادات الموسم تبدأ من صفر في 2026-08-13 وتتجاهل ما قبله."""

    def test_season_counts_only_from_season_start(self):
        rows = [_row("2026-08-01", 72, True),      # قبل الموسم — خارج العدّاد
                _row("2026-08-12", 55, False),     # قبل الموسم بيوم
                _row("2026-08-13", 72, True, top=True),
                _row("2026-08-14", 45, False)]
        s = P.compute_stats(rows)
        self.assertEqual(s["season"]["overall"], {"correct": 1, "total": 2})
        self.assertEqual(s["season"]["by_confidence"]["70+"],
                         {"correct": 1, "total": 1})
        self.assertEqual(s["season"]["start"], "2026-08-13")
        # السجل الكامل ما زال يشمل الجميع — محفوظ لا محذوف
        self.assertEqual(s["overall"]["total"], 4)

    def test_season_is_all_zeros_before_kickoff(self):
        s = P.compute_stats([_row("2026-08-05", 72, True)])
        self.assertEqual(s["season"]["overall"], {"correct": 0, "total": 0})
        self.assertEqual(s["season"]["by_confidence"]["70+"],
                         {"correct": 0, "total": 0})

    def test_existing_keys_unchanged_for_dashboard(self):
        """المفاتيح القديمة تبقى — كسرها يكسر اللوحة والنشرة معاً."""
        s = P.compute_stats([_row("2026-08-05", 65, True, top=True)])
        for k in ("overall", "last30", "top_leagues", "other_leagues",
                  "by_confidence", "daily", "season"):
            self.assertIn(k, s)


class TestHistoryBuckets(unittest.TestCase):
    """الأرشيف الدائم يحفظ تفصيل شرائح الثقة يوماً-بيوم — سجل الخانة الذهبية
    الكامل لا يعود أسير نافذة الذاكرة أبداً."""

    def _run_update(self, rows, existing=None):
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps(existing or {"days": {}}), encoding="utf-8")
        orig = P.HISTORY_FILE
        P.HISTORY_FILE = tmp
        self.addCleanup(lambda: (setattr(P, "HISTORY_FILE", orig),
                                 tmp.unlink(missing_ok=True)))
        stats = P.compute_stats(rows)
        P.update_history(stats, None, rows)
        return json.loads(tmp.read_text(encoding="utf-8"))

    def test_daily_bucket_detail_recorded(self):
        rows = [_row("2026-08-13", 72, True), _row("2026-08-13", 72, False),
                _row("2026-08-13", 45, True)]
        hist = self._run_update(rows)
        b = hist["days"]["2026-08-13"]["v2"]["buckets"]
        self.assertEqual(b["70+"], {"correct": 1, "total": 2})
        self.assertEqual(b["<50"], {"correct": 1, "total": 1})

    def test_rerun_is_idempotent(self):
        rows = [_row("2026-08-13", 72, True)]
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps({"days": {}}), encoding="utf-8")
        orig = P.HISTORY_FILE
        P.HISTORY_FILE = tmp
        self.addCleanup(lambda: (setattr(P, "HISTORY_FILE", orig),
                                 tmp.unlink(missing_ok=True)))
        stats = P.compute_stats(rows)
        P.update_history(stats, None, rows)
        P.update_history(stats, None, rows)   # تشغيل مكرر — نفس النتيجة
        hist = json.loads(tmp.read_text(encoding="utf-8"))
        self.assertEqual(hist["days"]["2026-08-13"]["v2"]["buckets"]["70+"],
                         {"correct": 1, "total": 1})


class TestDashboardWiring(unittest.TestCase):
    """اللوحة تعرض عدّاد الموسم وتعنون نطاق كل رقم — لا نافذة صامتة بعد اليوم."""

    def setUp(self):
        self.html = (Path(__file__).resolve().parent.parent
                     / "index.html").read_text(encoding="utf-8")

    def test_index_uses_season_block(self):
        self.assertIn("acc.season", self.html)
        self.assertIn("accSeason", self.html)

    def test_scope_labels_exist_in_both_languages(self):
        self.assertIn("accScopeSeason", self.html)
        self.assertIn("accScopeAll", self.html)
        self.assertIn("لا يُحذف أي سجل", self.html)
        self.assertIn("nothing is ever deleted", self.html)
