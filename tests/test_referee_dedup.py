# -*- coding: utf-8 -*-
"""اختبارات REC-001 (قرار المالك 2026-08-08): إصلاح ازدواج تسجيل الحكام.

الخطأ الأصلي: record_referee كان يُستدعى داخل actual_match_data قبل نجاح
التقييم، فيتكرر التسجيل مع كل إعادة محاولة (145 مباراة مسجلة مقابل 55 تقريراً
مُقيَّماً — عامل ~2.3؛ فينتشيتش بلغ 1.00 طرد لكل مباراة والمعدل الواقعي ≈0.2).
قاعدة SLA: ما شُفي لا يمرض مرة أخرى — التسجيل لا يتكرر لنفس المعرّف مهما
أعيد الاستدعاء، وactual_match_data لم يعد يسجّل بنفسه أبداً.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import predict_v2 as P


class RefereeDedupBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._orig_file = P.REFEREES_FILE
        P.REFEREES_FILE = Path(self.tmp.name) / "referees.json"

        def restore():
            P.REFEREES_FILE = self._orig_file
        self.addCleanup(restore)

    def db(self) -> dict:
        return json.loads(P.REFEREES_FILE.read_text(encoding="utf-8"))


class TestRecordRefereeDedup(RefereeDedupBase):
    def test_same_fid_counted_once_no_matter_how_many_calls(self):
        """جوهر REC-001: نفس المباراة لا تُحتسب مرتين مهما أعيد الاستدعاء."""
        for _ in range(5):
            P.record_referee("Slavko Vinčić, Slovenia", 6, 1, fid="12345")
        rec = self.db()["Slavko Vinčić, Slovenia"]
        self.assertEqual(rec["matches"], 1)
        self.assertEqual(rec["yellows"], 6)
        self.assertEqual(rec["reds"], 1)

    def test_different_fids_accumulate(self):
        P.record_referee("حكم", 3, 0, fid="1")
        P.record_referee("حكم", 5, 1, fid="2")
        rec = self.db()["حكم"]
        self.assertEqual(rec["matches"], 2)
        self.assertEqual(rec["yellows"], 8)
        self.assertEqual(rec["reds"], 1)

    def test_fids_stored_in_meta(self):
        P.record_referee("حكم", 1, 0, fid="77")
        self.assertIn("77", self.db()["_meta"]["fids"])

    def test_meta_key_does_not_shadow_referee_lookup(self):
        """قاعدة قديمة بلا _meta تعمل، والمفتاح الجديد لا يفسد بنية الحكام."""
        P.REFEREES_FILE.write_text(
            json.dumps({"حكم قديم": {"matches": 2, "yellows": 4, "reds": 0}}),
            encoding="utf-8",
        )
        P.record_referee("حكم قديم", 2, 1, fid="9")
        rec = self.db()["حكم قديم"]
        self.assertEqual(rec["matches"], 3)
        self.assertEqual(rec["yellows"], 6)

    def test_empty_name_records_nothing(self):
        P.record_referee("  ", 3, 1, fid="5")
        self.assertFalse(P.REFEREES_FILE.exists())

    def test_fids_capped(self):
        """قائمة المعرفات لا تنمو بلا حدود — تُقص عند REFEREE_FIDS_CAP."""
        cap = P.REFEREE_FIDS_CAP
        P.REFEREES_FILE.write_text(
            json.dumps({"_meta": {"fids": [str(i) for i in range(cap)]}}),
            encoding="utf-8",
        )
        P.record_referee("حكم", 1, 0, fid="جديد")
        fids = self.db()["_meta"]["fids"]
        self.assertEqual(len(fids), cap)
        self.assertIn("جديد", fids)


class TestActualMatchDataNoSideEffect(RefereeDedupBase):
    """actual_match_data لم يعد يسجّل الحكم — يرجع بياناته للمستدعي فقط."""

    def setUp(self):
        super().setUp()
        self._orig_api = P.api_football

        def restore():
            P.api_football = self._orig_api
        self.addCleanup(restore)

    def fake_api(self, path):
        if path.startswith("fixtures?ids="):
            return [{"fixture": {"status": {"short": "FT"},
                                 "referee": "حكم الاختبار"},
                     "goals": {"home": 2, "away": 1},
                     "score": {"fulltime": {"home": 2, "away": 1}}}]
        if path.startswith("fixtures/statistics"):
            return [{"team": {"name": "A"},
                     "statistics": [{"type": "Yellow Cards", "value": 3},
                                    {"type": "Red Cards", "value": 1}]},
                    {"team": {"name": "B"},
                     "statistics": [{"type": "Yellow Cards", "value": 2}]}]
        return []

    def test_returns_ref_info_without_writing_db(self):
        """الانحدار الأصلي: مجرد جلب البيانات كان يكتب في قاعدة الحكام."""
        P.api_football = self.fake_api
        actual, ref_info = P.actual_match_data("555")
        self.assertIn("2-1", actual)
        self.assertEqual(ref_info["referee"], "حكم الاختبار")
        self.assertEqual(ref_info["yellows"], 5)
        self.assertEqual(ref_info["reds"], 1)
        self.assertFalse(P.REFEREES_FILE.exists(),
                         "actual_match_data يجب ألا يكتب قاعدة الحكام أبداً")

    def test_repeated_fetch_then_single_record(self):
        """سيناريو إعادة المحاولة كاملاً: جلب متكرر + تسجيل بعد نجاح التقييم
        — النتيجة مباراة واحدة في القاعدة لا خمس."""
        P.api_football = self.fake_api
        for _ in range(5):
            actual, ref_info = P.actual_match_data("777")
        for _ in range(2):   # حتى لو تكرر التسجيل نفسه بنفس المعرف
            P.record_referee(ref_info["referee"], ref_info["yellows"],
                             ref_info["reds"], fid="777")
        self.assertEqual(self.db()["حكم الاختبار"]["matches"], 1)

    def test_unfinished_match_returns_empty(self):
        def api_live(path):
            if path.startswith("fixtures?ids="):
                return [{"fixture": {"status": {"short": "2H"}, "referee": "x"}}]
            return []
        P.api_football = api_live
        actual, ref_info = P.actual_match_data("888")
        self.assertEqual(actual, "")
        self.assertEqual(ref_info["referee"], "")


if __name__ == "__main__":
    unittest.main()
