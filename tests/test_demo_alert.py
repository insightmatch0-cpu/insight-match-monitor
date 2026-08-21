# -*- coding: utf-8 -*-
"""حارس الرسائل التوضيحية لقنوات الرادار (طلب المالك 2026-08-21).

الغرض: أن تبقى الأمثلة **محسوبة من السجل** لا مكتوبة يدوياً، وأن يبقى وسم
«مثال توضيحي» على كل رسالة حتى لا تُخلط أبداً بتنبيه حقيقي.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import demo_alert


def _row(fid, factors, failed, minute=85):
    return {"fid": fid, "home": "A", "away": "B", "pick": "home",
            "confidence": 50, "level": "red", "score": 66, "minute": minute,
            "alert_minute": minute, "factors": factors, "alerted": True,
            "failed": failed, "final_score": "1-2"}


class TestSlices(unittest.TestCase):
    def test_board_and_momentum_split_the_sent_slice(self):
        rows = [_row("1", ["لوحة"], True), _row("2", ["لوحة", "زخم"], False),
                dict(_row("3", ["لوحة"], True), alerted=None)]
        sent, board, momentum = demo_alert.slices(rows)
        self.assertEqual(len(sent), 2)          # الصف غير المُرسل خارج الشريحة
        self.assertEqual(len(board), 1)
        self.assertEqual(len(momentum), 1)
        self.assertEqual(len(board) + len(momentum), len(sent))

    def test_percent_never_divides_by_zero(self):
        self.assertEqual(demo_alert._pct(0, 0), "—")
        self.assertEqual(demo_alert._pct(3, 4), "75%")


class TestMessages(unittest.TestCase):
    def test_numbers_come_from_the_log_not_hardcoded(self):
        rows = [_row(str(i), ["لوحة"], True) for i in range(4)]
        msg = demo_alert.build_messages(rows)[0]
        self.assertIn("4 مُقيَّماً", msg)
        self.assertIn("100%", msg)

    def test_every_message_carries_the_example_tag(self):
        rows = [_row("1", ["لوحة"], True), _row("2", ["لوحة", "زخم"], False)]
        for msg in demo_alert.build_messages(rows):
            self.assertTrue(msg.startswith(demo_alert.TAG), msg[:40])

    def test_empty_log_still_produces_messages_without_crashing(self):
        msgs = demo_alert.build_messages([])
        self.assertEqual(len(msgs), 3)
        self.assertIn("لا مثال مُقيَّم", msgs[1])

    def test_verdict_wording_follows_the_grading_rule(self):
        hit = demo_alert.example_line([_row("1", ["لوحة"], True)])
        miss = demo_alert.example_line([_row("2", ["لوحة"], False)])
        self.assertIn("صحيحاً", hit)
        self.assertIn("خطأ", miss)


class TestNoApiCalls(unittest.TestCase):
    def test_script_never_touches_api_football_or_claude(self):
        src = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "demo_alert.py"), encoding="utf-8").read()
        for banned in ("api-sports.io", "anthropic", "API_FOOTBALL_KEY"):
            self.assertNotIn(banned, src)


if __name__ == "__main__":
    unittest.main()


class TestDeliveryEvidence(unittest.TestCase):
    """التشغيلة يجب أن تترك دليلاً مطبوعاً على التسليم، لا صمتاً أخضر."""

    def test_main_prints_a_receipt_per_message(self):
        src = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "demo_alert.py"), encoding="utf-8").read()
        self.assertIn("res['delivered']", src)
        self.assertIn("res['total']", src)
        self.assertIn("print(", src.split("def main(")[1])
