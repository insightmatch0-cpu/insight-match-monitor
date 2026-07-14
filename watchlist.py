# -*- coding: utf-8 -*-
"""
قائمة التركيز — أوامر تيليجرام
--------------------------------
يجعل تيليجرام قناة تحكم وليس إشعارات فقط: المستخدم يرسل رسالة عادية بالعربي
(مثل: "ركز على ريال مدريد ومباراة فرنسا") فيفهمها Claude ويحدّث قائمة
التركيز في watchlist.json. المراقب بعدها يرسل التنبيهات لمباريات القائمة فقط،
ويمنحها الأولوية القصوى في تحليل المحرك 2 المباشر.

يعمل في بداية كل تشغيلة مراقبة (كل 10 دقائق):
1) يقرأ الرسائل الجديدة عبر getUpdates (من محادثة المالك فقط — أي رسالة من
   محادثة أخرى تُتجاهل تماماً).
2) "مسح" / "مسح حي" → يشغّل المسح الحي فوراً (بدون نداء Claude).
3) أي رسالة أخرى → نداء Claude واحد يفسّر القصد: تحديد/إضافة/إزالة/تفريغ
   قائمة التركيز، ويرد بتأكيد يعرض توقعات المحركين للمباريات المختارة.

التكلفة: نداء تيليجرام واحد لكل تشغيلة + نداء Claude فقط عند وجود رسالة جديدة.
لا يستهلك أي نداء من API-Football.
"""

import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

WATCHLIST_FILE        = Path("watchlist.json")
PREDICTIONS_FILE      = Path("predictions.json")
PREDICTIONS_V2_FILE   = Path("predictions_v2.json")
USER_PREDICTIONS_FILE = Path("predictions_user.json")   # توقعات المالك — الطرف الثالث في السباق

CLAUDE_MODEL = "claude-haiku-4-5-20251001"   # تفسير الأوامر مهمة خفيفة — هايكو يكفي
SCAN_WORKFLOW = "scan.yml"

TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

SCAN_KEYWORDS = ("مسح", "مسح حي", "شنو الشغال الحين", "scan")
CLEAR_KEYWORDS = ("امسح القائمة", "الغ القائمة", "ألغ القائمة", "امسح التركيز", "clear")


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


def send_telegram(text: str) -> None:
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=30,
        )
    except Exception as e:
        print("Telegram error:", e)


def get_new_messages(last_update_id: int):
    """يرجع (عناصر جديدة من مالك البوت فقط، آخر update_id). كل عنصر إما
    {"type":"text","text":...} أو {"type":"callback","data":...,"id":...} (ضغطة زر).
    أي محادثة أخرى تُتجاهل تماماً."""
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": last_update_id + 1, "timeout": 0,
                    "allowed_updates": '["message","callback_query"]'},
            timeout=30,
        )
        r.raise_for_status()
        updates = r.json().get("result", [])
    except Exception as e:
        print("getUpdates error:", e)
        return [], last_update_id

    items = []
    for u in updates:
        last_update_id = max(last_update_id, int(u.get("update_id", 0)))
        msg = u.get("message") or {}
        cb = u.get("callback_query") or {}
        if msg:
            chat_id = str((msg.get("chat") or {}).get("id", ""))
            text = (msg.get("text") or "").strip()
            if chat_id == TELEGRAM_CHAT_ID and text:
                items.append({"type": "text", "text": text})
        elif cb:
            chat_id = str((((cb.get("message") or {}).get("chat")) or {}).get("id", ""))
            payload = (cb.get("data") or "").strip()
            if chat_id == TELEGRAM_CHAT_ID and payload:
                items.append({"type": "callback", "data": payload, "id": cb.get("id")})
    return items, last_update_id


def candidate_matches() -> dict:
    """كل المباريات المتاحة للاختيار: توقعات الـ 24 ساعة المنتظرة (المحركان يتشاركان fid)."""
    out = {}
    for src in (PREDICTIONS_V2_FILE, PREDICTIONS_FILE):
        store = load_json(src, {})
        for fid, p in (store.get("pending") or {}).items():
            if fid in out:
                continue
            out[fid] = {
                "fid": fid,
                "home": p.get("home", "?"), "away": p.get("away", "?"),
                "ar_home": p.get("ar_home") or "", "ar_away": p.get("ar_away") or "",
                "league": p.get("ar_league") or p.get("league", ""),
                "kickoff": p.get("kickoff", ""),
                "date": p.get("date", ""),
            }
    return out


def interpret(message: str, candidates: dict) -> dict:
    """نداء Claude واحد يفسر رسالة المستخدم. يرجع {"action": ..., "fids": [...]}."""
    listing = [
        {
            "fid": c["fid"],
            "home": c["home"], "away": c["away"],
            "ar_home": c["ar_home"], "ar_away": c["ar_away"],
            "league": c["league"], "kickoff_utc": c["kickoff"],
        }
        for c in candidates.values()
    ]
    system_prompt = (
        "أنت مفسّر أوامر لبوت مراقبة مباريات. سيصلك: رسالة من المستخدم (قد تكون "
        "منقولة صوتياً وفيها أخطاء إملائية) وقائمة مباريات قادمة.\n"
        "حدد قصده وأرجع JSON فقط بدون أي نص آخر وبدون ```:\n"
        '{"action":"set|add|remove|clear|predict|none","fids":["..."],'
        '"picks":[{"fid":"...","pick":"home|draw|away"}]}\n'
        "- set: يريد هذه المباريات قائمة تركيزه (الحالة الأشيع).\n"
        "- add/remove: يضيف أو يزيل مباريات من قائمته الحالية.\n"
        "- clear: يريد إلغاء قائمة التركيز.\n"
        "- predict: يعطي توقعه الشخصي لنتائج مباريات (مثل: الريال يفوز، تعادل فرنسا) "
        "— حينها املأ picks (home=فوز المضيف، away=فوز الضيف، draw=تعادل) واترك fids فارغة.\n"
        "- none: الرسالة ليست عن اختيار مباريات ولا توقعات.\n"
        "طابق أسماء الفرق بمرونة (عربي أو إنجليزي، أخطاء إملائية، اسم فريق واحد يكفي "
        "لتحديد المباراة). fids/picks من القائمة فقط."
    )
    user_text = json.dumps(
        {"message": message, "matches": listing}, ensure_ascii=False
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
                "max_tokens": 500,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_text}],
            },
            timeout=120,
        )
        r.raise_for_status()
        text = "".join(
            b.get("text", "") for b in r.json().get("content", [])
            if b.get("type") == "text"
        ).strip()
    except Exception as e:
        print("Claude error:", e)
        return {"action": "none", "fids": []}

    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {"action": "none", "fids": []}
    try:
        data = json.loads(m.group(0))
    except Exception:
        return {"action": "none", "fids": []}
    action = data.get("action")
    if action not in ("set", "add", "remove", "clear", "predict", "none"):
        action = "none"
    fids = [str(f) for f in (data.get("fids") or []) if str(f) in candidates]
    picks = []
    for p in (data.get("picks") or []):
        if not isinstance(p, dict):
            continue
        fid = str(p.get("fid", ""))
        pick = p.get("pick")
        if fid in candidates and pick in ("home", "draw", "away"):
            picks.append({"fid": fid, "pick": pick})
    return {"action": action, "fids": fids, "picks": picks}


def fire_scan() -> bool:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    try:
        subprocess.run(
            ["gh", "workflow", "run", SCAN_WORKFLOW, "--repo", repo],
            check=True, timeout=60,
        )
        return True
    except Exception as e:
        print("فشل تشغيل المسح:", e)
        return False


def match_label(c: dict) -> str:
    h = c.get("ar_home") or c.get("home", "?")
    a = c.get("ar_away") or c.get("away", "?")
    return f"{h} 🆚 {a}"


def engines_line(fid: str) -> str:
    """سطر مقارنة توقعي المحركين لمباراة — للتأكيد الذي يصل للمستخدم."""
    pick_ar = {"home": "فوز {h}", "draw": "تعادل", "away": "فوز {a}"}
    parts = []
    for name, path in (("المحرك 1", PREDICTIONS_FILE), ("المحرك 2", PREDICTIONS_V2_FILE)):
        p = (load_json(path, {}).get("pending") or {}).get(fid)
        if not p or p.get("pick") not in pick_ar:
            continue
        h = p.get("ar_home") or p.get("home", "?")
        a = p.get("ar_away") or p.get("away", "?")
        label = pick_ar[p["pick"]].format(h=h, a=a)
        line = f"{name}: {label} — ثقة {p.get('confidence', '?')}%"
        if p.get("prob_home") is not None:
            line += f" ({p['prob_home']}/{p['prob_draw']}/{p['prob_away']})"
        parts.append(line)
    return "\n".join(f"   {x}" for x in parts)


def apply_action(action: str, fids: list, candidates: dict, data: dict) -> str:
    """يحدّث القائمة ويبني رسالة التأكيد."""
    matches = data.setdefault("matches", {})
    data["results_sent"] = False   # القائمة تغيرت → ملخص نهاية اليوم يُعاد تفعيله
    if action == "clear":
        matches.clear()
        return ("🔕 ألغيت قائمة التركيز.\n"
                "رجعت للوضع الافتراضي: تنبيهات الدوريات الكبرى فقط.")
    if action == "set":
        matches.clear()
    if action == "remove":
        for fid in fids:
            matches.pop(fid, None)
    else:
        for fid in fids:
            c = candidates[fid]
            matches[fid] = {
                "label": match_label(c),
                "home": c["home"], "away": c["away"],
                "date": c.get("date") or (c.get("kickoff") or "")[:10],
            }
    if not matches:
        return ("🔕 قائمة التركيز فارغة الآن.\n"
                "الوضع الافتراضي: تنبيهات الدوريات الكبرى فقط.")
    lines = [f"⭐ قائمة التركيز ({len(matches)} مباراة):"]
    for fid, e in matches.items():
        lines.append(f"\n• {e['label']}")
        eng = engines_line(fid)
        if eng:
            lines.append(eng)
    lines.append("\n🔔 سأنبهك على هذه المباريات فقط: البداية، كل هدف مع تحليل "
                 "المحرك 2 المباشر الكامل، والنتيجة النهائية، "
                 "وملخص فوري عند انتهاء آخر مباراة.")
    lines.append("🎯 وأنت؟ اختر توقعك لكل مباراة بالأزرار التالية 👇 "
                 "(أو اكتبه كنص إن أحببت)")
    return "\n".join(lines)


PICK_AR = {"home": "فوز {h}", "draw": "تعادل", "away": "فوز {a}"}


def send_pick_buttons(fids: list, candidates: dict) -> None:
    """يرسل لكل مباراة رسالة بثلاثة أزرار (فوز/تعادل/فوز) — توقع بضغطة واحدة."""
    for fid in fids:
        c = candidates.get(fid)
        if not c:
            continue
        h = c.get("ar_home") or c.get("home", "?")
        a = c.get("ar_away") or c.get("away", "?")
        keyboard = {"inline_keyboard": [[
            {"text": f"فوز {h}", "callback_data": f"pick|{fid}|home"},
            {"text": "تعادل", "callback_data": f"pick|{fid}|draw"},
            {"text": f"فوز {a}", "callback_data": f"pick|{fid}|away"},
        ]]}
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID,
                      "text": f"🎯 توقعك: {h} 🆚 {a}؟",
                      "reply_markup": keyboard},
                timeout=30,
            )
        except Exception as e:
            print("Telegram error:", e)


def handle_pick_callback(payload: str, candidates: dict) -> str:
    """يعالج ضغطة زر التوقع: pick|fid|home — بدون أي نداء Claude."""
    parts = payload.split("|")
    if (len(parts) == 3 and parts[0] == "pick"
            and parts[1] in candidates and parts[2] in ("home", "draw", "away")):
        return record_user_picks([{"fid": parts[1], "pick": parts[2]}], candidates)
    return ""


def answer_callback(callback_id) -> None:
    """إغلاق مؤشر الانتظار على زر تيليجرام (قد يكون انتهى وقته — نتجاهل الفشل)."""
    if not callback_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
            json={"callback_query_id": callback_id, "text": "تم التسجيل ✅"},
            timeout=15,
        )
    except Exception:
        pass


def record_user_picks(picks: list, candidates: dict) -> str:
    """يسجل توقعات المالك في predictions_user.json (نفس بنية ذاكرة المحركين)
    ليجري تقييمها كل صباح بنفس منطق المحركين — سباق دقة ثلاثي."""
    store = load_json(USER_PREDICTIONS_FILE, {"pending": {}, "resolved": []})
    store.setdefault("pending", {})
    store.setdefault("resolved", [])
    v2_pending = load_json(PREDICTIONS_V2_FILE, {}).get("pending") or {}

    lines = []
    saved = 0
    for p in picks:
        fid = p["fid"]
        c = candidates[fid]
        # لا نقبل توقعاً لمباراة بدأت — العدالة أولاً
        try:
            kickoff = datetime.fromisoformat(c.get("kickoff", ""))
            if kickoff <= now_utc():
                lines.append(f"• {match_label(c)} — بدأت المباراة، لا يُقبل توقع متأخر.")
                continue
        except Exception:
            pass
        base = v2_pending.get(fid) or {}
        entry = {
            "fid": fid,
            "kickoff": c.get("kickoff", ""),
            "date": c.get("date") or (c.get("kickoff") or "")[:10],
            "home": c.get("home", "?"), "away": c.get("away", "?"),
            "ar_home": base.get("ar_home") or c.get("ar_home", ""),
            "ar_away": base.get("ar_away") or c.get("ar_away", ""),
            "ar_league": base.get("ar_league") or "",
            "league": c.get("league", ""),
            "league_id": base.get("league_id"),
            "home_logo": base.get("home_logo", ""),
            "away_logo": base.get("away_logo", ""),
            "league_logo": base.get("league_logo", ""),
            "top": bool(base.get("top")),
            "pick": p["pick"],
            "confidence": 60,   # ثقة افتراضية موحدة لتوقعات المالك
            "reason": "توقع المالك",
        }
        store["pending"][fid] = entry   # يجوز تغيير الرأي قبل انطلاق المباراة
        saved += 1
        h = entry["ar_home"] or entry["home"]
        a = entry["ar_away"] or entry["away"]
        label = PICK_AR[p["pick"]].format(h=h, a=a)
        lines.append(f"• {h} 🆚 {a} — توقعك: {label}")
        eng = engines_line(fid)
        if eng:
            lines.append(eng)

    save_json(USER_PREDICTIONS_FILE, store)
    if not saved:
        return "\n".join(["🎯 لم أسجل توقعات جديدة:"] + lines) if lines else \
            "لم أتعرف على توقعات في رسالتك."
    header = f"🎯 سجلت توقعاتك ({saved}):"
    footer = "\nغداً صباحاً أقيّم النتائج وأقارن دقتك مع المحركين. بالتوفيق! 🏆"
    return "\n".join([header] + lines) + footer


def cleanup_expired(data: dict) -> None:
    """يزيل مباريات انتهى يومها (أقدم من يومين) حتى لا تكتم تنبيهات الأيام التالية."""
    cutoff = (now_utc() - timedelta(days=2)).strftime("%Y-%m-%d")
    matches = data.get("matches") or {}
    for fid in list(matches.keys()):
        if (matches[fid].get("date") or "0000") < cutoff:
            del matches[fid]


def main() -> None:
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        print("لا مفاتيح تيليجرام — تخطي.")
        return

    data = load_json(WATCHLIST_FILE, {"last_update_id": 0, "matches": {}})
    cleanup_expired(data)

    items, last_id = get_new_messages(int(data.get("last_update_id", 0)))
    data["last_update_id"] = last_id

    for item in items:
        # ضغطات الأزرار: تسجيل توقع فوري بدون Claude
        if item["type"] == "callback":
            candidates = candidate_matches()
            reply = handle_pick_callback(item["data"], candidates)
            answer_callback(item.get("id"))
            if reply:
                send_telegram(reply)
            continue

        text = item["text"]
        low = text.strip().lower()
        if any(low == k or low.startswith(k) for k in SCAN_KEYWORDS):
            if fire_scan():
                send_telegram("🔍 بدأت المسح الحي العالمي — النتائج خلال دقائق.")
            continue
        if any(k in low for k in CLEAR_KEYWORDS):
            send_telegram(apply_action("clear", [], {}, data))
            continue
        candidates = candidate_matches()
        intent = interpret(text, candidates)
        if intent["action"] == "predict":
            if intent["picks"]:
                send_telegram(record_user_picks(intent["picks"], candidates))
            else:
                send_telegram(
                    "لم أتعرف على توقعاتك. اذكر الفريق والنتيجة "
                    "(مثال: الريال يفوز وتعادل فرنسا وإسبانيا)."
                )
            continue
        if intent["action"] == "none" or (intent["action"] != "clear" and not intent["fids"]):
            send_telegram(
                "لم أتعرف على مباريات في رسالتك. أرسل أسماء الفرق التي تهمك "
                "(مثال: ركز على ريال مدريد ومباراة فرنسا)، أو توقعك لنتيجة مباراة، "
                "أو \"امسح القائمة\" للإلغاء."
            )
            continue
        send_telegram(apply_action(intent["action"], intent["fids"], candidates, data))
        if intent["action"] in ("set", "add") and intent["fids"]:
            send_pick_buttons(intent["fids"], candidates)

    save_json(WATCHLIST_FILE, data)
    print(f"قائمة التركيز: {len(data.get('matches', {}))} مباراة، عناصر جديدة: {len(items)}")


if __name__ == "__main__":
    main()
