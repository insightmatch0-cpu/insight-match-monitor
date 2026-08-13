# -*- coding: utf-8 -*-
"""🔬 مجمّع ظل xG من Sportmonks — المرحلة أ (خطة المالك 2026-07-31، انطلقت 2026-08-12).

عقيدة الظل أولاً (بند 7 من عقيدة استكشاف البيانات): يجمع ويقيس **بصفر تأثير على
أي محرك** — يقرأ المباريات المُقيَّمة من ذاكرة المحرك 2، يجلب xG الحقيقي لكل
مباراة من Sportmonks، ويسجّل ماذا كانت سترجّح "فورمة xG" (متوسط فارق xG الأخير
لكل فريق) مقابل ما اختاره المحركان وما حدث فعلاً — كله في
sportmonks_shadow.json (سجل قياس — عقيدة لا-أسقف-قياس: لا يُقص أبداً).

فشله صامت بالتصميم: لا يوقف تشغيلة ولا يمس توقعاً — أقصى ما يفعله عند أي عطل
هو سطر في السجل. الحكم بعد ≥3 أسابيع (تقرير مرحلي 24 أغسطس، الحكم 1-3 سبتمبر).
انضباط الأسرار (القاعدة 3): المفتاح من البيئة، في ترويسة HTTP فقط، لا يُطبع أبداً.

بُني على بنية API المؤكدة بالمسبار (فرع sportmonks-probe، 2026-08-12):
xG يصل في include=xgfixture كقائمة {type_id: 5304, location, data.value}.
"""

import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# مفتاح التراجع (خطة المرحلة أ): False = تعطيل الجامع فوراً بلا حذف أي كود —
# مع غياب SPORTMONKS_KEY من البيئة يشكّلان طريقَي التعطيل الطبيعيَّين
XG_SHADOW = True

KEY = os.environ.get("SPORTMONKS_KEY", "").strip()
BASE = "https://api.sportmonks.com/v3/football"
SHADOW_FILE = Path("sportmonks_shadow.json")
V2_FILE = Path("predictions_v2.json")
V1_FILE = Path("predictions.json")

XG_TYPE_ID = 5304        # معرف نوع xG للمباراة (مؤكد من المسبار)
FORM_WINDOW = 5          # نافذة حساب الفورمة: آخر 5 مباريات موثقة للفريق
FORM_MIN_MATCHES = 3     # لا ترجيح قبل 3 مباريات موثقة لكلا الطرفين
FORM_EDGE = 0.30         # فارق متوسطين أدنى للترجيح — دونه "تعادل"
MAX_PAGES_PER_DAY = 4    # سقف صفحات الجلب لليوم الواحد (50 مباراة/صفحة)
LOOKBACK_DAYS = 2        # نغطي آخر يومين — نفس أفق تقييم المحركات الصباحي

# كلمات تُسقط من أسماء الفرق قبل المطابقة (لواحق شكلية لا تميّز)
_STOP_TOKENS = {"fc", "cf", "sc", "afc", "ac", "cd", "club", "de", "ssc",
                "if", "bk", "sk", "ii", "b", "the"}

# عينة التحقق مقابل Opta (خطة القاعدة 8: «تحقق أولاً من xG سبورتمونكس مقابل
# أرقام Opta العلنية على مباريات إنجليزية») — مباريات شهيرة من موسم 2022-23
# وأرقامها من FBref (مزوّده Opta منذ 2022)، منقولة من أرشيف worldfootballR_data
# العلني وقابلة للتحقق يدوياً على fbref.com. 3 تواريخ فقط = 3 نداءات جلب.
OPTA_SAMPLE = [
    {"date": "2022-08-06", "home": "Fulham", "away": "Liverpool",
     "opta_home": 1.2, "opta_away": 1.2},
    {"date": "2022-08-06", "home": "Newcastle United", "away": "Nottingham Forest",
     "opta_home": 1.7, "opta_away": 0.3},
    {"date": "2022-08-13", "home": "Brentford", "away": "Manchester United",
     "opta_home": 1.6, "opta_away": 0.9},
    {"date": "2022-08-13", "home": "Manchester City", "away": "Bournemouth",
     "opta_home": 1.7, "opta_away": 0.1},
    {"date": "2022-08-13", "home": "Arsenal", "away": "Leicester City",
     "opta_home": 2.7, "opta_away": 0.5},
    {"date": "2022-08-27", "home": "Liverpool", "away": "Bournemouth",
     "opta_home": 3.3, "opta_away": 0.3},
    {"date": "2022-08-27", "home": "Manchester City", "away": "Crystal Palace",
     "opta_home": 2.2, "opta_away": 0.1},
    {"date": "2022-08-27", "home": "Arsenal", "away": "Fulham",
     "opta_home": 2.6, "opta_away": 0.8},
]
VALIDATE_TOLERANCE = 0.35   # متوسط فرق مطلق مقبول بين مزودَي xG (نماذج مختلفة)


def _norm_tokens(name: str) -> frozenset:
    """اسم فريق → مجموعة كلمات مطبَّعة (بلا تشكيل لاتيني ولا لواحق شكلية)."""
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return frozenset(t for t in s.split() if t and t not in _STOP_TOKENS)


def names_match(a: str, b: str) -> bool:
    """مطابقة محافظة بين اسمي فريق من مزوّدين مختلفين."""
    ta, tb = _norm_tokens(a), _norm_tokens(b)
    if not ta or not tb:
        return False
    if ta == tb or ta <= tb or tb <= ta:
        return True
    inter = len(ta & tb)
    return inter / len(ta | tb) >= 0.5


def _outcome(score: str) -> str:
    """"2-1" → home/draw/away — نفس عرف التقييم في المحركين."""
    try:
        gh, ga = (int(x) for x in (score or "").split("-")[:2])
    except ValueError:
        return ""
    return "home" if gh > ga else ("away" if ga > gh else "draw")


def _team_form(hist: list) -> float:
    """متوسط فارق xG (له - عليه) على نافذة آخر المباريات الموثقة."""
    window = hist[-FORM_WINDOW:]
    return sum(h["xf"] - h["xa"] for h in window) / len(window)


def xgform_pick(h_hist: list, a_hist: list):
    """ماذا كانت سترجّح فورمة xG؟ None = عينة غير كافية (لا ادعاء بلا بيانات)."""
    if len(h_hist) < FORM_MIN_MATCHES or len(a_hist) < FORM_MIN_MATCHES:
        return None
    gap = _team_form(h_hist) - _team_form(a_hist)
    if gap > FORM_EDGE:
        return "home"
    if gap < -FORM_EDGE:
        return "away"
    return "draw"


def _api(path: str, params: dict):
    """نداء واحد — أي فشل يرجع None (الصمت مبدأ هنا، السجل يذكره فقط)."""
    try:
        r = requests.get(f"{BASE}/{path}", params=params,
                         headers={"Authorization": KEY}, timeout=25)
        if r.status_code != 200:
            print("🔬 ظل xG: حالة غير متوقعة", r.status_code, "على", path)
            return None
        return r.json()
    except Exception as e:
        print("🔬 ظل xG: نداء فشل صامتاً:", type(e).__name__)
        return None


def fetch_day_xg(day: str) -> list:
    """كل مباريات اليوم المذكور التي تحمل xG → [{home, away, xg_home, xg_away}]."""
    rows = []
    for page in range(1, MAX_PAGES_PER_DAY + 1):
        body = _api(f"fixtures/date/{day}",
                    {"per_page": 50, "page": page, "include": "xgfixture"})
        if body is None:
            break
        for fx in (body.get("data") or []):
            name = fx.get("name") or ""
            if " vs " not in name:
                continue
            home, away = name.split(" vs ", 1)
            xh = xa = None
            for x in (fx.get("xgfixture") or []):
                if x.get("type_id") != XG_TYPE_ID:
                    continue
                v = (x.get("data") or {}).get("value")
                if x.get("location") == "home":
                    xh = v
                elif x.get("location") == "away":
                    xa = v
            if xh is not None and xa is not None:
                rows.append({"home": home.strip(), "away": away.strip(),
                             "xg_home": float(xh), "xg_away": float(xa)})
        if not (body.get("pagination") or {}).get("has_more"):
            break
    return rows


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _team_key(name: str) -> str:
    return " ".join(sorted(_norm_tokens(name))) or (name or "").lower()


def _save(shadow: dict) -> None:
    """الكتابة الوحيدة في المجمّع كله — ملف الظل فقط، لا ذاكرة أي محرك."""
    SHADOW_FILE.write_text(
        json.dumps(shadow, ensure_ascii=False, indent=1), encoding="utf-8")


def validate(persist: bool = True) -> dict:
    """وضع التحقق مقابل Opta (--validate أو أول تشغيلة): يقارن xG سبورتمونكس
    بأرقام FBref/Opta العلنية لعينة OPTA_SAMPLE ويطبع جدول الفروق. الخلاصة
    تُحفظ في meta.opta_validation ليقرأها تقرير 24 أغسطس المرحلي. صامت
    المبدأ: لا يرفع استثناءً أبداً — «لا تغطية» حالة تُسجَّل، لا خطأ."""
    if not KEY:
        print("🔬 ظل xG: لا مفتاح في البيئة — تخطي التحقق (التشغيلة سليمة)")
        return {}
    print("🔬 تحقق xG مقابل Opta — عينة إنجليزية 2022-23 (المصدر: FBref):")
    print("التاريخ | المباراة | سبورتمونكس | Opta | الفرق")
    rows, no_coverage = [], 0
    for day in sorted({s["date"] for s in OPTA_SAMPLE}):
        day_rows = fetch_day_xg(day)
        for s in (x for x in OPTA_SAMPLE if x["date"] == day):
            label = f"{s['home']} – {s['away']}"
            sm = next((r for r in day_rows
                       if names_match(s["home"], r["home"])
                       and names_match(s["away"], r["away"])), None)
            if sm is None:
                no_coverage += 1
                print(f"{s['date']} | {label} | — | "
                      f"{s['opta_home']}-{s['opta_away']} | لا تغطية")
                continue
            dh = abs(sm["xg_home"] - s["opta_home"])
            da = abs(sm["xg_away"] - s["opta_away"])
            rows.append({"match": label, "date": s["date"],
                         "sm": [sm["xg_home"], sm["xg_away"]],
                         "opta": [s["opta_home"], s["opta_away"]],
                         "diff": round((dh + da) / 2, 2)})
            print(f"{s['date']} | {label} | "
                  f"{sm['xg_home']}-{sm['xg_away']} | "
                  f"{s['opta_home']}-{s['opta_away']} | ±{(dh + da) / 2:.2f}")
    mean_diff = (round(sum(r["diff"] for r in rows) / len(rows), 3)
                 if rows else None)
    summary = {
        "checked_on": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "n": len(rows), "no_coverage": no_coverage,
        "mean_abs_diff": mean_diff,
        # الحكم بصدق: لا عينة = لا حكم (باقة التجربة قد لا تشمل مواسم قديمة)
        "verdict": ("لا تغطية تاريخية في الباقة" if not rows else
                    "توافق جيد" if mean_diff <= VALIDATE_TOLERANCE else
                    "فروق ملحوظة — راجع قبل الوثوق"),
    }
    print(f"🔬 خلاصة التحقق: عينة {summary['n']}, بلا تغطية {no_coverage}, "
          f"متوسط الفرق {mean_diff} → {summary['verdict']}")
    if persist:
        shadow = load_json(SHADOW_FILE, {}) or {}
        shadow.setdefault("meta", {})["opta_validation"] = summary
        _save(shadow)
    return summary


def main() -> None:
    if not XG_SHADOW:
        print("🔬 ظل xG: المفتاح مطفأ (XG_SHADOW=False) — تخطٍ صامت")
        return
    if not KEY:
        print("🔬 ظل xG: لا مفتاح في البيئة — تخطٍ صامت (التشغيلة سليمة)")
        return
    shadow = load_json(SHADOW_FILE, {}) or {}
    shadow.setdefault("fixtures", [])          # سجل قياس — لا يُقص أبداً
    shadow.setdefault("teams", {})             # تاريخ xG لكل فريق — لا يُقص
    meta = shadow.setdefault("meta", {})
    meta.setdefault("started", datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    v2 = load_json(V2_FILE, {}) or {}
    v1_by_fid = {str(r.get("fid")): r for r in
                 (load_json(V1_FILE, {}) or {}).get("resolved") or []}
    seen = {str(f.get("fid")) for f in shadow["fixtures"]}
    today = datetime.now(timezone.utc)
    dates = [(today - timedelta(days=b)).strftime("%Y-%m-%d")
             for b in range(1, LOOKBACK_DAYS + 1)]
    rows = [r for r in (v2.get("resolved") or [])
            if r.get("date") in dates and str(r.get("fid")) not in seen
            and r.get("score")]
    matched = unmatched = 0
    if rows:
        day_xg = {d: fetch_day_xg(d) for d in sorted({r["date"] for r in rows})}
        for r in rows:
            sm = next((s for s in day_xg.get(r["date"], [])
                       if names_match(r.get("home"), s["home"])
                       and names_match(r.get("away"), s["away"])), None)
            if sm is None:
                unmatched += 1
                continue
            matched += 1
            hk, ak = _team_key(r.get("home")), _team_key(r.get("away"))
            h_hist = shadow["teams"].setdefault(hk, [])
            a_hist = shadow["teams"].setdefault(ak, [])
            # الترجيح يُحسب من التاريخ **قبل** هذه المباراة — لا تسريب مستقبل
            pick = xgform_pick(h_hist, a_hist)
            result = _outcome(r.get("score"))
            shadow["fixtures"].append({
                "fid": str(r.get("fid")), "date": r.get("date"),
                "home": r.get("home"), "away": r.get("away"),
                "sm_home": sm["home"], "sm_away": sm["away"],
                "xg_home": sm["xg_home"], "xg_away": sm["xg_away"],
                "result": result, "score": r.get("score"),
                "v2_pick": r.get("pick"), "v2_correct": bool(r.get("correct")),
                "v1_pick": (v1_by_fid.get(str(r.get("fid"))) or {}).get("pick"),
                "top": bool(r.get("top")),
                "xgform_pick": pick,
                "xgform_correct": (None if pick is None else pick == result),
            })
            h_hist.append({"date": r.get("date"), "xf": sm["xg_home"],
                           "xa": sm["xg_away"]})
            a_hist.append({"date": r.get("date"), "xf": sm["xg_away"],
                           "xa": sm["xg_home"]})

    judged = [f for f in shadow["fixtures"] if f.get("xgform_pick") is not None]
    meta.update({
        "updated": today.isoformat(),
        "last_day_matched": matched, "last_day_unmatched": unmatched,
        "total": len(shadow["fixtures"]),
        "xgform": {"n": len(judged),
                   "correct": sum(1 for f in judged if f.get("xgform_correct"))},
    })
    _save(shadow)
    print(f"🔬 ظل xG: مطابقة {matched} ومُفلت {unmatched} — "
          f"الإجمالي {meta['total']} | فورمة xG: "
          f"{meta['xgform']['correct']}/{meta['xgform']['n']}")
    # التحقق مقابل Opta مرة واحدة في أول تشغيلة (وضع --validate يعيده يدوياً)
    if "opta_validation" not in meta:
        validate()


if __name__ == "__main__":
    if "--validate" in sys.argv:
        validate()
    else:
        main()
