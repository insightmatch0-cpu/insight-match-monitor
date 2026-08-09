# -*- coding: utf-8 -*-
"""اختبارات بطاقة المباراة 360° — طلب المالك 2026-08-09.

كل بطاقة في مختبر الظل تحمل: النتيجة النهائية + عدّاد "كم من كم" لكل
طبقة (S1 توقعات الصباح، S2 التقرير، S3 الرادار) + تفصيل كل طبقة عند الفتح.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard_update as D


class TestShadowLab360(unittest.TestCase):

    def _build(self, scen, v1=None, v2=None, user=None, radar=None):
        files = {"SCENARIOS_V2_FILE": scen,
                 "PREDICTIONS_FILE": v1 or {},
                 "PREDICTIONS_V2_FILE": v2 or {},
                 "PREDICTIONS_USER_FILE": user or {},
                 "RADAR_LOG_FILE": radar or {}}
        for name, content in files.items():
            tmp = Path(tempfile.mkstemp(suffix=".json")[1])
            tmp.write_text(json.dumps(content, ensure_ascii=False),
                           encoding="utf-8")
            orig = getattr(D, name)
            setattr(D, name, tmp)
            self.addCleanup(lambda n=name, o=orig, t=tmp: (
                setattr(D, n, o), t.unlink(missing_ok=True)))
        return D.build_shadow_lab()

    def test_card_carries_score_s1_and_s3(self):
        lab = self._build(
            scen={"pending": {}, "resolved": [
                {"fid": "9", "home": "Estoril", "away": "Famalicao",
                 "date": "2026-08-07", "correct": 4, "total": 9,
                 "report": "x", "grades": []}]},
            v1={"resolved": [{"fid": "9", "pick": "away", "confidence": 55,
                              "correct": False, "score": "2-1",
                              "actual": "home"}]},
            v2={"resolved": [{"fid": "9", "pick": "home", "confidence": 60,
                              "correct": True, "score": "2-1",
                              "actual": "home"}]},
            user={"resolved": [{"fid": "9", "pick": "home", "confidence": 60,
                                "correct": True, "score": "2-1",
                                "actual": "home"}]},
            radar={"resolved": [{"fid": "9", "level": "red", "minute": 78,
                                 "failed": True}],
                   "alerts_resolved": [{"fid": "9", "key": "equalizer",
                                        "minute": 80, "hit": False,
                                        "silenced": True}]})
        r = lab["reports"][0]
        self.assertEqual(r["score"], "2-1")
        self.assertEqual(r["s1"]["v1"],
                         {"pick": "away", "confidence": 55, "correct": False})
        self.assertTrue(r["s1"]["v2"]["correct"])
        self.assertTrue(r["s1"]["user"]["correct"])
        self.assertEqual(r["s3"]["warnings"],
                         [{"level": "red", "minute": 78, "failed": True}])
        self.assertEqual(r["s3"]["alerts"][0]["key"], "equalizer")
        self.assertTrue(r["s3"]["alerts"][0]["silenced"])

    def test_match_without_layers_degrades_gracefully(self):
        """مباراة بلا توقعات صباح ولا رادار: البطاقة تعمل كما كانت."""
        lab = self._build(scen={"pending": {}, "resolved": [
            {"fid": "7", "home": "H", "away": "A", "date": "2026-08-07",
             "correct": 2, "total": 8, "report": "x", "grades": []}]})
        r = lab["reports"][0]
        self.assertEqual(r["score"], "")
        self.assertEqual(r["s1"], {})
        self.assertEqual(r["s3"], {"warnings": [], "alerts": []})


class TestPanelInDashboard(unittest.TestCase):

    def setUp(self):
        self.html = (Path(__file__).resolve().parent.parent
                     / "index.html").read_text(encoding="utf-8")

    def test_layer_chips_rendered_in_card_header(self):
        self.assertIn("function labLayerChips(", self.html)
        self.assertIn("labLayerChips(r)", self.html)
        self.assertIn("S1 '+k1+'/'+t1", self.html)
        self.assertIn("S3 '+k3", self.html)

    def test_breakdown_shows_all_three_layers(self):
        self.assertIn("labS1", self.html)
        self.assertIn("labS3", self.html)
        self.assertIn("labWarnRed", self.html)
        self.assertIn("labDramaAlert", self.html)

    def test_keys_exist_in_both_languages(self):
        for key in ("labScore", "labS1", "labS3", "labEngine1", "labEngine2",
                    "labYou", "labWarnRed", "labWarnAmber", "labDramaAlert",
                    "labSilencedTag"):
            self.assertEqual(self.html.count(key + ':"'), 2,
                             f"المفتاح {key} يجب أن يوجد بالعربية والإنجليزية")

    def test_build_bumped(self):
        import re
        m = re.search(r"IM_BUILD = (\d+)", self.html)
        self.assertGreaterEqual(int(m.group(1)), 69)
