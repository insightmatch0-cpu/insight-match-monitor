# -*- coding: utf-8 -*-
"""
الحارس الخارجي المستقل — مفتاح الرجل الميت
==========================================
سبب الوجود (حادثة الصمت 2026-08-14، السبب الجذري الثالث): فحص النزاهة كان
يعمل **داخل predict_v2.py نفسه**. حين مات المحرك مات حارسه معه، فلم يبقَ من
يقول "المحرك لم يعمل". حارس يسكن داخل ما يحرسه ليس حارساً.

هذا الملف مستقل تماماً:
- لا يستورد أي محرك ولا يعتمد على نجاحه.
- **صفر نداءات API** — يقرأ ملفاً على القرص فقط.
- يعمل داخل monitor.yml (كل 10 دقائق) — تشغيلة مستقلة عن المحركين، وهي
  الوحيدة التي تبقى حية حين يسقط المحركان.

المنطق: بعد 09:00 UTC (بعد موعد المحرك 2 بأكثر من أربع ساعات، وبعد أن يكون
حارس الجدولة watchdog.py قد استنفد محاولاته)، إن لم يتقدّم history.json اليوم
تُرسل رسالة واحدة: «⚠️ لم يجرِ التقييم الصباحي اليوم». مرة واحدة يومياً
(علم في state.json) — إنذار لا إغراق.

ملاحظة مهمة على قياس "التقدّم": مفاتيح history.json["days"] هي **تواريخ
المباريات** لا تواريخ التشغيل (تأتي من stats.daily المبنية على date الصف
المُقيَّم)، فقد لا يظهر مفتاح لليوم في يوم هادئ رغم أن التشغيلة جرت سليمة.
لذلك نقيس التقدّم بأحدث الاثنين: آخر مفتاح يوم **أو** meta.updated (طابع وقت
التشغيلة نفسها، يُكتب في كل تشغيلة ناجحة). هذا يمنع إنذاراً كاذباً كل يوم
هادئ، ويبقى وفياً للمقصد: هل جرى التقييم الصباحي فعلاً؟

مفتاح التراجع: DEADMAN_ENABLED=0 (يصمت الحارس تماماً).
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import api_guard

HISTORY_FILE = Path("history.json")

# لا ننذر قبل هذه الساعة: المحرك 2 مجدول 03:30 UTC وله تشغيلة احتياطية 04:30،
# وحارس الجدولة يطرق حتى ~05:00 — 09:00 تترك هامشاً واسعاً لأي تأخير طبيعي
DEADMAN_HOUR_UTC = "09:00"

TELEGRAM_TOKEN         = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID       = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_BROADCAST_IDS = os.environ.get("TELEGRAM_BROADCAST_IDS", "").strip()

ALERT_TEXT = (
    "⚠️ لم يجرِ التقييم الصباحي اليوم\n"
    "\n"
    "history.json لم يتقدّم منذ {last} — أي أن توقعات المحرك 2 الصباحية "
    "لم تُسجَّل، والأرجح أن التشغيلة فشلت أو لم تبدأ أصلاً.\n"
    "\n"
    "المطلوب منك: افتح صفحة Actions في المستودع وراجع آخر تشغيلة لـ "
    "predict_v2.yml — وإن كان السبب رفض API-Football فستجد رسالة منفصلة "
    "تشرحه.\n"
    "\n"
    "(هذا الحارس مستقل عن المحركين ولا يستهلك أي نداء API — لو كان موجوداً "
    "في 14 أغسطس لعرفتَ خلال ساعات لا بعد 19 ساعة.)"
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def history_progress(path: Path = None) -> str:
    """أحدث تقدّم مسجَّل في history.json بصيغة YYYY-MM-DD (فارغ إن تعذّر).

    أحدث الاثنين: آخر مفتاح يوم، أو تاريخ meta.updated — أيهما أحدث.
    ملف مفقود/تالف يرجع "" فيُعامل كتأخّر (وهو تأخّر فعلاً: لا سجل = لا تقييم).
    """
    try:
        hist = json.loads((path or HISTORY_FILE).read_text(encoding="utf-8"))
    except Exception:
        return ""
    days = [d for d in (hist.get("days") or {}) if isinstance(d, str)]
    latest_day = max(days) if days else ""
    updated = str(((hist.get("meta") or {}).get("updated") or ""))[:10]
    return max(latest_day, updated)


def should_fire(hhmm: str, today: str, progress: str, alerted_for: str) -> bool:
    """منطق القرار (نقي وقابل للاختبار — نفس عقيدة watchdog.decide).

    يطلق حين: مرّت 09:00 UTC + آخر تقدّم أقدم من اليوم + لم نُنذر اليوم بعد.
    """
    if hhmm < DEADMAN_HOUR_UTC:
        return False
    if progress >= today:
        return False
    return alerted_for != today


def main() -> None:
    if not api_guard._flag("DEADMAN_ENABLED"):
        print("الحارس الخارجي: مُعطَّل بمفتاح التراجع.")
        return
    now = now_utc()
    today = now.strftime("%Y-%m-%d")
    progress = history_progress()
    state = api_guard.load_state()
    alerted_for = (state.get("deadman") or {}).get("alerted_for", "")

    if not should_fire(now.strftime("%H:%M"), today, progress, alerted_for):
        print(f"الحارس الخارجي: لا إجراء (آخر تقدّم {progress or 'غير معروف'}).")
        return

    api_guard.send_telegram_multi(
        TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_BROADCAST_IDS,
        ALERT_TEXT.format(last=progress or "تاريخ غير معروف"),
    )
    # نعيد قراءة الحالة بعد الإرسال: طبقة التسليم كتبت مفتاح `delivery` في
    # الملف، ولو حفظنا فوق النسخة التي قرأناها قبل الإرسال لمحوناه — نفس فخ
    # الحالة المربوطة الموصوف في api_guard.py.
    state = api_guard.load_state()
    # العلم يُحفظ فوراً: لو ماتت التشغيلة بعد الإرسال مباشرة لتكرر الإنذار
    state.setdefault("deadman", {})["alerted_for"] = today
    api_guard.save_state(state)
    print(f"الحارس الخارجي: أُرسل إنذار التأخّر (آخر تقدّم {progress or '—'}).")

    # 🚨 خطوة deadman في monitor.yml محروسة بـ || echo فلا تُفشّل التشغيلة —
    # الطباعة الصاخبة هي المخرج هنا، والعلامة الحمراء تأتي من monitor.py
    # في نفس التشغيلة بعد دقائق. النداء موجود لاتساق كل المرسِلين.
    api_guard.exit_if_owner_unreachable()


if __name__ == "__main__":
    main()
