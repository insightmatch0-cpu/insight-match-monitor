# -*- coding: utf-8 -*-
"""
توقعات ما قبل المباراة — الـ 24 ساعة القادمة
---------------------------------------------
يعمل مرة واحدة يومياً (أو يدوياً):
1) يسوّي نتائج توقعات الأيام السابقة (صح/خطأ) ويحدّث سجل الدقة.
2) يسحب مباريات الـ 24 ساعة القادمة (نداءان فقط من API-Football).
3) يستبعد (الودية / الأفريقية / الهند / باكستان / بنغلادش).
4) يطلب توقعات من Claude على دفعات، مع تزويده بسجل دقته التاريخي
   ليعاير ثقته بنفسه (تعلّم ذاتي بالمعايرة).
5) يحفظ كل شيء في predictions.json ويرسل ملخصاً على تيليجرام.

لا تكتب أي مفتاح داخل هذا الملف — كل المفاتيح في GitHub Secrets.
استهلاك API-Football: 5 نداءات كحد أقصى في اليوم.
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
PREDICTIONS_FILE = Path("predictions.json")   # سجل التوقعات الدائم (ذاكرة التعلم)
NEWS_FILE        = Path("news.json")          # آخر عناوين الأخبار (يُستخدم كسياق للتوقع)

CLAUDE_MODEL = "claude-haiku-4-5-20251001"

MAX_PREDICTIONS_24H = 150   # رُفع من 60 (المالك 2026-07-18) ليطابق المحرك 2 —
                            # تغطية مباريات المساء أيضاً حتى تظهر حماية المحركين
                            # على المباريات الحية. Haiku رخيص فالتكلفة يسيرة.
BATCH_SIZE          = 12    # عدد المباريات في نداء Claude الواحد
MAX_RESOLVE_CALLS   = 3     # أقصى نداءات API لتسوية نتائج الأيام السابقة

SEND_TELEGRAM_DIGEST = True   # إرسال ملخص التوقعات على تيليجرام
DIGEST_TOP_ONLY      = True   # الملخص يعرض الدوريات الكبرى فقط (الباقي على اللوحة)
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
DEAD_STATUSES  = {"PST", "CANC", "ABD", "AWD", "WO"}  # مؤجلة/ملغاة — تُحذف من الانتظار


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
        # مفتاح خاطئ / انتهى الرصيد اليومي / خطأ في الطلب — نفشل بوضوح
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


# ================== سجل الدقة (التعلم الذاتي) ==================
def outcome_from_score(gh: int, ga: int) -> str:
    if gh > ga:
        return "home"
    if ga > gh:
        return "away"
    return "draw"


def compute_stats(resolved: list) -> dict:
    """يحسب دقة النظام: إجمالي، آخر 30 يوماً، حسب مستوى الثقة، وحسب نوع الدوري."""
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
        "daily": {},   # "2026-07-12": {"correct": 3, "total": 5}
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
    # نحتفظ بآخر 30 يوماً فقط في المخطط اليومي
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
    # التواريخ التي نحتاج نتائجها (كل يوم = نداء واحد)
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
                "actual": actual,
                "score": f"{gh}-{ga}",
                "correct": p.get("pick") == actual,
            })
            del pending[fid]
            resolved_now += 1
        elif status in DEAD_STATUSES or (p.get("date", "") < drop_before):
            # مؤجلة/ملغاة أو قديمة جداً — تُحذف بدون احتساب
            del pending[fid]

    # نحتفظ بآخر 1000 نتيجة فقط
    store["resolved"] = store.get("resolved", [])[-1000:]
    return resolved_now


# ================== سحب مباريات الـ 24 ساعة القادمة ==================
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
        if status != "NS":          # لم تبدأ بعد فقط
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
            "home_logo": (teams.get("home") or {}).get("logo", ""),
            "away_logo": (teams.get("away") or {}).get("logo", ""),
            "league_logo": league.get("logo", ""),
            "league": f"{league.get('name', '?')} ({league.get('country', '?')})",
            "league_id": league.get("id"),
            "top": league.get("id") in TOP_LEAGUE_IDS,
        })

    # الدوريات الكبرى أولاً، ثم حسب وقت الانطلاق
    out.sort(key=lambda m: (not m["top"], m["kickoff"]))
    return out[:MAX_PREDICTIONS_24H]


def backfill_logos(store: dict, fetched: list) -> int:
    """يكمل شعارات الفرق للتوقعات المنتظرة القديمة من بيانات المباريات المسحوبة
    (بدون أي نداء API إضافي)."""
    fixed = 0
    for m in fetched:
        p = (store.get("pending") or {}).get(m["fid"])
        if p is not None and not p.get("home_logo"):
            for k in ("home_logo", "away_logo", "league_logo"):
                p[k] = m.get(k, "")
            fixed += 1
    return fixed


# ================== توقعات Claude (على دفعات) ==================
def calibration_text(stats: dict) -> str:
    if not stats["overall"]["total"]:
        return "لا يوجد سجل تاريخي بعد — كن متحفظاً في نسب الثقة."
    return (
        f"سجل دقتك التاريخي الفعلي (استخدمه لمعايرة ثقتك):\n"
        f"- الإجمالي: {pct(stats['overall'])}\n"
        f"- آخر 30 يوماً: {pct(stats['last30'])}\n"
        f"- الدوريات الكبرى: {pct(stats['top_leagues'])} | البقية: {pct(stats['other_leagues'])}\n"
        f"- عندما كانت ثقتك 70%+: {pct(stats['by_confidence']['70+'])}\n"
        f"- عندما كانت ثقتك 60-69%: {pct(stats['by_confidence']['60-69'])}\n"
        f"- عندما كانت ثقتك 50-59%: {pct(stats['by_confidence']['50-59'])}\n"
        f"إذا كانت دقتك الفعلية أقل من ثقتك المعلنة فاخفض نسب الثقة، والعكس صحيح."
    )


def news_context() -> str:
    news = load_json(NEWS_FILE, {})
    items = news.get("items", [])[:10]
    if not items:
        return ""
    lines = [f"- {it.get('title', '')}" for it in items if it.get("title")]
    return "آخر عناوين الأخبار الكروية (قد تحتوي إصابات أو أخباراً مؤثرة):\n" + "\n".join(lines)


def claude_predict_batch(batch: list, stats: dict) -> dict:
    """يرسل دفعة مباريات لـ Claude ويرجع {fid: توقع}."""
    payload = [
        {
            "id": m["fid"],
            "home": m["home"],
            "away": m["away"],
            "league": m["league"],
            "kickoff_utc": m["kickoff"],
        }
        for m in batch
    ]
    system_prompt = (
        "أنت خبير توقع مباريات كرة قدم. ستصلك قائمة مباريات تقام خلال 24 ساعة.\n"
        "لكل مباراة أعط توقعاً مبنياً على معرفتك بالفريقين: قوتهما النسبية، عامل الأرض، "
        "ومستواهما العام.\n\n"
        + calibration_text(stats) + "\n\n"
        "أرجع ردك بصيغة JSON فقط — مصفوفة واحدة بدون أي نص قبلها أو بعدها وبدون علامات ```:\n"
        '[{"id":"...","ar_home":"...","ar_away":"...","ar_league":"...",'
        '"pick":"home|draw|away","confidence":55,"reason":"سطر واحد بالعربي"}]\n\n'
        "قواعد:\n"
        "- ar_home/ar_away/ar_league: الأسماء العربية الشائعة في الإعلام الرياضي، "
        "وإذا كان الاسم غير مشهور فاكتبه بحروف عربية.\n"
        "- confidence: رقم واقعي بين 40 و80. كرة القدم مليئة بالمفاجآت — "
        "لا تتجاوز 80 مهما كان الفارق.\n"
        "- استخدم الأرقام الإنجليزية (0-9) فقط.\n"
        "- reason: مختصر وواضح بدون حشو."
    )
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
            timeout=120,
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
    """يحوّل رد Claude إلى {fid: توقع} مع تسامح مع الأخطاء البسيطة."""
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
        fid = str(it.get("id", ""))
        pick = it.get("pick")
        if not fid or pick not in ("home", "draw", "away"):
            continue
        try:
            conf = max(30, min(85, int(it.get("confidence", 50))))
        except Exception:
            conf = 50
        out[fid] = {
            "pick": pick,
            "confidence": conf,
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


def build_digest(new_preds: list, stats: dict) -> str:
    lines = ["🔮 توقعات الـ 24 ساعة القادمة"]
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
        lines.append(f"   ↳ {pick_label(p)} — ثقة {p['confidence']}%")

    if not shown:
        lines.append("لا توجد مباريات في الدوريات الكبرى خلال 24 ساعة.")
    if rest > 0:
        lines.append(f"\n➕ {rest} مباراة أخرى بتوقعاتها على اللوحة:")
        lines.append(DASHBOARD_URL)
    if stats["last30"]["total"]:
        lines.append(f"\n📊 دقة النظام آخر 30 يوماً: {pct(stats['last30'])}")
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
    print(f"تمت تسوية {resolved_now} توقعاً. السجل: {pct(stats['overall'])}")

    # 2) مباريات الـ 24 ساعة القادمة (+ إكمال شعارات التوقعات القديمة)
    fetched = get_upcoming_24h()
    fixed = backfill_logos(store, fetched)
    if fixed:
        print(f"تم إكمال شعارات {fixed} توقعاً منتظراً.")
    upcoming = [m for m in fetched if m["fid"] not in store["pending"]]
    print(f"مباريات جديدة للتوقع: {len(upcoming)}")

    # 3) توقعات Claude على دفعات
    new_preds = []
    for i in range(0, len(upcoming), BATCH_SIZE):
        batch = upcoming[i:i + BATCH_SIZE]
        results = claude_predict_batch(batch, stats)
        for m in batch:
            r = results.get(m["fid"])
            if not r:
                continue
            entry = {**m, **r}
            store["pending"][m["fid"]] = entry
            new_preds.append(entry)

    store["meta"] = {"last_run": now_utc().isoformat(), "stats": stats}
    save_json(PREDICTIONS_FILE, store)
    print(f"تم حفظ {len(new_preds)} توقعاً جديداً.")

    # 4) ملخص تيليجرام
    if SEND_TELEGRAM_DIGEST and TELEGRAM_TOKEN and TELEGRAM_CHAT_ID and new_preds:
        send_telegram_long(build_digest(new_preds, stats))


if __name__ == "__main__":
    main()
