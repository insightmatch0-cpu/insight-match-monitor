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
PREDICTIONS_FILE    = Path("predictions_v2.json")  # ذاكرة المحرك 2 (مستقلة عن المحرك 1)
V1_PREDICTIONS_FILE = Path("predictions.json")     # ذاكرة المحرك 1 (للمقارنة في الملخص فقط)
LESSONS_FILE        = Path("lessons_v2.json")      # دروس من الأخطاء (تُملأ في المرحلة 3)
NEWS_FILE           = Path("news.json")            # آخر عناوين الأخبار (سياق مشترك)

CLAUDE_MODEL = "claude-fable-5"

MAX_PREDICTIONS_24H   = 60    # نفس حد المحرك 1 — نفس المباريات
MAX_RESOLVE_CALLS     = 3     # أقصى نداءات API لتسوية نتائج الأيام السابقة
MAX_ENRICHED_FIXTURES = 15    # أقصى مباريات كبرى تأخذ سياقاً إضافياً
ENRICH_CALL_BUDGET    = 120   # سقف أمان لنداءات السياق الإضافي
ENRICHED_BATCH_SIZE   = 4     # دفعات صغيرة للمباريات ذات السياق الغني
BASIC_BATCH_SIZE      = 12    # دفعات المباريات بدون سياق (مثل المحرك 1)
MAX_LESSONS_IN_PROMPT = 15    # أحدث الدروس التي تُحقن في كل توقع

SEND_TELEGRAM_DIGEST = True
DIGEST_TOP_ONLY      = True
DASHBOARD_URL = "https://insightmatch0-cpu.github.io/insight-match-monitor/"

TOP_LEAGUE_IDS = {
    1, 2, 3, 4, 9, 13, 15, 39, 61, 71, 78, 88, 94, 128, 135, 140, 253, 307,
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
EXCLUDED_LEAGUE_KEYWORDS = ["friendl", "caf ", "africa", "afcon"]

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


def resolve_pending(store: dict) -> int:
    """يتحقق من نتائج التوقعات المنتظرة ويحوّل ما انتهى إلى سجل الدقة."""
    pending = store.get("pending", {})
    if not pending:
        return 0

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
                logos = {
                    "home_logo": (teams.get("home") or {}).get("logo", ""),
                    "away_logo": (teams.get("away") or {}).get("logo", ""),
                    "league_logo": (fx.get("league") or {}).get("logo", ""),
                }
                finals[fid] = (status, goals.get("home"), goals.get("away"), logos)
        except Exception as e:
            print(f"فشل سحب نتائج {d}:", e)

    resolved_now = 0
    drop_before = (now_utc() - timedelta(days=3)).strftime("%Y-%m-%d")
    for fid in list(pending.keys()):
        p = pending[fid]
        status, gh, ga, logos = finals.get(fid, ("", None, None, {}))
        if status in FINAL_STATUSES and gh is not None and ga is not None:
            actual = outcome_from_score(int(gh), int(ga))
            store.setdefault("resolved", []).append({
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
            })
            del pending[fid]
            resolved_now += 1
        elif status in DEAD_STATUSES or (p.get("date", "") < drop_before):
            del pending[fid]

    store["resolved"] = store.get("resolved", [])[-1000:]
    return resolved_now


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
            "top": league.get("id") in TOP_LEAGUE_IDS,
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
    """آخر 5 نتائج للفريق (فوز/تعادل/خسارة مع النتيجة والخصم)."""
    if not team_id:
        return ""
    lines = []
    for fx in _enrich_call(f"fixtures?team={team_id}&last=5", budget):
        teams = fx.get("teams") or {}
        goals = fx.get("goals") or {}
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
    return (f"{team_name} last 5: " + ", ".join(lines)) if lines else ""


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


def build_context(m: dict, budget: dict, standings_cache: dict) -> str:
    parts = [
        standings_context(m, budget, standings_cache),
        h2h_context(m, budget),
        form_context(m.get("home_id"), m["home"], budget),
        form_context(m.get("away_id"), m["away"], budget),
        injuries_context(m, budget),
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
            "المباشرة، آخر 5 نتائج لكل فريق، والإصابات. اعتمد عليها أولاً قبل معرفتك العامة.\n\n"
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
        print("Claude error:", e)
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


def build_digest(new_preds: list, stats: dict, v1_preds: dict = None) -> str:
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
    lines.append("\n⚠️ توقعات تحليلية وليست ضمانات.")
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
    resolved_now = resolve_pending(store)
    stats = compute_stats(store["resolved"])
    print(f"المحرك 2: تمت تسوية {resolved_now} توقعاً. السجل: {pct(stats['overall'])}")

    # 2) مباريات الـ 24 ساعة القادمة (نفس اختيار المحرك 1) + إكمال الشعارات الناقصة
    fetched = get_upcoming_24h()
    for m in fetched:
        p = store["pending"].get(m["fid"])
        if p is not None and not p.get("home_logo"):
            for k in ("home_logo", "away_logo", "league_logo"):
                p[k] = m.get(k, "")
    upcoming = [m for m in fetched if m["fid"] not in store["pending"]]
    print(f"مباريات جديدة للتوقع: {len(upcoming)}")

    # 3) سياق إضافي لمباريات الدوريات الكبرى
    budget = {"used": 0}
    standings_cache = {}
    enriched, basic = [], []
    for m in upcoming:
        if m["top"] and len(enriched) < MAX_ENRICHED_FIXTURES:
            m["context"] = build_context(m, budget, standings_cache)
            enriched.append(m)
        else:
            basic.append(m)
    print(f"سياق إضافي: {len(enriched)} مباراة كبرى، {budget['used']} نداء API")

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

    # 5) ملخص تيليجرام (مع مقارنة توقعات المحرك 1 لنفس المباريات)
    if SEND_TELEGRAM_DIGEST and TELEGRAM_TOKEN and TELEGRAM_CHAT_ID and new_preds:
        send_telegram_long(build_digest(new_preds, stats, v1_pending()))


if __name__ == "__main__":
    main()
