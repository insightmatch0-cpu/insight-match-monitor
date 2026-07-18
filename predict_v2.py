# -*- coding: utf-8 -*-
"""
المحرك 2 (V2) — توقعات ما قبل المباراة بجيل أذكى
--------------------------------------------------
نفس مباريات الـ 24 ساعة التي يتوقعها المحرك 1 (نفس الاختيار والاستبعادات)
حتى تكون المقارنة بين المحركين عادلة، مع ترقيات:

1) النموذج: claude-fable-5 (أقوى من نموذج المحرك 1).
2) لمباريات الدوريات الكبرى: سياق إضافي من API-Football قبل التوقع —
   ترتيب الفريقين، آخر 5 مواجهات مباشرة، آخر 5 نتائج لكل فريق، والإصابات.
3) المخرجات: احتمالات فوز/تعادل/خسارة مجموعها 100، والاختيار = الأعلى.
4) ذاكرة مستقلة predictions_v2.json + دروس من الأخطاء في lessons_v2.json
   (تُملأ في المرحلة 3) تُحقن في كل توقع جديد.

لا تكتب أي مفتاح داخل هذا الملف — كل المفاتيح في GitHub Secrets.
استهلاك API-Football: نداءان لجلب المباريات + ≤3 للتسوية
+ سياق الدوريات الكبرى (بحد أقصى ENRICH_CALL_BUDGET نداء) — بعيد جداً عن حد 7,500/يوم.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ================== المفاتيح (تُقرأ من GitHub Secrets) ==================
API_FOOTBALL_KEY  = os.environ.get("API_FOOTBALL_KEY", "").strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

# ================== الإعدادات ==================
PREDICTIONS_FILE      = Path("predictions_v2.json")   # ذاكرة المحرك 2 (مستقلة عن المحرك 1)
V1_PREDICTIONS_FILE   = Path("predictions.json")      # ذاكرة المحرك 1 (للمقارنة في الملخص فقط)
USER_PREDICTIONS_FILE = Path("predictions_user.json") # توقعات المالك (يسجلها عبر تيليجرام)
LESSONS_FILE          = Path("lessons_v2.json")       # دروس من الأخطاء (تُملأ في المرحلة 3)
HISTORY_FILE          = Path("history.json")          # الأرشيف الدائم: تقدم الجميع يوماً بيوم (لا يُقص أبداً)
NEWS_FILE             = Path("news.json")             # آخر عناوين الأخبار (سياق مشترك)

CLAUDE_MODEL = "claude-fable-5"

MAX_PREDICTIONS_24H   = 150   # نفس حد المحرك 1 — نفس المباريات. رُفع من 60 (المالك
                              # 2026-07-18): 60 كانت تغطي أبكر مباريات اليوم فقط
                              # وتقطع مباريات المساء الأوروبية، فتظهر حية بلا توقع
                              # (ولا "حماية" على اللوحة). الدوريات الكبرى مضمونة
                              # دائماً (ترتيب كبرى-أولاً)؛ الرفع يوسّع تغطية البقية.
MAX_RESOLVE_CALLS     = 3     # أقصى نداءات API لتسوية نتائج الأيام السابقة
# رصيد API-Football (خطة Pro: 7500/يوم) مدفوع مسبقاً — نستخدمه بسخاء:
# كل المباريات تأخذ سياقاً إضافياً (الكبرى أولاً لأن القائمة مرتبة كبرى-أولاً)
MAX_ENRICHED_FIXTURES = 60    # كل مباريات اليوم (كانت 15 للكبرى فقط)
ENRICH_CALL_BUDGET    = 750   # سقف أمان لنداءات السياق الإضافي (~505 متوقعة مع الأودز والمدربين)
ENRICHED_BATCH_SIZE   = 4     # دفعات صغيرة للمباريات ذات السياق الغني
BASIC_BATCH_SIZE      = 12    # دفعات المباريات بدون سياق (مثل المحرك 1)
# توفير التكلفة (توجيه المالك 2026-07-17): السياق الغني (والنموذج المكلف عليه)
# للدوريات الكبرى فقط — بقية المباريات تُتوقّع بالنمط الخفيف. كل المباريات
# تبقى مُتوقَّعة ومُقيَّمة (التغطية كاملة، الدماغ والدروس لا يتأثران). للرجوع
# الفوري إلى تغطية غنية للجميع: اجعل هذه القيمة False.
ENRICH_TOP_ONLY       = True
# حارس مباريات الكأس/الإقصاء (توجيه المالك 2026-07-18): التعثّر الوحيد في خانة
# الثقة العالية (72% على كيري×شيلبورن في كأس أيرلندا انتهت 2-2) كان مباراة كأس.
# الفرق الصغيرة على أرضها تفاجئ الكبار بانتظام في الكأس، لذا نُلزم النموذج
# بحدّ أدنى للتعادل ونُسقّف الثقة حتى لا تدخل تخمينات الكأس خانة 70%+ العالية.
# لا يغيّر الطرف المُختار، ولا يمسّ التعلّم. للتعطيل الفوري: False.
CUP_GUARDRAIL         = True
CUP_CONF_CAP          = 65    # أقصى ثقة مسموحة في مباريات الكأس/الإقصاء
CUP_MIN_DRAW          = 25    # أدنى احتمال تعادل نفرضه في مباريات الكأس/الإقصاء
MAX_LESSONS_IN_PROMPT = 15    # أحدث الدروس التي تُحقن في كل توقع
MAX_LESSONS_STORED    = 100   # أقصى دروس محفوظة في lessons_v2.json
MAX_MISTAKES_PER_RUN  = 30    # كل أخطاء اليوم عملياً تُراجع لاستخلاص الدروس (نداء واحد)
CONSOLIDATE_THRESHOLD = 60    # عند تجاوز هذا العدد تُدمج الدروس المتشابهة
CONSOLIDATE_TARGET    = 30    # عدد المبادئ المركزة بعد الدمج

# قاعدة الحكام الذاتية (خطوة استكشاف 6): تتراكم من المباريات المُقيَّمة —
# معدل بطاقات الحكم يغذي تقارير ما قبل المباراة (بعض الحكام يشهرون بغزارة)
REFEREES_FILE = Path("referees.json")

# التقييم الذاتي لتقارير ما قبل المباراة (يكتبها monitor.py في scenarios_v2.json)
SCENARIOS_FILE = Path("scenarios_v2.json")
MAX_SCENARIO_GRADES_PER_RUN = 6    # نداء Claude لكل تقرير — قائمة التركيز صغيرة أصلاً
SCENARIO_MAX_AGE_DAYS = 4          # تقرير بلا بيانات نهائية بعد 4 أيام يُسقط (مؤجلة/ملغاة)
SCENARIOS_RESOLVED_CAP = 100

SEND_TELEGRAM_DIGEST = True
DIGEST_TOP_ONLY      = True
DASHBOARD_URL = "https://insightmatch0-cpu.github.io/insight-match-monitor/"

TOP_LEAGUE_IDS = {
    1, 2, 3, 4, 9, 13, 15, 39, 40, 61, 71, 78, 88, 94, 128, 135, 140, 253, 307, 417, 542,
}

EXCLUDED_COUNTRIES = {
    "india", "pakistan", "bangladesh",
    "algeria", "angola", "benin", "botswana", "burkina faso", "burkina-faso",
    "burundi", "cameroon", "cape verde", "cape-verde",
    "central african republic", "central-african-republic", "chad", "comoros",
    "congo", "congo dr", "congo-dr", "dr congo", "djibouti", "egypt",
    "equatorial guinea", "equatorial-guinea", "eritrea", "eswatini",
    "ethiopia", "gabon", "gambia", "ghana", "guinea", "guinea-bissau",
    "ivory coast", "ivory-coast", "kenya", "lesotho", "liberia", "libya",
    "madagascar", "malawi", "mali", "mauritania", "mauritius", "morocco",
    "mozambique", "namibia", "niger", "nigeria", "rwanda",
    "sao tome and principe", "sao-tome-and-principe", "senegal", "seychelles",
    "sierra leone", "sierra-leone", "somalia", "south africa", "south-africa",
    "south sudan", "south-sudan", "sudan", "tanzania", "togo", "tunisia",
    "uganda", "zambia", "zimbabwe",
}
EXCLUDED_LEAGUE_KEYWORDS = [
    "friendl", "caf ", "africa", "afcon",
    # بيانات لا نبني عليها التعلم (توجيه المالك 2026-07-18): دوريات السيدات
    # والفئات السنية والرديف — ضجيج يبطئ بناء دماغ موثوق للموسم
    "women", "femen", "femin", "frauen", "ladies", "wsl", "girls",
    "u16", "u17", "u18", "u19", "u20", "u21", "u23",
    "youth", "primavera", "juvenil", "junioren", "reserve", "reserva",
    "academy",
]

FINAL_STATUSES = {"FT", "AET", "PEN"}
DEAD_STATUSES  = {"PST", "CANC", "ABD", "AWD", "WO"}


# ================== أدوات مساعدة ==================
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def is_excluded(league: dict) -> bool:
    country = (league.get("country") or "").strip().lower()
    name = (league.get("name") or "").strip().lower()
    if country in EXCLUDED_COUNTRIES:
        return True
    return any(kw in name for kw in EXCLUDED_LEAGUE_KEYWORDS)


def api_football(path: str) -> list:
    resp = requests.get(
        f"https://v3.football.api-sports.io/{path}",
        headers={"x-apisports-key": API_FOOTBALL_KEY},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    errs = data.get("errors")
    if errs and (not isinstance(errs, list) or len(errs) > 0):
        raise RuntimeError(f"API-Football رفض الطلب: {errs}")
    return data.get("response", [])


def send_telegram(text: str) -> None:
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=30,
        )
    except Exception as e:
        print("Telegram error:", e)


def send_telegram_long(text: str) -> None:
    """يقسم الرسائل الطويلة (حد تيليجرام 4096 حرفاً)."""
    chunk = []
    size = 0
    for line in text.splitlines():
        if size + len(line) + 1 > 3800 and chunk:
            send_telegram("\n".join(chunk))
            chunk, size = [], 0
        chunk.append(line)
        size += len(line) + 1
    if chunk:
        send_telegram("\n".join(chunk))


# ================== سجل الدقة (التعلم الذاتي — مطابق للمحرك 1) ==================
def outcome_from_score(gh: int, ga: int) -> str:
    if gh > ga:
        return "home"
    if ga > gh:
        return "away"
    return "draw"


def compute_stats(resolved: list) -> dict:
    """يحسب دقة المحرك 2: إجمالي، آخر 30 يوماً، حسب مستوى الثقة، وحسب نوع الدوري."""
    def bucket(conf):
        if conf >= 70:
            return "70+"
        if conf >= 60:
            return "60-69"
        if conf >= 50:
            return "50-59"
        return "<50"

    stats = {
        "overall": {"correct": 0, "total": 0},
        "last30":  {"correct": 0, "total": 0},
        "top_leagues": {"correct": 0, "total": 0},
        "other_leagues": {"correct": 0, "total": 0},
        "by_confidence": {
            "70+": {"correct": 0, "total": 0},
            "60-69": {"correct": 0, "total": 0},
            "50-59": {"correct": 0, "total": 0},
            "<50": {"correct": 0, "total": 0},
        },
        "daily": {},
    }
    cutoff = (now_utc() - timedelta(days=30)).strftime("%Y-%m-%d")
    for r in resolved:
        ok = 1 if r.get("correct") else 0
        stats["overall"]["total"] += 1
        stats["overall"]["correct"] += ok
        if r.get("date", "") >= cutoff:
            stats["last30"]["total"] += 1
            stats["last30"]["correct"] += ok
        key = "top_leagues" if r.get("top") else "other_leagues"
        stats[key]["total"] += 1
        stats[key]["correct"] += ok
        b = bucket(int(r.get("confidence", 0)))
        stats["by_confidence"][b]["total"] += 1
        stats["by_confidence"][b]["correct"] += ok
        d = stats["daily"].setdefault(r.get("date", "?"), {"correct": 0, "total": 0})
        d["total"] += 1
        d["correct"] += ok
    stats["daily"] = dict(sorted(stats["daily"].items())[-30:])
    return stats


def pct(d: dict) -> str:
    if not d.get("total"):
        return "لا يوجد سجل بعد"
    return f"{round(100 * d['correct'] / d['total'])}% ({d['correct']}/{d['total']})"


def resolve_pending(store: dict):
    """يتحقق من نتائج التوقعات المنتظرة ويحوّل ما انتهى إلى سجل الدقة.
    يرجع (عدد المُسوَّى، قائمة المُسوَّى حديثاً) — القائمة تُستخدم لاستخلاص الدروس."""
    pending = store.get("pending", {})
    if not pending:
        return 0, []

    today = now_utc().strftime("%Y-%m-%d")
    dates = sorted({p.get("date", "") for p in pending.values() if p.get("date", "") <= today})
    dates = [d for d in dates if d][-MAX_RESOLVE_CALLS:]

    finals = {}
    for d in dates:
        try:
            for fx in api_football(f"fixtures?date={d}"):
                fid = str((fx.get("fixture") or {}).get("id"))
                status = (((fx.get("fixture") or {}).get("status")) or {}).get("short") or ""
                goals = fx.get("goals") or {}
                teams = fx.get("teams") or {}
                # عرف التوقعات العالمي: النتيجة بعد 90 دقيقة (score.fulltime) —
                # مباراة محسومة بالأشواط الإضافية تُقيَّم على نتيجة الوقت الأصلي
                ft = ((fx.get("score") or {}).get("fulltime")) or {}
                gh = ft.get("home") if ft.get("home") is not None else goals.get("home")
                ga = ft.get("away") if ft.get("away") is not None else goals.get("away")
                logos = {
                    "home_logo": (teams.get("home") or {}).get("logo", ""),
                    "away_logo": (teams.get("away") or {}).get("logo", ""),
                    "league_logo": (fx.get("league") or {}).get("logo", ""),
                }
                finals[fid] = (status, gh, ga, logos)
        except Exception as e:
            print(f"فشل سحب نتائج {d}:", e)

    resolved_now = 0
    newly_resolved = []
    drop_before = (now_utc() - timedelta(days=3)).strftime("%Y-%m-%d")
    for fid in list(pending.keys()):
        p = pending[fid]
        status, gh, ga, logos = finals.get(fid, ("", None, None, {}))
        if status in FINAL_STATUSES and gh is not None and ga is not None:
            actual = outcome_from_score(int(gh), int(ga))
            entry = {
                "fid": fid,
                "date": p.get("date"),
                "home": p.get("home"), "away": p.get("away"),
                "ar_home": p.get("ar_home"), "ar_away": p.get("ar_away"),
                "home_logo": p.get("home_logo") or logos.get("home_logo", ""),
                "away_logo": p.get("away_logo") or logos.get("away_logo", ""),
                "league_logo": p.get("league_logo") or logos.get("league_logo", ""),
                "league": p.get("league"), "ar_league": p.get("ar_league"),
                "top": p.get("top", False),
                "pick": p.get("pick"),
                "confidence": p.get("confidence"),
                "prob_home": p.get("prob_home"),
                "prob_draw": p.get("prob_draw"),
                "prob_away": p.get("prob_away"),
                "actual": actual,
                "score": f"{gh}-{ga}",
                "correct": p.get("pick") == actual,
            }
            store.setdefault("resolved", []).append(entry)
            newly_resolved.append(entry)
            del pending[fid]
            resolved_now += 1
        elif status in DEAD_STATUSES or (p.get("date", "") < drop_before):
            del pending[fid]

    store["resolved"] = store.get("resolved", [])[-1000:]
    return resolved_now, newly_resolved


# ================== المرحلة 3: التعلم من الأخطاء ==================
def claude_request(system_prompt: str, user_text: str, max_tokens: int = 2000) -> str:
    """نداء Claude عام يرجع النص فقط (فارغ عند الفشل — لا يوقف التشغيلة)."""
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_text}],
            },
            timeout=180,
        )
        r.raise_for_status()
        return "".join(
            b.get("text", "")
            for b in r.json().get("content", [])
            if b.get("type") == "text"
        ).strip()
    except Exception as e:
        detail = ""
        resp = getattr(e, "response", None)
        if resp is not None:
            try: detail = " — " + resp.text[:300]
            except Exception: pass
        print(f"Claude error: {e}{detail}")
        return ""


def parse_json_array(text: str) -> list:
    """يستخرج مصفوفة JSON من رد Claude بتسامح (أسوار، نص زائد)."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    if not text.startswith("["):
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if not m:
            return []
        text = m.group(0)
    try:
        items = json.loads(text)
    except Exception as e:
        print("JSON parse error:", e)
        return []
    return items if isinstance(items, list) else []


def generate_lessons(newly_resolved: list) -> int:
    """يستخلص درساً قابلاً للتطبيق من كل توقع خاطئ ويضيفه إلى lessons_v2.json.
    الدروس الأحدث تُحقن تلقائياً في كل توقع قادم (lessons_text)."""
    mistakes = [r for r in newly_resolved if not r.get("correct")][:MAX_MISTAKES_PER_RUN]
    if not mistakes:
        return 0

    payload = [
        {
            "match": f"{r.get('home')} vs {r.get('away')}",
            "league": r.get("league"),
            "my_pick": r.get("pick"),
            "my_probs_home_draw_away":
                f"{r.get('prob_home', '?')}/{r.get('prob_draw', '?')}/{r.get('prob_away', '?')}",
            "confidence": r.get("confidence"),
            "actual_outcome": r.get("actual"),
            "final_score": r.get("score"),
            "top_league": bool(r.get("top")),
        }
        for r in mistakes
    ]
    system_prompt = (
        "أنت المراجع الذاتي لمحرك توقعات كرة قدم. ستصلك توقعات خاطئة من الأمس "
        "مع النتائج الفعلية.\n"
        "استخلص من كل توقع خاطئ درساً واحداً قصيراً وقابلاً للتطبيق في توقعات "
        "قادمة: نمط عام يجب الانتباه له (مثل المبالغة في قوة صاحب الأرض، أو تجاهل "
        "احتمال التعادل بين متقاربين) — وليس مجرد وصف لما حدث في تلك المباراة.\n"
        "أرجع ردك بصيغة JSON فقط — مصفوفة واحدة بدون أي نص قبلها أو بعدها وبدون ```:\n"
        '[{"match":"...","lesson":"درس من سطر واحد بالعربي"}]\n'
        "استخدم الأرقام الإنجليزية (0-9) فقط ولا تستخدم الأرقام العربية (٠-٩) أبداً."
    )
    # حتى 30 درساً عربياً في رد واحد — يلزم سقف إخراج واسع وإلا يُقص الرد ويفشل التحليل
    raw = claude_request(system_prompt, json.dumps(payload, ensure_ascii=False),
                         max_tokens=6000)
    items = parse_json_array(raw)
    if not items:
        print("لم تُستخلص دروس (رد فارغ أو غير صالح) — أخطاء اليوم تبقى في السجل.")
        return 0

    data = load_json(LESSONS_FILE, {"lessons": []})
    data.setdefault("lessons", [])
    today = now_utc().strftime("%Y-%m-%d")
    added = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        lesson = str(it.get("lesson") or "").strip()
        if not lesson:
            continue
        data["lessons"].append({
            "date": today,
            "match": str(it.get("match") or "").strip(),
            "text": lesson,
        })
        added += 1
    if added:
        data["lessons"] = data["lessons"][-MAX_LESSONS_STORED:]
        save_json(LESSONS_FILE, data)
    return added


def record_referee(name: str, yellows: int, reds: int) -> None:
    """يراكم سجل الحكم في referees.json — قاعدة بيانات ذاتية تنمو مع كل
    مباراة مُقيَّمة (لا يوجد مصدر مجاني لإحصائيات الحكام — نبنيها بأنفسنا)."""
    name = (name or "").strip()
    if not name:
        return
    db = load_json(REFEREES_FILE, {})
    rec = db.get(name) or {"matches": 0, "yellows": 0, "reds": 0}
    rec["matches"] += 1
    rec["yellows"] += max(0, yellows)
    rec["reds"] += max(0, reds)
    db[name] = rec
    save_json(REFEREES_FILE, db)


def actual_match_data(fid: str) -> str:
    """البيانات النهائية الحقيقية لمباراة منتهية: النتيجة + الإحصائيات
    (ركنيات، تسديدات، بطاقات، تصديات) + الأحداث (المسجلون والبطاقات بالأسماء).
    ترجع '' إذا لم تنته المباراة بعد. 3 نداءات API — الرصيد مدفوع مسبقاً.
    أثر جانبي مقصود: تحديث قاعدة الحكام من نفس البيانات (بلا نداء إضافي)."""
    parts = []
    referee = ""
    try:
        fx = api_football(f"fixtures?ids={fid}")
        if not fx:
            return ""
        status = (((fx[0].get("fixture") or {}).get("status")) or {}).get("short")
        if status not in ("FT", "AET", "PEN"):
            return ""
        referee = ((fx[0].get("fixture") or {}).get("referee")) or ""
        goals = fx[0].get("goals") or {}
        ft = (fx[0].get("score") or {}).get("fulltime") or {}
        gh = ft.get("home") if ft.get("home") is not None else goals.get("home")
        ga = ft.get("away") if ft.get("away") is not None else goals.get("away")
        parts.append(f"النتيجة النهائية (90 دقيقة): {gh}-{ga} — الحالة {status}")
        if referee:
            parts.append(f"الحكم: {referee}")
    except Exception as e:
        print("تقييم التقرير — فشل جلب النتيجة:", e)
        return ""
    yellows = reds = 0
    try:
        for side in api_football(f"fixtures/statistics?fixture={fid}"):
            name = (side.get("team") or {}).get("name", "?")
            vals = []
            for s in (side.get("statistics") or []):
                if s.get("value") is None:
                    continue
                vals.append(f"{s.get('type')}: {s.get('value')}")
                try:
                    if s.get("type") == "Yellow Cards":
                        yellows += int(s.get("value") or 0)
                    elif s.get("type") == "Red Cards":
                        reds += int(s.get("value") or 0)
                except Exception:
                    pass
            if vals:
                parts.append(f"إحصائيات {name} — " + ", ".join(vals))
    except Exception as e:
        print("تقييم التقرير — فشل الإحصائيات:", e)
    if referee:
        record_referee(referee, yellows, reds)
    try:
        ev_lines = []
        for ev in api_football(f"fixtures/events?fixture={fid}"):
            minute = (ev.get("time") or {}).get("elapsed")
            team = (ev.get("team") or {}).get("name", "?")
            player = (ev.get("player") or {}).get("name") or ""
            ev_lines.append(f"{minute}' {ev.get('type')} ({ev.get('detail')}) "
                            f"{player} [{team}]")
        if ev_lines:
            parts.append("الأحداث:\n" + "\n".join(ev_lines))
    except Exception as e:
        print("تقييم التقرير — فشل الأحداث:", e)
    return "\n".join(parts)


def grade_scenario_report(entry: dict, actual: str) -> dict:
    """نداء Claude واحد: يقارن بنود التقرير المتوقعة بالبيانات النهائية،
    يرجع {'summary','grades':[{'claim','result'}],'lessons':[...]} أو {} عند الفشل."""
    system_prompt = (
        "أنت المقيّم الذاتي لتقارير ما قبل المباراة في محرك توقعات كرة قدم. "
        "ستصلك بنود تقرير كتبته قبل المباراة (نتيجة متوقعة، كلا الفريقين يسجلان، "
        "إجمالي الأهداف، مسجل محتمل، ركنيات، بطاقات، كرات ثابتة، نمط الشوطين) "
        "والبيانات النهائية الحقيقية.\n"
        "قيّم كل بند تحقق منه البيانات وأرجع JSON فقط بدون أي نص آخر وبدون ```:\n"
        '{"summary":"سطر واحد: كم أصاب التقرير من بنوده",'
        '"grades":[{"claim":"البند باختصار","result":"صح|خطأ|جزئي"}],'
        '"lessons":[{"lesson":"درس عام قابل للتطبيق في تقارير قادمة — سطر واحد"}]}\n'
        "الدروس تُستخلص من البنود الخاطئة فقط: نمط عام (مثل المبالغة في توقع "
        "الركنيات في المباريات المغلقة) وليس وصفاً لما حدث. إن لم توجد أخطاء "
        "أرجع lessons فارغة. استخدم الأرقام الإنجليزية (0-9) فقط."
    )
    user_text = json.dumps(
        {"match": f"{entry.get('home')} vs {entry.get('away')}",
         "league": entry.get("league"),
         "prematch_report": entry.get("report", ""),
         "actual_final_data": actual},
        ensure_ascii=False,
    )
    raw = claude_request(system_prompt, user_text, max_tokens=2500)
    if not raw:
        return {}
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except Exception:
        return {}
    return data if isinstance(data, dict) and data.get("grades") else {}


def resolve_scenarios() -> int:
    """حلقة التعلم الذاتي للسيناريوهات: يقيّم كل تقرير ما قبل مباراة محفوظ
    مقابل البيانات النهائية، يرسل بطاقة التقييم للمالك، ويضيف الدروس إلى
    lessons_v2.json (فتُحقن تلقائياً في كل تقرير وتوقع قادم)."""
    scen = load_json(SCENARIOS_FILE, {"pending": {}, "resolved": []})
    scen.setdefault("pending", {})
    scen.setdefault("resolved", [])
    if not scen["pending"]:
        return 0
    icon = {"صح": "✅", "خطأ": "❌", "جزئي": "🟡"}
    graded = 0
    dirty = False
    today = now_utc().strftime("%Y-%m-%d")
    for fid in sorted(list(scen["pending"].keys())):
        if graded >= MAX_SCENARIO_GRADES_PER_RUN:
            break
        entry = scen["pending"][fid]
        try:
            kickoff = datetime.fromisoformat(entry.get("kickoff", ""))
        except Exception:
            kickoff = None
        if kickoff and kickoff > now_utc() - timedelta(hours=3):
            continue                              # لم تنته بعد — دورها لاحقاً
        actual = actual_match_data(fid)
        if not actual:
            # لا بيانات نهائية: مؤجلة/ملغاة أو خلل — نسقطها بعد مهلة
            age_ok = (entry.get("date") or "9999") >= \
                (now_utc() - timedelta(days=SCENARIO_MAX_AGE_DAYS)).strftime("%Y-%m-%d")
            if not age_ok:
                del scen["pending"][fid]
                dirty = True
            continue
        result = grade_scenario_report(entry, actual)
        if not result:
            continue                              # فشل التقييم — إعادة غداً
        graded += 1
        dirty = True
        grades = [g for g in result.get("grades", []) if isinstance(g, dict)]
        correct = sum(1 for g in grades if g.get("result") == "صح")
        partial = sum(1 for g in grades if g.get("result") == "جزئي")
        h = entry.get("ar_home") or entry.get("home", "?")
        a = entry.get("ar_away") or entry.get("away", "?")
        shadow_tag = " (تقرير ظل — تدريب ذاتي)" if entry.get("shadow") else ""
        lines = [f"📋 تقييم تقرير المحرك 2{shadow_tag} — {h} 🆚 {a}",
                 f"📊 أصاب {correct}/{len(grades)}"
                 + (f" (+{partial} جزئياً)" if partial else "")]
        if result.get("summary"):
            lines.append(str(result["summary"]))
        for g in grades:
            lines.append(f"{icon.get(g.get('result'), '•')} {g.get('claim', '')}")
        # الدروس → نفس دفتر دروس المحرك 2 (يُحقن في التوقعات والتقارير القادمة)
        lessons = [str((it or {}).get("lesson") or "").strip()
                   for it in result.get("lessons", []) if isinstance(it, dict)]
        lessons = [x for x in lessons if x]
        if lessons:
            ldata = load_json(LESSONS_FILE, {"lessons": []})
            ldata.setdefault("lessons", [])
            for text in lessons:
                ldata["lessons"].append({
                    "date": today,
                    "match": f"{entry.get('home')} vs {entry.get('away')} (تقرير)",
                    "text": text,
                })
            ldata["lessons"] = ldata["lessons"][-MAX_LESSONS_STORED:]
            save_json(LESSONS_FILE, ldata)
            lines.append(f"📚 دروس جديدة من هذا التقرير: {len(lessons)}")
        send_telegram_long("\n".join(lines))
        entry["graded_on"] = today
        entry["correct"] = correct
        entry["total"] = len(grades)
        scen["resolved"].append(entry)
        del scen["pending"][fid]
    if dirty:
        scen["resolved"] = scen["resolved"][-SCENARIOS_RESOLVED_CAP:]
        save_json(SCENARIOS_FILE, scen)
    return graded


def consolidate_lessons() -> int:
    """عندما يتضخم دفتر الدروس، يدمج Claude الدروس المتشابهة في مبادئ عامة أقوى
    وأقل عدداً — فتبقى الدروس المحقونة في كل توقع مركزة بلا تكرار.
    يرجع عدد المبادئ بعد الدمج (0 = لم يحدث دمج)."""
    data = load_json(LESSONS_FILE, {"lessons": []})
    lessons = data.get("lessons") or []
    if len(lessons) <= CONSOLIDATE_THRESHOLD:
        return 0

    texts = []
    for it in lessons:
        t = it if isinstance(it, str) else (it.get("text") or "")
        t = str(t).strip()
        if t:
            texts.append(t)

    system_prompt = (
        "أنت محرر معرفة لمحرك توقعات كرة قدم. ستصلك قائمة دروس مستخلصة من أخطاء "
        f"سابقة، كثير منها متشابه أو متكرر. ادمجها في {CONSOLIDATE_TARGET} مبدأً "
        "عاماً أو أقل: اجمع المتشابه في مبدأ واحد أقوى وأوضح، واحذف المكرر، "
        "وحافظ على أي درس فريد مهم.\n"
        "أرجع ردك بصيغة JSON فقط — مصفوفة نصوص بدون أي شيء آخر وبدون ```:\n"
        '["مبدأ عام بالعربي من سطر واحد", ...]\n'
        "استخدم الأرقام الإنجليزية (0-9) فقط ولا تستخدم الأرقام العربية (٠-٩) أبداً."
    )
    raw = claude_request(system_prompt, json.dumps(texts, ensure_ascii=False), max_tokens=3000)
    items = parse_json_array(raw)
    principles = [str(x).strip() for x in items if str(x).strip() and isinstance(x, (str,))]
    if not principles:
        return 0   # فشل الدمج → نبقي الدروس كما هي (لا نخسر شيئاً أبداً)

    today = now_utc().strftime("%Y-%m-%d")
    data["lessons"] = [
        {"date": today, "match": "خلاصة مُجمّعة", "text": t}
        for t in principles[:CONSOLIDATE_TARGET]
    ]
    save_json(LESSONS_FILE, data)
    return len(data["lessons"])


# ================== سحب مباريات الـ 24 ساعة القادمة (مطابق للمحرك 1) ==================
def get_upcoming_24h() -> list:
    start = now_utc()
    end = start + timedelta(hours=24)
    days = {start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")}

    matches = []
    fetch_errors = []
    for d in sorted(days):
        try:
            matches.extend(api_football(f"fixtures?date={d}"))
        except Exception as e:
            print(f"فشل سحب مباريات {d}:", e)
            fetch_errors.append(str(e))
    if fetch_errors and not matches:
        raise RuntimeError(
            "تعذر سحب أي مباريات — غالباً مفتاح API_FOOTBALL_KEY في Secrets "
            "قديم أو خاطئ. آخر خطأ: " + fetch_errors[-1]
        )

    out = []
    seen = set()
    for fx in matches:
        fixture = fx.get("fixture") or {}
        league = fx.get("league") or {}
        teams = fx.get("teams") or {}
        fid = str(fixture.get("id"))
        if fid in seen:
            continue
        seen.add(fid)
        status = ((fixture.get("status") or {}).get("short")) or ""
        if status != "NS":
            continue
        if is_excluded(league):
            continue
        try:
            kickoff = datetime.fromisoformat(fixture.get("date"))
        except Exception:
            continue
        if not (start <= kickoff <= end):
            continue
        out.append({
            "fid": fid,
            "kickoff": kickoff.isoformat(),
            "date": kickoff.strftime("%Y-%m-%d"),
            "home": (teams.get("home") or {}).get("name", "?"),
            "away": (teams.get("away") or {}).get("name", "?"),
            "home_id": (teams.get("home") or {}).get("id"),
            "away_id": (teams.get("away") or {}).get("id"),
            "home_logo": (teams.get("home") or {}).get("logo", ""),
            "away_logo": (teams.get("away") or {}).get("logo", ""),
            "league_logo": league.get("logo", ""),
            "league": f"{league.get('name', '?')} ({league.get('country', '?')})",
            "league_id": league.get("id"),
            "season": league.get("season"),
            "round": league.get("round") or "",
            "venue": ", ".join(x for x in (
                ((fixture.get("venue") or {}).get("name")),
                ((fixture.get("venue") or {}).get("city")),
                league.get("country"),
            ) if x),
            "top": league.get("id") in TOP_LEAGUE_IDS,
            "is_cup": is_cup_fixture(league.get("name"), league.get("round")),
        })

    out.sort(key=lambda m: (not m["top"], m["kickoff"]))
    return out[:MAX_PREDICTIONS_24H]


# ================== السياق الإضافي للدوريات الكبرى ==================
def _enrich_call(path: str, budget: dict) -> list:
    """نداء API ضمن سقف الأمان — يرجع [] عند تجاوز السقف أو أي فشل."""
    if budget["used"] >= ENRICH_CALL_BUDGET:
        return []
    budget["used"] += 1
    try:
        return api_football(path)
    except Exception as e:
        print(f"فشل نداء السياق {path}:", e)
        return []


def standings_context(m: dict, budget: dict, cache: dict) -> str:
    """ترتيب الفريقين في الدوري (نداء واحد لكل دوري، يُخزّن مؤقتاً)."""
    league_id, season = m.get("league_id"), m.get("season")
    if not league_id or not season:
        return ""
    key = f"{league_id}-{season}"
    if key not in cache:
        rows = {}
        for entry in _enrich_call(f"standings?league={league_id}&season={season}", budget):
            for group in ((entry.get("league") or {}).get("standings") or []):
                for row in group:
                    tid = ((row.get("team") or {}).get("id"))
                    if tid:
                        rows[tid] = row
        cache[key] = rows
    rows = cache[key]
    parts = []
    for side, tid in (("home", m.get("home_id")), ("away", m.get("away_id"))):
        row = rows.get(tid)
        if not row:
            continue
        allg = (row.get("all") or {})
        goals = (allg.get("goals") or {})
        parts.append(
            f"{m[side]}: rank {row.get('rank')}, {row.get('points')} pts, "
            f"played {allg.get('played')}, GF {goals.get('for')} GA {goals.get('against')}, "
            f"form {row.get('form') or '?'}"
        )
    return ("Standings — " + " | ".join(parts)) if parts else ""


def h2h_context(m: dict, budget: dict) -> str:
    """آخر 5 مواجهات مباشرة بين الفريقين."""
    hid, aid = m.get("home_id"), m.get("away_id")
    if not hid or not aid:
        return ""
    lines = []
    for fx in _enrich_call(f"fixtures/headtohead?h2h={hid}-{aid}&last=5", budget):
        teams = fx.get("teams") or {}
        goals = fx.get("goals") or {}
        date = (((fx.get("fixture") or {}).get("date")) or "")[:10]
        gh, ga = goals.get("home"), goals.get("away")
        if gh is None or ga is None:
            continue
        lines.append(
            f"{date}: {(teams.get('home') or {}).get('name', '?')} {gh}-{ga} "
            f"{(teams.get('away') or {}).get('name', '?')}"
        )
    return ("Head-to-head (last 5): " + "; ".join(lines)) if lines else ""


def form_context(team_id, team_name: str, budget: dict) -> str:
    """آخر 5 نتائج للفريق + أيام الراحة منذ آخر مباراة (من نفس النداء —
    الإرهاق وضغط الجدول عامل حقيقي، خطوة استكشاف البيانات 2)."""
    if not team_id:
        return ""
    lines = []
    last_dates = []
    for fx in _enrich_call(f"fixtures?team={team_id}&last=5", budget):
        teams = fx.get("teams") or {}
        goals = fx.get("goals") or {}
        try:
            last_dates.append(datetime.fromisoformat(
                ((fx.get("fixture") or {}).get("date") or "").replace("Z", "+00:00")))
        except Exception:
            pass
        gh, ga = goals.get("home"), goals.get("away")
        if gh is None or ga is None:
            continue
        home = (teams.get("home") or {})
        away = (teams.get("away") or {})
        at_home = home.get("id") == team_id
        mine, theirs = (gh, ga) if at_home else (ga, gh)
        opp = (away if at_home else home).get("name", "?")
        letter = "W" if mine > theirs else ("L" if mine < theirs else "D")
        lines.append(f"{letter} {mine}-{theirs} v {opp} ({'H' if at_home else 'A'})")
    if not lines:
        return ""
    rest = ""
    if last_dates:
        days = max(0, (now_utc() - max(last_dates)).days)
        rest = f" — أيام الراحة منذ آخر مباراة: {days}"
    return f"{team_name} last 5: " + ", ".join(lines) + rest


def injuries_context(m: dict, budget: dict) -> str:
    """الإصابات والغيابات المعلنة لهذه المباراة."""
    lines = []
    for item in _enrich_call(f"injuries?fixture={m['fid']}", budget)[:12]:
        player = (item.get("player") or {})
        team = (item.get("team") or {}).get("name", "?")
        name = player.get("name", "?")
        reason = player.get("reason") or player.get("type") or "?"
        lines.append(f"{name} ({team}: {reason})")
    return ("Injuries/absences: " + "; ".join(lines)) if lines else ""


def odds_context(m: dict, budget: dict) -> str:
    """أودز السوق (إجماع المراهنين) لنتيجة المباراة، مع الاحتمالات الضمنية
    بعد إزالة هامش الشركة — أقوى إشارة منفردة متاحة."""
    for entry in _enrich_call(f"odds?fixture={m['fid']}", budget):
        for bm in (entry.get("bookmakers") or []):
            for bet in (bm.get("bets") or []):
                if (bet.get("name") or "").lower() != "match winner":
                    continue
                vals = {v.get("value"): v.get("odd") for v in (bet.get("values") or [])}
                try:
                    oh = float(vals["Home"])
                    od = float(vals["Draw"])
                    oa = float(vals["Away"])
                except Exception:
                    continue
                inv = [1 / oh, 1 / od, 1 / oa]
                s = sum(inv)
                ph, pd, pa = (round(100 * x / s) for x in inv)
                return (
                    f"Market odds ({bm.get('name', '?')}): home {oh} / draw {od} / away {oa}"
                    f" => implied probabilities {ph}% / {pd}% / {pa}%"
                )
    return ""


def api_prediction_context(m: dict, budget: dict) -> str:
    """توقع النموذج الإحصائي لـ API-Football (رأي ثانٍ مستقل)."""
    for entry in _enrich_call(f"predictions?fixture={m['fid']}", budget):
        pred = entry.get("predictions") or {}
        parts = []
        pct_ = pred.get("percent") or {}
        if pct_:
            parts.append(
                f"home {pct_.get('home', '?')}, draw {pct_.get('draw', '?')}, "
                f"away {pct_.get('away', '?')}"
            )
        comp = ((entry.get("comparison") or {}).get("total")) or {}
        if comp:
            parts.append(f"overall strength: home {comp.get('home', '?')} vs away {comp.get('away', '?')}")
        advice = (pred.get("advice") or "").strip()
        if advice:
            parts.append(f"advice: {advice}")
        if parts:
            return "Statistical model (API-Football): " + "; ".join(parts)
    return ""


def competition_context(m: dict) -> str:
    """سياق البطولة بلا أي نداء API: كأس أم دوري، أي جولة/مرحلة، ذهاب أم إياب.
    (خطوة استكشاف البيانات 2026-07-15 — المدرب وسياق البطولة يصنعان فرقاً.)"""
    rnd = (m.get("round") or "").strip()
    if not rnd:
        return ""
    return (f"سياق البطولة: {m.get('league', '')} — المرحلة: {rnd}. "
            "انتبه: مباريات الكؤوس والأدوار الإقصائية (ذهاب/إياب) لها منطق "
            "مختلف عن الدوري — الدوافع، التحفظ، وإدارة النتيجة.")


def travel_context(m: dict) -> str:
    """عبء السفر والبيئة بلا أي نداء API (خطوة استكشاف البيانات 2):
    ملعب المباراة ومدينته معروفان، والنموذج يعرف مواقع الفرق — فيُطلب منه
    تقدير مسافة سفر الضيف، فرق التوقيت، المناخ/الارتفاع، وأي عوامل لوجستية
    (رحلة طيران طويلة قبل المباراة تصنع فرقاً حقيقياً)."""
    venue = (m.get("venue") or "").strip()
    if not venue:
        return ""
    return (
        f"ملعب المباراة: {venue}. "
        f"من معرفتك بموقع فريق {m.get('away', 'الضيف')}: قدّر عبء سفره لهذه "
        "المباراة — مسافة الرحلة وعدد ساعات الطيران، فرق التوقيت، المناخ أو "
        "الارتفاع، وأي عوامل لوجستية أو بيئية أخرى تؤثر على الجاهزية."
    )


def coach_context(m: dict, budget: dict) -> str:
    """مدربا الفريقين (نداءان): اسم المدرب وجنسيته وعمره — شخصية المدرب
    وخبرته (مدرب كبير، بداية عهد جديدة، مغامر أم متحفظ) عامل مؤثر."""
    lines = []
    for team_id, team_name in ((m.get("home_id"), m["home"]),
                               (m.get("away_id"), m["away"])):
        if not team_id:
            continue
        try:
            coaches = _enrich_call(f"coachs?team={team_id}", budget)
        except Exception:
            continue
        if not coaches:
            continue
        c = coaches[0] or {}
        name = c.get("name") or ""
        if not name:
            continue
        extra = ", ".join(x for x in (c.get("nationality"),
                                      f"العمر {c.get('age')}" if c.get("age") else "")
                          if x)
        lines.append(f"{team_name}: المدرب {name}" + (f" ({extra})" if extra else ""))
    if not lines:
        return ""
    return ("المدربان (استخدم معرفتك بهما — أسلوبهما وخبرتهما وتأثيرهما):\n"
            + "\n".join(lines))



# طقس ساعة الانطلاق — Open-Meteo مجاني تماماً وبلا مفتاح (خطوة استكشاف 4)
WEATHER_GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
_GEO_CACHE: dict = {}   # مدينة → (خط عرض، خط طول) — مرة واحدة لكل تشغيلة


def weather_context(m: dict) -> str:
    """حرارة/أمطار/رياح ساعة الانطلاق في مدينة الملعب — المطر الغزير والرياح
    القوية والحر الشديد تغيّر أسلوب اللعب. أي فشل يرجع '' بصمت."""
    venue = m.get("venue") or ""
    parts = [x.strip() for x in venue.split(",")]
    city = parts[1] if len(parts) >= 2 else ""
    if not city:
        return ""
    try:
        if city not in _GEO_CACHE:
            r = requests.get(WEATHER_GEO_URL,
                             params={"name": city, "count": 1}, timeout=15)
            res = (r.json().get("results") or [])
            _GEO_CACHE[city] = (
                (res[0].get("latitude"), res[0].get("longitude")) if res else None
            )
        loc = _GEO_CACHE.get(city)
        if not loc:
            return ""
        kickoff = datetime.fromisoformat(m["kickoff"])
        day = kickoff.strftime("%Y-%m-%d")
        r = requests.get(WEATHER_URL, params={
            "latitude": loc[0], "longitude": loc[1],
            "hourly": "temperature_2m,precipitation,wind_speed_10m",
            "timezone": "UTC", "start_date": day, "end_date": day,
        }, timeout=15)
        hourly = r.json().get("hourly") or {}
        times = hourly.get("time") or []
        target = kickoff.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00")
        if target not in times:
            return ""
        i = times.index(target)
        temp = (hourly.get("temperature_2m") or [])[i]
        rain = (hourly.get("precipitation") or [])[i]
        wind = (hourly.get("wind_speed_10m") or [])[i]
        return (f"طقس ساعة الانطلاق في {city}: حرارة {temp}°م، أمطار {rain} ملم، "
                f"رياح {wind} كم/س — خذه بالحسبان إن كان مؤثراً على أسلوب اللعب.")
    except Exception:
        return ""


def transfers_context(m: dict, budget: dict) -> str:
    """انتقالات آخر 90 يوماً للفريقين (نداءان) — خطوة استكشاف 3: القادمون
    والمغادرون يغيّرون قوة الفريق قبل أن تعكسها النتائج."""
    cutoff = (now_utc() - timedelta(days=90)).strftime("%Y-%m-%d")
    lines = []
    for team_id, team_name in ((m.get("home_id"), m["home"]),
                               (m.get("away_id"), m["away"])):
        if not team_id:
            continue
        try:
            items = _enrich_call(f"transfers?team={team_id}", budget)
        except Exception:
            continue
        recent = []
        for it in items or []:
            player = ((it.get("player") or {}).get("name")) or "?"
            for tr in (it.get("transfers") or []):
                date = (tr.get("date") or "")[:10]
                if not date or date < cutoff:
                    continue
                t_in = (((tr.get("teams") or {}).get("in")) or {}).get("id")
                t_out = (((tr.get("teams") or {}).get("out")) or {}).get("id")
                if t_in == team_id:
                    recent.append(f"وصل {player} ({date})")
                elif t_out == team_id:
                    recent.append(f"غادر {player} ({date})")
        if recent:
            lines.append(f"{team_name}: " + "، ".join(sorted(recent, reverse=True)[:6]))
    if not lines:
        return ""
    return ("انتقالات آخر 90 يوماً (قد تغيّر قوة الفريق قبل أن تعكسها النتائج):\n"
            + "\n".join(lines))


def build_context(m: dict, budget: dict, standings_cache: dict) -> str:
    parts = [
        competition_context(m),
        travel_context(m),
        standings_context(m, budget, standings_cache),
        h2h_context(m, budget),
        form_context(m.get("home_id"), m["home"], budget),
        form_context(m.get("away_id"), m["away"], budget),
        injuries_context(m, budget),
        odds_context(m, budget),
        api_prediction_context(m, budget),
        coach_context(m, budget),
        transfers_context(m, budget),
        weather_context(m),
    ]
    return "\n".join(p for p in parts if p)


# ================== توقعات Claude (احتمالات، على دفعات) ==================
def calibration_text(stats: dict) -> str:
    if not stats["overall"]["total"]:
        return "لا يوجد سجل تاريخي بعد — كن متحفظاً في توزيع الاحتمالات."
    return (
        f"سجل دقتك التاريخي الفعلي (استخدمه لمعايرة احتمالاتك):\n"
        f"- الإجمالي: {pct(stats['overall'])}\n"
        f"- آخر 30 يوماً: {pct(stats['last30'])}\n"
        f"- الدوريات الكبرى: {pct(stats['top_leagues'])} | البقية: {pct(stats['other_leagues'])}\n"
        f"- عندما كانت ثقتك 70%+: {pct(stats['by_confidence']['70+'])}\n"
        f"- عندما كانت ثقتك 60-69%: {pct(stats['by_confidence']['60-69'])}\n"
        f"- عندما كانت ثقتك 50-59%: {pct(stats['by_confidence']['50-59'])}\n"
        f"إذا كانت دقتك الفعلية أقل من ثقتك المعلنة فاخفض الاحتمال الأعلى، والعكس صحيح."
    )


def lessons_text() -> str:
    """أحدث الدروس المستخلصة من الأخطاء السابقة (تُملأ في المرحلة 3)."""
    data = load_json(LESSONS_FILE, {"lessons": []})
    lessons = data.get("lessons") or []
    lines = []
    for it in lessons[-MAX_LESSONS_IN_PROMPT:]:
        text = it if isinstance(it, str) else (it.get("text") or it.get("lesson") or "")
        text = str(text).strip()
        if text:
            lines.append(f"- {text}")
    if not lines:
        return ""
    return "دروس من أخطائك السابقة:\n" + "\n".join(lines)


def news_context() -> str:
    news = load_json(NEWS_FILE, {})
    items = news.get("items", [])[:10]
    if not items:
        return ""
    lines = [f"- {it.get('title', '')}" for it in items if it.get("title")]
    return "آخر عناوين الأخبار الكروية (قد تحتوي إصابات أو أخباراً مؤثرة):\n" + "\n".join(lines)


def claude_predict_batch(batch: list, stats: dict, enriched: bool) -> dict:
    """يرسل دفعة مباريات لـ Claude ويرجع {fid: توقع بالاحتمالات}."""
    payload = []
    for m in batch:
        item = {
            "id": m["fid"],
            "home": m["home"],
            "away": m["away"],
            "league": m["league"],
            "kickoff_utc": m["kickoff"],
        }
        if enriched and m.get("context"):
            item["context"] = m["context"]
        payload.append(item)

    extra = ""
    if enriched:
        extra = (
            "لكل مباراة حقل context يحتوي بيانات حقيقية محدثة: الترتيب، المواجهات "
            "المباشرة، آخر 5 نتائج لكل فريق، الإصابات، أودز السوق باحتمالاتها "
            "الضمنية، وتوقع نموذج إحصائي مستقل. اعتمد عليها أولاً قبل معرفتك العامة.\n"
            "أودز السوق إشارة قوية جداً — خذها مرجعاً أساسياً، لكنك لست مقلداً لها: "
            "ابتعد عنها فقط عندما تملك سبباً حقيقياً من البيانات أو من دروسك السابقة، "
            "واذكر السبب في reason.\n\n"
        )

    prompt_parts = [
        "أنت خبير توقع مباريات كرة قدم من الطراز الأول. ستصلك قائمة مباريات تقام خلال 24 ساعة.\n"
        "لكل مباراة وزّع احتمالات النتائج الثلاث (فوز المضيف / تعادل / فوز الضيف) "
        "بحيث يكون مجموعها 100 بالضبط.\n",
        extra,
        calibration_text(stats),
    ]
    lessons = lessons_text()
    if lessons:
        prompt_parts.append("\n" + lessons)
    prompt_parts.append(
        "\nأرجع ردك بصيغة JSON فقط — مصفوفة واحدة بدون أي نص قبلها أو بعدها وبدون علامات ```:\n"
        '[{"id":"...","ar_home":"...","ar_away":"...","ar_league":"...",'
        '"prob_home":55,"prob_draw":25,"prob_away":20,"reason":"سطر واحد بالعربي"}]\n\n'
        "قواعد:\n"
        "- prob_home + prob_draw + prob_away = 100 بالضبط، أرقام صحيحة.\n"
        "- كرة القدم مليئة بالمفاجآت — لا تعطِ أي نتيجة احتمالاً أعلى من 85.\n"
        "- ar_home/ar_away/ar_league: الأسماء العربية الشائعة في الإعلام الرياضي، "
        "وإذا كان الاسم غير مشهور فاكتبه بحروف عربية.\n"
        "- استخدم الأرقام الإنجليزية (0-9) فقط ولا تستخدم الأرقام العربية (٠-٩) أبداً.\n"
        "- reason: مختصر وواضح بدون حشو، يذكر العامل الحاسم."
    )
    system_prompt = "".join(prompt_parts)

    user_text = json.dumps(payload, ensure_ascii=False)
    ctx = news_context()
    if ctx:
        user_text = ctx + "\n\nالمباريات:\n" + user_text

    body = {
        "model": CLAUDE_MODEL,
        "max_tokens": 3000,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_text}],
    }
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
            timeout=180,
        )
        r.raise_for_status()
        data = r.json()
        text = "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        ).strip()
        return parse_predictions_json(text)
    except Exception as e:
        detail = ""
        resp = getattr(e, "response", None)
        if resp is not None:
            try: detail = " — " + resp.text[:300]
            except Exception: pass
        print(f"Claude error: {e}{detail}")
        return {}


def parse_predictions_json(text: str) -> dict:
    """يحوّل رد Claude إلى {fid: توقع} — يطبّع الاحتمالات لمجموع 100 ويشتق الاختيار."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    if not text.startswith("["):
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if not m:
            return {}
        text = m.group(0)
    try:
        items = json.loads(text)
    except Exception as e:
        print("JSON parse error:", e)
        return {}
    out = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        fid = str(it.get("id", ""))
        if not fid:
            continue
        try:
            probs = {
                k: max(0, int(round(float(it.get(f"prob_{k}", 0)))))
                for k in ("home", "draw", "away")
            }
        except Exception:
            continue
        total = sum(probs.values())
        if total <= 0:
            continue
        if total != 100:
            scaled = {k: round(v * 100 / total) for k, v in probs.items()}
            kmax = max(scaled, key=scaled.get)
            scaled[kmax] += 100 - sum(scaled.values())
            probs = scaled
        pick = max(("home", "draw", "away"), key=lambda k: probs[k])
        conf = max(30, min(85, probs[pick]))
        out[fid] = {
            "pick": pick,
            "confidence": conf,
            "prob_home": probs["home"],
            "prob_draw": probs["draw"],
            "prob_away": probs["away"],
            "reason": (it.get("reason") or "").strip(),
            "ar_home": (it.get("ar_home") or "").strip(),
            "ar_away": (it.get("ar_away") or "").strip(),
            "ar_league": (it.get("ar_league") or "").strip(),
        }
    return out


# كلمات تكشف مباريات الكأس (بالاسم) والإقصاء/التصفيات (بالجولة)
_CUP_NAME_KW = ("cup", "coupe", "copa", "pokal", "beker", "taça", "taca",
                "cupa", "kupa", "cupen", "supercup", "كأس")
_CUP_ROUND_KW = ("qualif", "preliminary", "play-off", "playoff", "knockout",
                 "round of", "replay")


def is_cup_fixture(league_name: str, round_str: str) -> bool:
    """كأس/إقصاء؟ — الأودز غالباً مفقودة لهذه المباريات وهي كثيرة المفاجآت."""
    ln = (league_name or "").lower()
    rn = (round_str or "").lower()
    return (any(k in ln for k in _CUP_NAME_KW)
            or any(k in rn for k in _CUP_ROUND_KW))


def apply_cup_guardrail(entry: dict) -> None:
    """حارس مباريات الكأس/الإقصاء (توجيه المالك 2026-07-18).

    يعمل بعد النموذج على مباريات الكأس فقط: يرفع احتمال التعادل إلى حدّ أدنى
    (مفاجآت الكأس كثيرة) ثم يُسقّف الثقة عند CUP_CONF_CAP حتى لا تتسلّل تخمينات
    الكأس إلى خانة الثقة العالية (70%+). لا يغيّر الطرف المُختار (رفع التعادل
    لا يتجاوز المرشّح أبداً)، ولا يمسّ التعلّم — المعايرة والدروس تتعلّمان من
    النتيجة الحقيقية كالمعتاد. للتعطيل: CUP_GUARDRAIL=False."""
    if not (CUP_GUARDRAIL and entry.get("is_cup")):
        return
    try:
        ph = int(entry["prob_home"]); pd = int(entry["prob_draw"]); pa = int(entry["prob_away"])
    except (KeyError, TypeError, ValueError):
        return
    if pd < CUP_MIN_DRAW:
        need = CUP_MIN_DRAW - pd
        rest = ph + pa
        if rest > 0:
            ph -= int(round(need * ph / rest))
            pa -= int(round(need * pa / rest))
        pd = CUP_MIN_DRAW
        probs = {"home": max(0, ph), "draw": max(0, pd), "away": max(0, pa)}
        tot = sum(probs.values()) or 1
        if tot != 100:
            probs = {k: round(v * 100 / tot) for k, v in probs.items()}
            km = max(probs, key=probs.get)
            probs[km] += 100 - sum(probs.values())
        entry["prob_home"], entry["prob_draw"], entry["prob_away"] = (
            probs["home"], probs["draw"], probs["away"])
        entry["pick"] = max(("home", "draw", "away"), key=lambda k: probs[k])
    # سقّف الثقة (الطرف المُختار ثابت)
    entry["confidence"] = max(30, min(CUP_CONF_CAP, int(entry["prob_" + entry["pick"]])))


# ================== ملخص تيليجرام ==================
PICK_AR = {"home": "فوز {h}", "draw": "تعادل", "away": "فوز {a}"}


def pick_label(p: dict) -> str:
    h = p.get("ar_home") or p.get("home", "?")
    a = p.get("ar_away") or p.get("away", "?")
    return PICK_AR[p["pick"]].format(h=h, a=a)


def v1_pending() -> dict:
    """توقعات المحرك 1 المنتظرة — للمقارنة جنباً إلى جنب في الملخص."""
    store = load_json(V1_PREDICTIONS_FILE, {})
    return store.get("pending") or {}


def update_history(v2_stats: dict, user_stats: dict) -> int:
    """الأرشيف الدائم للتقدم: يدمج أرقام اليوم (صح/مجموع لكل طرف) في history.json.
    ذاكرة المحرّكات التفصيلية تُقص بعد 1000 نتيجة — هذا الملف لا يُقص أبداً،
    فهو سجل مسيرة المشروع الكامل يوماً بيوم. الدمج آمن التكرار (idempotent)."""
    hist = load_json(HISTORY_FILE, {"days": {}})
    days = hist.setdefault("days", {})
    v1_stats = (load_json(V1_PREDICTIONS_FILE, {}).get("meta") or {}).get("stats") or {}
    for key, st in (("v1", v1_stats), ("v2", v2_stats or {}), ("user", user_stats or {})):
        for d, row in ((st.get("daily") or {}) if st else {}).items():
            days.setdefault(d, {})[key] = {
                "correct": int(row.get("correct", 0)),
                "total": int(row.get("total", 0)),
            }
    lessons = load_json(LESSONS_FILE, {"lessons": []}).get("lessons") or []
    hist["meta"] = {
        "updated": now_utc().isoformat(),
        "lessons_stored": len(lessons),
    }
    save_json(HISTORY_FILE, hist)
    return len(days)


def race_line(user_stats: dict, v2_stats: dict) -> str:
    """سطر سباق الدقة الثلاثي: المالك ضد المحركين — يظهر متى وُجد سجل للمالك."""
    if not (user_stats and user_stats.get("overall", {}).get("total")):
        return ""
    v1_stats = (load_json(V1_PREDICTIONS_FILE, {}).get("meta") or {}).get("stats") or {}
    parts = [f"أنت: {pct(user_stats['overall'])}"]
    if v1_stats.get("overall", {}).get("total"):
        parts.append(f"المحرك 1: {pct(v1_stats['overall'])}")
    if v2_stats.get("overall", {}).get("total"):
        parts.append(f"المحرك 2: {pct(v2_stats['overall'])}")
    return "🏆 سباق الدقة — " + " | ".join(parts)


def build_digest(new_preds: list, stats: dict, v1_preds: dict = None,
                 new_lessons: int = 0, user_stats: dict = None) -> str:
    lines = ["🤖 المحرك 2 — توقعات الـ 24 ساعة القادمة"]
    v1_preds = v1_preds or {}
    shown = [p for p in new_preds if p["top"]] if DIGEST_TOP_ONLY else new_preds
    rest = len(new_preds) - len(shown)

    current_league = None
    for p in shown:
        lg = p.get("ar_league") or p.get("league")
        if lg != current_league:
            lines.append(f"\n🏆 {lg}")
            current_league = lg
        t = ""
        try:
            t = datetime.fromisoformat(p["kickoff"]).astimezone(
                timezone(timedelta(hours=3))
            ).strftime("%H:%M")
        except Exception:
            pass
        h = p.get("ar_home") or p["home"]
        a = p.get("ar_away") or p["away"]
        lines.append(f"⏰ {t} — {h} 🆚 {a}")
        v1 = v1_preds.get(p["fid"])
        if v1 and v1.get("pick") in PICK_AR:
            lines.append(f"   المحرك 1: {pick_label(v1)} — ثقة {v1.get('confidence', '?')}%")
        lines.append(f"   المحرك 2: {pick_label(p)} — ثقة {p['confidence']}%")
        lines.append(
            f"   📊 {h} {p['prob_home']}% | تعادل {p['prob_draw']}% | {a} {p['prob_away']}%"
        )

    if not shown:
        lines.append("لا توجد مباريات في الدوريات الكبرى خلال 24 ساعة.")
    if rest > 0:
        lines.append(f"\n➕ {rest} مباراة أخرى بتوقعاتها على اللوحة:")
        lines.append(DASHBOARD_URL)
    if stats["last30"]["total"]:
        lines.append(f"\n📊 دقة المحرك 2 آخر 30 يوماً: {pct(stats['last30'])}")
    if new_lessons:
        lines.append(f"📚 دروس جديدة من أخطاء الأمس: {new_lessons} — تدخل في توقعات اليوم.")
    race = race_line(user_stats, stats)
    if race:
        lines.append(race)
    lines.append("\n⭐ أرسل لي أسماء المباريات التي تهمك اليوم وسأركز تنبيهاتي عليها فقط.")
    lines.append("⚠️ توقعات تحليلية وليست ضمانات.")
    return "\n".join(lines)


# ================== المنطق الرئيسي ==================
def main() -> None:
    missing = [
        name
        for name, val in [
            ("API_FOOTBALL_KEY", API_FOOTBALL_KEY),
            ("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY),
        ]
        if not val
    ]
    if missing:
        print("مفاتيح ناقصة في Secrets:", ", ".join(missing))
        sys.exit(1)

    store = load_json(PREDICTIONS_FILE, {"pending": {}, "resolved": []})
    store.setdefault("pending", {})
    store.setdefault("resolved", [])

    # 1) تسوية نتائج الأيام السابقة (التعلم)
    resolved_now, newly_resolved = resolve_pending(store)
    stats = compute_stats(store["resolved"])
    print(f"المحرك 2: تمت تسوية {resolved_now} توقعاً. السجل: {pct(stats['overall'])}")

    # 1.5) المرحلة 3: استخلاص دروس من أخطاء الأمس (كلها)، ثم دمجها عند التضخم
    new_lessons = generate_lessons(newly_resolved)
    if new_lessons:
        print(f"دروس جديدة مستخلصة من الأخطاء: {new_lessons}")
    consolidated = consolidate_lessons()
    if consolidated:
        print(f"تم دمج الدروس في {consolidated} مبدأً عاماً.")

    # 1.55) التقييم الذاتي لتقارير ما قبل المباراة (سيناريوهات المحرك 2)
    scenario_graded = resolve_scenarios()
    if scenario_graded:
        print(f"قُيّمت {scenario_graded} من تقارير ما قبل المباراة مقابل البيانات النهائية.")

    # 1.6) تقييم توقعات المالك بنفس المنطق (سباق الدقة الثلاثي)
    user_store = load_json(USER_PREDICTIONS_FILE, {"pending": {}, "resolved": []})
    user_store.setdefault("pending", {})
    user_store.setdefault("resolved", [])
    user_stats = None
    user_resolved_now = 0
    if user_store["pending"] or user_store["resolved"]:
        user_resolved_now, _ = resolve_pending(user_store)
        user_stats = compute_stats(user_store["resolved"])
        user_store["meta"] = {"last_run": now_utc().isoformat(), "stats": user_stats}
        save_json(USER_PREDICTIONS_FILE, user_store)
        print(f"توقعات المالك: تم تقييم {user_resolved_now}. السجل: {pct(user_stats['overall'])}")

    # 2) مباريات الـ 24 ساعة القادمة (نفس اختيار المحرك 1) + إكمال الشعارات الناقصة
    fetched = get_upcoming_24h()
    for m in fetched:
        p = store["pending"].get(m["fid"])
        if p is not None and not p.get("home_logo"):
            for k in ("home_logo", "away_logo", "league_logo"):
                p[k] = m.get(k, "")
    upcoming = [m for m in fetched if m["fid"] not in store["pending"]]
    print(f"مباريات جديدة للتوقع: {len(upcoming)}")

    # 3) سياق إضافي لكل المباريات (القائمة مرتبة كبرى-أولاً فتأخذ الأولوية عند السقف)
    budget = {"used": 0}
    standings_cache = {}
    enriched, basic = [], []
    for m in upcoming:
        # النمط الغني (المكلف) للدوريات الكبرى فقط عند تفعيل التوفير — البقية
        # تبقى مُتوقَّعة بالنمط الخفيف (تغطية كاملة، تعلّم كامل، تكلفة أقل)
        want_rich = (m.get("top") or not ENRICH_TOP_ONLY)
        if want_rich and len(enriched) < MAX_ENRICHED_FIXTURES and budget["used"] < ENRICH_CALL_BUDGET:
            m["context"] = build_context(m, budget, standings_cache)
            enriched.append(m)
        else:
            basic.append(m)
    print(f"سياق إضافي: {len(enriched)} مباراة (غنية)، {len(basic)} خفيفة، {budget['used']} نداء API")

    # 4) توقعات Claude على دفعات
    new_preds = []
    groups = [(enriched, ENRICHED_BATCH_SIZE, True), (basic, BASIC_BATCH_SIZE, False)]
    for matches, batch_size, is_enriched in groups:
        for i in range(0, len(matches), batch_size):
            batch = matches[i:i + batch_size]
            results = claude_predict_batch(batch, stats, is_enriched)
            for m in batch:
                r = results.get(m["fid"])
                if not r:
                    continue
                entry = {k: v for k, v in m.items() if k != "context"}
                entry.update(r)
                apply_cup_guardrail(entry)   # سقف ثقة الكأس/الإقصاء
                store["pending"][m["fid"]] = entry
                new_preds.append(entry)

    store["meta"] = {
        "last_run": now_utc().isoformat(),
        "engine": "v2",
        "model": CLAUDE_MODEL,
        "stats": stats,
    }
    save_json(PREDICTIONS_FILE, store)
    print(f"تم حفظ {len(new_preds)} توقعاً جديداً للمحرك 2.")

    # الأرشيف الدائم للتقدم (كل الأطراف، لا يُقص أبداً)
    days_total = update_history(stats, user_stats)
    print(f"الأرشيف الدائم: {days_total} يوماً مسجلاً.")

    # 5) ملخص تيليجرام (مقارنة المحرك 1 + سباق الدقة الثلاثي مع المالك)
    if SEND_TELEGRAM_DIGEST and TELEGRAM_TOKEN and TELEGRAM_CHAT_ID and new_preds:
        send_telegram_long(
            build_digest(new_preds, stats, v1_pending(), new_lessons, user_stats)
        )


if __name__ == "__main__":
    main()
