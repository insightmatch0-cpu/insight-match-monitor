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
from pathlib import Path

import requests

# ================== المفاتيح (تُقرأ من GitHub Secrets) ==================
API_FOOTBALL_KEY  = os.environ.get("API_FOOTBALL_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")

# ================== الإعدادات ==================
STATE_FILE = Path("state.json")          # ذاكرة البوت بين التشغيلات
MAX_ANALYSES_PER_RUN = 20                # حد أقصى لتحليلات Claude في التشغيلة الواحدة
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

# ---- إعدادات التغطية العالمية ----
# ANALYZE_ALL = True  → كل مباراة في العالم توصلك مع توقع (استهلاك رصيد أعلى)
# ANALYZE_ALL = False → التوقع للدوريات الكبرى فقط، والباقي إشعار نتيجة بدون تحليل
ANALYZE_ALL = True

# ALERTS_TOP_ONLY = True → زر الطوارئ: إشعارات الدوريات الكبرى فقط (إذا غرقت بالرسائل)
ALERTS_TOP_ONLY = False

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


def get_live_fixtures() -> list:
    resp = requests.get(
        "https://v3.football.api-sports.io/fixtures?live=all",
        headers={"x-apisports-key": API_FOOTBALL_KEY},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        print("API-Football errors:", data["errors"])
    return data.get("response", [])


def analyze_with_claude(context_text: str) -> str:
    """يرسل وضع المباراة لـ Claude ويرجع توقعاً مختصراً بالعربي."""
    system_prompt = (
        "أنت محلل وخبير توقع مباريات كرة قدم. اعتمد على معرفتك بقوة الفريقين "
        "ومستواهما العام، وعلى النتيجة الحالية والدقيقة وطبيعة البطولة. "
        "رد بالعربي، مباشر وبدون مقدمات، 3 أسطر كحد أقصى. "
        "اختم دائماً بسطر بهذا الشكل: التوقع: [اسم الفريق أو تعادل] — ثقة X%"
    )
    body = {
        "model": CLAUDE_MODEL,
        "max_tokens": 400,
        "system": system_prompt,
        "messages": [{"role": "user", "content": context_text}],
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
        if ALERTS_TOP_ONLY and league.get("id") not in TOP_LEAGUE_IDS:
            continue

        fixture = fx.get("fixture", {}) or {}
        teams = fx.get("teams", {}) or {}
        goals = fx.get("goals", {}) or {}

        fid = str(fixture.get("id"))
        status = ((fixture.get("status") or {}).get("short")) or ""
        minute = ((fixture.get("status") or {}).get("elapsed")) or 0
        home = (teams.get("home") or {}).get("name", "?")
        away = (teams.get("away") or {}).get("name", "?")
        gh = goals.get("home")
        ga = goals.get("away")
        gh = 0 if gh is None else gh
        ga = 0 if ga is None else ga
        score = f"{gh}-{ga}"
        league_line = f"{league.get('name', '?')} ({league.get('country', '?')})"

        live_ids.add(fid)
        prev = state.get(fid)

        # --- حدث 1: مباراة جديدة بدأت ---
        if prev is None and status in LIVE_STATUSES:
            analysis = ""
            if should_analyze(league, analyses_used):
                analysis = analyze_with_claude(
                    f"مباراة حية بدأت الآن: {home} ضد {away} — {league_line}. "
                    f"النتيجة {score}، الدقيقة {minute}. "
                    f"أعطني توقعك النهائي لهذه المباراة."
                )
                analyses_used += 1
            msg = (
                f"⚽️ بدأت المباراة\n"
                f"🏆 {league_line}\n"
                f"{home} 🆚 {away}\n"
            )
            if analysis:
                msg += f"\n🤖 التوقع:\n{analysis}"
            send_telegram(msg)
            state[fid] = {"score": score, "status": status}
            continue

        if prev is None:
            # مباراة بحالة غير حية (توقف/تأجيل) — نسجلها بدون تنبيه
            state[fid] = {"score": score, "status": status}
            continue

        # --- حدث 2: تغير النتيجة (هدف) ---
        if score != prev.get("score") and status in LIVE_STATUSES:
            analysis = ""
            if should_analyze(league, analyses_used):
                analysis = analyze_with_claude(
                    f"تحديث مباراة حية: {home} ضد {away} — {league_line}. "
                    f"النتيجة الآن {score} بعد هدف جديد، الدقيقة {minute}. "
                    f"هل يتغير توقعك؟ أعطني قراءة الموقف والتوقع النهائي."
                )
                analyses_used += 1
            msg = (
                f"🚨 هدف!\n"
                f"🏆 {league_line}\n"
                f"{home} {gh} - {ga} {away} (د{minute})\n"
            )
            if analysis:
                msg += f"\n🤖 قراءة المباراة الآن:\n{analysis}"
            send_telegram(msg)

        # --- حدث 3: نهاية المباراة ---
        if status in FINAL_STATUSES and prev.get("status") not in FINAL_STATUSES:
            send_telegram(
                f"🏁 انتهت المباراة\n"
                f"🏆 {league_line}\n"
                f"{home} {gh} - {ga} {away}"
            )

        state[fid] = {"score": score, "status": status}

    # تنظيف الذاكرة: نحذف المباريات التي لم تعد حية
    for fid in list(state.keys()):
        if fid not in live_ids:
            del state[fid]

    save_state(state)
    print(f"تم: {len(live_ids)} مباراة حية (بعد الفلترة)، تحليلات مستخدمة: {analyses_used}")


if __name__ == "__main__":
    main()
