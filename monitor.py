# -*- coding: utf-8 -*-
"""
بوت مراقبة المباريات الحية — النسخة المجانية
--------------------------------------------
يسحب المباريات الجارية الآن من API-Football، يستبعد (الودية / الأفريقية /
الهند / باكستان / بنغلادش)، وعند أي حدث مهم (بداية مباراة، هدف، نهاية مباراة)
يحلل الموقف عبر Claude ويرسل تنبيهاً على تيليجرام.

لا تكتب أي مفتاح داخل هذا الملف — كل المفاتيح توضع في GitHub Secrets.
"""

import json
import os
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
STATE_FILE = Path("state.json")          # ذاكرة البوت بين التشغيلات
MAX_ANALYSES_PER_RUN = 20                # حد أقصى لتحليلات Claude في التشغيلة الواحدة
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

# ---- المحرك 2 المباشر (للدوريات الكبرى فقط) ----
# يسحب إحصائيات وأحداث وتشكيلات المباراة الحية (3 نداءات API لكل مباراة)
# ويحلل عبر النموذج الأقوى مع تفكير عميق ممتد قبل الإجابة، بتوقع كل
# السيناريوهات: هدف قادم، ركنيات، كرات ثابتة، اللاعب الأخطر، بطاقات.
# مقيد بعدد مباريات لكل تشغيلة حفاظاً على الرصيد.
CLAUDE_MODEL_V2 = "claude-fable-5"
MAX_LIVE_ENRICHED_PER_RUN = 12   # رصيد API-Football مدفوع مسبقاً — نرفع السقف بسخاء
LIVE_THINKING_BUDGET = 2048   # ميزانية التفكير العميق (توكنز) لتحليل المحرك 2 المباشر

# ---- نبض المحرك 2 (لمباريات قائمة التركيز فقط) ----
# بين الأحداث (لا هدف ولا بداية/نهاية) يفحص المحرك 2 المباراة كل تشغيلة:
# إن تشكل سيناريو خطر جديد (هدف قادم، كلا الفريقين يسجلان، موجة ركنيات،
# لاعب يهدد، بطاقة محتملة، انقلاب سيطرة) يرسل تنبيهاً — وإلا يبقى صامتاً.
MAX_PULSE_PER_RUN = 6            # حد نداءات Claude للنبض في التشغيلة الواحدة
PULSE_STATUSES = {"1H", "2H", "ET"}   # لا نبض في الاستراحة/الركلات الترجيحية

# ---- إعدادات التغطية العالمية ----
# ANALYZE_ALL = True  → كل مباراة تصلك تنبيهاتها تأتي مع تحليل
# ANALYZE_ALL = False → التحليل للدوريات الكبرى فقط
ANALYZE_ALL = True

# ---- قائمة التركيز (يديرها المستخدم عبر رسائل تيليجرام — watchlist.py) ----
# القائمة غير فارغة → التنبيهات لمباريات القائمة فقط (مع أولوية المحرك 2 المباشر).
# القائمة فارغة    → التنبيهات للدوريات الكبرى فقط (الوضع الافتراضي الهادئ).
# البيانات واللوحة تغطي كل المباريات دائماً — الفلترة على تيليجرام فقط.
WATCHLIST_FILE = Path("watchlist.json")

# معرفات الدوريات الكبرى في API-Football (تقدر تضيف عليها)
TOP_LEAGUE_IDS = {
    1,    # كأس العالم
    2,    # دوري أبطال أوروبا
    3,    # الدوري الأوروبي
    4,    # يورو
    9,    # كوبا أمريكا
    13,   # كوبا ليبرتادوريس
    15,   # كأس العالم للأندية
    39,   # الدوري الإنجليزي الممتاز
    61,   # الدوري الفرنسي
    71,   # الدوري البرازيلي
    78,   # الدوري الألماني
    88,   # الدوري الهولندي
    94,   # الدوري البرتغالي
    128,  # الدوري الأرجنتيني
    135,  # الدوري الإيطالي
    140,  # الدوري الإسباني
    253,  # الدوري الأمريكي MLS
    307,  # دوري روشن السعودي
}

# الدول المستبعدة (أفريقيا + الهند وباكستان وبنغلادش)
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

# كلمات في اسم البطولة تؤدي للاستبعاد (الودية + بطولات أفريقيا القارية)
EXCLUDED_LEAGUE_KEYWORDS = ["friendl", "caf ", "africa", "afcon"]

# حالات المباراة الحية والمنتهية في API-Football
LIVE_STATUSES  = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE", "INT"}
FINAL_STATUSES = {"FT", "AET", "PEN"}


# ================== أدوات مساعدة ==================
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def is_excluded(league: dict) -> bool:
    country = (league.get("country") or "").strip().lower()
    name = (league.get("name") or "").strip().lower()
    if country in EXCLUDED_COUNTRIES:
        return True
    for kw in EXCLUDED_LEAGUE_KEYWORDS:
        if kw in name:
            return True
    return False


def should_analyze(league: dict, used: int) -> bool:
    """هل نطلب تحليل Claude لهذه المباراة؟"""
    if used >= MAX_ANALYSES_PER_RUN:
        return False
    if ANALYZE_ALL:
        return True
    return league.get("id") in TOP_LEAGUE_IDS


def load_json_file(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def load_watchlist_data() -> dict:
    return load_json_file(WATCHLIST_FILE, {})


def valid_watch_fids(data: dict) -> set:
    """معرفات مباريات قائمة التركيز الصالحة (غير منتهية الصلاحية)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
    return {
        fid for fid, e in (data.get("matches") or {}).items()
        if isinstance(e, dict) and (e.get("date") or "9999") >= cutoff
    }


def load_watchlist() -> set:
    return valid_watch_fids(load_watchlist_data())


def all_focus_finished(data: dict, watch: set) -> bool:
    """هل انتهت كل مباريات قائمة التركيز؟ (كل واحدة سجلت نتيجتها النهائية)"""
    matches = data.get("matches") or {}
    return bool(watch) and all((matches.get(f) or {}).get("result") for f in watch)


def build_focus_summary(matches: dict) -> str:
    """ملخص فوري عند انتهاء آخر مباراة في القائمة: نتائجك ضد المحركين.
    التقييم الرسمي (وسجل الدقة الدائم) يبقى لملخص الصباح."""
    pick_ar = {"home": "فوز {h}", "draw": "تعادل", "away": "فوز {a}"}
    stores = [
        ("أنت", (load_json_file(Path("predictions_user.json"), {}).get("pending") or {})),
        ("المحرك 1", (load_json_file(Path("predictions.json"), {}).get("pending") or {})),
        ("المحرك 2", (load_json_file(Path("predictions_v2.json"), {}).get("pending") or {})),
    ]
    tallies = {name: [0, 0] for name, _ in stores}
    lines = ["🏁 انتهت كل مباريات قائمة التركيز — النتائج السريعة:"]
    for fid, e in matches.items():
        score = e.get("result") or "?"
        try:
            gh, ga = (int(x) for x in score.split("-"))
        except Exception:
            continue
        outcome = "home" if gh > ga else ("away" if ga > gh else "draw")
        lines.append(f"\n• {e.get('label', '?')} — {score}")
        parts = []
        for name, pending in stores:
            p = pending.get(fid)
            if not p or p.get("pick") not in pick_ar:
                continue
            h = p.get("ar_home") or p.get("home", "?")
            a = p.get("ar_away") or p.get("away", "?")
            ok = p["pick"] == outcome
            tallies[name][0] += 1 if ok else 0
            tallies[name][1] += 1
            parts.append(f"{name}: {pick_ar[p['pick']].format(h=h, a=a)} {'✅' if ok else '❌'}")
        if parts:
            lines.append("   " + " | ".join(parts))
    totals = [f"{name} {c}/{t}" for name, (c, t) in tallies.items() if t]
    if totals:
        lines.append("\n📊 حصيلة اليوم: " + " | ".join(totals))
    lines.append("التقييم الرسمي والدروس في ملخص الصباح.")
    return "\n".join(lines)


def should_alert(league: dict, fid: str, watch: set) -> bool:
    """هل نرسل تنبيه تيليجرام لهذه المباراة؟
    قائمة تركيز غير فارغة → مبارياتها فقط. فارغة → الدوريات الكبرى فقط."""
    if watch:
        return fid in watch
    return league.get("id") in TOP_LEAGUE_IDS


def api_football(path: str) -> list:
    resp = requests.get(
        f"https://v3.football.api-sports.io/{path}",
        headers={"x-apisports-key": API_FOOTBALL_KEY},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        print("API-Football errors:", data["errors"])
    return data.get("response", [])


def get_live_fixtures() -> list:
    return api_football("fixtures?live=all")


# إحصائيات مهمة تُلخص لتحليل المحرك 2 المباشر
KEY_LIVE_STATS = {
    "Shots on Goal", "Total Shots", "Ball Possession", "Corner Kicks",
    "Yellow Cards", "Red Cards", "expected_goals", "Goalkeeper Saves", "Fouls",
}


def get_live_details(fid: str) -> str:
    """3 نداءات API: إحصائيات المباراة الحية + أحداثها + التشكيلات،
    يرجع سياقاً نصياً مضغوطاً. أي فشل يرجع نصاً أقصر — لا يوقف التحليل أبداً."""
    parts = []
    try:
        team_lines = []
        for side in api_football(f"fixtures/statistics?fixture={fid}"):
            name = (side.get("team") or {}).get("name", "?")
            vals = [
                f"{s.get('type')}: {s.get('value')}"
                for s in (side.get("statistics") or [])
                if s.get("type") in KEY_LIVE_STATS and s.get("value") is not None
            ]
            if vals:
                team_lines.append(f"{name} — " + ", ".join(vals))
        if team_lines:
            parts.append("Live stats:\n" + "\n".join(team_lines))
    except Exception as e:
        print("فشل سحب الإحصائيات الحية:", e)
    try:
        ev_lines = []
        for ev in api_football(f"fixtures/events?fixture={fid}")[-15:]:
            minute = ((ev.get("time") or {}).get("elapsed"))
            team = (ev.get("team") or {}).get("name", "?")
            player = (ev.get("player") or {}).get("name") or ""
            etype = ev.get("type") or "?"
            detail = ev.get("detail") or ""
            ev_lines.append(f"{minute}' {etype} ({detail}) {player} [{team}]")
        if ev_lines:
            parts.append("Match events:\n" + "\n".join(ev_lines))
    except Exception as e:
        print("فشل سحب أحداث المباراة:", e)
    try:
        lu_lines = []
        for side in api_football(f"fixtures/lineups?fixture={fid}"):
            team = (side.get("team") or {}).get("name", "?")
            formation = side.get("formation") or "?"
            starters = [
                ((x.get("player") or {}).get("name") or "?")
                for x in (side.get("startXI") or [])
            ]
            if starters:
                lu_lines.append(f"{team} ({formation}): " + ", ".join(starters))
        if lu_lines:
            parts.append("Lineups:\n" + "\n".join(lu_lines))
    except Exception as e:
        print("فشل سحب التشكيلات:", e)
    return "\n".join(parts)


SYSTEM_PROMPT_BASIC = (
    "أنت محلل وخبير توقع مباريات كرة قدم. سيصلك وضع مباراة بأسماء إنجليزية. "
    "أرجع ردك بهذا الشكل بالضبط:\n"
    "الأسماء: [الفريق المضيف بالعربي] | [الفريق الضيف بالعربي] | [البطولة بالعربي (الدولة بالعربي)]\n"
    "ثم سطران إلى ثلاثة: تحليل مختصر مبني على معرفتك بالفريقين والنتيجة والدقيقة، "
    "ينتهي بسطر: التوقع: [اسم الفريق بالعربي أو تعادل] — ثقة X%\n"
    "استخدم الأسماء العربية الشائعة في الإعلام الرياضي "
    "(مثال: Real Madrid → ريال مدريد، Manchester City → مانشستر سيتي)، "
    "وإذا كان الاسم غير مشهور فاكتبه بحروف عربية. "
    "استخدم الأرقام الإنجليزية (0-9) فقط ولا تستخدم الأرقام العربية (٠-٩) أبداً."
)

SYSTEM_PROMPT_LIVE_V2 = (
    "أنت محلل مباريات حية من الطراز الأول. سيصلك وضع مباراة جارية بأسماء إنجليزية، "
    "وقد يتضمن إحصائيات حية (تسديدات، استحواذ، ركنيات، xG، بطاقات) وقائمة أحداث "
    "(أهداف بأسماء المسجلين، بطاقات، تبديلات). اعتمد على هذه البيانات أولاً.\n"
    "أرجع ردك بهذا الشكل بالضبط:\n"
    "الأسماء: [الفريق المضيف بالعربي] | [الفريق الضيف بالعربي] | [البطولة بالعربي (الدولة بالعربي)]\n"
    "ثم قراءة مركزة في 2-3 أسطر: من يسيطر فعلياً (الاستحواذ وحده يخدع — اربطه بالخطورة)، "
    "وأثر أي طرد أو تبديل هجومي.\n"
    "ثم سطر يبدأ بـ: السيناريو: أخطر سيناريو متوقع قادم — هدف قادم ومن أي فريق، "
    "اللاعب الأخطر بالاسم إن دلت الأحداث عليه، خطورة الركنيات أو الكرات الثابتة، "
    "احتمال بطاقة تغير المباراة، أو إغلاق المتقدم للمباراة.\n"
    "ثم سطر أخير: التوقع: [اسم الفريق بالعربي أو تعادل] — ثقة X%\n"
    "استخدم الأسماء العربية الشائعة في الإعلام الرياضي، وإذا كان الاسم غير مشهور "
    "فاكتبه بحروف عربية. "
    "استخدم الأرقام الإنجليزية (0-9) فقط ولا تستخدم الأرقام العربية (٠-٩) أبداً."
)


SYSTEM_PROMPT_PULSE = (
    "أنت عين حية على مباراة جارية من قائمة تركيز المستخدم. سيصلك وضع المباراة "
    "مع إحصائياتها وأحداثها الحية، وقراءتك السابقة قبل نحو 10 دقائق.\n"
    "مهمتك: هل يتشكل الآن سيناريو مهم جديد يستحق تنبيه المستخدم؟ أمثلة: هدف قادم "
    "ومن أي فريق، كلا الفريقين سيسجلان، موجة ركنيات أو كرات ثابتة خطرة، لاعب بعينه "
    "يهدد بالاسم، بطاقة حمراء محتملة تغير المباراة، انقلاب في السيطرة، أو المتقدم "
    "بدأ يغلق المباراة.\n"
    "إن لم يكن هناك تغير حقيقي مهم عن قراءتك السابقة فأرجع سطراً واحداً فقط: لا جديد\n"
    "وإن وجد تغير مهم فأرجع 2-4 أسطر: أولها يبدأ بـ 🔮 وفيه خلاصة السيناريو في جملة، "
    "ثم التفاصيل (السيناريو المتوقع، اللاعب الأخطر بالاسم إن دلّت الأحداث عليه، "
    "ونسبة تقديرية للاحتمال).\n"
    "لا تخلط 'لا جديد' مع أي نص آخر. استخدم الأسماء العربية الشائعة في الإعلام "
    "الرياضي والأرقام الإنجليزية (0-9) فقط، لا الأرقام العربية (٠-٩)."
)


def analyze_with_claude(context_text: str, model: str = CLAUDE_MODEL,
                        system_prompt: str = SYSTEM_PROMPT_BASIC,
                        max_tokens: int = 400, thinking_budget: int = 0) -> str:
    """يرسل وضع المباراة لـ Claude ويرجع الأسماء بالعربي + توقعاً مختصراً.
    thinking_budget > 0 يفعّل التفكير العميق الممتد قبل الإجابة (للمحرك 2 المباشر)."""
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": context_text}],
    }
    if thinking_budget > 0:
        body["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
        # max_tokens يجب أن يتسع للتفكير + الرد النهائي
        body["max_tokens"] = max(max_tokens, thinking_budget + 800)
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        text = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        ).strip()
        return text or "(تعذر الحصول على تحليل)"
    except Exception as e:
        print("Claude error:", e)
        return "(تعذر التحليل حالياً — تحقق من رصيد مفتاح Claude)"


def analyze_match(prompt_base: str, league: dict, fid: str, live_budget: dict,
                  watch: set = frozenset()):
    """يختار التحليل المناسب: المحرك 2 المباشر (لمباريات قائمة التركيز — من أي
    دوري — وللدوريات الكبرى) أو التحليل الأساسي.
    يرجع (نص التحليل، هل هو تحليل المحرك 2؟)."""
    vip = fid in watch or league.get("id") in TOP_LEAGUE_IDS
    if vip and live_budget["used"] < MAX_LIVE_ENRICHED_PER_RUN:
        live_budget["used"] += 1
        details = get_live_details(fid)
        text = prompt_base + (("\n\n" + details) if details else "")
        raw = analyze_with_claude(
            text, model=CLAUDE_MODEL_V2,
            system_prompt=SYSTEM_PROMPT_LIVE_V2, max_tokens=600,
            thinking_budget=LIVE_THINKING_BUDGET,
        )
        return raw, True
    return analyze_with_claude(prompt_base), False


def live_pulse(fid: str, home: str, away: str, league_line: str,
               score: str, minute, prev_pulse: str) -> str:
    """نبضة مراقبة بين الأحداث لمباراة من قائمة التركيز: يقرأ المحرك 2 الوضع
    الحي كاملاً ويقارنه بقراءته السابقة. يرجع نص التنبيه أو '' إذا لا جديد."""
    details = get_live_details(fid)
    ctx = (
        f"مباراة جارية: {home} ضد {away} — {league_line}. "
        f"النتيجة {score}، الدقيقة {minute}.\n"
        f"قراءتك السابقة:\n{prev_pulse or 'لا توجد قراءة سابقة (هذه أول نبضة).'}"
        + (("\n\n" + details) if details else "")
    )
    raw = analyze_with_claude(
        ctx, model=CLAUDE_MODEL_V2, system_prompt=SYSTEM_PROMPT_PULSE,
        max_tokens=600, thinking_budget=LIVE_THINKING_BUDGET,
    )
    if not raw or raw.strip().startswith("لا جديد") or raw.startswith("(تعذر"):
        return ""
    return raw.strip()


def parse_claude_reply(text: str):
    """يفصل سطر الأسماء العربية عن نص التحليل. يرجع (dict أو None, التحليل)."""
    names = None
    body = []
    for line in text.splitlines():
        s = line.strip()
        if names is None and s.startswith("الأسماء:"):
            parts = [p.strip() for p in s[len("الأسماء:"):].split("|")]
            if len(parts) == 3 and all(parts):
                names = {"home": parts[0], "away": parts[1], "league": parts[2]}
            continue
        if s:
            body.append(s)
    return names, "\n".join(body)


def send_telegram(text: str) -> None:
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=30,
        )
    except Exception as e:
        print("Telegram error:", e)


# ================== المنطق الرئيسي ==================
def main() -> None:
    missing = [
        name
        for name, val in [
            ("API_FOOTBALL_KEY", API_FOOTBALL_KEY),
            ("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY),
            ("TELEGRAM_TOKEN", TELEGRAM_TOKEN),
            ("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID),
        ]
        if not val
    ]
    if missing:
        print("مفاتيح ناقصة في Secrets:", ", ".join(missing))
        sys.exit(1)

    state = load_state()
    analyses_used = 0
    pulse_used = 0                  # عداد نبضات المحرك 2 في هذه التشغيلة
    live_budget = {"used": 0}       # عداد مباريات المحرك 2 المباشر في هذه التشغيلة
    wl_data = load_watchlist_data() # قائمة التركيز — تتحكم بمن يستحق تنبيه تيليجرام
    watch = valid_watch_fids(wl_data)
    wl_dirty = False

    try:
        fixtures = get_live_fixtures()
    except Exception as e:
        print("فشل سحب المباريات:", e)
        sys.exit(0)  # لا نفشّل التشغيلة، نحاول في الجولة القادمة

    live_ids = set()

    for fx in fixtures:
        league = fx.get("league", {}) or {}
        if is_excluded(league):
            continue

        fixture = fx.get("fixture", {}) or {}
        teams = fx.get("teams", {}) or {}
        goals = fx.get("goals", {}) or {}

        fid = str(fixture.get("id"))
        status = ((fixture.get("status") or {}).get("short")) or ""
        minute = ((fixture.get("status") or {}).get("elapsed")) or 0
        home = (teams.get("home") or {}).get("name", "?")
        away = (teams.get("away") or {}).get("name", "?")
        home_logo = (teams.get("home") or {}).get("logo", "")
        away_logo = (teams.get("away") or {}).get("logo", "")
        league_logo = league.get("logo", "")
        gh = goals.get("home")
        ga = goals.get("away")
        gh = 0 if gh is None else gh
        ga = 0 if ga is None else ga
        score = f"{gh}-{ga}"
        league_line = f"{league.get('name', '?')} ({league.get('country', '?')})"

        live_ids.add(fid)
        prev = state.get(fid)
        alert_ok = should_alert(league, fid, watch)

        # --- حدث 1: مباراة جديدة بدأت ---
        if prev is None and status in LIVE_STATUSES:
            ar_names = None
            analysis = ""
            enriched = False
            # التحليل يُطلب فقط لمباراة سنرسل تنبيهها (توفير Claude)
            if alert_ok and should_analyze(league, analyses_used):
                raw, enriched = analyze_match(
                    f"مباراة حية بدأت الآن: {home} ضد {away} — {league_line}. "
                    f"النتيجة {score}، الدقيقة {minute}. "
                    f"أعطني توقعك النهائي لهذه المباراة.",
                    league, fid, live_budget, watch,
                )
                analyses_used += 1
                ar_names, analysis = parse_claude_reply(raw)
            h_disp = ar_names["home"] if ar_names else home
            a_disp = ar_names["away"] if ar_names else away
            l_disp = ar_names["league"] if ar_names else league_line
            if alert_ok:
                msg = (
                    f"⚽️ بدأت المباراة\n"
                    f"🏆 {l_disp}\n"
                    f"{h_disp} 🆚 {a_disp}\n"
                )
                if analysis:
                    label = "🤖 المحرك 2 (مباشر)" if enriched else "🤖 التوقع"
                    msg += f"\n{label}:\n{analysis}"
                send_telegram(msg)
            entry = {
                "score": score, "status": status, "minute": minute,
                "home": home, "away": away, "league": league_line,
                "home_logo": home_logo, "away_logo": away_logo, "league_logo": league_logo,
            }
            if ar_names:
                entry["ar"] = ar_names
            state[fid] = entry
            continue

        if prev is None:
            # مباراة بحالة غير حية (توقف/تأجيل) — نسجلها بدون تنبيه
            state[fid] = {
                "score": score, "status": status, "minute": minute,
                "home": home, "away": away, "league": league_line,
                "home_logo": home_logo, "away_logo": away_logo, "league_logo": league_logo,
            }
            continue

        # --- حدث 2: تغير النتيجة (هدف) ---
        ar_names = prev.get("ar")
        pulse_text = prev.get("pulse") or ""
        if score != prev.get("score") and status in LIVE_STATUSES and alert_ok:
            analysis = ""
            enriched = False
            if should_analyze(league, analyses_used):
                raw, enriched = analyze_match(
                    f"تحديث مباراة حية: {home} ضد {away} — {league_line}. "
                    f"النتيجة الآن {score} بعد هدف جديد، الدقيقة {minute}. "
                    f"هل يتغير توقعك؟ أعطني قراءة الموقف والتوقع النهائي.",
                    league, fid, live_budget, watch,
                )
                analyses_used += 1
                ar_new, analysis = parse_claude_reply(raw)
                if ar_new:
                    ar_names = ar_new
                if enriched and analysis:
                    pulse_text = analysis   # قراءة الهدف تصبح المرجع لنبضات ما بعده
            h_disp = ar_names["home"] if ar_names else home
            a_disp = ar_names["away"] if ar_names else away
            l_disp = ar_names["league"] if ar_names else league_line
            msg = (
                f"🚨 هدف!\n"
                f"🏆 {l_disp}\n"
                f"{h_disp} {gh} - {ga} {a_disp} (د{minute})\n"
            )
            if analysis:
                label = "🤖 المحرك 2 (مباشر)" if enriched else "🤖 قراءة المباراة الآن"
                msg += f"\n{label}:\n{analysis}"
            send_telegram(msg)

        # --- حدث 3: نهاية المباراة ---
        if status in FINAL_STATUSES and prev.get("status") not in FINAL_STATUSES:
            if alert_ok:
                h_disp = ar_names["home"] if ar_names else home
                a_disp = ar_names["away"] if ar_names else away
                l_disp = ar_names["league"] if ar_names else league_line
                send_telegram(
                    f"🏁 انتهت المباراة\n"
                    f"🏆 {l_disp}\n"
                    f"{h_disp} {gh} - {ga} {a_disp}"
                )
            # تسجيل النتيجة النهائية لمباراة من قائمة التركيز (لملخص نهاية اليوم)
            wl_entry = (wl_data.get("matches") or {}).get(fid)
            if fid in watch and wl_entry is not None and not wl_entry.get("result"):
                wl_entry["result"] = f"{gh}-{ga}"
                wl_dirty = True

        # --- نبض المحرك 2: مراقبة مستمرة لمباريات قائمة التركيز بين الأحداث ---
        # (لا هدف هذه الجولة — لكن هل يتشكل سيناريو خطر؟ ركنيات، هدف قادم،
        #  كلا الفريقين يسجلان، لاعب يهدد، بطاقة... يرسل فقط عند وجود جديد)
        if (alert_ok and fid in watch and status in PULSE_STATUSES
                and score == prev.get("score")
                and pulse_used < MAX_PULSE_PER_RUN):
            pulse_used += 1
            alert = live_pulse(fid, home, away, league_line, score, minute, pulse_text)
            if alert:
                h_disp = ar_names["home"] if ar_names else home
                a_disp = ar_names["away"] if ar_names else away
                send_telegram(
                    f"👁 عين المحرك 2 — {h_disp} {gh} - {ga} {a_disp} (د{minute})\n\n{alert}"
                )
                pulse_text = alert

        entry = {
            "score": score, "status": status, "minute": minute,
            "home": home, "away": away, "league": league_line,
                "home_logo": home_logo, "away_logo": away_logo, "league_logo": league_logo,
        }
        if ar_names:
            entry["ar"] = ar_names
        if pulse_text:
            entry["pulse"] = pulse_text
        state[fid] = entry

    # تنظيف الذاكرة: نحذف المباريات التي لم تعد حية
    for fid in list(state.keys()):
        if fid not in live_ids:
            del state[fid]

    # ملخص نهاية اليوم: يُرسل فور انتهاء آخر مباراة في قائمة التركيز (مرة واحدة)
    if watch and not wl_data.get("results_sent") and all_focus_finished(wl_data, watch):
        focus_matches = {
            f: e for f, e in (wl_data.get("matches") or {}).items() if f in watch
        }
        send_telegram(build_focus_summary(focus_matches))
        wl_data["results_sent"] = True
        wl_dirty = True
    if wl_dirty:
        WATCHLIST_FILE.write_text(
            json.dumps(wl_data, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    save_state(state)
    print(
        f"تم: {len(live_ids)} مباراة حية (بعد الفلترة)، تحليلات مستخدمة: {analyses_used}، "
        f"منها بالمحرك 2 المباشر: {live_budget['used']}، نبضات: {pulse_used}، "
        f"قائمة التركيز: {len(watch)}"
    )


if __name__ == "__main__":
    main()
