# -*- coding: utf-8 -*-
"""مسبار لمرة واحدة: هل يعيد API-Football حقل expected_goals في إحصائيات
المباريات المنتهية لدوريات المالك التسعة؟ يطبع نعم/لا لكل دوري — لا يكتب
أي ملف ولا يرسل أي رسالة. صفر نداءات Claude."""
import os, sys, requests

KEY = os.environ.get("API_FOOTBALL_KEY", "").strip()
if not KEY:
    print("لا مفتاح — خروج"); sys.exit(0)
H = {"x-apisports-key": KEY}
LEAGUES = {39: "EPL", 40: "Championship", 61: "Ligue 1", 78: "Bundesliga",
           135: "Serie A", 140: "La Liga", 307: "Saudi", 417: "Bahrain", 542: "Iraq"}
calls = 0
for lid, name in LEAGUES.items():
    r = requests.get("https://v3.football.api-sports.io/fixtures",
                     params={"league": lid, "season": 2026, "last": 2}, headers=H, timeout=30)
    calls += 1
    fx = (r.json().get("response") or [])
    if not fx:
        print(f"{name}: لا مباريات منتهية بعد"); continue
    for f in fx:
        fid = f["fixture"]["id"]
        s = requests.get("https://v3.football.api-sports.io/fixtures/statistics",
                         params={"fixture": fid}, headers=H, timeout=30)
        calls += 1
        rows = s.json().get("response") or []
        vals = []
        for side in rows:
            xg = [t.get("value") for t in side.get("statistics", []) if t.get("type") == "expected_goals"]
            vals.append(xg[0] if xg else "—")
        print(f"{name}: {f['teams']['home']['name']} × {f['teams']['away']['name']} "
              f"{f['goals']['home']}-{f['goals']['away']} → xG {vals}")
print(f"نداءات المسبار: {calls}")
