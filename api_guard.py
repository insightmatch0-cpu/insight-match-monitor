# -*- coding: utf-8 -*-
"""
حارس الأعطال الصامتة — الطبقة المشتركة بين المحركين
=====================================================
سبب الوجود (حادثة 2026-08-14): انتهى اشتراك API-Football فارتدّ الحساب إلى
الخطة المجانية (100 نداء/يوم بدل 7,500)، فنفد الرصيد خلال ساعة. النظام يحتاج
~2,366 نداءً في يوم مباريات نموذجي. النتيجة: صفر توقعات، صفر تقارير، صفر
إنذارات رادار — **والمالك لم يعلم 19 ساعة** حتى اكتشف العطل بنفسه من هاتفه.

السبب الجذري لم يكن انتهاء الاشتراك (هذا يحدث)، بل **الصمت**:
1. القاعدة 5 ("البيانات الفارغة ليست خطأً") جعلت رفض المزوّد يبدو كيوم هادئ.
2. الإنذار كان ينتظر ثلاث تشغيلات فاشلة متتالية — وقعت اثنتان فقط.
3. فحص النزاهة كان يسكن داخل المحرك نفسه، فمات حارسه معه.

العلاج هنا: **التمييز الصريح بين "لا بيانات" و"رفض المزوّد"**. القائمة الفارغة
تبقى مقبولة كما كانت (القاعدة 5 صحيحة ولم تتغير)، أما رفض الخادم فيُرفع
استثناءً مصنَّفاً (ApiRefused) ويصرخ على تيليجرام من **أول** فشل.

لماذا ملف مشترك واحد ولا نكرر المنطق في كل محرك: منطق التصنيف والتهدئة يجب
أن يكون متطابقاً حرفياً في المحركين. قوائم الاستبعاد المكرَّرة في أربعة ملفات
علّمتنا الدرس — النسخ يتباعد بصمت، والحارس الذي يتباعد ليس حارساً.

صفر نداءات API. لا يطبع أي مفتاح أبداً (قاعدة الأسرار 3) — كل رسالة تمرّ على
redact() التي تمسح أي قيمة سرّية قد تسربت إلى نص خطأ.

مفاتيح التراجع (متغيرات بيئة، أي قيمة من 0/false/no تُطفئ):
- API_REFUSAL_STRICT=0        → لا يُرفع استثناء عند الرفض (السلوك القديم: ابتلاع)
- API_ALERTS_ENABLED=0        → لا رسائل تيليجرام فورية عند الرفض
- API_QUOTA_LINE=0            → لا قراءة لعدّاد الرصيد ولا تحذير استباقي
- DELIVERY_ALERTS_ENABLED=0   → لا إنذار عند فشل مستقبِل ولا خروج بحالة فشل
                                عند فشل المالك (الطباعة تبقى دائماً)
- DELIVERY_LINE=0             → لا سطر نبض التسليم في النشرة الصباحية
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

STATE_FILE = Path("state.json")

# قراءة الرصيد تصل مع **كل** رد (predict_v2 وحده يتجاوز 400 نداء في التشغيلة)،
# فكتابة state.json في كل مرة إهدار قرصي بلا فائدة — القراءة معلومة إرشادية.
# نكتب مرة كل دقيقة على الأكثر؛ تعديل الذاكرة يبقى في كل مرة، والتحذير
# الاستباقي لا يمرّ من هنا أصلاً (له مانع تكراره الخاص).
QUOTA_WRITE_INTERVAL_SECONDS = 60
_last_quota_write = 0.0

# مهلة التهدئة: رسالة واحدة لكل نوع عطل كل 6 ساعات — إنذار فوري بلا إغراق
ALERT_COOLDOWN_HOURS = 6

# تحذير استباقي حين ينزل المتبقي تحت هذه النسبة من السقف اليومي
QUOTA_LOW_RATIO = 0.20

# الكلمات المفتاحية في رد API-Football التي تعني "اشتراك/حد طلبات" — أي منها
# يعني أن الحساب نفسه هو المشكلة، لا شبكة عابرة، فيستحق صراخاً فورياً
REFUSAL_KEYWORDS = ("requests", "plan", "limit", "subscription")

# أسماء متغيرات البيئة التي تحمل أسراراً — تُمسح من أي نص يُرسل
SECRET_ENV_NAMES = (
    "API_FOOTBALL_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_ADMIN_KEY", "TELEGRAM_TOKEN",
    "SPORTMONKS_KEY", "GH_TOKEN", "GITHUB_TOKEN",
    # TELEGRAM_CHAT_ID سرّ أيضاً (2026-08-15): المستودع عام وسجل Actions
    # يقرؤه الجميع. المعرّفات تخرج مقنَّعة عبر mask_id، وهذا السطر دفاع ثانٍ
    # يمسح أي ظهور كامل تسرّب من نص خطأ لم نتوقعه.
    "TELEGRAM_CHAT_ID",
)

REDACTED = "«محجوب»"


def _flag(name: str, default: bool = True) -> bool:
    """مفتاح تراجع من البيئة: أي من 0/false/no/off يُطفئ الميزة."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ================== الاستثناء المصنَّف ==================
class ApiRefused(RuntimeError):
    """رفض من مزوّد البيانات — ليس "يوماً هادئاً" ولا يُبتلع أبداً.

    kind: نوع العطل، ويحدد أيضاً مفتاح التهدئة في state.json:
      - "quota"   : نفد الرصيد / حد الطلبات (429 أو كلمة مفتاحية في errors)
      - "plan"    : قيد خطة أو اشتراك منتهٍ
      - "auth"    : مفتاح مرفوض (401/403)
      - "http"    : بقية أخطاء 4xx/5xx
      - "api"     : errors غير فارغة بلا تصنيف أوضح
    """

    def __init__(self, message: str, kind: str = "api", status: int = None):
        super().__init__(message)
        self.kind = kind
        self.status = status


# الأنواع التي تعني "الحساب مات" — هذه وحدها تصرخ على تيليجرام فوراً.
# بقية الأنواع (http عابر مثلاً) تُرفع استثناءً وتُلوّن التشغيلة بالأحمر بلا
# إغراق رسائل: خطأ شبكة عابر لا يستحق إيقاظ المالك.
SCREAMING_KINDS = ("quota", "plan", "auth")

# ما المطلوب من المالك بالضبط لكل نوع — الرسالة تخبره بالإجراء، لا بالعطل فقط
OWNER_ACTION = {
    "quota": "افتح dashboard.api-football.com وتحقق من الرصيد اليومي والخطة — "
             "الأرجح أن الاشتراك انتهى وارتدّ الحساب إلى الخطة المجانية.",
    "plan": "الخطة الحالية لا تسمح بهذا الطلب — جدّد اشتراك API-Football Pro "
            "(7,500 نداء/يوم) من dashboard.api-football.com.",
    "auth": "مفتاح API-Football مرفوض — أنشئ مفتاحاً جديداً وضعه في "
            "GitHub Secrets باسم API_FOOTBALL_KEY (لا ترسله في أي رسالة).",
}


# ================== تنظيف الأسرار ==================
def redact(text: str) -> str:
    """يمسح أي قيمة سرّية من النص قبل إرساله (قاعدة الأسرار 3).

    رد الخادم لا يتضمن المفتاح عادةً، لكن "عادةً" ليست ضماناً — تسريب مفتاح
    وقع فعلاً في هذا المشروع مرة. نمسح كل قيمة سرّية معروفة صراحةً.
    """
    out = str(text)
    for name in SECRET_ENV_NAMES:
        val = (os.environ.get(name) or "").strip()
        # القيم القصيرة جداً قد تطابق نصاً بريئاً — نتجاهلها
        if len(val) >= 8:
            out = out.replace(val, REDACTED)
    return out


# ================== ذاكرة الحالة (state.json) ==================
# monitor.py يحمل حالته في الذاكرة ويحفظها في نهاية التشغيلة، فلو كتبنا الملف
# من تحته لَمَحا حفظُه الأخير علمَ التهدئة وعاد الإغراق. الحل: المضيف يربط
# قاموس حالته الحي هنا، فنعدّله نحن ونحفظه فوراً (متانة ضد موت التشغيلة)
# ويبقى حفظه الأخير متسقاً معنا. من لا يربط شيئاً (predict_v2 / deadman)
# يعمل على الملف مباشرة.
_bound_state = None
_bound_save = None


def attach_state(state: dict, save_fn=None) -> None:
    """يربط قاموس الحالة الحي للمضيف — يُستدعى مرة واحدة بعد load_state()."""
    global _bound_state, _bound_save
    _bound_state = state
    _bound_save = save_fn


def detach_state() -> None:
    """يفك الربط ويصفّر مؤقت كتابة الرصيد وأعلام التسليم (للاختبارات أساساً)."""
    global _bound_state, _bound_save, _last_quota_write
    _bound_state = None
    _bound_save = None
    _last_quota_write = 0.0
    reset_delivery_flags()


def load_state() -> dict:
    if _bound_state is not None:
        return _bound_state
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state: dict) -> None:
    """حفظ فوري (write-through): علم التهدئة يجب أن ينجو حتى لو ماتت التشغيلة
    بعد الرسالة مباشرة — وإلا تكررت الرسالة كل عشر دقائق."""
    if _bound_save is not None:
        try:
            _bound_save(state)
            return
        except Exception as e:
            print("تعذر حفظ الحالة عبر المضيف:", e)
    try:
        STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except Exception as e:
        print("تعذر حفظ الحالة:", e)


# ================== بث تيليجرام متعدد المستقبِلين ==================
def broadcast_ids(chat_id: str, raw: str) -> list:
    """قائمة المستقبِلين النهائية.

    المالك (TELEGRAM_CHAT_ID) أولاً دائماً، ثم معرّفات TELEGRAM_BROADCAST_IDS
    مفصولة بفواصل. بلا تكرار، وبلا فراغات. السرّ غائب أو فارغ → القائمة =
    المالك وحده، أي **السلوك القديم حرفياً** (وهذا هو مفتاح التراجع نفسه:
    أفرغ السرّ فيعود كل شيء كما كان).
    """
    out = []
    for cid in [chat_id] + [p for p in str(raw or "").replace("\n", ",").split(",")]:
        cid = str(cid or "").strip()
        if cid and cid not in out:
            out.append(cid)
    return out


def mask_id(cid: str) -> str:
    """يعرض آخر 4 أرقام فقط من معرّف المحادثة — «…3333».

    السبب (قاعدة الأسرار 3): TELEGRAM_CHAT_ID سرّ في GitHub Secrets، وسجل
    Actions في مستودع **عام** يقرؤه أي شخص، و state.json مُلتزَم في نفس
    المستودع العام. آخر 4 أرقام تكفي تماماً للتمييز بين جهازين أو ثلاثة —
    وهذا كل ما يحتاجه المالك ليعرف أي جهاز صمت — ولا تكشف المعرّف نفسه.
    لذلك كل معرّف يخرج من هذه الطبقة (طباعة أو رسالة أو ملف) يمرّ من هنا.
    """
    s = str(cid or "").strip()
    return "…" + (s[-4:] if len(s) > 4 else s)


# ================== تأكيد التسليم الذاتي (2026-08-15) ==================
# ⚠️ حدّ صادق لا يجوز تجاوزه في أي رسالة أو تعليق أو توثيق:
# تيليجرام يؤكد **التسليم** لا **القراءة**. رد {"ok": true} يعني أن الرسالة
# سُلّمت إلى محادثة المستقبِل على خوادم تيليجرام — لا يعني إطلاقاً أن أحداً
# فتح هاتفه ورآها. Bot API لا يوفّر إيصال قراءة للبوتات أصلاً. لذلك الصياغة
# الوحيدة المسموحة هي «وصلت إلى الجهاز» / «استلم»، وممنوع «رأى» أو «قرأ».
# ادّعاء القراءة سيكون كذباً على المالك — وهذا النظام بُني كله ضد الوهم.

# مهلة التهدئة لإنذار فشل التسليم: رسالة واحدة **لكل معرّف** كل 6 ساعات
DELIVERY_ALERT_COOLDOWN_HOURS = 6

# ترجمة رد تيليجرام إلى سبب بلغة مفهومة + الإجراء المطلوب من المالك.
# المطابقة على description (نص إنجليزي من تيليجرام) بأول كلمة مفتاحية تُصادَف.
DELIVERY_REASONS = (
    ("bot was blocked", "الجهاز حظر البوت",
     "افتح محادثة البوت من ذلك الجهاز واضغط Start (أو ارفع الحظر)."),
    ("user is deactivated", "الحساب معطّل",
     "حساب تيليجرام هذا معطّل — احذف معرّفه من سرّ البث."),
    ("chat not found", "معرّف غير موجود",
     "المعرّف خاطئ أو لم تبدأ محادثة مع البوت من ذلك الجهاز — "
     "صحّح الرقم في سرّ البث ثم أرسل /start للبوت."),
    ("bot was kicked", "البوت مطرود من المجموعة",
     "أعد إضافة البوت إلى تلك المجموعة."),
    ("not enough rights", "البوت بلا صلاحية إرسال",
     "امنح البوت صلاحية إرسال الرسائل في تلك المحادثة."),
    ("too many requests", "تيليجرام يطلب التمهّل",
     "لا إجراء مطلوب — ضغط مؤقت من تيليجرام ويزول وحده."),
    ("unauthorized", "رمز البوت مرفوض",
     "رمز البوت غير صالح — أنشئ رمزاً جديداً وضعه في GitHub Secrets "
     "باسم TELEGRAM_TOKEN (لا ترسله في أي رسالة)."),
)

# حين لا يطابق النص أي كلمة مفتاحية نرجع إلى كود الخطأ وحده
DELIVERY_CODES = {
    400: ("معرّف غير صالح", "تحقق من صحة المعرّف في سرّ البث."),
    401: ("رمز البوت مرفوض", "أنشئ رمز بوت جديداً وضعه في GitHub Secrets."),
    403: ("الجهاز حظر البوت أو أخرجه",
          "افتح محادثة البوت من ذلك الجهاز واضغط Start."),
    429: ("تيليجرام يطلب التمهّل", "لا إجراء مطلوب — يزول وحده."),
}

DELIVERY_DEFAULT_ACTION = (
    "تحقق من المعرّف في سرّ البث TELEGRAM_BROADCAST_IDS، ومن أن ذلك "
    "الجهاز بدأ محادثة مع البوت."
)


def _delivery_reason(payload: dict, status: int = None) -> tuple:
    """يترجم رد فشل من تيليجرام إلى (سبب مقروء، الإجراء المطلوب)."""
    desc = str((payload or {}).get("description") or "").lower()
    code = (payload or {}).get("error_code")
    try:
        code = int(code)
    except Exception:
        code = int(status) if status else 0
    for keyword, reason, action in DELIVERY_REASONS:
        if keyword in desc:
            return reason, action
    if code in DELIVERY_CODES:
        return DELIVERY_CODES[code]
    if desc:
        # نص غير معروف: نمرّره كما هو (مقصوصاً ومنظَّفاً من أي سرّ) بدل ادّعاء
        # سبب لا نعرفه — الصدق أهم من رسالة أنيقة
        return f"رفض تيليجرام: {redact(desc)[:120]}", DELIVERY_DEFAULT_ACTION
    return (f"رفض تيليجرام (رمز {code})" if code else "سبب غير معروف"), \
        DELIVERY_DEFAULT_ACTION


def _send_one(token: str, cid: str, text: str) -> tuple:
    """يرسل لمستقبِل واحد ويقرأ رد تيليجرام. يرجع (نجح, سبب, إجراء).

    لا يرمي استثناءً أبداً مهما حدث — أي عطل يتحول إلى نتيجة فشل مقروءة.

    قاعدة التسامح المتعمَّدة: **غياب الدليل ليس دليل غياب**. إن تعذّرت قراءة
    جسم الرد (وسيط، اختبار مزيّف، رد بلا JSON) ولم يحمل الرد كود خطأ، نعدّها
    وصلت — تماماً كالسلوك القديم. العكس (اعتبار المجهول فشلاً) كان سيُنتج
    إنذارات كاذبة، وأخطر منها: خروجاً كاذباً بحالة فشل يوهم المالك أن قناته
    مقطوعة وهي سليمة.
    """
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": cid, "text": text},
            timeout=30,
        )
    except Exception as e:
        # نصّ الاستثناء قد يحمل المعرّف الكامل (بعض الوسطاء يضمّنون الجسم) —
        # نقنّعه صراحةً بعد redact: قاعدة الأسرار 3 لا تُترك للصدفة
        detail = redact(str(e)).replace(str(cid), mask_id(cid))
        return False, "تعذّر الاتصال بتيليجرام: " + detail[:120], \
            "غالباً عطل شبكة عابر — إن تكرر فتحقق من حالة تيليجرام."
    payload = {}
    try:
        parsed = resp.json()
        if isinstance(parsed, dict):
            payload = parsed
    except Exception:
        payload = {}
    status = getattr(resp, "status_code", None)
    if "ok" in payload:
        if payload.get("ok"):
            return True, "", ""
        reason, action = _delivery_reason(payload, status)
        return False, reason, action
    try:
        if status is not None and int(status) >= 400:
            reason, action = _delivery_reason(payload, status)
            return False, reason, action
    except Exception:
        pass
    return True, "", ""


def send_telegram_multi(token: str, chat_id: str, raw_broadcast: str, text: str,
                        notify: bool = True) -> dict:
    """يرسل النص إلى كل المستقبِلين **ويقرأ رد تيليجرام لكل واحد على حدة**.

    يرجع نتيجة منظّمة بدل العدد المجرد القديم:
        {"sent": ["…1111"],
         "failed": [{"id": "…3333", "reason": "الجهاز حظر البوت",
                     "action": "...", "owner": False}],
         "total": 2, "delivered": 1}

    المعرّفات في النتيجة **مقنَّعة** (mask_id) لأنها تُطبع وتُخزَّن وتُرسل.

    «وصلت» هنا تعني التسليم إلى الجهاز لا القراءة — انظر الحدّ الصادق أعلاه.

    الضمانة القائمة محفوظة حرفياً ولم تُمس: فشل مستقبِل واحد (حظر البوت،
    معرّف خاطئ، شبكة) **لا يمنع البقية** ولا يرمي استثناءً ولا يكسر التشغيلة
    أبداً. التنبيه خدمة مساعدة، لا يجوز أن يُسقط المحرك.

    notify=False يوقف الإنذار التلقائي فقط (يستعمله أمر /تحقق الذي يردّ
    بتقرير مفصّل فوراً، فالإنذار معه ضجيج مكرَّر).
    """
    owner = str(chat_id or "").strip()
    result = {"sent": [], "failed": [], "total": 0, "delivered": 0}
    for cid in broadcast_ids(chat_id, raw_broadcast):
        result["total"] += 1
        ok, reason, action = _send_one(token, cid, text)
        if ok:
            result["delivered"] += 1
            result["sent"].append(mask_id(cid))
            continue
        # لا نطبع المعرّف الكامل ولا التوكن — المعرّف مقنَّع دائماً
        print(f"تيليجرام: لم تصل إلى {mask_id(cid)} — {reason} "
              "(البقية تكمل، التشغيلة سليمة)")
        result["failed"].append({
            "id": mask_id(cid), "reason": reason, "action": action,
            "owner": bool(owner) and cid == owner,
        })
    _record_delivery(result)
    if notify and result["failed"]:
        _notify_delivery_failures(result, token, owner)
    return result


# ---------- ذاكرة آخر تسليم (state.json → delivery) ----------
def _record_delivery(result: dict, now: datetime = None) -> None:
    """يخزّن آخر نتيجة تسليم تحت مفتاح **مستقل** `delivery` في state.json.

    مفتاح مستقل عمداً: نبض التسليم في النشرة الصباحية يقرأ من هنا وحده، ولا
    يختلط بمانع تكرار الإنذارات (api_alerts) ولا بعدّاد الرصيد (api_quota) —
    كل قياس في خانته، فلا يمحو أحدهما الآخر.

    لا نسجّل إرسال الإنذار الداخلي نفسه (_notifying): إنذار فشل التسليم يُرسل
    إلى المالك وحده، فلو سجّلناه لَمحا نتيجة البث الحقيقي وصار النبض يقول
    «1 من 1 ✅» بينما جهاز صامت فعلاً — أي عطل صامت داخل حارس العطل الصامت.
    """
    if _notifying or not result.get("total"):
        return
    try:
        state = load_state()
        state["delivery"] = {
            "at": (now or now_utc()).isoformat(),
            "delivered": result["delivered"],
            "total": result["total"],
            # المعرّفات هنا مقنَّعة أصلاً — الملف مُلتزَم في مستودع عام
            "failed": [{"id": f["id"], "reason": f["reason"]}
                       for f in result["failed"]],
        }
        save_state(state)
    except Exception as e:
        print("تعذر حفظ نتيجة التسليم:", redact(str(e)))


def delivery_line(state: dict = None) -> str:
    """سطر نبض التسليم في نشرة المحرك 2 الصباحية.

    «📡 التسليم: 2 من 2 ✅» حين وصلت إلى الجميع، و«⚠️ التسليم: 1 من 2 —
    جهاز لم يستلم» حين نقص أحدهم. يُحسب من آخر بث مسجّل في state.json.

    الصياغة «استلم» لا «رأى»: تيليجرام يؤكد التسليم إلى الجهاز فقط.
    """
    if not _flag("DELIVERY_LINE"):
        return ""
    d = (state if state is not None else load_state()).get("delivery") or {}
    try:
        total = int(d.get("total") or 0)
        delivered = int(d.get("delivered") or 0)
    except Exception:
        return ""
    if total <= 0:
        return ""
    if delivered >= total:
        return f"📡 التسليم: {delivered} من {total} ✅"
    missing = total - delivered
    tail = "جهاز لم يستلم" if missing == 1 else f"{missing} أجهزة لم تستلم"
    return f"⚠️ التسليم: {delivered} من {total} — {tail}"


# ---------- الإنذار عند فشل مستقبِل ----------
# حارس عدم التكرار الذاتي: إنذار فشل التسليم يُرسل بنفس طبقة الإرسال، فلولا
# هذا العلم لَدار في حلقة لا نهائية حين يفشل الإنذار نفسه.
_notifying = False

# يُرفع حين يفشل تسليم رسالة إلى معرّف المالك نفسه — يقرؤه exit_if_owner_unreachable
_owner_unreachable = False


def build_delivery_message(masked_id: str, reason: str, action: str = "") -> str:
    """رسالة عربية صريحة إلى المالك وحده: أي معرّف فشل، ولماذا، وما المطلوب."""
    return "\n".join([
        "📡 رسالة لم تصل إلى أحد أجهزة البث",
        "",
        f"المعرّف: {masked_id} (آخر 4 أرقام)",
        f"السبب: {reason}",
        "",
        f"المطلوب منك: {action or DELIVERY_DEFAULT_ACTION}",
        "",
        "بقية الأجهزة استلمت بشكل طبيعي والنظام يعمل — لكن هذا الجهاز صامت "
        "حتى تُصلحه.",
        "تنبيه واحد لكل معرّف كل 6 ساعات حتى لا يتحول هذا إلى إغراق.",
    ])


def _shout_owner_unreachable(failure: dict) -> None:
    """فشل معرّف المالك نفسه: لا سبيل للتبليغ عبر تيليجرام إطلاقاً.

    فنطبع بأعلى صوت في سجل Actions ونرفع علماً يجعل التشغيلة تخرج بحالة فشل
    فتظهر **حمراء** في صفحة Actions. الفشل الصاخب أفضل من الصمت — هذا هو
    الدرس الوحيد الذي كلّف 19 ساعة في 14 أغسطس.
    """
    global _owner_unreachable
    _owner_unreachable = True
    print("=" * 64)
    print("🚨 تعذّر تسليم رسالة تيليجرام إلى المالك نفسه — لا قناة تبليغ بديلة!")
    print(f"   المعرّف: {failure.get('id')} (آخر 4 أرقام)")
    print(f"   السبب: {failure.get('reason')}")
    print(f"   المطلوب منك: {failure.get('action') or DELIVERY_DEFAULT_ACTION}")
    print("   هذه التشغيلة ستخرج بحالة فشل عمداً لتظهر حمراء في صفحة Actions،")
    print("   لأن الصمت هنا يعني أن النظام يتكلم ولا أحد يسمعه.")
    print("=" * 64)


def _notify_delivery_failures(result: dict, token: str, owner_id: str) -> None:
    """إنذار المالك **وحده** عن كل معرّف فشل، بمانع تكرار 6 ساعات لكل معرّف."""
    global _notifying
    if _notifying:
        return
    if not _flag("DELIVERY_ALERTS_ENABLED"):
        return
    _notifying = True
    try:
        for f in result.get("failed", []):
            if f.get("owner"):
                _shout_owner_unreachable(f)
                continue
            # raw_broadcast="" عمداً: هذا الإنذار للمالك وحده، لا يُبث إلى
            # الأجهزة الأخرى — أحدها هو موضوع الرسالة أصلاً.
            alert_once(
                f"delivery_{f['id']}",
                build_delivery_message(f["id"], f["reason"], f.get("action")),
                token=token, chat_id=owner_id, raw_broadcast="",
                hours=DELIVERY_ALERT_COOLDOWN_HOURS,
            )
    except Exception as e:
        print("تعذر إنذار فشل التسليم:", redact(str(e)))
    finally:
        _notifying = False


def owner_unreachable() -> bool:
    """هل فشل تسليم رسالة إلى المالك في هذه التشغيلة؟"""
    return _owner_unreachable


def reset_delivery_flags() -> None:
    """تصفير أعلام التسليم — تستعملها الاختبارات أساساً."""
    global _owner_unreachable, _notifying
    _owner_unreachable = False
    _notifying = False


def exit_if_owner_unreachable() -> None:
    """يُستدعى في **نهاية** main() لكل سكربت يرسل تيليجرام.

    التأجيل إلى النهاية متعمَّد: التشغيلة تُنجز عملها كاملاً وتحفظ حالتها
    أولاً، ثم تخرج حمراء. الخروج الفوري من داخل الإرسال كان سيكسر الضمانة
    القائمة (الإرسال لا يُسقط المحرك أبداً) ويضيّع حفظ الحالة معه — وهو
    بالضبط الخطأ الذي أصلحه شرط if: always() في monitor.yml.
    """
    if not _owner_unreachable:
        return
    if not _flag("DELIVERY_ALERTS_ENABLED"):
        print("🚨 المالك لم يستلم — الخروج بحالة فشل مُعطَّل بمفتاح التراجع.")
        return
    print("🚨 التشغيلة تخرج بحالة فشل: رسائل تيليجرام لم تصل إلى المالك.")
    sys.exit(1)


# ---------- أمر /تحقق: بث اختباري + تقرير مفصّل ----------
VERIFY_TEXT = (
    "🧪 رسالة تحقق من InsightMatch\n\n"
    "اختبار قصير للتأكد أن هذا الجهاز ما زال يستقبل تنبيهات النظام. "
    "لا إجراء مطلوب منك."
)


def verify_delivery(token: str = None, chat_id: str = None,
                    text: str = None) -> dict:
    """يبث رسالة اختبار قصيرة إلى كل المستقبِلين ويرجع نتيجة التسليم المنظّمة.

    قائمة البث تُقرأ من البيئة **هنا** عمداً: watchlist.py هو قناة التحكم
    ويجب ألا يذكر سرّ البث إطلاقاً — حارس بنيوي في tests/test_telegram_broadcast.py
    يسقط لو ظهر اسم السرّ في ذلك الملف. البث يخرج، والتحكم يدخل، ولا يلتقيان.

    notify=False: المالك طلب الفحص بنفسه وسيصله التقرير المفصّل فوراً،
    فإنذار إضافي ضجيج مكرَّر.
    """
    token = token if token is not None else os.environ.get("TELEGRAM_TOKEN", "").strip()
    if chat_id is None:
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    raw = os.environ.get("TELEGRAM_BROADCAST_IDS", "")
    return send_telegram_multi(token, chat_id, raw, text or VERIFY_TEXT,
                               notify=False)


def verify_report(result: dict) -> str:
    """تقرير عربي مفصّل عن نتيجة /تحقق: من استلم ومن فشل ولماذا."""
    total = int(result.get("total") or 0)
    delivered = int(result.get("delivered") or 0)
    if total <= 0:
        return ("📡 لا مستقبِلين مضبوطين — تحقق من TELEGRAM_CHAT_ID "
                "في GitHub Secrets.")
    lines = [
        "✅ تحقق التسليم: وصلت إلى كل الأجهزة" if delivered >= total
        else "⚠️ تحقق التسليم: لم تصل إلى كل الأجهزة",
        "",
        f"النتيجة: {delivered} من {total}",
    ]
    if result.get("sent"):
        lines += ["", "وصلت إلى:"] + [f"• {cid}" for cid in result["sent"]]
    if result.get("failed"):
        lines += ["", "لم تصل إلى:"]
        for f in result["failed"]:
            lines.append(f"• {f['id']} — {f['reason']}")
            if f.get("action"):
                lines.append(f"   المطلوب: {f['action']}")
    lines += [
        "",
        "ملاحظة صادقة: تيليجرام يؤكد أن الرسالة **وصلت إلى الجهاز** فقط — "
        "لا يوجد إيصال قراءة للبوتات، فوصولها لا يعني أن أحداً فتحها.",
    ]
    return "\n".join(lines)


# ================== مانع تكرار الإنذارات ==================
def _parse_iso(raw: str):
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return None


def alert_due(state: dict, kind: str, now: datetime = None,
              hours: int = ALERT_COOLDOWN_HOURS) -> bool:
    """هل حان وقت إنذار جديد من هذا النوع؟ (قراءة فقط — لا يعدّل الحالة)"""
    now = now or now_utc()
    last = ((state.get("api_alerts") or {}).get(kind) or {}).get("last")
    prev = _parse_iso(last)
    if prev is None:
        return True
    if prev.tzinfo is None:
        prev = prev.replace(tzinfo=timezone.utc)
    return (now - prev) >= timedelta(hours=hours)


def mark_alerted(state: dict, kind: str, now: datetime = None) -> None:
    now = now or now_utc()
    slot = state.setdefault("api_alerts", {}).setdefault(kind, {})
    slot["last"] = now.isoformat()
    slot["count"] = int(slot.get("count", 0)) + 1


def alert_once(kind: str, text: str, token: str = None, chat_id: str = None,
               raw_broadcast: str = None, now: datetime = None,
               hours: int = ALERT_COOLDOWN_HOURS) -> bool:
    """إنذار فوري مع مانع تكرار: يرسل ويسجّل، أو يصمت إن أُرسل مثله قبل <6h.

    يرجع True إن أُرسلت رسالة فعلاً. لا يرمي أي استثناء مهما حدث — فشل
    الإنذار لا يجوز أن يصير عطلاً ثانياً.

    hours: مهلة التهدئة لهذا النوع تحديداً (الافتراضي 6 ساعات لكل الأنواع؛
    إنذار فشل التسليم يمرره صراحةً لأن مفتاحه معرّف مستقبِل لا نوع عطل).
    """
    if not _flag("API_ALERTS_ENABLED"):
        return False
    token = token if token is not None else os.environ.get("TELEGRAM_TOKEN", "").strip()
    chat_id = chat_id if chat_id is not None else os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if raw_broadcast is None:
        raw_broadcast = os.environ.get("TELEGRAM_BROADCAST_IDS", "")
    if not (token and chat_id):
        return False
    try:
        state = load_state()
        if not alert_due(state, kind, now, hours=hours):
            return False
        send_telegram_multi(token, chat_id, raw_broadcast, redact(text))
        # نعيد قراءة الحالة: الإرسال نفسه كتب مفتاح `delivery` في الملف، ولو
        # حفظنا فوق النسخة الأقدم لمحوناه (نفس فخ الحالة المربوطة)
        state = load_state()
        mark_alerted(state, kind, now)
        save_state(state)
        return True
    except Exception as e:
        print("تعذر إرسال الإنذار:", redact(str(e)))
        return False


# ================== تصنيف رد المزوّد ==================
def classify_errors(errors) -> tuple:
    """يفحص حقل errors في رد API-Football.

    يرجع (هل هو رفض, النوع, النص المقروء).
    الشكل يختلف: [] أو {} حين لا خطأ، و{"requests": "..."} أو
    {"plan": "..."} أو قائمة نصوص حين يرفض الخادم.
    """
    if not errors:
        return False, "", ""
    text = json.dumps(errors, ensure_ascii=False)
    low = text.lower()
    kind = "api"
    if "requests" in low or "limit" in low or "rate" in low:
        kind = "quota"
    elif "plan" in low or "subscription" in low:
        kind = "plan"
    elif "token" in low or "key" in low:
        kind = "auth"
    return True, kind, text


def classify_status(status: int) -> tuple:
    """يصنّف كود HTTP. يرجع (هل هو رفض, النوع)."""
    if status is None or status < 400:
        return False, ""
    if status == 429:
        return True, "quota"
    if status in (401, 403):
        return True, "auth"
    return True, "http"


def refusal_is_screaming(kind: str, text: str = "") -> bool:
    """هل يستحق هذا الرفض رسالة تيليجرام فورية؟

    نعم لعائلة "الحساب مات" (رصيد/خطة/مفتاح)، أو حين يحمل نص الرد إحدى
    الكلمات المفتاحية الصريحة (requests / plan / limit / subscription).
    """
    if kind in SCREAMING_KINDS:
        return True
    low = str(text or "").lower()
    return any(k in low for k in REFUSAL_KEYWORDS)


def build_refusal_message(component: str, kind: str, detail: str,
                          status: int = None) -> str:
    """رسالة عربية صريحة: نوع العطل + المكوّن المتأثر + المطلوب من المالك."""
    titles = {
        "quota": "🚨 نفد رصيد API-Football (حد الطلبات اليومي)",
        "plan": "🚨 قيد خطة في API-Football — الاشتراك غالباً منتهٍ",
        "auth": "🚨 مفتاح API-Football مرفوض",
        "http": "🚨 مزوّد البيانات يرفض الطلبات",
        "api": "🚨 مزوّد البيانات يرفض الطلبات",
    }
    action = OWNER_ACTION.get(
        kind, "افتح dashboard.api-football.com وتحقق من حالة الحساب والخطة."
    )
    lines = [
        titles.get(kind, titles["api"]),
        "",
        f"المكوّن المتأثر: {component}",
    ]
    if status:
        lines.append(f"رد الخادم: HTTP {status}")
    if detail:
        lines.append(f"تفصيل: {detail[:300]}")
    lines += [
        "",
        f"المطلوب منك: {action}",
        "",
        "هذا ليس يوماً هادئاً — المزوّد رفض، والنظام سيبقى بلا بيانات "
        "حتى يُصلَح الحساب.",
    ]
    return "\n".join(lines)


def handle_refusal(component: str, kind: str, detail: str, status: int = None,
                   now: datetime = None) -> None:
    """الإجراء الكامل عند الرفض: إنذار فوري (بمانع تكرار) ثم يترك للمنادي
    أن يرفع الاستثناء. الطباعة تبقى دائماً حتى لو أُطفئت الإنذارات."""
    print(f"API-Football رفض الطلب [{kind}] في {component}:", redact(detail))
    if refusal_is_screaming(kind, detail):
        alert_once(kind, build_refusal_message(component, kind, detail, status), now=now)


# ================== عدّاد الرصيد المرئي ==================
def read_quota(headers, now: datetime = None) -> dict:
    """يقرأ ترويسات الرصيد من رد API-Football ويخزّن آخر قراءة في state.json.

    الترويسات: x-ratelimit-requests-remaining / x-ratelimit-requests-limit
    (سقف اليوم، لا سقف الدقيقة). يرجع القراءة أو {} إن غابت الترويسات.
    نرى الاختناق قبل أن يقتلنا: لو كان هذا موجوداً في 14 أغسطس لرأى المالك
    السقف 100 بدل 7,500 قبل أن يبتلعه الصمت.
    """
    if not _flag("API_QUOTA_LINE"):
        return {}
    try:
        get = headers.get if hasattr(headers, "get") else (lambda k, d=None: None)
        remaining = get("x-ratelimit-requests-remaining")
        limit = get("x-ratelimit-requests-limit")
        if remaining is None or limit is None:
            return {}
        reading = {
            "remaining": int(str(remaining).strip()),
            "limit": int(str(limit).strip()),
            "at": (now or now_utc()).isoformat(),
        }
    except Exception:
        return {}
    if reading["limit"] <= 0:
        return {}
    global _last_quota_write
    try:
        state = load_state()
        state["api_quota"] = reading
        # الحالة المربوطة (monitor.py) تُحفظ في نهاية التشغيلة على أي حال،
        # فيكفي تعديل الذاكرة هنا؛ الكتابة القرصية بمعدل مرة كل دقيقة
        now_mono = time.monotonic()
        if now_mono - _last_quota_write >= QUOTA_WRITE_INTERVAL_SECONDS:
            _last_quota_write = now_mono
            save_state(state)
    except Exception as e:
        print("تعذر حفظ قراءة الرصيد:", e)
    _warn_if_low(reading, now=now)
    return reading


def _warn_if_low(reading: dict, now: datetime = None) -> None:
    """تحذير استباقي حين ينزل المتبقي تحت 20% — مرة كل 6 ساعات."""
    limit = int(reading.get("limit") or 0)
    remaining = int(reading.get("remaining") or 0)
    if limit <= 0 or remaining > limit * QUOTA_LOW_RATIO:
        return
    used = limit - remaining
    pct = int(round(remaining * 100.0 / limit))
    alert_once(
        "quota_low",
        "⚠️ رصيد API-Football يقترب من النفاد\n\n"
        f"المتبقي: {remaining} من {limit} ({pct}%)\n"
        f"المستهلك اليوم: {used}\n\n"
        "المطلوب منك: تحقق من الخطة في dashboard.api-football.com — "
        "إن كان السقف أقل من 7,500 فالاشتراك ارتدّ إلى الخطة المجانية.\n"
        "إن كان السقف سليماً فهذا استهلاك طبيعي في يوم مزدحم ولا إجراء مطلوب.",
        now=now,
    )


def quota_line(state: dict = None) -> str:
    """سطر النشرة الصباحية: «📊 رصيد API: مستهلك X من Y» — فارغ إن لا قراءة."""
    if not _flag("API_QUOTA_LINE"):
        return ""
    q = (state if state is not None else load_state()).get("api_quota") or {}
    try:
        limit = int(q.get("limit") or 0)
        remaining = int(q.get("remaining") or 0)
    except Exception:
        return ""
    if limit <= 0:
        return ""
    used = max(0, limit - remaining)
    line = f"📊 رصيد API: مستهلك {used} من {limit}"
    if remaining <= limit * QUOTA_LOW_RATIO:
        line += " ⚠️"
    return line


# ================== الاستدعاء المحروس ==================
def guarded_get(url: str, headers: dict, component: str, timeout: int = 30) -> list:
    """نداء API-Football واحد، محروساً — القلب المشترك بين المحركين.

    التمييز الذي كلّف يوم إنتاج كامل:
      • response قائمة فارغة و errors فارغة → **يوم هادئ**، مقبول بالقاعدة 5،
        يرجع [] بلا ضجيج (هذا هو السلوك القديم وهو صحيح).
      • errors غير فارغة، أو 4xx/5xx، أو حد طلبات، أو قيد خطة → **رفض**،
        يُرفع ApiRefused مصنَّفاً ولا يُبتلع أبداً.
    """
    resp = requests.get(url, headers=headers, timeout=timeout)

    # عدّاد الرصيد يُقرأ من كل رد — حتى رد الرفض يحمل الترويسات وهو أهمها
    read_quota(getattr(resp, "headers", {}) or {})

    refused, kind = classify_status(getattr(resp, "status_code", None))
    if refused:
        detail = ""
        try:
            detail = json.dumps(resp.json(), ensure_ascii=False)[:400]
        except Exception:
            detail = (getattr(resp, "text", "") or "")[:400]
        # كود HTTP وحده قد لا يصنّف بدقة — نص الرد قد يكشف "plan"/"requests"
        _, body_kind, _ = classify_errors(detail if detail else None)
        if kind == "http" and body_kind in ("quota", "plan", "auth"):
            kind = body_kind
        handle_refusal(component, kind, detail, status=resp.status_code)
        if not _flag("API_REFUSAL_STRICT"):
            return []
        raise ApiRefused(
            f"API-Football رفض الطلب (HTTP {resp.status_code}) في {component}",
            kind=kind, status=resp.status_code,
        )

    data = resp.json()
    refused, kind, detail = classify_errors(data.get("errors"))
    if refused:
        handle_refusal(component, kind, detail)
        if not _flag("API_REFUSAL_STRICT"):
            return data.get("response", [])
        raise ApiRefused(
            f"API-Football رفض الطلب في {component}: {redact(detail)[:300]}",
            kind=kind,
        )

    # هنا فقط: البيانات الفارغة ليست خطأً (القاعدة 5) — يوم هادئ حقيقي
    return data.get("response", [])
