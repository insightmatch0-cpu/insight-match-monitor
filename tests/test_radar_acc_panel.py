# -*- coding: utf-8 -*-
"""اختبارات لوحة دقة الرادار S3 — طلب المالك 2026-08-09.

سجل دقة كامل مرئي لطبقة S3 مثل سجل المحركين: مستويات الإنذار، كل نوع
ادعاء بحالته (تجريبي/مُثبَت/صامت) وعدّاد الـ 30، واتجاه يومي — تراكمي بلا حذف.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard_update as D


class TestBuildRadarAccuracy(unittest.TestCase):

    def _build(self, log_content):
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps(log_content, ensure_ascii=False),
                       encoding="utf-8")
        orig = D.RADAR_LOG_FILE
        D.RADAR_LOG_FILE = tmp
        self.addCleanup(lambda: (setattr(D, "RADAR_LOG_FILE", orig),
                                 tmp.unlink(missing_ok=True)))
        return D.build_radar_accuracy()

    def test_merges_stats_lists_and_daily_trend(self):
        acc = self._build({
            "meta": {"stats": {"red": {"fired": 10, "hit": 8},
                               "alerts": {"next_goal": {"fired": 7, "hit": 2}}}},
            "silenced": ["flip"], "proven": ["next_goal"],
            "resolved": [
                {"fid": "1", "graded_on": "2026-08-08", "failed": True},
                {"fid": "2", "graded_on": "2026-08-08", "failed": False},
                {"fid": "3", "graded_on": "2026-08-09", "failed": True},
            ]})
        self.assertEqual(acc["red"], {"fired": 10, "hit": 8})
        self.assertEqual(acc["silenced"], ["flip"])
        self.assertEqual(acc["proven"], ["next_goal"])
        self.assertEqual(acc["daily_warnings"]["2026-08-08"],
                         {"hit": 1, "total": 2})
        self.assertEqual(acc["daily_warnings"]["2026-08-09"],
                         {"hit": 1, "total": 1})

    def test_empty_log_gives_empty_but_valid_payload(self):
        acc = self._build({})
        self.assertEqual(acc["silenced"], [])
        self.assertEqual(acc["proven"], [])
        self.assertEqual(acc["daily_warnings"], {})

    def test_wired_into_data_json(self):
        import inspect
        self.assertIn("build_radar_accuracy()", inspect.getsource(D.main))


class TestPanelInDashboard(unittest.TestCase):

    def setUp(self):
        self.html = (Path(__file__).resolve().parent.parent
                     / "index.html").read_text(encoding="utf-8")

    def test_panel_renderer_exists_and_used(self):
        self.assertIn("function radarAccPanel(", self.html)
        self.assertIn("radarAccPanel(acc || {})", self.html)

    def test_claim_names_and_statuses_in_both_languages(self):
        for key in ("radarAccTitle", "radarClaim_next_goal", "radarClaim_flip",
                    "radarStProven", "radarStSilenced", "radarStTrial",
                    "radarTrendW"):
            self.assertEqual(self.html.count(key + ':"'), 2,
                             f"المفتاح {key} يجب أن يوجد بالعربية والإنجليزية")

    def test_thirty_verdict_counter_rendered(self):
        self.assertIn("/30", self.html)
        self.assertIn('Math.min(o.fired, 30)', self.html)

    def test_build_bumped_for_stale_screen_rule(self):
        """قاعدة الشاشة العالقة: كل PR يلمس index.html يرفع IM_BUILD."""
        import re
        m = re.search(r"IM_BUILD = (\d+)", self.html)
        self.assertIsNotNone(m)
        self.assertGreaterEqual(int(m.group(1)), 68)


class TestRecentWarningsFaces(unittest.TestCase):
    """«من هو من» (طلب المالك 2026-08-16): العدّاد بلا وجوهه لا يكفي للقرار —
    آخر الإنذارات المُقيَّمة تُصدَّر بأسمائها وتُعرض تحت اللوحة تتبع المفتاح."""

    def test_export_carries_recent_faces(self):
        import dashboard_update as D
        import json, tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkstemp(suffix=".json")[1])
        tmp.write_text(json.dumps({"resolved": [
            {"date": "2026-08-15", "home": "Sheffield Utd", "away": "Birmingham",
             "league": "Championship (England)", "level": "red", "minute": 90,
             "score": 100, "pick": "home", "confidence": 38,
             "final_score": "0-0", "failed": True, "top": True},
        ], "meta": {"stats": {}}}), encoding="utf-8")
        orig = D.RADAR_LOG_FILE
        D.RADAR_LOG_FILE = tmp
        try:
            out = D.build_radar_accuracy()
        finally:
            D.RADAR_LOG_FILE = orig
            tmp.unlink(missing_ok=True)
        rec = out.get("recent") or []
        self.assertEqual(len(rec), 1)
        r = rec[0]
        self.assertEqual(r["home"], "Sheffield Utd")
        self.assertTrue(r["hit"], "failed=True يعني الإنذار أصاب — التوقع سقط")
        self.assertTrue(r["top"], "علامة الدوري ترافق الوجه كي يتبع المفتاح")
        self.assertEqual(r["final"], "0-0")

    def test_panel_renders_and_filters_by_scope(self):
        """بنيوي: الواجهة تقرأ recent من التراكمي وتفلتر «دورياتي» بعلامة top."""
        src = (Path(__file__).resolve().parent.parent / "index.html").read_text(encoding="utf-8")
        self.assertIn("full.recent", src)
        self.assertIn('rec40.filter(function(w){ return w.top; })', src)
        self.assertIn("radarRecent", src)
