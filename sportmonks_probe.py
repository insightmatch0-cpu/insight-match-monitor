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
        # 1) الدوريات المشمولة بالاشتراك — نتأكد أن السعودي والإنجليزي معنا
        out["leagues"] = call("leagues", {"per_page": 50})
        # 2) بحث بالاسم للتثبت من المعرفات
        out["search_saudi"] = call("leagues/search/Saudi")
        out["search_premier"] = call("leagues/search/Premier League")
        # 3) مباريات الأمس واليوم — شكل المباراة الخام
        y = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        t = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        out["fixtures_yesterday"] = call(f"fixtures/date/{y}", {"per_page": 5})
        # 4) الأهم: هل يصلنا xG؟ (تسميات v3 المحتملة تُجرَّب كلها)
        for inc in ("xGFixture", "statistics", "statistics.type"):
            out[f"fixtures_today_inc_{inc}"] = call(
                f"fixtures/date/{t}", {"per_page": 3, "include": inc})
        # 5) عينة xG المتوقع (توقعات ما قبل المباراة إن كانت ضمن الباقة)
        out["expected_lineups_probe"] = call(
            f"fixtures/date/{t}", {"per_page": 2, "include": "metadata"})
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    print("المسبار كتب", OUT, "| المفتاح موجود:", bool(KEY))


if __name__ == "__main__":
    main()
