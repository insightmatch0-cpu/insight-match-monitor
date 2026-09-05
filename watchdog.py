# -*- coding: utf-8 -*-
"""
حارس الجدولة — الحل الدائم لتخلف جدولة GitHub
------------------------------------------------
جدولة GitHub (cron) ليست مضمونة: قد تتأخر أو تُهمل تشغيلات كاملة في أوقات
الضغط (حدث فعلاً صباح 2026-07-14). هذا الحارس يعمل مع كل تشغيلة مراقبة
(3 مرات في الساعة) ويضمن ألا يضيع يوم توقعات أبداً:

- بعد 04:00 UTC: إذا لم يعمل المحرك 1 اليوم → يشغّله فوراً عبر gh CLI.
- بعد 04:30 UTC: إذا عمل المحرك 1 اليوم ولم يعمل المحرك 2 → يشغّل المحرك 2.
  (الترتيب مقصود: المحرك 2 يقرأ توقعات المحرك 1 للمقارنة في ملخصه.)
- عند أي تشغيل تلقائي يرسل إشعار تيليجرام حتى يعرف المستخدم ما حدث.

لا يستهلك أي نداء من API-Football. يحتاج صلاحية actions: write في monitor.yml
و GH_TOKEN (يُمرر تلقائياً من GITHUB_TOKEN — ليس سراً جديداً).
"""

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import requests

V1_FILE = Path("predictions.json")
V2_FILE = Path("predictions_v2.json")

# موعد الاستحقاق = الجدولة الأصلية (03:15 / 03:30 UTC) + مهلة سماح للتأخير العادي
V1_DUE_UTC = "04:00"
V2_DUE_UTC = "04:30"

V1_WORKFLOW = "predict.yml"
V2_WORKFLOW = "predict_v2.yml"

# 🔁 التعافي الذاتي (HOLD-013-1، قرار المالك 2026-09-05): إذا سجّلت آخر تشغيلة
# للمحرك 2 رفضاً من عائلة رصيد Anthropic (meta.claude_refused > 0) يعيد الحارس
# إطلاقها تلقائياً كل ساعتين — حتى 4 محاولات في اليوم بعد التشغيلة الصباحية —
# فتُكمَل الفجوة خلال ≤2 ساعة من التعبئة بدل انتظار أمر يدوي (5 ساعات يوم
# الحادثة). محاولة على رصيد ما زال فارغاً تفشل في أول دفعة وتصمت بتهدئة 6h —
# كلفتها صفر. مفتاح التراجع: V2_AUTO_RESUME = False.
V2_AUTO_RESUME = True
RESUME_GAP_HOURS = 2
RESUME_MAX_RUNS_PER_DAY = 5   # التشغيلة الصباحية + 4 محاولات

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()


def last_run_date(path: Path) -> str:
    """تاريخ آخر تشغيل ناجح (YYYY-MM-DD) من meta.last_run — فارغ إن لم يوجد."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ((data.get("meta") or {}).get("last_run") or "")[:10]
    except Exception:
        return ""


def decide(hhmm: str, today: str, v1_date: str, v2_date: str):
    """منطق القرار (نقي وقابل للاختبار): يرجع 'v1' أو 'v2' أو None."""
    if hhmm >= V1_DUE_UTC and v1_date != today:
        return "v1"
    if hhmm >= V2_DUE_UTC and v1_date == today and v2_date != today:
        return "v2"
    return None


def v2_refusal(path: Path = V2_FILE) -> dict:
    """{"refused": n, "at": iso} من meta المحرك 2 — صفر/فارغ إن لم توجد."""
    try:
        meta = json.loads(path.read_text(encoding="utf-8")).get("meta") or {}
        return {"refused": int(meta.get("claude_refused") or 0),
                "at": str(meta.get("claude_refused_at") or "")}
    except Exception:
        return {"refused": 0, "at": ""}


def resume_decide(refused: int, refused_at: str, today: str,
                  runs_today: int, last_created: str, now_iso: str) -> bool:
    """منطق التعافي الذاتي (نقي وقابل للاختبار): هل نعيد إطلاق المحرك 2؟
    - يوجد رفض رصيد مسجّل، ومن اليوم نفسه (رفض الأمس شأن تشغيلة اليوم الصباحية)
    - لم نستهلك حصة اليوم (الصباحية + 4 محاولات)
    - مرّت ساعتان على آخر تشغيلة (أياً كانت نتيجتها)"""
    if not V2_AUTO_RESUME or refused <= 0 or refused_at[:10] != today:
        return False
    if runs_today >= RESUME_MAX_RUNS_PER_DAY:
        return False
    try:
        last = datetime.fromisoformat(last_created.replace("Z", "+00:00"))
        now = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        if (now - last).total_seconds() < RESUME_GAP_HOURS * 3600:
            return False
    except Exception:
        pass   # لا قراءة لآخر تشغيلة = لا نمنع المحاولة
    return True


def fire(workflow: str) -> bool:
    """يشغّل الـ workflow عبر gh CLI (متوفر في بيئة Actions مع GH_TOKEN)."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    try:
        subprocess.run(
            ["gh", "workflow", "run", workflow, "--repo", repo],
            check=True, timeout=60,
        )
        print(f"الحارس: شغّل {workflow}")
        return True
    except Exception as e:
        # لا نطبع أي قيمة سرية — رسالة الخطأ من gh لا تتضمن التوكن
        print(f"الحارس: فشل تشغيل {workflow}:", e)
        return False


def recent_activity(workflow: str, cooldown_minutes: int = 25) -> dict:
    """يفحص آخر تشغيلات الـ workflow حتى لا يطرقه الحارس بلا توقف:
    - busy: يوجد تشغيل جارٍ/في الانتظار أو تشغيل أحدث من فترة التهدئة
      (بأي نتيجة — حتى الفاشل، كي لا نكرر محاولة فاشلة كل 10 دقائق).
    - tried_today: جرت محاولة اليوم (يمنع تكرار إشعار تيليجرام).
    عند أي فشل في الفحص نرجع قيماً متحفظة تسمح بالتشغيل."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    try:
        out = subprocess.run(
            ["gh", "run", "list", "--workflow", workflow, "--repo", repo,
             "--limit", "10", "--json", "createdAt,status"],
            check=True, timeout=60, capture_output=True, text=True,
        ).stdout
        runs = json.loads(out or "[]")
    except Exception as e:
        print(f"الحارس: تعذر فحص تشغيلات {workflow}:", e)
        return {"busy": False, "tried_today": False, "runs_today": 0, "last_created": ""}

    return summarize_runs(runs, datetime.now(timezone.utc), cooldown_minutes)


def summarize_runs(runs: list, now: datetime, cooldown_minutes: int = 25) -> dict:
    """يلخص قائمة تشغيلات gh (نقي): busy / tried_today / runs_today / last_created."""
    today = now.strftime("%Y-%m-%d")
    busy = False
    tried_today = False
    runs_today = 0
    last_created = ""
    for r in runs:
        if r.get("status") in ("queued", "in_progress"):
            busy = True
        created_raw = r.get("createdAt") or ""
        try:
            created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            if (now - created).total_seconds() < cooldown_minutes * 60:
                busy = True
        except Exception:
            pass
        if created_raw[:10] == today:
            tried_today = True
            runs_today += 1
        if created_raw > last_created:
            last_created = created_raw
    return {"busy": busy, "tried_today": tried_today,
            "runs_today": runs_today, "last_created": last_created}


def notify(text: str) -> None:
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=30,
        )
    except Exception as e:
        print("Telegram error:", e)


def main() -> None:
    now = datetime.now(timezone.utc)
    action = decide(
        now.strftime("%H:%M"),
        now.strftime("%Y-%m-%d"),
        last_run_date(V1_FILE),
        last_run_date(V2_FILE),
    )
    if action == "v1":
        act = recent_activity(V1_WORKFLOW)
        if act["busy"]:
            print("الحارس: توجد محاولة حديثة/جارية للمحرك 1 — انتظار (لا طرق متكرر).")
        elif fire(V1_WORKFLOW) and not act["tried_today"]:
            notify("⏰ جدولة GitHub تأخرت اليوم — شغّلت توقعات المحرك 1 تلقائياً الآن.")
    elif action == "v2":
        act = recent_activity(V2_WORKFLOW)
        if act["busy"]:
            print("الحارس: توجد محاولة حديثة/جارية للمحرك 2 — انتظار (لا طرق متكرر).")
        elif fire(V2_WORKFLOW) and not act["tried_today"]:
            notify("⏰ شغّلت توقعات المحرك 2 تلقائياً الآن (بعد اكتمال المحرك 1).")
    else:
        print("الحارس: الجدولة سليمة اليوم — لا إجراء.")
        maybe_resume_v2(now)


def maybe_resume_v2(now: datetime) -> bool:
    """🔁 التعافي الذاتي: يعيد إطلاق المحرك 2 إن سجّلت تشغيلته الأخيرة رفض
    رصيد اليوم — بفاصل ساعتين وحدّ 4 محاولات. يرجع True إن أطلق."""
    ref = v2_refusal()
    if not (V2_AUTO_RESUME and ref["refused"] > 0):
        return False
    act = recent_activity(V2_WORKFLOW)
    if act["busy"]:
        print("الحارس: تشغيلة المحرك 2 جارية/حديثة — لا تعافٍ الآن.")
        return False
    if not resume_decide(ref["refused"], ref["at"], now.strftime("%Y-%m-%d"),
                         act.get("runs_today", 0), act.get("last_created", ""),
                         now.isoformat()):
        print("الحارس: رفض رصيد مسجّل لكن شروط التعافي لم تتحقق بعد (فاصل/حصة).")
        return False
    if fire(V2_WORKFLOW):
        notify("🔁 تعافٍ ذاتي: أعدت إطلاق توقعات المحرك 2 لإكمال ما رفضه رصيد "
               f"Anthropic صباحاً ({ref['refused']} دفعة). إن كان الرصيد ما زال "
               "فارغاً تفشل المحاولة بصمت وتُعاد بعد ساعتين (حتى 4 محاولات).")
        return True
    return False


if __name__ == "__main__":
    main()
