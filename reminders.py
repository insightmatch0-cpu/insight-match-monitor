# -*- coding: utf-8 -*-
"""
📅 سجل المواعيد والتذكيرات — قرار المالك 2026-08-14
====================================================
سبب الوجود: مواعيد المشروع الحرجة (انتهاء اشتراك، حكم تجربة، نقطة مراجعة)
كانت تعيش في Routines على جانب Claude وحده. الروتين يوقظ الوكيل، لكنه **لا
يصل هاتف المالك**. أمر المالك: كل تذكير من اليوم يمرّ على تيليجرام أيضاً.

معيار الأولويات (وضعه الوكيل بتفويض المالك «أنت تقرر، أنت تضع المعيار»)،
ومدى المهلة 3-5 أيام كما اشترط:

جدول التنبيه (أمر المالك 2026-08-14، معمَّم على **كل** واجهة نستعملها لا
على Sportmonks وحدها): تنبيهان قبل الموعد — **قبل 3 أيام، ثم قبل يومين** —
وكل رسالة تحمل **اسم الخدمة وسعرها** لا الموعد وحده. `remind_at` في الموعد
نفسه يتجاوز الافتراضي حين يلزم.

  P1 — مال أو فقدان بيانات لا يُعوَّض (اشتراك يتجدّد، رصيد ينفد).
       تنبيها 3 و2، ويبقى ⛔ متأخراً أسبوعاً بعد الموعد. لماذا محدود لا
       أبدي: تذكير لا ينطفئ يُدرِّب العين على تجاهل كتلة المواعيد كلها،
       فيُخفي P1 التالي — إرهاق الإنذار، أخو العطل الصامت.
  P2 — قرار أو حكم مجدول (حكم تجربة، تقرير مرحلي). تنبيها 3 و2، ويصمت
       بعد يوم من الموعد.
  P3 — روتيني/معلوماتي. تنبيها 3 و2، ويصمت يوم الموعد.

ملاحظة صريحة على الجدول: بهذا الضبط **لا يصلك شيء يومَي T-1 وT-0** — هذا
حرفياً ما طلبه المالك (يتصرّف قبل يومين)، ومذكور هنا كي لا يُقرأ الصمت
في اليومين الأخيرين على أنه عطل.

مواعيد بلا تاريخ أو بلا سعر لا تُخمَّن أبداً: تُدرَج في كتلة «بيانات ناقصة»
تظهر في النشرة حتى يملأها المالك. اختلاق تاريخ تجديد أو سعر أسوأ من الفراغ.

عقيدة هذا الملف (نفس عقيدة api_guard/deadman): **صفر نداءات API**، يقرأ ملف
JSON على القرص فقط، ولا يكسر أي تشغيلة مهما حدث. لا يمسّ أي محرك ولا أي
توقع — قراءة وتذكير فقط. لا يطبع أي سرّ (قاعدة الأسرار 3): كل نص يمرّ على
redact() داخل api_guard قبل الإرسال.

يُستدعى من موضعين عمداً (درس 14 أغسطس: الحارس الذي يسكن داخل ما يحرسه ليس
حارساً): النشرة الصباحية في predict_v2.py، و**deadman.py** المستقل الذي
يعمل كل 10 دقائق في monitor.yml ويبقى حياً حين يسقط المحركان. مانع التكرار
يجعل النداء المزدوج بلا أثر: رسالة واحدة لكل موعد في اليوم.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import api_guard

REMINDERS_FILE = Path("reminders.json")

# أيام التنبيه قبل الموعد (أمر المالك: «قبل ثلاثة أيام، وقبل يومين»)
REMIND_AT = (3, 2)

# يبقى للتوافق: أقصى تنبيه مسبق، يُشتق من REMIND_AT
LEAD_DAYS = {"P1": max(REMIND_AT), "P2": max(REMIND_AT), "P3": max(REMIND_AT)}

# كم يوماً يبقى التذكير بعد فوات الموعد قبل أن يصمت
GRACE_DAYS = {"P1": 7, "P2": 1, "P3": 0}

PRIORITY_MARK = {"P1": "🔴", "P2": "🟠", "P3": "🔵"}

# حالتا إغلاق: done = نُفِّذ، deferred = أرجأه المالك صراحةً (قراره 2026-08-15:
# «keep it for later») — كلاهما يُسكِت التذكير، والفرق توثيقي لا سلوكي
CLOSED_STATUSES = ("done", "deferred")

# عملة العرض المفضّلة للمالك (أمره 2026-08-15: «كل الأسعار بالدولار لا اليورو»).
# ما يُفوتر باليورو (Sportmonks) يُعرض بعملته الأصلية **ومعها** تقدير بالدولار
# محسوب من سعر صرف **مسجَّل ومؤرَّخ** في reminders.json → fx. بلا سعر مسجَّل
# لا يُعرض رقم دولاري مخترَع: تُرفع الفجوة في كتلة «بيانات ناقصة».
# السعر الحقيقي بالدولار هو ما يخصمه مصرف المالك، وسعرُه هو المرجع النهائي.
DISPLAY_CURRENCY = "USD"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def load_deadlines(path: Path = None) -> list:
    """يقرأ سجل المواعيد. أي عطل → قائمة فارغة (لا يكسر تشغيلة أبداً)."""
    try:
        raw = json.loads((path or REMINDERS_FILE).read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for d in (raw.get("deadlines") or []):
        if isinstance(d, dict) and d.get("id"):
            out.append(d)          # بلا تاريخ = بانتظار بيانات، لا مُستبعَد
    return out


def _days_left(due: str, today: datetime):
    """أيام متبقية للموعد. None = تاريخ غير صالح (يُتجاهل بصمت)."""
    try:
        d = datetime.strptime(str(due), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (d.date() - today.date()).days


def _offsets(item: dict) -> tuple:
    """أيام التنبيه لهذا الموعد: ما نصّ عليه، وإلا الافتراضي (3 ثم 2)."""
    raw = item.get("remind_at")
    if isinstance(raw, (list, tuple)) and raw:
        try:
            return tuple(sorted({max(int(x), 0) for x in raw}, reverse=True))
        except (TypeError, ValueError):
            pass
    try:                       # دعم lead_days القديم: يعني «كل يوم حتى الموعد»
        return tuple(range(int(item["lead_days"]), -1, -1))
    except (KeyError, TypeError, ValueError):
        return REMIND_AT


def _lead(item: dict) -> int:
    """أقصى تنبيه مسبق لهذا الموعد (أول يوم يُسمع فيه)."""
    return max(_offsets(item))


def is_due(item: dict, today: datetime) -> bool:
    """هل يستحق هذا الموعد تذكيراً اليوم؟

    مغلق يدوياً (status=done) → أبداً. داخل نافذة المهلة → نعم. بعد الموعد →
    حسب مهلة السماح لشريحته (P1 أسبوع، P2 يوم، P3 صفر).
    """
    if str(item.get("status", "open")).lower() in CLOSED_STATUSES:
        return False
    left = _days_left(item.get("due"), today)
    if left is None:
        return False
    if left >= 0:
        return left in _offsets(item)
    grace = GRACE_DAYS.get(item.get("priority"), 0)
    return True if grace is None else -left <= grace


def due_reminders(today: datetime = None, path: Path = None) -> list:
    """المواعيد المستحقة اليوم، الأقرب أولاً ثم الأعلى أولوية."""
    today = today or now_utc()
    out = []
    for item in load_deadlines(path):
        if not is_due(item, today):
            continue
        row = dict(item)
        row["days_left"] = _days_left(item.get("due"), today)
        out.append(row)
    out.sort(key=lambda r: (r["days_left"],
                            str(r.get("priority", "P3"))))
    return out


def fx_rate(path: Path = None):
    """سعر صرف EUR→USD المسجَّل، أو None. لا يُخترع ولا يُقدَّر أبداً."""
    try:
        raw = json.loads((path or REMINDERS_FILE).read_text(encoding="utf-8"))
    except Exception:
        return None
    try:
        rate = float(((raw.get("fx") or {}).get("eur_usd")))
    except (TypeError, ValueError):
        return None
    return rate if rate > 0 else None


def _usd_hint(item: dict, rate) -> str:
    """«≈ $X/شهر» للصفوف المفوترة باليورو حين يكون سعر الصرف مسجَّلاً."""
    if rate is None:
        return ""
    try:
        amount = float(item.get("amount_eur"))
    except (TypeError, ValueError):
        return ""
    return f" ≈ ${amount * rate:,.0f}/شهر"


def _arabic_days(n: int) -> str:
    """صيغة العدد بالعربية الصحيحة: مثنّى وجمع قلة وتمييز مفرد منصوب.
    «2 أيام» ليست عربية — والنشرة تُقرأ على الهاتف، فركاكتها تُلاحَظ."""
    if n == 1:
        return "يوم واحد"
    if n == 2:
        return "يومين"
    if 3 <= n <= 10:
        return f"{n} أيام"
    return f"{n} يوماً"


def reminder_line(item: dict, rate=None) -> str:
    """سطر تذكير واحد للنشرة — يقول الموعد والمتبقي والإجراء المطلوب."""
    left = item.get("days_left")
    if left is None:
        when = ""
    elif left > 1:
        when = f"بعد {_arabic_days(left)}"
    elif left == 1:
        when = "غداً"
    elif left == 0:
        when = "**اليوم**"
    else:
        when = f"⛔ فات الموعد منذ {_arabic_days(-left)}"
    mark = PRIORITY_MARK.get(item.get("priority"), "🔵")
    # اسم الخدمة وسعرها في كل رسالة (أمر المالك): الموعد وحده لا يكفي
    # ليقرّر — يحتاج أن يعرف **ما** يتجدّد و**بكم** دون فتح أي ملف.
    head = item.get("service") or ""
    if item.get("price"):
        head += f" ({item['price']}{_usd_hint(item, rate)})"
    head = f"{head}: " if head else ""
    line = (f"{mark} {item.get('priority', 'P3')} — {head}"
            f"{item.get('title')}: {when} ({item.get('due')})")
    if item.get("action"):
        line += f"\n   ⇦ {item['action']}"
    return line


def pending_input(path: Path = None) -> list:
    """مواعيد لا نعرف تاريخها أو سعرها — تُعرَض ولا تُخمَّن.

    اختلاق تاريخ تجديد أو سعر اشتراك أسوأ من الفراغ بكثير: الفراغ يُسأل عنه،
    والرقم المختلق يُبنى عليه قرار مالي. تبقى هذه الكتلة في النشرة حتى يملأها
    المالك، فهي نفسها تذكير دائم بأن السجل ناقص.
    """
    out = []
    for item in load_deadlines(path):
        if str(item.get("status", "open")).lower() in CLOSED_STATUSES:
            continue
        missing = []
        if _days_left(item.get("due"), now_utc()) is None:
            missing.append("تاريخ التجديد")
        if item.get("billable") and not item.get("price"):
            missing.append("السعر")
        if missing:
            row = dict(item)
            row["missing"] = missing
            out.append(row)
    return out


def pending_lines(path: Path = None) -> str:
    """كتلة «بيانات ناقصة» للنشرة — فارغة حين يكتمل السجل."""
    rows = pending_input(path)
    try:
        _fx_meta = (json.loads((path or REMINDERS_FILE)
                               .read_text(encoding="utf-8")).get("fx") or {})
    except Exception:
        _fx_meta = {}
    needs_fx = (fx_rate(path) is None
                and not _fx_meta.get("deferred")
                and any(i.get("amount_eur") for i in load_deadlines(path)))
    if not rows and not needs_fx:
        return ""
    out = ["⚠️ سجل الاشتراكات ناقص — لا أخمّن تاريخاً ولا سعراً:"]
    if needs_fx:
        out.append("   • عرض الأسعار بالدولار: سعر صرف EUR→USD غير مسجَّل "
                   "(fx.eur_usd في reminders.json). Sportmonks تفوتر باليورو — "
                   "زوّدني بالمبلغ الدولاري الذي خصمه مصرفك، أو بسعر الصرف.")
    for r in rows:
        name = r.get("service") or r.get("title") or r["id"]
        out.append(f"   • {name}: ينقصه {' و'.join(r['missing'])}"
                   + (f" — {r['action']}" if r.get("action") else ""))
    return "\n".join(out)


def next_deadline(today: datetime = None, path: Path = None):
    """أقرب موعد قادم (ولو خارج نافذة التنبيه) — وقود سطر الحارس اليومي."""
    today = today or now_utc()
    future = []
    for item in load_deadlines(path):
        if str(item.get("status", "open")).lower() in CLOSED_STATUSES:
            continue
        left = _days_left(item.get("due"), today)
        if left is not None and left >= 0:
            row = dict(item)
            row["days_left"] = left
            future.append(row)
    return min(future, key=lambda r: r["days_left"]) if future else None


def reminder_lines(today: datetime = None, path: Path = None) -> str:
    """كتلة المواعيد للنشرة الصباحية — **تظهر كل يوم بلا استثناء**.

    كانت ترجع نصاً فارغاً حين لا استحقاق، وهذا بالضبط عيب «اختفاء السطر»
    الذي أخفى عطل تجربة xG: الصمت يقرأه المالك «لا موعد قريب» بينما قد
    يعني «الحارس نفسه مات». حارس لا يُسمع صوته يومياً لا يُعرف أنه حي.
    فحين لا استحقاق نقول ذلك صراحةً ونذكر أقرب موعد وكم بقي له — سطر
    واحد قصير يثبت أن السجل يُقرأ فعلاً كل صباح.
    """
    today = today or now_utc()
    rows = due_reminders(today, path)
    blocks = []
    if rows:
        rate = fx_rate(path)
        blocks.append("📅 مواعيد قادمة:\n"
                      + "\n".join(reminder_line(r, rate) for r in rows))
    else:
        # يوم هادئ: يُقال إنه هادئ، لا يُترك فراغاً يُشبه العطل
        nxt = next_deadline(today, path)
        if nxt is None:
            # سجل بلا أي موعد قادم = حالة عطل بحد ذاتها: إما فرغ السجل أو
            # تقادم كله. مدير التجديد لا يجوز أن يكون بلا موعد واحد يراقبه.
            blocks.append("⚠️ حارس المواعيد: لا موعد قادم في السجل إطلاقاً — "
                          "راجع reminders.json")
        else:
            head = nxt.get("service") or nxt.get("title")
            price = f" ({nxt['price']})" if nxt.get("price") else ""
            blocks.append(
                f"📅 حارس المواعيد: لا استحقاق اليوم — أقرب موعد "
                f"{head}{price} بعد {_arabic_days(nxt['days_left'])} "
                f"({nxt['due']})")
    gaps = pending_lines(path)
    if gaps:
        blocks.append(gaps)
    return "\n".join(blocks)


def fire(today: datetime = None, path: Path = None) -> int:
    """يرسل تذكيرات تيليجرام المستحقة — رسالة واحدة لكل موعد في اليوم.

    مانع التكرار: مفتاح التهدئة يحمل التاريخ (`reminder:<id>:<يوم>`) فيفتح
    نافذة جديدة كل يوم ويُغلقها أول إرسال — فالنداء من النشرة ومن الحارس
    الخارجي معاً لا يُنتج رسالتين. يرجع عدد ما أُرسل فعلاً، ولا يرفع استثناءً.
    """
    today = today or now_utc()
    stamp = today.strftime("%Y-%m-%d")
    sent = 0
    for item in due_reminders(today, path):
        try:
            if api_guard.alert_once(
                    f"reminder:{item['id']}:{stamp}",
                    "📅 تذكير موعد\n" + reminder_line(item, fx_rate(path))):
                sent += 1
        except Exception as e:                   # pragma: no cover - دفاعي
            print("تعذر إرسال تذكير:", type(e).__name__)
    return sent


if __name__ == "__main__":
    print(reminder_lines() or "📅 لا مواعيد مستحقة اليوم.")
