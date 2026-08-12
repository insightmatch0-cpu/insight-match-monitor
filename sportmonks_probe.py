# -*- coding: utf-8 -*-
"""🔬 مسبار Sportmonks — استكشاف لمرة واحدة قبل بناء مجمّع ظل xG (خطة 2026-07-31).

قاعدة المالك المسجلة: "لا كود تخميني قبل وجود المفتاح" — هذا المسبار يعمل داخل
Actions (حيث المفتاح في Secrets)، يستدعي نقاط النهاية الفعلية، ويكتب عينات
البنية الحقيقية إلى sportmonks_probe.json على فرع المسبار فقط (لا يقترب من main).
المجمّع الحقيقي يُبنى بعد قراءة هذا الملف.

انضباط الأسرار (القاعدة 3): المفتاح يُقرأ من البيئة ويُرسل في ترويسة HTTP فقط —
لا يظهر أبداً في روابط ولا مخرجات ولا رسائل أخطاء.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import requests

KEY = os.environ.get("SPORTMONKS_KEY", "").strip()
BASE = "https://api.sportmonks.com/v3/football"
OUT = "sportmonks_probe.json"
TRUNCATE = 3   # أول 3 عناصر من كل رد تكفي لفهم البنية


def call(path: str, params: dict = None) -> dict:
    """نداء واحد بترويسة Authorization — يرجع الحالة وعينة مقتضبة من الرد."""
    try:
        r = requests.get(f"{BASE}/{path}", params=params or {},
                         headers={"Authorization": KEY}, timeout=25)
        body = {}
        try:
            body = r.json()
        except ValueError:
            return {"status": r.status_code, "note": "رد غير JSON",
                    "text_head": r.text[:200]}
        data = body.get("data")
        if isinstance(data, list):
            sample = data[:TRUNCATE]
            count = len(data)
        else:
            sample = data
            count = 1 if data else 0
        return {"status": r.status_code, "count_in_page": count,
                "sample": sample,
                "pagination": body.get("pagination"),
                "subscription": body.get("subscription"),
                "message": body.get("message")}
    except Exception as e:
        # لا نطبع نص الاستثناء الخام خشية تسرب أي شيء — نوعه يكفي للتشخيص
        return {"status": "exception", "type": type(e).__name__}


def main():
    out = {"probed_at": datetime.now(timezone.utc).isoformat(),
           "key_present": bool(KEY)}
    if not KEY:
        out["error"] = "SPORTMONKS_KEY غير موجود في البيئة — أضفه في Secrets"
    else:
        # 1) كل الدوريات المشمولة بالاشتراك (قائمة خفيفة عبر الصفحات) —
        #    السؤال الحاسم: أين دوري روشن السعودي؟
        out["all_league_names"] = call_all_league_names()
        # 2) بحث مباشر عن دوري روشن بأسمائه المحتملة
        out["search_pro_league"] = call("leagues/search/Pro League")
        out["search_roshn"] = call("leagues/search/Roshn")
        # 3) مباراة منتهية حديثاً مع xG — آخر يومين
        for back in (1, 2):
            day = (datetime.now(timezone.utc) - timedelta(days=back)).strftime("%Y-%m-%d")
            out[f"fixtures_{day}_xg"] = call(
                f"fixtures/date/{day}", {"per_page": 5, "include": "xgfixture"})
        # 4) عينة تحقق تاريخية: آخر جولة من الدوري الإنجليزي الماضي (24 مايو 2026)
        #    مع xG — هذه مرجع مقارنة Opta المطلوب في الخطة
        out["epl_last_round_xg"] = call(
            "fixtures/date/2026-05-24",
            {"per_page": 10, "include": "xgfixture",
             "filters": "fixtureLeagues:8"})
        # 5) شكل إحصائيات مباراة منتهية (للطبقة الحية لاحقاً — تسجيل HT/FT فقط)
        yday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        out["finished_statistics"] = call(
            f"fixtures/date/{yday}", {"per_page": 2, "include": "statistics"})


def call_all_league_names() -> list:
    """كل (id، اسم) للدوريات المشمولة — قائمة خفيفة عبر الصفحات (سقف 4)."""
    rows = []
    for page in range(1, 5):
        try:
            r = requests.get(f"{BASE}/leagues",
                             params={"per_page": 50, "page": page},
                             headers={"Authorization": KEY}, timeout=25)
            body = r.json()
        except Exception as e:
            rows.append({"page": page, "error": type(e).__name__})
            break
        for l in (body.get("data") or []):
            rows.append({"id": l.get("id"), "name": l.get("name"),
                         "country_id": l.get("country_id")})
        if not (body.get("pagination") or {}).get("has_more"):
            break
    return rows
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    print("المسبار كتب", OUT, "| المفتاح موجود:", bool(KEY))


if __name__ == "__main__":
    main()
