# -*- coding: utf-8 -*-
"""🔮 سطر «غداً» في نشرة المحرك 2 (HOLD-013-2، قرار المالك 2026-09-05):
حجم جدول الغد ودفعات Claude المتوقعة — الإنذار قبل السبت الضخم لا بعده."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import predict_v2 as P2

TOP_ID = next(iter(P2.TOP_LEAGUE_IDS))


def fx(fid, status="NS", league_id=9999, country="Spain", name="Liga X", home="A", away="B"):
    return {"fixture": {"id": fid, "status": {"short": status}, "date": "2026-09-06T18:00:00+00:00"},
            "league": {"id": league_id, "name": name, "country": country, "type": "League"},
            "teams": {"home": {"name": home}, "away": {"name": away}}}


class TestForecast(unittest.TestCase):
    def test_counts_only_eligible_and_computes_batches(self):
        fixtures = [fx(i) for i in range(1, 25)]                 # 24 خفيفة → 2 دفعات
        fixtures += [fx(100 + i, league_id=TOP_ID) for i in range(8)]  # 8 صدارة → 2 دفعات غنية
        fixtures += [fx(500, status="FT"), fx(501, home="Real W", away="Barca W"),
                     fx(502, league_id=1, country="India"), fx(1)]  # منتهية/سيدات/مستثناة/مكررة
        f = P2.forecast_tomorrow(fixtures)
        self.assertEqual(f["total"], 32)
        self.assertEqual(f["top"], 8)
        self.assertEqual(f["batches"], 2 + 2)

    def test_line_names_tomorrow_and_warns_on_peak(self):
        big = [fx(i) for i in range(1, 1201)]
        now = datetime(2026, 9, 5, 5, 0, tzinfo=timezone.utc)
        line = P2.tomorrow_forecast_line(fetch=lambda path: big, now=now)
        self.assertIn("2026-09-06", line)
        self.assertIn("1200 مباراة", line)
        self.assertIn("100 دفعة", line)
        self.assertIn("يوم ذروة", line)

    def test_quiet_day_has_no_warning_and_empty_day_is_silent(self):
        now = datetime(2026, 9, 5, 5, 0, tzinfo=timezone.utc)
        line = P2.tomorrow_forecast_line(fetch=lambda path: [fx(i) for i in range(1, 50)], now=now)
        self.assertIn("دفعة Claude", line)
        self.assertNotIn("ذروة", line)
        self.assertEqual(P2.tomorrow_forecast_line(fetch=lambda path: [], now=now), "")

    def test_failure_is_silent(self):
        def boom(path):
            raise RuntimeError("api down")
        self.assertEqual(P2.tomorrow_forecast_line(fetch=boom), "")

    def test_kill_switch(self):
        old = P2.DIGEST_TOMORROW_LINE
        try:
            P2.DIGEST_TOMORROW_LINE = False
            self.assertEqual(P2.tomorrow_forecast_line(fetch=lambda p: [fx(1)]), "")
        finally:
            P2.DIGEST_TOMORROW_LINE = old

    def test_wired_into_digest_before_send(self):
        src = (ROOT / "predict_v2.py").read_text(encoding="utf-8")
        body = src[src.index("def main("):]
        self.assertLess(body.index("tomorrow_forecast_line()"), body.index("send_telegram_long(digest)"))


if __name__ == "__main__":
    unittest.main()
