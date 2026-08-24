# -*- coding: utf-8 -*-
"""🔬 مجمّع ظل xG من Sportmonks — المرحلة أ (خطة المالك 2026-07-31، انطلقت 2026-08-12).

عقيدة الظل أولاً (بند 7 من عقيدة استكشاف البيانات): يجمع ويقيس **بصفر تأثير على
أي محرك** — يقرأ المباريات المُقيَّمة من ذاكرة المحرك 2، يجلب xG الحقيقي لكل
مباراة من Sportmonks، ويسجّل ماذا كانت سترجّح "فورمة xG" (متوسط فارق xG الأخير
لكل فريق) مقابل ما اختاره المحركان وما حدث فعلاً — كله في
sportmonks_shadow.json (سجل قياس — عقيدة لا-أسقف-قياس: لا يُقص أبداً).

فشله صامت بالتصميم: لا يوقف تشغيلة ولا يمس توقعاً — أقصى ما يفعله عند أي عطل
هو سطر في السجل. الحكم بعد النافذة المُمدَّدة (تقرير مرحلي 24 أغسطس، الحكم ~17 سبتمبر).
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

# حارس الأعطال الصامتة (PR #95) — مصدر آلية الإنذار. الاستيراد محروس لأن
# المجمّع لا يجوز أن يسقط لأي سبب (عقيدة الظل: فشله صامت بالتصميم).
try:
    import api_guard
except Exception:                                # pragma: no cover - دفاعي
    api_guard = None

# مفتاح التراجع (خطة المرحلة أ): False = تعطيل الجامع فوراً بلا حذف أي كود —
# مع غياب SPORTMONKS_KEY من البيئة يشكّلان طريقَي التعطيل الطبيعيَّين
XG_SHADOW = True

# 🔴 مفتاح التعطيل الفوري للطبقة الحية وحدها — مستقل عن XG_SHADOW عمداً:
# المجمّع الصباحي تجربة جارية في منتصف نافذتها (حتى ~17 سبتمبر)، فيجب أن
# يبقى حياً حتى لو أُطفئت الطبقة الحية بالكامل.
XG_LIVE_SHADOW = True

# رصيد محجوز للمجمّع الصباحي: الطبقة الحية تتوقف عن الجلب متى هبط المتبقي
# تحت هذا الحد. **هذا هو ما يجعل البناء ممكناً قبل معرفة السقف**: الكود
# يقرأ المتبقي من كل رد ويكبح نفسه، فأياً كان السقف لا تجوّع الطبقةُ الحية
# التجربةَ الصباحية. الرقم متحفظ عمداً (المجمّع الصباحي يكلّف ~8 نداءات/يوم).
XG_LIVE_RESERVE = 300

KEY = os.environ.get("SPORTMONKS_KEY", "").strip()
BASE = "https://api.sportmonks.com/v3/football"
SHADOW_FILE = Path("sportmonks_shadow.json")
V2_FILE = Path("predictions_v2.json")
V1_FILE = Path("predictions.json")

XG_TYPE_ID = 5304        # معرف نوع xG للمباراة (مؤكد من المسبار)
FORM_WINDOW = 5          # نافذة حساب الفورمة: آخر 5 مباريات موثقة للفريق
FORM_MIN_MATCHES = 2     # كان 3 — تشخيص 2026-08-24: بعد 11 يوماً و62 مباراة
                         # لم يبلغ **أي** فريق 3 مباريات موثقة (78 بواحدة، 23
                         # باثنتين)، فأنتج القياس التنبؤي صفر نقاط. التغطية
                         # الضيقة (~53 دوري من 1200+) تعني أن الفرق تتراكم ببطء،
                         # والنافذة تنتهي ~17 سبتمبر. اثنتان تفتح القياس فوراً
                         # وتبقى شرطاً حقيقياً (لا ترجيح من مباراة واحدة يتيمة)
FORM_EDGE = 0.30         # فارق متوسطين أدنى للترجيح — دونه "تعادل"

# 🆕 عتبة "متفوّق xG" داخل المباراة الواحدة (إضافة 2026-08-24). هذا قياس
# **لاحق لا تنبؤي**: xG المباراة لا يتوفر قبلها، فلا يصلح مُدخلاً لتوقع.
# قيمته أنه يجيب — بعينة تبدأ من اليوم لا بعد أسابيع — السؤالَ الذي تقوم
# عليه المرحلة B كلها: هل يحمل xG إشارةً يفوّتها المحرك؟ فحين يختلف
# المتفوّق في xG عن اختيار المحرك، أيّهما يطابق النتيجة أكثر؟ إن لم يتفوّق
# xG في هذه المواجهة فمخمّد الثقة المقترح بلا أساس، وتُغلق المرحلة B مبكراً
# ونوفّر الاشتراك. تحت العتبة = تكافؤ، ولا ادعاء بلا فارق واضح.
XG_LEAD_EDGE = 0.50
MAX_PAGES_PER_DAY = 4    # سقف صفحات الجلب لليوم الواحد (50 مباراة/صفحة)
LOOKBACK_DAYS = 2        # نغطي آخر يومين — نفس أفق تقييم المحركات الصباحي
# سقف أيام التأسيس لمرة واحدة (بناء تاريخ الفرق بأثر رجعي من بداية التجربة)
HISTORY_BOOTSTRAP_MAX_DAYS = 14

# ترويسات الحد التي قد يرسلها المزوّد أو أي وسيط أمامه. سبورتمونكس v3 يضع
# الحد في **جسم** الرد (حقل rate_limit) لا في ترويسة، لكننا نقرأ الاثنين عمداً:
# الافتراض بأن مصدراً واحداً كافٍ هو بالضبط نوع الافتراض الذي كلّفنا يوم إنتاج
# كامل (عطل 14 أغسطس). أسماء ترويسات عامة لا تحمل أي سرّ.
_RATE_HEADER_KEYS = (
    "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset",
    "ratelimit-limit", "ratelimit-remaining", "ratelimit-reset",
    "retry-after",
)

# آخر ترويسات حد رُصدت — يملؤها _request ويقرأها المسبار. عامد أن يكون متغيراً
# عاماً لا قيمة راجعة: تغيير عدد قيم _request كان سيكسر كل بدائل الاختبارات.
_LAST_HEADERS = {}

# إنذار صفر الجمع (الدرس المعمّم من عطل 14 أغسطس): تجربة ظل تجمع صفر مدخلات
# يومين متتاليين تصرخ. **العطل الصامت هو العدو، لا العطل نفسه** — تجربة تجمع
# صفراً بصمت أخطر من تجربة تفشل بصوت.
ZERO_STREAK_ALERT = 2    # عدد الأيام المتتالية بصفر مطابقة قبل الإنذار
PROBE_SAMPLE_NAMES = 10  # كم اسماً نطبع من كل جانب في وضع --probe

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

# 📏 التغطية المقاسة (مسبار 2026-08-15، يوم 14 أغسطس): الباقة أرجعت 8 مباريات
# فقط لليوم كله، وهذه معرفات الدوريات التي **ثبت** أنها تحمل xG وتقع ضمن
# دوريات المالك. الأرقام معرفات سبورتمونكس لا معرفات API-Football.
# ⛔ قائمة مرجعية للعرض والتشخيص فقط — لا تُستعمل بوابةً تمنع الجمع: مطابقة
# الاسم هي البوابة الحقيقية، ومباراة خارج القائمة تمرّ بلا xG من تلقاء نفسها.
# (قائمة الحظر التي تفشل مفتوحة هي درس حادثة الدوريات النسائية — وهنا نتجنب
# النمط أصلاً بألا نجعلها بوابة.)
XG_COVERED_LEAGUES = {
    944: "الدوري السعودي للمحترفين",
    9: "التشامبيونشيب الإنجليزي",
}


# حروف لاتينية لا تفكّكها NFKD، فكانت تُحذف صامتةً ويتشوّه الرمز كله.
# دليل مقاس (مسبار 14 أغسطس على رد سبورتمونكس الحقيقي ليوم 11 أغسطس):
# «Bodø / Glimt» كانت تُطبَّع إلى {bod, glimt} بينما اسمنا «Bodo/Glimt» إلى
# {bodo, glimt} — تقاطع 1 من 3 = 0.33 فسقطت المطابقة على فريق مطابق تماماً.
# هذا **نقل حرفي قياسي لا مطابقة فضفاضة**: يصحّح الرمز ولا يوسّع دائرة
# المطابقة، فلا يمكنه توليد زوج خاطئ (بيانات خاطئة أسوأ من لا بيانات).
# الحركات الألمانية (ä ö ü) متروكة عمداً: NFKD يفكّكها أصلاً، وتغيير عرفها
# (ü → ue) كان سيكسر «Bayern München» ↔ «Bayern Munchen» المطابقة اليوم.
_LATIN_FOLD = str.maketrans({
    "ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe",
    "ß": "ss", "đ": "d", "Đ": "d", "ð": "d", "Ð": "d",
    "ł": "l", "Ł": "l", "þ": "th", "Þ": "th", "ı": "i",
})


def _norm_tokens(name: str) -> frozenset:
    """اسم فريق → مجموعة كلمات مطبَّعة (بلا تشكيل لاتيني ولا لواحق شكلية)."""
    s = unicodedata.normalize("NFKD", (name or "").translate(_LATIN_FOLD))
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


def xg_leader(xh, xa):
    """من تفوّق في xG داخل المباراة نفسها؟ None = تكافؤ (لا ادعاء).

    قياس لاحق لا تنبؤي — انظر تعليق XG_LEAD_EDGE أعلاه."""
    if xh is None or xa is None:
        return None
    if xh - xa > XG_LEAD_EDGE:
        return "home"
    if xa - xh > XG_LEAD_EDGE:
        return "away"
    return None


def backfill_leader(fixtures: list) -> int:
    """يحسب حقلي المتفوّق للمباريات المحفوظة سابقاً — من أرقامها المخزّنة
    وحدها (صفر نداءات)، وآمن التكرار: يتخطى ما حُسب. بهذا تبدأ العينة من
    62 مباراة مجموعة أصلاً بدل الصفر."""
    n = 0
    for f in fixtures:
        if "xg_leader" in f:
            continue
        lead = xg_leader(f.get("xg_home"), f.get("xg_away"))
        f["xg_leader"] = lead
        f["xg_leader_correct"] = (None if lead is None
                                  else lead == f.get("result"))
        n += 1
    return n


def xg_signal_stats(fixtures: list) -> dict:
    """لوحة الإشارة اللاحقة: هل المتفوّق في xG هو من يفوز؟ والأهم —
    عند اختلافه مع اختيار المحرك 2، أيّهما طابق النتيجة؟ (سؤال المرحلة B)."""
    lead = [f for f in fixtures if f.get("xg_leader")]
    dis = [f for f in lead
           if f.get("v2_pick") and f["v2_pick"] != f["xg_leader"]]
    return {
        "n": len(lead),
        "leader_right": sum(1 for f in lead if f.get("xg_leader_correct")),
        "disagree": len(dis),
        "xg_right": sum(1 for f in dis if f.get("xg_leader_correct")),
        "v2_right": sum(1 for f in dis if f.get("v2_correct")),
    }


def _request(path: str, params: dict) -> tuple:
    """النداء الخام → (رمز الحالة, الجسم). الطبقة التي يبني عليها _api والمسبار.

    وُجدت لأن _api يبتلع رمز الحالة عمداً (الصمت مبدؤه)، والمسبار يحتاج الرمز
    نفسه ليقول الحقيقة. رمز الحالة None = لم يصل رد أصلاً (استثناء شبكة).
    قاعدة الأسرار 3: المفتاح يسافر في الترويسة فقط ولا يُطبع هنا ولا في أي
    فرع من فروع الخطأ — نطبع اسم نوع الاستثناء لا نصه، فنص الاستثناء قد يحمل
    ما لا نريد تسريبه (وقع تسريب حقيقي سابقاً).
    """
    try:
        r = requests.get(f"{BASE}/{path}", params=params,
                         headers={"Authorization": KEY}, timeout=25)
    except Exception as e:
        return None, {"_exception": type(e).__name__}
    # ترويسات الحد تُلتقط من **كل** رد بما فيه المرفوض — الرد الرافض هو أصدق
    # لحظة يقول فيها المزوّد إن السقف نفد (درس عطل 14 أغسطس: الرفض يحمل المعلومة)
    try:
        _LAST_HEADERS.clear()
        _LAST_HEADERS.update({k: v for k, v in
                              ((k.lower(), v) for k, v in r.headers.items())
                              if k in _RATE_HEADER_KEYS})
    except Exception:                            # pragma: no cover - دفاعي
        pass
    try:
        return r.status_code, r.json()
    except Exception as e:
        return r.status_code, {"_exception": type(e).__name__}


def _api(path: str, params: dict):
    """نداء واحد — أي فشل يرجع None (الصمت مبدأ هنا، السجل يذكره فقط)."""
    status, body = _request(path, params)
    if status is None or not isinstance(body, dict) or "_exception" in body:
        print("🔬 ظل xG: نداء فشل صامتاً:",
              (body or {}).get("_exception", "رد غير مقروء"))
        return None
    if status != 200:
        print("🔬 ظل xG: حالة غير متوقعة", status, "على", path)
        return None
    return body


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


def live_xg_map(last_remaining=None, reserve: int = None) -> tuple:
    """🔴 xG الحي لكل المباريات الجارية — **نداء واحد لكل دورة، لا نداء لكل مباراة**.

    نقطة التصميم الحاسمة: سبورتمونكس يرجع كل المباريات الجارية في نداء واحد
    (livescores/inplay)، فكلفة الطبقة الحية ~144 نداءً/يوم مهما بلغ عدد
    المباريات — لا 20 نداءً كل 10 دقائق كما بدا في الطرح الأول. هذا وحده
    يُخرج الاستطلاع الحي من دائرة الخطر على الرصيد.

    الكبح الذاتي: يقرأ المتبقي من كل رد ويرجعه للمنادي ليخزّنه؛ متى هبط تحت
    الحجز توقف الطبقة الحية نفسها **قبل** أن تلمس رصيد المجمّع الصباحي.
    فالسقف المجهول لم يعد حاجزاً — صار مُدخلاً يتعامل معه الكود وقت التشغيل.

    سؤال التغطية يحل نفسه هنا: مباراة بلا xG في الباقة ببساطة لا ترد بـxG،
    فلا يوجد "نداء مهدور" أصلاً — لا نداء لكل مباراة كي يُهدر.

    يرجع (خريطة {(المضيف, الضيف): (xg_h, xg_a)}, المتبقي, ملاحظة).
    صامت المبدأ: أي عطل يرجع خريطة فارغة ولا يرفع استثناءً أبداً.
    """
    reserve = XG_LIVE_RESERVE if reserve is None else reserve
    if not XG_LIVE_SHADOW or not KEY:
        return {}, last_remaining, "مطفأ"
    # الكبح قبل النداء: المتبقي المرصود في الدورة السابقة هو ما نحتكم إليه
    if last_remaining is not None and last_remaining <= reserve:
        return {}, last_remaining, f"كبح ذاتي — المتبقي {last_remaining} تحت الحجز {reserve}"
    status, body = _request("livescores/inplay", {"include": "xgfixture"})
    if status != 200 or not isinstance(body, dict) or "_exception" in body:
        return {}, last_remaining, f"تعذر الجلب (HTTP {status})"
    remaining = _rate_sample(body).get("remaining")
    out = {}
    for fx in (body.get("data") or []):
        name = fx.get("name") or ""
        if " vs " not in name:
            continue
        home, away = (p.strip() for p in name.split(" vs ", 1))
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
            try:
                out[(home, away)] = (float(xh), float(xa))
            except (TypeError, ValueError):
                continue
    return out, (remaining if remaining is not None else last_remaining), ""


def live_xg_for(xg_map: dict, home: str, away: str):
    """يبحث عن xG مباراة بعينها في خريطة الدورة — بالمطابقة المحافظة نفسها.

    نفس names_match المستعمل في المجمّع الصباحي عمداً: مطابقة أضعف هنا كانت
    ستنتج **أزواجاً خاطئة**، وبيانات خاطئة أسوأ من لا بيانات (درس المطابقة).
    """
    for (h, a), xg in (xg_map or {}).items():
        if names_match(home, h) and names_match(away, a):
            return xg
    return None


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


def xg_table(day: str) -> list:
    """📋 جدول xG ليوم واحد — للمقارنة اليدوية مع FBref (مزوّده Opta منذ 2022).

    سبب الوجود: التحقق الأصلي (OPTA_SAMPLE) مبني على مباريات 2022-23، وباقة
    التجربة **بلا تغطية تاريخية** فرجع بصفر عينة (مسجَّل في meta.opta_validation).
    التحقق الوحيد الممكن قبل انتهاء التجربة هو على مباريات **حالية** في
    الدوريات المغطاة: يُطبع أرقام سبورتمونكس، وتُقارن بالعين على fbref.com.

    مقارنة يدوية عمداً لا آلية: كشط FBref يخالف شروطه، وربطُ التحقق بمصدر
    نكشطه كان سيصنع اعتماداً هشاً على صفحة قد تتغيّر — والرقم المطبوع هنا
    يكفي لحكم بشري في دقيقة واحدة.

    قراءة محضة: لا يكتب شيئاً ولا يمس أي محرك.
    """
    rows = []
    for page in range(1, MAX_PAGES_PER_DAY + 1):
        status, body = _request(f"fixtures/date/{day}",
                                {"per_page": 50, "page": page,
                                 "include": "xgfixture"})
        if status != 200 or not isinstance(body, dict) or "_exception" in body:
            print(f"📋 جدول xG: تعذر الجلب (HTTP {status})")
            break
        for fx in (body.get("data") or []):
            name = fx.get("name") or ""
            if " vs " not in name:
                continue
            home, away = (p.strip() for p in name.split(" vs ", 1))
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
                rows.append({"home": home, "away": away,
                             "xg_home": float(xh), "xg_away": float(xa),
                             "league_id": fx.get("league_id")})
        if not (body.get("pagination") or {}).get("has_more"):
            break
    print(f"📋 جدول xG ليوم {day} — {len(rows)} مباراة تحمل xG")
    print("المباراة | xG سبورتمونكس | الدوري")
    for r in rows:
        tag = XG_COVERED_LEAGUES.get(r["league_id"], f"دوري {r['league_id']}")
        print(f"    {r['home']} – {r['away']} | "
              f"{r['xg_home']:.2f}-{r['xg_away']:.2f} | {tag}")
    covered = [r for r in rows if r["league_id"] in XG_COVERED_LEAGUES]
    print(f"📊 منها {len(covered)} في دوريات المالك المغطاة "
          f"({', '.join(XG_COVERED_LEAGUES.values())})")
    if covered:
        print("⇦ قارن هذه الأرقام يدوياً على fbref.com (نفس المباراة، خانة xG). "
              "فارق أقل من ±0.35 في المتوسط = توافق مقبول بين نموذجَي xG.")
    else:
        print("⇦ لا مباراة في الدوريات المغطاة هذا اليوم — جرّب يوم مباريات سعودي")
    return rows


def _plan_lines(body: dict) -> list:
    """ملخص الاشتراك كما ترجعه سبورتمونكس مع **كل** رد — أسماء خطط وحزم فقط.

    هذا أثمن سطر تشخيصي عندنا: يقول أي حزمة نملك وحتى متى، فيفصل «الباقة بلا
    xG» عن «الباقة بها xG لكن دورياتنا خارجها». لا يحوي أي سرّ — أسماء تجارية.
    """
    out = []
    for sub in (body.get("subscription") or []):
        for pl in (sub.get("plans") or []):
            out.append(f"    خطة: {pl.get('plan')} | {pl.get('sport')} | "
                       f"{pl.get('category')}")
        for bn in (sub.get("bundles") or []):
            out.append(f"    حزمة: {bn.get('bundle')} | {bn.get('category')}")
    return out or ["    (لا معلومات اشتراك في الرد)"]


def _rate_sample(body: dict, headers: dict = None) -> dict:
    """يلتقط قراءة واحدة لحالة الحد من مصدرَيها: جسم الرد ثم الترويسات.

    سبورتمونكس v3 يضع rate_limit في الجسم: {remaining, resets_in_seconds,
    requested_entity} — والحد **لكل كيان** لا لكل الحساب، فاسم الكيان جزء من
    الإجابة لا تفصيل. الترويسات تُقرأ كاحتياط لو غيّر المزوّد أو وسيط أمامه العرف.
    """
    rl = (body or {}).get("rate_limit") or {}
    h = headers if headers is not None else _LAST_HEADERS
    def _num(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None
    return {
        "remaining": _num(rl.get("remaining")) if rl else _num(
            h.get("x-ratelimit-remaining") or h.get("ratelimit-remaining")),
        "resets_in": _num(rl.get("resets_in_seconds")) if rl else _num(
            h.get("x-ratelimit-reset") or h.get("ratelimit-reset")),
        "entity": rl.get("requested_entity") if rl else None,
        "limit_header": _num(h.get("x-ratelimit-limit")
                             or h.get("ratelimit-limit")),
        "source": "جسم الرد" if rl else ("ترويسة" if h else "لا مصدر"),
    }


def _window_label(seconds) -> str:
    """ثواني التصفير → وصف النافذة، **مع حدود ما يثبته رقم واحد**.

    التحفظ مقصود: resets_in_seconds عدّاد تنازلي **داخل** النافذة، فقراءة 1200
    قد تكون نافذة ساعية مرّ منها 40 دقيقة أو نافذة يومية بقي لها 20 دقيقة.
    القيمة **القصوى** المرصودة عبر عدة نداءات هي وحدها التي تحدّ النافذة من أسفل
    — لذلك يجمع المسبار قراءة لكل صفحة بدل قراءة واحدة.
    """
    if seconds is None:
        return "غير معروفة"
    s = int(seconds)
    if s <= 3600:
        return f"{s} ثانية — تتسق مع نافذة ساعية (3600 ثانية)"
    if s <= 86400:
        return f"{s} ثانية — أطول من ساعة، تتسق مع نافذة يومية"
    return f"{s} ثانية — أطول من يوم"


def rate_limit_lines(samples: list) -> list:
    """أسطر تقرير سقف النداءات — الحاجز القاطع قبل أي بناء على الطبقة الحية.

    يطبع: السقف (أو تعذُّر قراءته)، المتبقي، نافذة التصفير، وكلفة النداء الواحد
    مقاسةً من فرق المتبقي بين نداءين متتاليين. الكلفة المقاسة هي الرقم الذي
    يُبنى عليه التصميم: سقف ÷ كلفة الدورة = عدد الدورات التي يحتملها الرصيد.
    """
    real = [s for s in samples if s.get("remaining") is not None]
    if not real:
        return ["⛔ سقف النداءات: لم يرجع المزوّد أي بيانات حد "
                "(لا حقل rate_limit في الجسم ولا ترويسة X-RateLimit-*) — "
                "**لا تفترض السقف**؛ التصميم الحي يبقى محجوباً حتى يُقاس."]
    first, last = real[0], real[-1]
    lines = [f"🚦 سقف النداءات (المصدر: {last['source']}"
             + (f" | الكيان: {last['entity']}" if last.get("entity") else "")
             + ")"]
    lines.append(f"    السقف المعلن: "
                 + (str(last["limit_header"]) if last.get("limit_header")
                    else "غير معلن في الرد — يُستدل عليه من المتبقي"))
    lines.append(f"    المتبقي الآن: {last['remaining']}")
    lines.append(f"    نافذة التصفير: {_window_label(last.get('resets_in'))}")
    # كلفة النداء الواحد: تُقاس من نداءات المسبار نفسها لا تُفترض
    if len(real) >= 2 and first["remaining"] is not None:
        used = first["remaining"] - last["remaining"]
        calls = len(real) - 1
        if used >= 0 and calls:
            lines.append(f"    كلفة مقاسة: {used} من الرصيد مقابل {calls} نداء "
                         f"({used / calls:.2f} لكل نداء)")
    else:
        lines.append("    كلفة النداء: قراءة واحدة فقط — لا تكفي للقياس")
    mx = max((s["resets_in"] for s in real
              if s.get("resets_in") is not None), default=None)
    if mx is not None:
        lines.append(f"    أقصى عدّاد تصفير مرصود: {mx} ثانية "
                     f"(الحد الأدنى المثبت لطول النافذة)")
    return lines


def _probe_day_fixtures(day: str) -> tuple:
    """يجلب صفحات اليوم ويطبع لكل صفحة: حالة HTTP، العدد، وكم منها يحمل xG.

    يرجع (كل المباريات, أسطر الاشتراك, قراءات الحد). كل مباراة قاموس فيه
    الاسمان وعلم has_xg — **بما فيها التي بلا xG**، لأن الفرق بين «رجعت بلا xG»
    و«لم ترجع أصلاً» هو بيت القصيد في تشخيص صفر الجمع. وقراءات الحد تُجمع لكل
    صفحة لا مرة واحدة، كي تُقاس كلفة النداء الواحد بدل أن تُفترض.
    """
    fixtures, plan_lines, rate_samples = [], [], []
    for page in range(1, MAX_PAGES_PER_DAY + 1):
        status, body = _request(f"fixtures/date/{day}",
                                {"per_page": 50, "page": page,
                                 "include": "xgfixture"})
        # قراءة الحد تُلتقط من كل صفحة **قبل** أي فرع خروج: الرد الرافض يحمل
        # حالة الحد أيضاً، وهو أهم رد نقرأه إن كان النفاد هو سبب الرفض
        if isinstance(body, dict):
            rate_samples.append(_rate_sample(body))
        if not isinstance(body, dict) or "_exception" in body:
            print(f"  صفحة {page}: HTTP {status} | تعذر قراءة الرد "
                  f"({(body or {}).get('_exception', 'غير معروف')})")
            break
        if status != 200:
            # رسالة الخادم تُطبع كما هي: لا تحوي المفتاح (هو في الترويسة)
            print(f"  صفحة {page}: HTTP {status} | "
                  f"رسالة: {body.get('message')}")
            break
        data = body.get("data") or []
        page_rows, with_xg = [], 0
        for fx in data:
            name = fx.get("name") or ""
            home, away = (name.split(" vs ", 1) + [""])[:2] if " vs " in name \
                else (name, "")
            xh = xa = None
            for x in (fx.get("xgfixture") or []):
                if x.get("type_id") != XG_TYPE_ID:
                    continue
                v = (x.get("data") or {}).get("value")
                if x.get("location") == "home":
                    xh = v
                elif x.get("location") == "away":
                    xa = v
            has_xg = xh is not None and xa is not None
            with_xg += 1 if has_xg else 0
            page_rows.append({"home": home.strip(), "away": away.strip(),
                              "has_xg": has_xg,
                              "league_id": fx.get("league_id")})
        pag = body.get("pagination") or {}
        print(f"  صفحة {page}: HTTP {status} | مباريات {len(data)} | "
              f"بـxG {with_xg} | بلا xG {len(data) - with_xg} | "
              f"has_more={pag.get('has_more')}")
        if not plan_lines:
            plan_lines = _plan_lines(body)
        if not data and body.get("message"):
            print(f"    رسالة الخادم: {body.get('message')}")
        fixtures.extend(page_rows)
        if not pag.get("has_more"):
            break
    return fixtures, plan_lines, rate_samples


def probe(day: str = None) -> None:
    """وضع التشخيص (--probe): يطبع حقيقة ما ترجعه سبورتمونكس ليوم واحد.

    سبب الوجود (عطل صفر الجمع 13 أغسطس): المفتاح في Secrets ولا يصل لأحد،
    فالطريقة الوحيدة لرؤية الحقيقة هي طباعتها في سجل Actions. يطبع: رمز حالة
    HTTP لكل صفحة، كم مباراة رجعت فعلاً، **كم منها يحمل xG وكم لا يحمل**،
    ملخص الاشتراك، وعيّنة أسماء من الجانبين لفحص المطابقة بالعين.

    ويطبع كذلك **حالة سقف النداءات** (السقف، المتبقي، نافذة التصفير، وكلفة
    النداء الواحد مقاسةً): هذا هو الحاجز القاطع أمام أي استطلاع حي — رصيد
    ساعي صغير يعني أن تصميم xG الحي يجب أن يُبنى على لقطات متباعدة أو على
    دوريات المالك وحدها، لا على استطلاع كل دورة. **يُقاس ولا يُفترض.**

    ثم يفصل الفرضيات صراحةً: عدد مبارياتنا التي لها نظير بالاسم في سبورتمونكس
    (بصرف النظر عن xG) مقابل عدد ما يحمل xG — الفارق بين الرقمين يقول أي طبقة
    هي المعطلة: التغطية أم المطابقة.

    قراءة محضة: لا يكتب أي ملف ولا يمس أي محرك (عقيدة الظل أولاً، صفر تأثير).
    قاعدة الأسرار 3: لا يطبع المفتاح ولا أي جزء منه، أبداً.
    """
    if not KEY:
        print("🔬 مسبار: لا مفتاح في البيئة — تخطٍ نظيف (التشغيلة سليمة)")
        return
    day = day or (datetime.now(timezone.utc)
                  - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"🔬 مسبار ظل xG — اليوم {day} "
          f"(per_page=50، سقف {MAX_PAGES_PER_DAY} صفحات = "
          f"{50 * MAX_PAGES_PER_DAY} مباراة كحد أقصى)")

    fixtures, plan_lines, rate_samples = _probe_day_fixtures(day)
    with_xg = [f for f in fixtures if f["has_xg"]]
    print(f"📊 الإجمالي من سبورتمونكس: {len(fixtures)} مباراة — "
          f"{len(with_xg)} بـxG و{len(fixtures) - len(with_xg)} بلا xG")

    # الحاجز القاطع أولاً في المخرَج: لا يُبنى استطلاع حي قبل قراءة هذا الرقم
    for line in rate_limit_lines(rate_samples):
        print(line)

    print("📦 الاشتراك (كما يرجعه المزوّد — أسماء تجارية، لا أسرار):")
    for line in plan_lines:
        print(line)

    # جانبنا: المباريات التي قيّمها المحرك 2 في اليوم نفسه — هي التي تُعرض
    # على المطابقة كل صباح، فعيّنتها هي الطرف الثاني من فحص العين
    v2 = load_json(V2_FILE, {}) or {}
    ours = [r for r in (v2.get("resolved") or [])
            if r.get("date") == day and r.get("score")]

    print(f"🔤 عيّنة أسماء سبورتمونكس ({min(PROBE_SAMPLE_NAMES, len(fixtures))} "
          f"من {len(fixtures)}):")
    for f in fixtures[:PROBE_SAMPLE_NAMES]:
        print(f"    {f['home']} vs {f['away']} "
              f"[xG: {'نعم' if f['has_xg'] else 'لا'} | دوري {f['league_id']}]")
    print(f"🔤 عيّنة أسماء محركاتنا لليوم نفسه "
          f"({min(PROBE_SAMPLE_NAMES, len(ours))} من {len(ours)}):")
    for r in ours[:PROBE_SAMPLE_NAMES]:
        print(f"    {r.get('home')} vs {r.get('away')} "
              f"[{r.get('league')}]")

    # فصل الفرضيات: المطابقة بالاسم على **كل** ما رجع (بلا شرط xG) مقابل
    # المطابقة على ما يحمل xG فقط. التفسير مطبوع تحتها كي لا يُقرأ الرقم خطأً.
    def _hits(pool):
        return sum(1 for r in ours
                   if any(names_match(r.get("home"), f["home"])
                          and names_match(r.get("away"), f["away"])
                          for f in pool))

    hits_any, hits_xg = _hits(fixtures), _hits(with_xg)
    print(f"🔗 من مبارياتنا ({len(ours)}): {hits_any} لها نظير بالاسم في "
          f"سبورتمونكس، و{hits_xg} منها يحمل xG فعلاً")
    if not fixtures:
        print("   ⇦ التشخيص: المزوّد لم يرجع مباريات لهذا اليوم أصلاً — "
              "تغطية الباقة أو رفض الخادم، لا المطابقة")
    elif not with_xg:
        print("   ⇦ التشخيص: مباريات ترجع لكنها **بلا xG** — "
              "الحزمة أو الدوريات، لا المطابقة")
    elif hits_any and not hits_xg:
        print("   ⇦ التشخيص: نظراؤنا موجودون لكن بلا xG — تغطية xG لدورياتنا")
    elif not hits_any:
        print("   ⇦ التشخيص: لا تقاطع بين دورياتنا ودوريات الباقة "
              "(أو المطابقة تفشل) — قارن العيّنتين أعلاه بالعين")
    else:
        print("   ⇦ التشخيص: التقاطع موجود ويحمل xG — "
              "لو بقي الجمع صفراً فالخلل في طبقة المطابقة")


def zero_alert_text(meta: dict, offered: int) -> str:
    """نص إنذار صفر الجمع — صريح، ويقول للمالك ما الخطوة التالية بالضبط.

    لا يحوي المفتاح ولا أي جزء منه (قاعدة الأسرار 3)، ويمر فوق ذلك على
    redact() داخل api_guard قبل الإرسال — حزامان لا حزام واحد.
    """
    return (
        f"🔬 إنذار: تجربة ظل xG جمعت **صفر** مباراة "
        f"{meta.get('zero_streak', 0)} أيام متتالية "
        f"(آخر يوم: {meta.get('zero_last_day', '?')}).\n"
        f"عُرضت {offered} مباراة من محركاتنا على المطابقة ولم تُطابق ولا واحدة.\n"
        f"الإجمالي منذ البداية: {meta.get('total', 0)} مباراة موثقة.\n"
        f"الخطوة التالية: شغّل workflow «🔬 xG Shadow Probe» يدوياً "
        f"(أو python sportmonks_shadow.py --probe) واقرأ سجل Actions — "
        f"يفصل تغطية الباقة عن طبقة المطابقة.\n"
        f"التجربة **لا تؤثر على أي محرك**؛ هذا إنذار قياس لا إنذار عطل."
    )


def _maybe_alert_zero(meta: dict, offered: int, matched: int, day: str) -> bool:
    """يحدّث عدّاد أيام الصفر ويصرخ عند بلوغ ZERO_STREAK_ALERT.

    يُحسب اليوم يوماً صفرياً فقط حين **عُرضت** مباريات فعلاً ولم تُطابق أي منها:
    يوم بلا مباريات مقيَّمة أصلاً ليس عطلاً بل يوم هادئ (القاعدة 5-أ: البيانات
    الفارغة ليست خطأً)، والإنذار الكاذب يُفقد الإنذارَ الصادقَ قيمتَه.
    العدّاد مربوط بالتاريخ فلا ترفعه تشغيلتان في اليوم نفسه (آمن التكرار).
    يرجع True إن أُرسل إنذار فعلاً.
    """
    if not offered:
        return False
    if meta.get("zero_last_day") != day:
        meta["zero_streak"] = (int(meta.get("zero_streak", 0)) + 1
                               if matched == 0 else 0)
        meta["zero_last_day"] = day
    elif matched:
        meta["zero_streak"] = 0
    if int(meta.get("zero_streak", 0)) < ZERO_STREAK_ALERT or api_guard is None:
        return False
    try:
        # مانع التكرار (6 ساعات) داخل alert_once نفسه — لا إغراق للمالك
        return bool(api_guard.alert_once("xg_shadow_zero",
                                         zero_alert_text(meta, offered)))
    except Exception as e:                       # pragma: no cover - دفاعي
        # فشل الإنذار لا يجوز أن يصير عطلاً ثانياً (نفس عقيدة api_guard)
        print("🔬 ظل xG: تعذر إرسال إنذار الصفر:", type(e).__name__)
        return False


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

    # 🔓 فكّ الاختناق (2026-08-24): تاريخ الفرق يُبنى من **كل** مباراة تعيدها
    # الباقة، لا من المباريات المطابَقة فقط. كنا نجلب اليوم كاملاً ثم نرمي
    # 95% منه، فتراكمت الفرق مباراةً كل أسبوعين وبقي القياس التنبؤي صفراً.
    # نفس النداءات بالضبط — صفر تكلفة إضافية، والمعلومة كانت بين أيدينا.
    hist_seen = set(shadow.setdefault("hist_seen", []))
    if not hist_seen:
        # بذرة أولى: ما دخل التاريخ سابقاً عبر المباريات المطابَقة لا يُعاد
        hist_seen = {f"{f.get('date')}|{f.get('sm_home')}|{f.get('sm_away')}"
                     for f in shadow["fixtures"]}
    boot = []
    if not meta.get("hist_bootstrap"):
        # تأسيس لمرة واحدة: أيام التجربة الماضية تُجلب لبناء تاريخ الفرق
        start = meta.get("started") or today.strftime("%Y-%m-%d")
        d0 = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        span = min((today - d0).days, HISTORY_BOOTSTRAP_MAX_DAYS)
        boot = [(d0 + timedelta(days=i)).strftime("%Y-%m-%d")
                for i in range(max(0, span))]

    day_xg = {}
    # أيام النافذة تُجلب دائماً (لا أيام الصفوف فقط): التاريخ ينمو حتى في
    # يوم لم يقيّم فيه المحرك شيئاً — وهو المورد النادر في هذه التجربة
    for d in sorted(set(boot) | set(dates) | {r["date"] for r in rows}):
        day_xg[d] = fetch_day_xg(d)

    # الترتيب الزمني إلزامي: ترجيحات يومٍ تُحسب **قبل** إدخال مبارياته للتاريخ،
    # وإلا رأى الترجيح مباراته نفسها (تسريب مستقبل يُبطل القياس كله)
    for d in sorted(day_xg):
        for r in [x for x in rows if x.get("date") == d]:
            sm = next((s for s in day_xg.get(d, [])
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
                # القياس اللاحق (2026-08-24): من تفوّق في xG وهل فاز فعلاً
                "xg_leader": xg_leader(sm["xg_home"], sm["xg_away"]),
                "xg_leader_correct": (
                    None if xg_leader(sm["xg_home"], sm["xg_away"]) is None
                    else xg_leader(sm["xg_home"], sm["xg_away"]) == result),
            })
        # كل مباريات اليوم (المطابَقة وغيرها) تدخل تاريخ الفرق — بعد الترجيحات
        for sm in day_xg.get(d, []):
            key = f"{d}|{sm['home']}|{sm['away']}"
            if key in hist_seen:
                continue
            hist_seen.add(key)
            shadow["teams"].setdefault(_team_key(sm["home"]), []).append(
                {"date": d, "xf": sm["xg_home"], "xa": sm["xg_away"]})
            shadow["teams"].setdefault(_team_key(sm["away"]), []).append(
                {"date": d, "xf": sm["xg_away"], "xa": sm["xg_home"]})

    # تاريخ كل فريق مرتب زمنياً — نافذة الفورمة تقرأ الأحدث، فالترتيب معنى لا شكل
    for hist in shadow["teams"].values():
        hist.sort(key=lambda e: e.get("date") or "")
    shadow["hist_seen"] = sorted(hist_seen)
    if boot:
        meta["hist_bootstrap"] = today.strftime("%Y-%m-%d")
    # تعبئة رجعية لحقول المتفوّق على المباريات المحفوظة قبل هذا الإصلاح
    filled = backfill_leader(shadow["fixtures"])
    judged = [f for f in shadow["fixtures"] if f.get("xgform_pick") is not None]
    meta.update({
        "updated": today.isoformat(),
        "last_day_matched": matched, "last_day_unmatched": unmatched,
        "total": len(shadow["fixtures"]),
        "xgform": {"n": len(judged),
                   "correct": sum(1 for f in judged if f.get("xgform_correct"))},
        # اللوحة الثانية المستقلة (قاعدة المالك ج: لا خلط بين الوظائف)
        "xg_signal": xg_signal_stats(shadow["fixtures"]),
    })
    # إنذار صفر الجمع قبل الحفظ: العدّاد نفسه جزء من السجل المحفوظ
    alerted = _maybe_alert_zero(meta, len(rows), matched,
                                today.strftime("%Y-%m-%d"))
    _save(shadow)
    sig = meta.get("xg_signal") or {}
    print(f"🔬 ظل xG: مطابقة {matched} ومُفلت {unmatched} — "
          f"الإجمالي {meta['total']} | فورمة xG: "
          f"{meta['xgform']['correct']}/{meta['xgform']['n']} | "
          f"إشارة xG: {sig.get('leader_right', 0)}/{sig.get('n', 0)}"
          + (f" (عُبِّئ رجعياً {filled})" if filled else ""))
    if meta.get("zero_streak"):
        print(f"⚠️ ظل xG: {meta['zero_streak']} يوم متتالٍ بصفر جمع"
              + (" — أُرسل إنذار تيليجرام" if alerted else ""))
    # التحقق مقابل Opta مرة واحدة في أول تشغيلة (وضع --validate يعيده يدوياً)
    if "opta_validation" not in meta:
        validate()


if __name__ == "__main__":
    if "--probe" in sys.argv:
        # --probe [YYYY-MM-DD] — بلا تاريخ = أمس (نفس أول يوم في نافذة الجمع)
        _i = sys.argv.index("--probe") + 1
        _day = (sys.argv[_i] if len(sys.argv) > _i
                and not sys.argv[_i].startswith("-") else None)
        probe(_day)
    elif "--table" in sys.argv:
        # --table [YYYY-MM-DD] — جدول xG ليوم للمقارنة اليدوية مع FBref
        _i = sys.argv.index("--table") + 1
        _day = (sys.argv[_i] if len(sys.argv) > _i
                and not sys.argv[_i].startswith("-")
                else (datetime.now(timezone.utc)
                      - timedelta(days=1)).strftime("%Y-%m-%d"))
        if KEY:
            xg_table(_day)
        else:
            print("📋 جدول xG: لا مفتاح في البيئة — تخطٍ نظيف")
    elif "--validate" in sys.argv:
        validate()
    else:
        main()
