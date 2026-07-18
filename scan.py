# -*- coding: utf-8 -*-
"""
مسح حي عالمي — عند الطلب
-------------------------
يسحب كل المباريات الحية الآن حول العالم (+1200 دوري)، يستبعد
(الودية / الأفريقية / الهند / باكستان / بنغلادش)، يطلب من Claude توقعاً
سريعاً لكل مباراة في نداء واحد، ويرسل المسح كاملاً على تيليجرام.

التشغيل: من GitHub → تبويب Actions → Live Scan → Run workflow
التكلفة لكل مسح: طلب واحد من حصة API-Football + نداء Claude واحد.
"""

import os
import re
import sys

import requests

# ================== المفاتيح (تُقرأ من GitHub Secrets) ==================
API_FOOTBALL_KEY  = os.environ.get("API_FOOTBALL_KEY", "").strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

# ================== الإعدادات ==================
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
MAX_PREDICTIONS = 50   # أقصى عدد مباريات تأخذ توقعاً في المسح الواحد

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


def is_excluded(league: dict) -> bool:
    country = (league.get("country") or "").strip().lower()
    name = (league.get("name") or "").strip().lower()
    if country in EXCLUDED_COUNTRIES:
        return True
    return any(kw in name for kw in EXCLUDED_LEAGUE_KEYWORDS)


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


def send_telegram(text: str) -> None:
    """يرسل النص مقسماً إذا تجاوز حد تيليجرام (4096 حرف)."""
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > 3500:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    for chunk in chunks:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk},
                timeout=30,
            )
        except Exception as e:
            print("Telegram error:", e)


def get_batch_predictions(matches: list) -> dict:
    """نداء Claude واحد يرجع الأسماء بالعربي + توقعاً لكل مباراة مرقمة."""
    listing = "\n".join(
        f"{i+1}. {m['home']} ضد {m['away']} — {m['league']} ({m['country']}) "
        f"— النتيجة {m['score']} — الدقيقة {m['minute']}"
        for i, m in enumerate(matches)
    )
    system_prompt = (
        "أنت خبير توقع مباريات كرة قدم. ستصلك قائمة مباريات حية مرقمة بأسماء إنجليزية. "
        "أرجع لكل مباراة سطراً واحداً فقط بنفس رقمها وبهذا الشكل بالضبط:\n"
        "1| الفريق المضيف بالعربي | الفريق الضيف بالعربي | البطولة بالعربي (الدولة بالعربي) | "
        "التوقع: [اسم الفريق بالعربي أو تعادل] — ثقة X%\n"
        "استخدم الأسماء العربية الشائعة في الإعلام الرياضي "
        "(مثال: Real Madrid → ريال مدريد)، وإذا كان الاسم غير مشهور فاكتبه بحروف عربية. "
        "اعتمد في التوقع على معرفتك بالفريقين وعلى النتيجة والدقيقة. "
        "استخدم الأرقام الإنجليزية (0-9) فقط ولا تستخدم الأرقام العربية (٠-٩) أبداً. "
        "لا تكتب أي شيء آخر غير الأسطر المرقمة."
    )
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
                "max_tokens": 4000,
                "system": system_prompt,
                "messages": [{"role": "user", "content": listing}],
            },
            timeout=120,
        )
        r.raise_for_status()
        text = "".join(
            b.get("text", "")
            for b in r.json().get("content", [])
            if b.get("type") == "text"
        )
    except Exception as e:
        print("Claude error:", e)
        return {}

    results = {}
    for line in text.splitlines():
        match = re.match(r"^\s*(\d+)\s*\|(.+)$", line.strip())
        if not match:
            continue
        parts = [p.strip() for p in match.group(2).split("|")]
        idx = int(match.group(1))
        if len(parts) >= 4:
            results[idx] = {
                "home": parts[0],
                "away": parts[1],
                "league": parts[2],
                "pred": parts[3],
            }
        elif parts and parts[-1]:
            results[idx] = {"pred": parts[-1]}
    return results


def main() -> None:
    missing = [
        n for n, v in [
            ("API_FOOTBALL_KEY", API_FOOTBALL_KEY),
            ("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY),
            ("TELEGRAM_TOKEN", TELEGRAM_TOKEN),
            ("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID),
        ] if not v
    ]
    if missing:
        print("مفاتيح ناقصة في Secrets:", ", ".join(missing))
        sys.exit(1)

    try:
        fixtures = get_live_fixtures()
    except Exception as e:
        send_telegram(f"⚠️ تعذر سحب المباريات الحية: {e}")
        sys.exit(0)

    matches = []
    for fx in fixtures:
        league = fx.get("league", {}) or {}
        if is_excluded(league):
            continue
        fixture = fx.get("fixture", {}) or {}
        teams = fx.get("teams", {}) or {}
        goals = fx.get("goals", {}) or {}
        gh = goals.get("home") or 0
        ga = goals.get("away") or 0
        matches.append({
            "home": (teams.get("home") or {}).get("name", "?"),
            "away": (teams.get("away") or {}).get("name", "?"),
            "league": league.get("name", "?"),
            "country": league.get("country", "?"),
            "league_id": league.get("id"),
            "score": f"{gh}-{ga}",
            "minute": ((fixture.get("status") or {}).get("elapsed")) or 0,
        })

    if not matches:
        send_telegram(
            "📡 مسح حي عالمي\n\n"
            "لا توجد مباريات حية الآن في الدوريات المشمولة.\n"
            "جرّب المسح مرة أخرى وقت المباريات."
        )
        return

    # الترتيب: الدوريات الكبرى أولاً، ثم الباقي حسب الدولة
    matches.sort(
        key=lambda m: (
            0 if m["league_id"] in TOP_LEAGUE_IDS else 1,
            m["country"],
            m["league"],
        )
    )

    predictions = get_batch_predictions(matches[:MAX_PREDICTIONS])

    lines = [f"📡 مسح حي عالمي — {len(matches)} مباراة جارية الآن", ""]
    current_section = None
    for i, m in enumerate(matches):
        section = "🌟 الدوريات الكبرى" if m["league_id"] in TOP_LEAGUE_IDS else "🌍 بقية العالم"
        if section != current_section:
            lines.append(f"\n{section}")
            lines.append("—" * 20)
            current_section = section
        info = predictions.get(i + 1) or {}
        h_disp = info.get("home") or m["home"]
        a_disp = info.get("away") or m["away"]
        l_disp = info.get("league") or f"{m['league']} ({m['country']})"
        lines.append(f"⚽ {h_disp} {m['score']} {a_disp} (د{m['minute']})")
        lines.append(f"   🏆 {l_disp}")
        pred = info.get("pred")
        if pred:
            lines.append(f"   🤖 {pred}")
        lines.append("")

    if len(matches) > MAX_PREDICTIONS:
        lines.append(
            f"ℹ️ التوقعات لأول {MAX_PREDICTIONS} مباراة (الأهم). "
            f"لتحليل مفصل لأي مباراة، اطلبها من Claude في البروجكت."
        )

    send_telegram("\n".join(lines))
    print(f"تم المسح: {len(matches)} مباراة، {len(predictions)} توقع")


if __name__ == "__main__":
    main()
