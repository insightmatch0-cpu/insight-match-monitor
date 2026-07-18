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
import re
import sys
import time
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
MAX_PULSE_PER_RUN = 12           # حد نداءات Claude للنبض في التشغيلة الواحدة
PULSE_STATUSES = {"1H", "2H", "ET"}   # لا نبض في الاستراحة/الركلات الترجيحية

# ---- الرصد السريع (قائمة التركيز فقط): فحص كل ~90 ثانية بدل 10 دقائق ----
# بعد الجولة العادية تبقى التشغيلة مستيقظة وتفحص مباريات القائمة الحية كل
# 90 ثانية (طلب المالك: تنبيه خلال دقيقة إلى دقيقتين). نداء Claude يحدث فقط
# عند تحرك حقيقي في الأرقام (ركنية، تسديدة على المرمى، بطاقة...) — البصمة أدناه.
FOCUS_SWEEP_SECONDS = 90
FOCUS_LOOP_BUDGET_SECONDS = 8 * 60   # ثم نسلّم للتشغيلة التالية (كل 10 دقائق)
SIG_STATS = {
    "Corner Kicks", "Shots on Goal", "Total Shots",
    "Yellow Cards", "Red Cards", "Goalkeeper Saves",
}
SIG_THRESHOLDS = {
    "Corner Kicks": 2, "Shots on Goal": 2, "Total Shots": 3,
    "Yellow Cards": 1, "Red Cards": 1, "Goalkeeper Saves": 2,
}

# ---- تقرير ما قبل المباراة (قائمة التركيز فقط) ----
# قبل ~45 دقيقة من الانطلاق يرسل المحرك 2 تقرير سيناريوهات شاملاً لكل مباراة
# تركيز (طلب المالك 2026-07-15). يمكن توسيع النافذة مؤقتاً عبر متغير البيئة
# PREMATCH_WINDOW (زر التشغيل اليدوي في monitor.yml).
PREMATCH_REPORT_MINUTES = int(os.environ.get("PREMATCH_WINDOW", "").strip() or 45)

# ذاكرة تقارير السيناريوهات: كل تقرير ما قبل مباراة يُحفظ هنا، ويقيّمه
# predict_v2.py صباحاً مقابل البيانات النهائية الحقيقية ويستخلص دروساً
SCENARIOS_FILE = Path("scenarios_v2.json")
LESSONS_FILE = Path("lessons_v2.json")   # دروس المحرك 2 — تُحقن في تقرير ما قبل المباراة
REFEREES_FILE = Path("referees.json")    # قاعدة الحكام الذاتية (يبنيها predict_v2)

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
    40,   # دوري البطولة الإنجليزية (تشامبيونشيب)
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
    417,  # الدوري البحريني الممتاز
    542,  # الدوري العراقي الممتاز
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
    "Shots on Goal", "Shots off Goal", "Total Shots", "Blocked Shots",
    "Shots insidebox", "Shots outsidebox", "Ball Possession", "Corner Kicks",
    "Offsides", "Yellow Cards", "Red Cards", "expected_goals",
    "Goalkeeper Saves", "Fouls",
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

# قائمة السيناريوهات الكاملة التي يغطيها المحرك 2 المباشر (طلب المالك —
# مستوحاة من أسواق التحليل الاحترافية). تُحقن في تحليل الأحداث وفي النبض.
SCENARIO_MENU_V2 = (
    "قائمة السيناريوهات التي تراقبها (اذكر فقط المرجح فعلاً الآن، أبرز 2-3):\n"
    "- الهدف القادم: أي فريق، ومن المرشح لتسجيله أو صناعته بالاسم\n"
    "- كلا الفريقين يسجلان؟ ومسار إجمالي الأهداف (مباراة مفتوحة أم مقفلة)\n"
    "- الركنيات: موجة ركنيات قادمة، وأي فريق يكسب أغلبها\n"
    "- الكرات الثابتة: ركلة حرة خطرة قادمة، رميات تماس طويلة قرب المنطقة، "
    "ركلات مرمى/تشتيت متكرر يدل على ضغط مستمر\n"
    "- البطاقات: لاعب مرشح للإنذار بالاسم (تدخلات وأخطاء متكررة)، بطاقة حمراء "
    "محتملة تغير المباراة، كلا الفريقين ينالان بطاقات\n"
    "- حارس تحت الحصار (تصديات متتالية)، تسلل متكرر يقتل هجمات فريق\n"
    "- شكل النهاية: هامش الفوز المرجح، أي شوط أغزر أهدافاً، انقلاب محتمل في "
    "النتيجة بين الشوطين، أو المتقدم يغلق المباراة\n"
    "- في مباريات الكأس/الإقصاء فقط: احتمال انتهاء الوقت الأصلي بالتعادل "
    "والامتداد للأشواط الإضافية أو ركلات الترجيح (%)، ومن الأوفر حظاً في "
    "الترجيح (خبرة الحارس والمنفذين) — درس كيري×شيلبورن 2026-07-18\n"
)

SYSTEM_PROMPT_LIVE_V2 = (
    "أنت محلل مباريات حية من الطراز الأول. سيصلك وضع مباراة جارية بأسماء إنجليزية، "
    "وقد يتضمن إحصائيات حية (تسديدات، استحواذ، ركنيات، xG، تسلل، تصديات، بطاقات) "
    "وقائمة أحداث (أهداف بأسماء المسجلين، بطاقات، تبديلات). اعتمد على هذه البيانات أولاً.\n"
    + SCENARIO_MENU_V2 +
    "أرجع ردك بهذا الشكل بالضبط:\n"
    "الأسماء: [الفريق المضيف بالعربي] | [الفريق الضيف بالعربي] | [البطولة بالعربي (الدولة بالعربي)]\n"
    "ثم قراءة مركزة في 2-3 أسطر: من يسيطر فعلياً (الاستحواذ وحده يخدع — اربطه بالخطورة)، "
    "وأثر أي طرد أو تبديل هجومي.\n"
    "ثم سطر يبدأ بـ: السيناريو: أخطر 2-3 سيناريوهات مرجحة من القائمة أعلاه بتفاصيلها "
    "(الفريق، اللاعب بالاسم إن دلت الأحداث عليه، ونسبة تقديرية لكل سيناريو).\n"
    "ثم سطر أخير: التوقع: [اسم الفريق بالعربي أو تعادل] — ثقة X%\n"
    "استخدم الأسماء العربية الشائعة في الإعلام الرياضي، وإذا كان الاسم غير مشهور "
    "فاكتبه بحروف عربية. "
    "استخدم الأرقام الإنجليزية (0-9) فقط ولا تستخدم الأرقام العربية (٠-٩) أبداً."
)


SYSTEM_PROMPT_PULSE = (
    "أنت عين حية على مباراة جارية من قائمة تركيز المستخدم. سيصلك وضع المباراة "
    "مع إحصائياتها وأحداثها الحية، وقراءتك السابقة قبل نحو 10 دقائق.\n"
    "مهمتك: هل يتشكل الآن سيناريو مهم جديد يستحق تنبيه المستخدم؟\n"
    + SCENARIO_MENU_V2 +
    "وكذلك أي انقلاب في السيطرة أو تغير جوهري في نمط المباراة.\n"
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
        # نموذج المحرك 2 يفكر تلقائياً وبعمق افتراضياً — أي معامل تفكير إضافي
        # يُرفض (خطأ 400). نكتفي بنفس شكل الطلب البسيط الذي يعمل في predict_v2
        # مع متسع أكبر في max_tokens للتفكير + الرد النهائي.
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
        # نص خطأ الـ API (لا يتضمن أي مفتاح) — ضروري لتشخيص أخطاء 400 من السجلات
        detail = ""
        resp = getattr(e, "response", None)
        if resp is not None:
            try:
                detail = " — " + resp.text[:300]
            except Exception:
                pass
        print(f"Claude error: {e}{detail}")
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


SYSTEM_PROMPT_PREMATCH = (
    "أنت محلل ما قبل المباراة للمحرك 2 — من الطراز الأول. سيصلك سياق مباراة "
    "تنطلق قريباً: توقعات المحركين واحتمالاتهما، وقد يتضمن تشكيلات معلنة، "
    "إصابات، أرقام سوق المراهنات، ومقارنة إحصائية للفريقين. اعتمد على البيانات "
    "أولاً ثم معرفتك بالفريقين.\n"
    + SCENARIO_MENU_V2 +
    "أرجع تقريراً عربياً منظماً بهذه البنود (سطر لكل بند، مع نسبة تقديرية):\n"
    "⚽ النتيجة المتوقعة وهامش الفوز\n"
    "🥅 كلا الفريقين يسجلان؟ وإجمالي الأهداف المتوقع (فوق/تحت 2.5)\n"
    "🎯 المسجل المحتمل بالاسم (وصانع اللعب الأخطر)\n"
    "🚩 الركنيات: من يكسب أكثر وتقدير إجماليها\n"
    "🟨 البطاقات: لاعبون مرشحون بالاسم إن أمكن، واحتمال بطاقة حمراء\n"
    "⚡ الكرات الثابتة: خطورة الركلات الحرة والرميات الطويلة\n"
    "⏱ نمط الشوطين: أيهما أغزر أهدافاً، واحتمال انقلاب النتيجة\n"
    "🔑 مفتاح المباراة: المعركة الحاسمة التي تحسم اللقاء\n"
    "ثم سطر أخير: تذكير: هذه توقعات تحليلية وليست ضمانات.\n"
    "استخدم الأسماء العربية الشائعة في الإعلام الرياضي والأرقام الإنجليزية "
    "(0-9) فقط، لا الأرقام العربية (٠-٩)."
)


def team_news_headlines(team: str) -> list:
    """عناوين مستهدفة للفريق من Google News RSS (مجاني) — خطوة استكشاف 5:
    الأخبار الصغيرة (انتقال، أزمة، غياب) قبل أن تصل للعناوين الكبرى."""
    if not team:
        return []
    try:
        import html as html_mod
        r = requests.get(
            "https://news.google.com/rss/search",
            params={"q": f'"{team}" football', "hl": "en", "gl": "US",
                    "ceid": "US:en"},
            timeout=15,
        )
        titles = re.findall(r"<title>(.*?)</title>", r.text)[1:6]
        return [html_mod.unescape(t).strip() for t in titles if t.strip()]
    except Exception as e:
        print("أخبار الفريق — فشل الجلب:", e)
        return []


def build_prematch_context(fid: str, v2p: dict, v1p: dict, userp: dict) -> str:
    """يجمع سياق التقرير: توقعات المحركين والمالك + بيانات API قبل المباراة
    (تشكيلات إن أُعلنت، إصابات، أرقام السوق، التوقع الإحصائي) — 4 نداءات API."""
    p = v2p or v1p or {}
    lines = [
        f"مباراة تنطلق قريباً: {p.get('home', '?')} ضد {p.get('away', '?')} — "
        f"{p.get('league', '')}."
    ]
    if v2p:
        lines.append(
            f"توقع المحرك 2: {v2p.get('pick')} "
            f"(احتمالات {v2p.get('prob_home')}/{v2p.get('prob_draw')}/{v2p.get('prob_away')}) — "
            f"السبب: {v2p.get('reason', '')}"
        )
    if v1p:
        lines.append(f"توقع المحرك 1: {v1p.get('pick')} بثقة {v1p.get('confidence')}%")
    if userp:
        lines.append(f"توقع المالك: {userp.get('pick')}")
    try:
        lu = []
        for side in api_football(f"fixtures/lineups?fixture={fid}"):
            team = (side.get("team") or {}).get("name", "?")
            formation = side.get("formation") or "?"
            starters = [((x.get("player") or {}).get("name") or "?")
                        for x in (side.get("startXI") or [])]
            if starters:
                lu.append(f"{team} ({formation}): " + ", ".join(starters))
        if lu:
            lines.append("التشكيلات المعلنة:\n" + "\n".join(lu))
    except Exception as e:
        print("تقرير ما قبل المباراة — فشل التشكيلات:", e)
    try:
        inj = [f"{((i.get('player') or {}).get('name') or '?')} "
               f"({(i.get('team') or {}).get('name', '?')}: "
               f"{(i.get('player') or {}).get('reason', '?')})"
               for i in api_football(f"injuries?fixture={fid}")[:12]]
        if inj:
            lines.append("الإصابات/الغيابات: " + "، ".join(inj))
    except Exception as e:
        print("تقرير ما قبل المباراة — فشل الإصابات:", e)
    try:
        for bk in (api_football(f"odds?fixture={fid}") or [{}])[0].get("bookmakers", [])[:1]:
            for bet in bk.get("bets", []):
                if bet.get("name") == "Match Winner":
                    vals = {v.get("value"): v.get("odd") for v in bet.get("values", [])}
                    lines.append(f"أرقام السوق (1X2): {vals}")
    except Exception as e:
        print("تقرير ما قبل المباراة — فشل أرقام السوق:", e)
    try:
        for pr in api_football(f"predictions?fixture={fid}")[:1]:
            pred = pr.get("predictions") or {}
            pct = pred.get("percent") or {}
            lines.append(
                f"التوقع الإحصائي: {pct} — نصيحة: {pred.get('advice', '')} — "
                f"فوز مرجح: {((pred.get('winner') or {}).get('name'))}"
            )
            comp = pr.get("comparison") or {}
            if comp:
                lines.append(f"مقارنة الفريقين: {json.dumps(comp, ensure_ascii=False)[:600]}")
    except Exception as e:
        print("تقرير ما قبل المباراة — فشل التوقع الإحصائي:", e)
    # الحكم المعلن + سجله من قاعدتنا الذاتية (بعض الحكام يشهرون بغزارة)
    try:
        fx = api_football(f"fixtures?ids={fid}")
        referee = ((fx[0].get("fixture") or {}).get("referee") or "") if fx else ""
        if referee:
            rec = (load_json_file(REFEREES_FILE, {}) or {}).get(referee.strip())
            if rec and rec.get("matches"):
                avg_y = round(rec["yellows"] / rec["matches"], 1)
                lines.append(
                    f"الحكم: {referee} — من سجلنا: معدل {avg_y} بطاقة صفراء"
                    f" و{rec.get('reds', 0)} حمراء في {rec['matches']} مباراة."
                )
            else:
                lines.append(f"الحكم: {referee} (استخدم معرفتك بأسلوبه إن كان مشهوراً).")
    except Exception as e:
        print("تقرير ما قبل المباراة — فشل جلب الحكم:", e)
    # أخبار مستهدفة للفريقين (الأخبار الصغيرة تصنع فرقاً — توجيه المالك)
    news_lines = []
    for team in (p.get("home"), p.get("away")):
        for title in team_news_headlines(team):
            news_lines.append(f"- {title}")
    if news_lines:
        lines.append("أخبار حديثة مستهدفة للفريقين (استخدم المؤثر منها فقط):\n"
                     + "\n".join(news_lines[:10]))
    # دروس المحرك 2 من تقييم تقاريره السابقة — حلقة التعلم الذاتي للسيناريوهات
    lessons = (load_json_file(LESSONS_FILE, {}).get("lessons") or [])[-15:]
    lesson_lines = [f"- {(it.get('text') or '').strip()}" for it in lessons
                    if isinstance(it, dict) and (it.get("text") or "").strip()]
    if lesson_lines:
        lines.append("دروس من أخطائك السابقة (طبقها في هذا التقرير):\n"
                     + "\n".join(lesson_lines))
    return "\n".join(lines)


def prematch_reports(wl_data: dict, watch: set) -> bool:
    """يرسل تقرير سيناريوهات المحرك 2 لكل مباراة تركيز تنطلق خلال
    PREMATCH_REPORT_MINUTES دقيقة (مرة واحدة لكل مباراة — علم prematch_sent).
    يحفظ watchlist.json فوراً عند الإرسال حتى لا يتكرر التقرير لو فشل ما بعده."""
    if not watch:
        return False
    v2_pending = load_json_file(Path("predictions_v2.json"), {}).get("pending") or {}
    v1_pending = load_json_file(Path("predictions.json"), {}).get("pending") or {}
    user_pending = load_json_file(Path("predictions_user.json"), {}).get("pending") or {}
    now = datetime.now(timezone.utc)
    dirty = False
    for fid in sorted(watch):
        entry = (wl_data.get("matches") or {}).get(fid)
        if entry is None or entry.get("prematch_sent") or entry.get("result"):
            continue
        p = v2_pending.get(fid) or v1_pending.get(fid) or {}
        try:
            kickoff = datetime.fromisoformat(p.get("kickoff", ""))
        except Exception:
            continue
        if kickoff <= now:                                   # بدأت — فات الوقت
            continue
        minutes_left = (kickoff - now).total_seconds() / 60
        if minutes_left > PREMATCH_REPORT_MINUTES:
            continue
        ctx = build_prematch_context(fid, v2_pending.get(fid),
                                     v1_pending.get(fid), user_pending.get(fid))
        report = analyze_with_claude(
            ctx, model=CLAUDE_MODEL_V2, system_prompt=SYSTEM_PROMPT_PREMATCH,
            max_tokens=900, thinking_budget=LIVE_THINKING_BUDGET,
        )
        if report.startswith("(تعذر"):
            continue                                          # نحاول في الجولة القادمة
        h = p.get("ar_home") or p.get("home", "?")
        a = p.get("ar_away") or p.get("away", "?")
        league = p.get("ar_league") or p.get("league", "")
        send_telegram(
            f"📋 تقرير المحرك 2 — ما قبل المباراة\n"
            f"🏆 {league}\n{h} 🆚 {a}\n"
            f"⏰ الانطلاق خلال ~{int(minutes_left)} دقيقة\n\n{report}"
        )
        entry["prematch_sent"] = True
        dirty = True
        # حفظ التقرير للتقييم الذاتي: predict_v2 يقارنه صباحاً بالبيانات
        # النهائية الحقيقية ويستخلص دروساً تتحسن بها التقارير القادمة
        scen = load_json_file(SCENARIOS_FILE, {"pending": {}, "resolved": []})
        scen.setdefault("pending", {})
        scen["pending"][fid] = {
            "fid": fid,
            "date": p.get("date") or (p.get("kickoff") or "")[:10],
            "kickoff": p.get("kickoff", ""),
            "home": p.get("home", "?"), "away": p.get("away", "?"),
            "ar_home": p.get("ar_home", ""), "ar_away": p.get("ar_away", ""),
            "league": p.get("ar_league") or p.get("league", ""),
            "report": report,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        SCENARIOS_FILE.write_text(
            json.dumps(scen, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    if dirty:
        WATCHLIST_FILE.write_text(
            json.dumps(wl_data, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return dirty


def live_signature(fid: str) -> dict:
    """بصمة أرقام المباراة (نداء API واحد): تحركها يعني حدثاً يستحق نبضة.
    ترجع {} عند أي فشل — فلا نخزنها كأساس."""
    sig = {}
    try:
        for side in api_football(f"fixtures/statistics?fixture={fid}"):
            team = (side.get("team") or {}).get("id")
            for s in side.get("statistics") or []:
                t, v = s.get("type"), s.get("value")
                if t in SIG_STATS and v is not None:
                    try:
                        sig[f"{team}:{t}"] = int(str(v).replace("%", ""))
                    except Exception:
                        pass
    except Exception as e:
        print("فشل سحب بصمة الإحصائيات:", e)
    return sig


def significant_delta(base: dict, now: dict) -> bool:
    """هل تحركت الأرقام بما يكفي منذ آخر نبضة؟ (ركنيتان، تسديدتان على المرمى،
    أي بطاقة، ...) — البوابة الحتمية التي توفر نداءات Claude في الرصد السريع."""
    for key, val in now.items():
        stat = key.split(":", 1)[-1]
        if val - base.get(key, 0) >= SIG_THRESHOLDS.get(stat, 10**9):
            return True
    return False


def focus_fast_watch(state: dict, wl_data: dict, watch: set,
                     live_budget: dict, pulses: dict) -> bool:
    """الرصد السريع لمباريات قائمة التركيز: بعد الجولة العادية تبقى التشغيلة
    مستيقظة حتى ~8 دقائق وتفحص مباريات القائمة كل ~90 ثانية — هدف/بداية/نهاية
    تُعلن فوراً، والنبض يعمل فقط عند تحرك الأرقام (significant_delta).
    يرجع True إذا سجل نتيجة نهائية في watchlist (تعديل يجب حفظه)."""
    if not watch:
        return False
    wl_dirty = False
    deadline = time.monotonic() + FOCUS_LOOP_BUDGET_SECONDS
    ids = "-".join(sorted(watch))
    while time.monotonic() < deadline:
        time.sleep(FOCUS_SWEEP_SECONDS)
        try:
            fixtures = api_football(f"fixtures?ids={ids}")
        except Exception as e:
            print("الرصد السريع: فشل السحب:", e)
            continue
        keep = False
        for fx in fixtures:
            fixture = fx.get("fixture", {}) or {}
            fid = str(fixture.get("id"))
            if fid not in watch:
                continue
            league = fx.get("league", {}) or {}
            teams = fx.get("teams", {}) or {}
            goals = fx.get("goals", {}) or {}
            status = ((fixture.get("status") or {}).get("short")) or ""
            minute = ((fixture.get("status") or {}).get("elapsed")) or 0
            home = (teams.get("home") or {}).get("name", "?")
            away = (teams.get("away") or {}).get("name", "?")
            gh = goals.get("home") or 0
            ga = goals.get("away") or 0
            score = f"{gh}-{ga}"
            league_line = f"{league.get('name', '?')} ({league.get('country', '?')})"

            if status in LIVE_STATUSES:
                keep = True
            elif status == "NS":
                # لم تبدأ بعد — نواصل الانتظار إن كانت الانطلاقة قريبة
                try:
                    ko = datetime.fromisoformat(
                        (fixture.get("date") or "").replace("Z", "+00:00"))
                    if ko - datetime.now(timezone.utc) <= timedelta(minutes=12):
                        keep = True
                except Exception:
                    pass
                continue

            prev = state.get(fid)

            # بداية مباراة التقطها الرصد السريع قبل الجولة القادمة
            if prev is None and status in LIVE_STATUSES:
                raw, enriched = analyze_match(
                    f"مباراة حية بدأت الآن: {home} ضد {away} — {league_line}. "
                    f"النتيجة {score}، الدقيقة {minute}. "
                    f"أعطني توقعك النهائي لهذه المباراة.",
                    league, fid, live_budget, watch,
                )
                ar_names, analysis = parse_claude_reply(raw)
                h = ar_names["home"] if ar_names else home
                a = ar_names["away"] if ar_names else away
                l = ar_names["league"] if ar_names else league_line
                msg = f"⚽️ بدأت المباراة\n🏆 {l}\n{h} 🆚 {a}\n"
                if analysis:
                    label = "🤖 المحرك 2 (مباشر)" if enriched else "🤖 التوقع"
                    msg += f"\n{label}:\n{analysis}"
                send_telegram(msg)
                entry = {"score": score, "status": status, "minute": minute,
                         "home": home, "away": away, "league": league_line,
                         "home_logo": (teams.get("home") or {}).get("logo", ""),
                         "away_logo": (teams.get("away") or {}).get("logo", ""),
                         "league_logo": league.get("logo", "")}
                if ar_names:
                    entry["ar"] = ar_names
                if enriched and analysis:
                    entry["pulse"] = analysis
                state[fid] = entry
                continue
            if prev is None:
                continue

            ar_names = prev.get("ar")
            h = ar_names["home"] if ar_names else home
            a = ar_names["away"] if ar_names else away
            l = ar_names["league"] if ar_names else league_line

            # هدف — تنبيه فوري مع تحليل المحرك 2 الكامل
            if score != prev.get("score") and status in LIVE_STATUSES:
                raw, enriched = analyze_match(
                    f"تحديث مباراة حية: {home} ضد {away} — {league_line}. "
                    f"النتيجة الآن {score} بعد هدف جديد، الدقيقة {minute}. "
                    f"هل يتغير توقعك؟ أعطني قراءة الموقف والتوقع النهائي.",
                    league, fid, live_budget, watch,
                )
                ar_new, analysis = parse_claude_reply(raw)
                if ar_new:
                    ar_names = ar_new
                    prev["ar"] = ar_new
                    h, a = ar_new["home"], ar_new["away"]
                    l = ar_new["league"]
                msg = f"🚨 هدف!\n🏆 {l}\n{h} {gh} - {ga} {a} (د{minute})\n"
                if analysis:
                    label = "🤖 المحرك 2 (مباشر)" if enriched else "🤖 قراءة المباراة الآن"
                    msg += f"\n{label}:\n{analysis}"
                send_telegram(msg)
                if enriched and analysis:
                    prev["pulse"] = analysis
                sig = live_signature(fid)
                if sig:
                    prev["sig"] = sig
                prev.update({"score": score, "status": status, "minute": minute})
                continue

            # نهاية المباراة
            if status in FINAL_STATUSES and prev.get("status") not in FINAL_STATUSES:
                send_telegram(f"🏁 انتهت المباراة\n🏆 {l}\n{h} {gh} - {ga} {a}")
                wl_entry = (wl_data.get("matches") or {}).get(fid)
                if wl_entry is not None and not wl_entry.get("result"):
                    wl_entry["result"] = f"{gh}-{ga}"
                    wl_dirty = True
                prev.update({"score": score, "status": status, "minute": minute})
                continue

            # لا حدث — نبضة مشروطة بتحرك الأرقام فقط (توفير نداءات Claude)
            if status in PULSE_STATUSES:
                sig_now = live_signature(fid)
                base = prev.get("sig")
                if sig_now and (not base or significant_delta(base, sig_now)) \
                        and pulses["used"] < MAX_PULSE_PER_RUN:
                    pulses["used"] += 1
                    alert = live_pulse(fid, home, away, league_line,
                                       score, minute, prev.get("pulse") or "")
                    if alert:
                        send_telegram(f"👁 عين المحرك 2 — {h} {gh} - {ga} {a} "
                                      f"(د{minute})\n\n{alert}")
                        prev["pulse"] = alert
                    prev["sig"] = sig_now
            prev.update({"score": score, "status": status, "minute": minute})

        if not keep:
            break
    return wl_dirty


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
    pulses = {"used": 0}            # عداد نبضات المحرك 2 في هذه التشغيلة
    live_budget = {"used": 0}       # عداد مباريات المحرك 2 المباشر في هذه التشغيلة
    wl_data = load_watchlist_data() # قائمة التركيز — تتحكم بمن يستحق تنبيه تيليجرام
    watch = valid_watch_fids(wl_data)
    wl_dirty = False

    # تقرير سيناريوهات ما قبل المباراة (قبل ~45 دقيقة من انطلاق مباريات القائمة)
    if prematch_reports(wl_data, watch):
        wl_dirty = True

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
        sig_val = prev.get("sig")
        if (alert_ok and fid in watch and status in PULSE_STATUSES
                and score == prev.get("score")
                and pulses["used"] < MAX_PULSE_PER_RUN):
            pulses["used"] += 1
            alert = live_pulse(fid, home, away, league_line, score, minute, pulse_text)
            if alert:
                h_disp = ar_names["home"] if ar_names else home
                a_disp = ar_names["away"] if ar_names else away
                send_telegram(
                    f"👁 عين المحرك 2 — {h_disp} {gh} - {ga} {a_disp} (د{minute})\n\n{alert}"
                )
                pulse_text = alert
            # بصمة الأرقام الآن = الأساس الذي يقيس عليه الرصد السريع تحرك المباراة
            new_sig = live_signature(fid)
            if new_sig:
                sig_val = new_sig

        entry = {
            "score": score, "status": status, "minute": minute,
            "home": home, "away": away, "league": league_line,
                "home_logo": home_logo, "away_logo": away_logo, "league_logo": league_logo,
        }
        if ar_names:
            entry["ar"] = ar_names
        if pulse_text:
            entry["pulse"] = pulse_text
        if sig_val:
            entry["sig"] = sig_val
        state[fid] = entry

    # تنظيف الذاكرة: نحذف المباريات التي لم تعد حية
    for fid in list(state.keys()):
        if fid not in live_ids:
            del state[fid]

    # الرصد السريع: تبقى التشغيلة مستيقظة وتفحص مباريات قائمة التركيز
    # كل ~90 ثانية حتى تسليم الجولة التالية (تنبيه خلال دقيقة إلى دقيقتين)
    if focus_fast_watch(state, wl_data, watch, live_budget, pulses):
        wl_dirty = True

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
        f"منها بالمحرك 2 المباشر: {live_budget['used']}، نبضات: {pulses['used']}، "
        f"قائمة التركيز: {len(watch)}"
    )


if __name__ == "__main__":
    main()
