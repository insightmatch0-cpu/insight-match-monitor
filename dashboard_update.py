# -*- coding: utf-8 -*-
"""
مولّد بيانات اللوحة — data.json
--------------------------------
يجمع في ملف واحد تقرؤه لوحة GitHub Pages:
- المباريات الحية الآن (من state.json)
- توقعات الـ 24 ساعة (من predictions.json)
- سجل دقة النظام (محسوب من التوقعات المسوّاة)
- آخر الأخبار الكروية (من خلاصات RSS مجانية — يُحدَّث كل 3 ساعات)

يعمل تلقائياً بعد كل تشغيلة للمراقب وبعد التوقعات اليومية.
لا يستهلك أي رصيد من API-Football.
"""

import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

STATE_FILE          = Path("state.json")
PREDICTIONS_FILE    = Path("predictions.json")
PREDICTIONS_V2_FILE = Path("predictions_v2.json")
LESSONS_V2_FILE     = Path("lessons_v2.json")
HISTORY_FILE        = Path("history.json")
NEWS_FILE           = Path("news.json")
DATA_FILE           = Path("data.json")
DATA_V2_FILE        = Path("data_v2.json")
SCENARIOS_V2_FILE   = Path("scenarios_v2.json")
RADAR_LOG_FILE      = Path("radar_log.json")   # إنذارات الرادار + إحصاءات صدقها
PREDICTIONS_USER_FILE = Path("predictions_user.json")  # توقعات المالك (سباق الدقة)
SPORTMONKS_SHADOW_FILE = Path("sportmonks_shadow.json")  # سجل تجربة ظل xG
SHADOW_LAB_ROWS     = 10   # أحدث بطاقات التقييم المعروضة في مختبر الظل
# مهلة إسقاط تقرير بلا بيانات نهائية — تُطابق SCENARIO_MAX_AGE_DAYS في
# predict_v2.py (هناك القرار، وهنا العرض فقط). حارس التطابق يفحص تساويهما.
SCENARIO_MAX_AGE_DAYS = 4
# نافذة تجربة ظل xG: مُدِّدت أسبوعين بأمر المالك 2026-08-14 (الحكم ~17 سبتمبر).
# ⚠️ يجب أن تساوي XG_SHADOW_DAYS في predict_v2.py — حارس التطابق يفحص ذلك
# (كانت 21 هنا و35 هناك: نفس عيّنة انحراف السجلات الذي أمسكه تدقيق 15 أغسطس)
XG_SHADOW_TOTAL_DAYS = 35

# مباراة انتهت بعد التشغيلة الصباحية تُقيَّم صباح الغد — وطوال تلك الساعات كانت
# تختفي من اللوحة كلياً: لا حية ولا قادمة ولا مُقيَّمة (فجوة ديفونبورت،
# ملاحظة المالك 2026-08-15: بحث عن مباراة الصباح فوجد «لا نتائج» وظنها ضاعت).
# بعد هذه المدة من الانطلاق نعتبر المباراة منتهية منطقياً ومعلَّقة للتقييم.
AWAITING_AFTER_KICKOFF_MIN = 150   # دقيقتان ونصف ≈ مباراة كاملة بوقتها الضائع

LESSONS_ON_DASHBOARD = 30   # أحدث الدروس المعروضة في لوحة المحرك 2
RECENT_RESULTS_SHOWN = 50   # عدد النتائج المُقيَّمة المعروضة (تكفي أياماً لا ساعات)

NEWS_MAX_AGE_HOURS = 3     # لا نحدّث الأخبار قبل مرور هذه المدة
NEWS_MAX_ITEMS     = 15

# خلاصات مجانية وغير محدودة — أضف أو احذف ما تريد
NEWS_FEEDS = [
    ("BBC عربي",     "https://feeds.bbci.co.uk/arabic/sports/rss.xml"),
    ("BBC Sport",    "https://feeds.bbci.co.uk/sport/football/rss.xml"),
    ("Sky Sports",   "https://www.skysports.com/rss/12040"),
    ("The Guardian", "https://www.theguardian.com/football/rss"),
    # الأخبار الصغيرة تصنع فرقاً كبيراً (انتقالات، أخبار فرق) — توجيه المالك 2026-07-15
    ("Sky News",     "https://www.skysports.com/rss/12691"),
]

LIVE_STATUSES = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE", "INT"}


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


# ================== الأخبار (RSS) ==================
def parse_feed(xml_text: str, source: str) -> list:
    """يقرأ RSS أو Atom بتسامح ويرجع عناصر موحدة."""
    items = []
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return items

    # RSS 2.0
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        ts = None
        if pub:
            try:
                ts = parsedate_to_datetime(pub).astimezone(timezone.utc).isoformat()
            except Exception:
                pass
        if title:
            items.append({"title": title, "link": link, "source": source, "time": ts})

    # Atom
    if not items:
        ns = "{http://www.w3.org/2005/Atom}"
        for entry in root.iter(f"{ns}entry"):
            title = (entry.findtext(f"{ns}title") or "").strip()
            link_el = entry.find(f"{ns}link")
            link = link_el.get("href", "") if link_el is not None else ""
            pub = (entry.findtext(f"{ns}updated") or "").strip()
            ts = None
            if pub:
                try:
                    ts = datetime.fromisoformat(pub.replace("Z", "+00:00")).isoformat()
                except Exception:
                    pass
            if title:
                items.append({"title": title, "link": link, "source": source, "time": ts})
    return items


def refresh_news() -> dict:
    news = load_json(NEWS_FILE, {})
    fetched_at = news.get("fetched_at")
    if fetched_at:
        try:
            age = now_utc() - datetime.fromisoformat(fetched_at)
            if age < timedelta(hours=NEWS_MAX_AGE_HOURS):
                return news
        except Exception:
            pass

    all_items = []
    for source, url in NEWS_FEEDS:
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": "insight-match/1.0"})
            r.raise_for_status()
            all_items.extend(parse_feed(r.text, source))
        except Exception as e:
            print(f"تعذر سحب خلاصة {source}:", e)

    if not all_items:
        # فشل الاتصال — نبقي الأخبار القديمة كما هي
        return news

    all_items.sort(key=lambda it: it.get("time") or "", reverse=True)
    news = {"fetched_at": now_utc().isoformat(), "items": all_items[:NEWS_MAX_ITEMS]}
    save_json(NEWS_FILE, news)
    return news


# ================== تجميع data.json ==================
def _live_pred(store: dict, fid: str):
    """توقع المحرك لمباراة حية (من pending) — للعرض على بطاقة المباراة."""
    p = (store.get("pending") or {}).get(fid)
    if not p or not p.get("pick"):
        return None
    return {"pick": p["pick"], "confidence": p.get("confidence")}


def build_live(state: dict, store_v1: dict, store_v2: dict) -> list:
    live = []
    for fid, e in state.items():
        if not isinstance(e, dict):
            continue
        if e.get("status") not in LIVE_STATUSES:
            continue
        ar = e.get("ar") or {}
        item = {
            "fid": fid,
            "home": ar.get("home") or e.get("home", "?"),
            "away": ar.get("away") or e.get("away", "?"),
            "home_en": e.get("home", "?"), "away_en": e.get("away", "?"),
            "league": ar.get("league") or e.get("league", ""),
            "home_logo": e.get("home_logo", ""),
            "away_logo": e.get("away_logo", ""),
            "league_logo": e.get("league_logo", ""),
            "score": e.get("score", "0-0"),
            "minute": e.get("minute", 0),
            "status": e.get("status", ""),
            # لحظة رصد الدقيقة — اللوحة تُقدّمها بما مضى منذها (لا تجمّد)
            "seen": e.get("seen", ""),
        }
        # توقع كل محرك يظهر على البطاقة الحية مباشرة (طلب المالك 2026-07-18)
        p1 = _live_pred(store_v1, fid)
        p2 = _live_pred(store_v2, fid)
        if p1:
            item["pred_v1"] = p1
        if p2:
            item["pred_v2"] = p2
        # الرادار (طلب المالك 2026-08-01): درجة الخطر وعواملها واتجاهات الأرقام
        radar = e.get("radar") or {}
        if radar.get("score") is not None:
            snaps = radar.get("snaps") or []
            item["radar"] = {
                "score": radar.get("score"),
                "level": radar.get("level"),
                "factors": radar.get("factors") or [],
                "pick": radar.get("pick"),
                "confidence": radar.get("confidence"),
                # قمع الاستباق (طلب المالك 2026-08-02): الإشارة الخام + الجاهزية
                "drama": radar.get("drama"),
                "alerted": radar.get("alerted"),
                # 🎛 REC-010: علامة دوريات المالك كما خُتمت في monitor.py
                "top": bool(radar.get("top")),
                "trend": {
                    "min": [s.get("minute", 0) for s in snaps],
                    # 📈 منحنى تصاعد الخطر — مشتق في monitor.py من نفس اللقطات
                    "danger": radar.get("dscores") or [],
                    "h_sog": [(s.get("h") or {}).get("sog", 0) for s in snaps],
                    "a_sog": [(s.get("a") or {}).get("sog", 0) for s in snaps],
                    "h_cor": [(s.get("h") or {}).get("cor", 0) for s in snaps],
                    "a_cor": [(s.get("a") or {}).get("cor", 0) for s in snaps],
                },
            }
        live.append(item)
    live.sort(key=lambda m: -(m["minute"] or 0))
    return live


def build_upcoming(store: dict) -> list:
    upcoming = []
    cutoff = now_utc() - timedelta(hours=2)   # نبقي المباراة ظاهرة ساعتين بعد انطلاقها
    for fid, p in (store.get("pending") or {}).items():
        try:
            kickoff = datetime.fromisoformat(p.get("kickoff"))
        except Exception:
            continue
        if kickoff < cutoff:
            continue
        item = {
            "fid": fid,
            "kickoff": p.get("kickoff"),
            "home": p.get("ar_home") or p.get("home", "?"),
            "away": p.get("ar_away") or p.get("away", "?"),
            "home_en": p.get("home", "?"),
            "home_logo": p.get("home_logo", ""),
            "away_logo": p.get("away_logo", ""),
            "league_logo": p.get("league_logo", ""),
            "away_en": p.get("away", "?"),
            "league": p.get("ar_league") or p.get("league", ""),
            "top": bool(p.get("top")),
            "pick": p.get("pick"),
            "confidence": p.get("confidence"),
            "reason": p.get("reason", ""),
        }
        # المحرك 2 يخزن احتمالات النتائج الثلاث — تظهر على اللوحة إن وجدت
        if p.get("prob_home") is not None:
            item["prob_home"] = p.get("prob_home")
            item["prob_draw"] = p.get("prob_draw")
            item["prob_away"] = p.get("prob_away")
        # احتمالات السوق الضمنية (المحرك 2، المباريات الغنية) → شريحة
        # "المحرك ضد السوق" على اللوحة
        if p.get("mkt_home") is not None:
            item["mkt_home"] = p.get("mkt_home")
            item["mkt_draw"] = p.get("mkt_draw")
            item["mkt_away"] = p.get("mkt_away")
        upcoming.append(item)
    upcoming.sort(key=lambda m: (not m["top"], m["kickoff"]))
    return upcoming


def build_recent_results(store: dict) -> list:
    """آخر 20 نتيجة مُقيَّمة — الأحدث أولاً، والدوريات الكبرى في المقدمة
    حتى لا تدفن مباريات المستخدم المهمة تحت نتائج الدوريات الصغيرة."""
    pool = (store.get("resolved") or [])[-300:]
    pool = sorted(pool,
                  key=lambda r: (r.get("date") or "", 1 if r.get("top") else 0),
                  reverse=True)
    out = []
    for r in pool[:RECENT_RESULTS_SHOWN]:
        item = {
            "date": r.get("date"),
            "home": r.get("ar_home") or r.get("home", "?"),
            "away": r.get("ar_away") or r.get("away", "?"),
            "home_en": r.get("home", "?"), "away_en": r.get("away", "?"),
            "league": r.get("ar_league") or r.get("league", ""),
            "home_logo": r.get("home_logo", ""),
            "away_logo": r.get("away_logo", ""),
            "pick": r.get("pick"),
            "confidence": r.get("confidence"),
            "score": r.get("score"),
            "actual": r.get("actual"),
            "correct": bool(r.get("correct")),
        }
        # سطر "لماذا" (طلب المالك 2026-08-01): يصل اللوحة مع النتيجة المُقيَّمة
        # (إدخالات المحرك 1 القديمة بلا reason — تمر بأمان بقيمة فارغة)
        if (r.get("reason") or "").strip():
            item["reason"] = r["reason"].strip()
        if r.get("prob_home") is not None:
            item["prob_home"] = r.get("prob_home")
            item["prob_draw"] = r.get("prob_draw")
            item["prob_away"] = r.get("prob_away")
        out.append(item)
    return out


def build_awaiting(store: dict, now=None) -> list:
    """⏳ مباريات انتهت (منطقياً) ولم تُقيَّم بعد — سدّ فجوة ديفونبورت.

    التقييم الرسمي يبقى صباحياً كما هو (سلامة القياس لا تُمَس)؛ هذه القائمة
    عرضٌ فقط: تُبنى من pending بصفر نداءات API، وتفرغ تلقائياً كل صباح بعد
    التقييم. بدونها كل مباراة تنتهي بعد التشغيلة الصباحية «تختفي» من اللوحة
    حتى الغد، والغياب يُقرأ خطأً على أنه فقدان بيانات أو دوري غير مغطى.

    مباراة ما زالت حية في state (وقت إضافي طويل) تُستثنى — قسم «الحية»
    يعرضها أصلاً، وظهورها في القائمتين معاً تكرار يشوّش.
    """
    now = now or now_utc()
    live_now = {str(k) for k, v in (load_json(STATE_FILE, {}) or {}).items()
                if isinstance(v, dict) and v.get("status") in LIVE_STATUSES}
    out = []
    pend = store.get("pending") or {}
    rows = pend.values() if isinstance(pend, dict) else pend
    for r in rows:
        ko = r.get("kickoff")
        if not ko or str(r.get("fid")) in live_now:
            continue
        try:
            kicked = datetime.fromisoformat(str(ko))
        except ValueError:
            continue
        if kicked.tzinfo is None:
            kicked = kicked.replace(tzinfo=timezone.utc)
        mins = (now - kicked).total_seconds() / 60
        if mins < AWAITING_AFTER_KICKOFF_MIN:
            continue
        out.append({
            "date": r.get("date"),
            "home": r.get("ar_home") or r.get("home", "?"),
            "away": r.get("ar_away") or r.get("away", "?"),
            "home_en": r.get("home", "?"), "away_en": r.get("away", "?"),
            "league": r.get("ar_league") or r.get("league", ""),
            "home_logo": r.get("home_logo", ""),
            "away_logo": r.get("away_logo", ""),
            "pick": r.get("pick"), "confidence": r.get("confidence"),
            "kickoff": str(ko),
            "awaiting": True,      # اللوحة تعرض ⏳ بدل ✓/✗ — لا حكم قبل التقييم
        })
    out.sort(key=lambda x: x.get("kickoff") or "", reverse=True)
    return out


def recent_lessons() -> list:
    """أحدث دروس المحرك 2 (المرحلة 3) لعرضها على اللوحة."""
    data = load_json(LESSONS_V2_FILE, {"lessons": []})
    out = []
    for it in (data.get("lessons") or [])[-LESSONS_ON_DASHBOARD:]:
        if isinstance(it, dict) and (it.get("text") or "").strip():
            out.append({
                "date": it.get("date", ""),
                "match": it.get("match", ""),
                "text": it["text"].strip(),
            })
        elif isinstance(it, str) and it.strip():
            out.append({"date": "", "match": "", "text": it.strip()})
    out.reverse()
    return out


def _by_fid(rows) -> dict:
    return {str(r.get("fid")): r for r in (rows or [])
            if isinstance(r, dict) and r.get("fid")}


def build_active_experiments() -> list:
    """🧪 صف التجارب النشطة (قاعدة الحوكمة هـ 2026-08-01): أي تجربة ظل نشطة
    تظهر للمالك يومياً بلا سؤال — في النشرة سطرٌ وفي مختبر الظل صفٌّ معاً.
    حالياً: تجربة ظل xG (Sportmonks) — تُقرأ حالتها من سجل المجمّع قراءةً فقط."""
    out = []
    meta = (load_json(SPORTMONKS_SHADOW_FILE, {}) or {}).get("meta") or {}
    if meta.get("started"):
        try:
            started = datetime.strptime(meta["started"], "%Y-%m-%d").date()
            day = (now_utc().date() - started).days + 1
        except ValueError:
            day = None
        xf = meta.get("xgform") or {}
        out.append({
            "key": "xg_shadow",
            "name": "🔬 ظل xG (Sportmonks)",
            "day": day, "days_total": XG_SHADOW_TOTAL_DAYS,
            "yday_matched": meta.get("last_day_matched", 0),
            "yday_unmatched": meta.get("last_day_unmatched", 0),
            "total": meta.get("total", 0),
            "form_n": xf.get("n", 0),
            "form_correct": xf.get("correct", 0),
        })
    return out


def build_shadow_lab() -> dict:
    """🔬 مختبر الظل — آخر بطاقات تقييم تقارير ما قبل المباراة (قائمة التركيز
    + تقارير الظل الصامتة) حتى يرى المالك تدريب القنّاص وهو يحدث، بدل انتظار
    التقارير المجدولة (طلب المالك 2026-08-01).
    بطاقة 360° (طلب المالك 2026-08-09): كل بطاقة تحمل أيضاً النتيجة النهائية
    وأحكام S1 (المحركان + توقع المالك) وS3 (إنذارات الرادار وتنبيهات الدراما
    لنفس المباراة) — الطبقات الثلاث لكل مباراة في مكان واحد."""
    sc = load_json(SCENARIOS_V2_FILE, {"pending": {}, "resolved": []})
    resolved = sc.get("resolved") or []
    v1_res = _by_fid(load_json(PREDICTIONS_FILE, {}).get("resolved"))
    v2_res = _by_fid(load_json(PREDICTIONS_V2_FILE, {}).get("resolved"))
    user_res = _by_fid(load_json(PREDICTIONS_USER_FILE, {}).get("resolved"))
    radar = load_json(RADAR_LOG_FILE, {})
    rows = []
    for e in resolved[-SHADOW_LAB_ROWS:][::-1]:
        if not isinstance(e, dict):
            continue
        fid = str(e.get("fid") or "")
        # S1: حكم توقعات الصباح لنفس المباراة — المحركان وتوقع المالك إن وجد
        s1 = {}
        score = ""
        for key, idx in (("v1", v1_res), ("v2", v2_res), ("user", user_res)):
            p = idx.get(fid)
            if p and p.get("pick"):
                s1[key] = {"pick": p["pick"],
                           "confidence": p.get("confidence"),
                           "correct": bool(p.get("correct"))}
                score = score or p.get("score", "")
        # S3: ما قاله الرادار عن نفس المباراة — إنذارات المستويات وتنبيهات الدراما
        warns = [{"level": w.get("level"), "minute": w.get("minute"),
                  "failed": bool(w.get("failed"))}
                 for w in (radar.get("resolved") or [])
                 if str(w.get("fid")) == fid]
        alerts = [{"key": a.get("key"), "minute": a.get("minute"),
                   "hit": bool(a.get("hit")),
                   "silenced": bool(a.get("silenced"))}
                  for a in (radar.get("alerts_resolved") or [])
                  if str(a.get("fid")) == fid]
        rows.append({
            "fid": fid,
            "score": score,
            "s1": s1,
            "s3": {"warnings": warns, "alerts": alerts},
            # تاريخ المباراة نفسها لا تاريخ التقييم (بلاغ المالك 2026-08-02:
            # مباراة كأس قديمة ظهرت بتاريخ صباح تقييمها فبدا التاريخ خاطئاً)
            "date": e.get("date") or e.get("graded_on", ""),
            "home": e.get("ar_home") or e.get("home", "?"),
            "away": e.get("ar_away") or e.get("away", "?"),
            "home_en": e.get("home", "?"), "away_en": e.get("away", "?"),
            "league": e.get("league", ""),
            "shadow": bool(e.get("shadow")),
            "correct": e.get("correct"),
            "total": e.get("total"),
            # نص التقرير الأصلي — يُفتح من البطاقة ليقرأ المالك ماذا قال
            # المحرك قبل المباراة حرفياً (شفافية كاملة، لا أرقام معلقة)
            "report": e.get("report", ""),
            # التصحيح بنداً‑ببند (متوفر للتقارير المقيَّمة بعد 2026-08-01)
            "grades": e.get("grades") or [],
            "grade_summary": e.get("grade_summary", ""),
        })
    # التقارير المنتظرة (أُرسلت/التُقطت ولم تُقيَّم بعد) — تظهر بحالة حية
    waiting = []
    today = now_utc().strftime("%Y-%m-%d")
    for p in (sc.get("pending") or {}).values():
        if not isinstance(p, dict):
            continue
        # كم يوماً انتظر هذا التقرير؟ بلاغ المالك 2026-08-15: تقرير من 12
        # أغسطس بدا «عالقاً» بلا تفسير بين تقارير اليوم. الرقم يحوّل الغموض
        # إلى حقيقة معلومة: ينتظر البيانات النهائية ويُسقط تلقائياً بعد المهلة.
        try:
            waited = (datetime.fromisoformat(today)
                      - datetime.fromisoformat(str(p.get("date")))).days
        except ValueError:
            waited = 0
        waiting.append({
            "date": p.get("date", ""),
            "kickoff": p.get("kickoff", ""),
            "home": p.get("ar_home") or p.get("home", "?"),
            "away": p.get("ar_away") or p.get("away", "?"),
            "home_en": p.get("home", "?"), "away_en": p.get("away", "?"),
            "league": p.get("league", ""),
            "shadow": bool(p.get("shadow")),
            "report": p.get("report", ""),
            "waited_days": max(0, waited),
            "drop_after": SCENARIO_MAX_AGE_DAYS,
        })
    # ⚠️ الأحدث أولاً — نفس اتجاه القائمة المُقيَّمة تحتها. كان تصاعدياً بينما
    # المُقيَّمة تنازلية، فكانت التواريخ تُقرأ 12 ثم 15 ثم 13 (بلاغ المالك
    # 2026-08-15: «غير مرتّب»). قائمة واحدة يجب أن يكون لها اتجاه واحد.
    waiting.sort(key=lambda x: x.get("kickoff") or x.get("date") or "",
                 reverse=True)
    # دقة كل نوع على السجل الكامل (لا العينة المعروضة فقط)
    def acc(flag):
        sub = [e for e in resolved if bool(e.get("shadow")) == flag]
        return {"correct": sum(e.get("correct") or 0 for e in sub),
                "total": sum(e.get("total") or 0 for e in sub),
                "reports": len(sub)}
    return {
        "reports": rows,
        "waiting": waiting,
        "shadow_acc": acc(True),
        "watch_acc": acc(False),
        "graded_total": len(resolved),
        "pending": len(sc.get("pending") or {}),
        "experiments": build_active_experiments(),
    }


def build_data_v2() -> None:
    """يبني data_v2.json للمحرك 2 بنفس مخطط data.json.

    اللوحة تعرض لوحة "قيد الإنشاء" ما دام الملف غير موجود، لذلك لا نُنشئه
    قبل أول توقعات فعلية. ولا نعيد كتابته إذا لم يتغير المحتوى — حتى لا
    يتّسخ مستودع تشغيلات المراقب (التي لا تعمل commit لهذا الملف).
    """
    store = load_json(PREDICTIONS_V2_FILE, {})
    if not (store.get("pending") or store.get("resolved")):
        return

    data = {
        "live": [],
        "upcoming": build_upcoming(store),
        "recent_results": build_recent_results(store),
        # ⏳ فجوة ديفونبورت: المنتهية بانتظار تقييم الصباح — عرض فقط
        "awaiting": build_awaiting(store),
        # 🎛 REC-010: المحرك 2 يكتب شريحة دورياته في meta.stats بنفسه؛ هنا
        # نضمن وجودها حتى قبل أول تشغيلة صباحية بعد النشر (اللوحة تفتح على
        # "دورياتي" افتراضياً، فلا يصح أن تفتح فارغة يوم النشر)
        "accuracy": _with_top_only(
            (store.get("meta") or {}).get("stats") or {}, store),
        "news": [],
        "lessons": recent_lessons(),
        # الأرشيف الدائم (كل الأيام، كل الأطراف) — للوحة ولأي تحليل مستقبلي
        "history": load_json(HISTORY_FILE, {}).get("days") or {},
        "shadow_lab": build_shadow_lab(),
        # خط التحديث (طلب المالك 2026-08-02): متى صدرت توقعات هذا المحرك
        "pred_updated": (store.get("meta") or {}).get("last_run", ""),
    }
    existing = load_json(DATA_V2_FILE, {})
    existing.pop("updated", None)
    if existing == data:
        return
    data["updated"] = now_utc().isoformat()
    save_json(DATA_V2_FILE, data)
    print(
        f"data_v2.json: {len(data['upcoming'])} قادمة، "
        f"{len(data['recent_results'])} نتيجة أخيرة."
    )


def freshness_warnings(state: dict) -> list:
    """حارس الطزاجة (بلاغ المالك 2026-08-02 — اللوحة 9 والواقع 18): يفحص عمر
    لقطات المباريات الحية وقت بناء اللوحة. طابع رصد مفقود أو أقدم من 20 دقيقة
    يعني أن الشاشة ستعرض ماضياً على أنه حاضر — يُطبع صاخباً في سجل التشغيل،
    واللوحة نفسها تعرض تحذيراً كهرمانياً للمالك (فحص العميل المستقل)."""
    warns = []
    for fid, e in (state or {}).items():
        if not isinstance(e, dict) or e.get("status") not in LIVE_STATUSES:
            continue
        seen = e.get("seen")
        label = f"{e.get('home', '?')} × {e.get('away', '?')}"
        if not seen:
            warns.append(f"{label}: بلا طابع رصد (seen)")
            continue
        try:
            age = (now_utc() - datetime.fromisoformat(seen)).total_seconds() / 60
            if age > 20:
                warns.append(f"{label}: لقطة عمرها {int(age)} دقيقة")
        except ValueError:
            warns.append(f"{label}: طابع رصد تالف")
    return warns


def _daily_warnings(rows: list) -> dict:
    """اتجاه صدق الإنذارات يوماً بيوم من قائمة إنذارات مُقيَّمة."""
    daily = {}
    for w in rows:
        d = (w or {}).get("graded_on")
        if not d:
            continue
        s = daily.setdefault(d, {"hit": 0, "total": 0})
        s["total"] += 1
        s["hit"] += 1 if w.get("failed") else 0
    return dict(sorted(daily.items())[-30:])


def top_only_accuracy(resolved: list) -> dict:
    """🎛 شريحة دوريات المالك لمحرك تُقرأ ذاكرته هنا — REC-010.

    تُستدعى لأرقام **المحرك 1**: المحرك 1 مجمّد بأمر المشروع (قاعدة 7) فلا
    يُضاف إليه حساب، فتُشتق شريحته هنا قراءةً من predictions.json وحده.
    الحساب نفسه مستعار حرفياً من predict_v2.top_only_stats — مصدر رياضيات
    واحد للمحركين، فلا تنحرف نسخة عن أخرى (يحرسه tests/test_my_leagues.py).
    صفر نداءات API/Claude: كل الأرقام محلية."""
    from predict_v2 import top_only_stats
    return top_only_stats(resolved or [])


def _with_top_only(stats: dict, store: dict) -> dict:
    """يعيد نسخة من إحصاءات محرك مضموناً فيها كتلة `top_only` (REC-010).

    المحرك 2 يكتبها بنفسه كل صباح فتُمرَّر كما هي؛ المحرك 1 مجمّد فتُشتق
    هنا، وكذلك تُشتق لأي لوحة أُنتجت قبل أول تشغيلة صباحية بعد النشر.
    **لا يُمسّ أي مفتاح آخر** — أرقام "الكل" تخرج كما دخلت حرفياً."""
    out = dict(stats or {})
    if not isinstance(out.get("top_only"), dict):
        out["top_only"] = top_only_accuracy((store or {}).get("resolved") or [])
    return out


def build_radar_accuracy() -> dict:
    """لوحة دقة الرادار S3 (طلب المالك 2026-08-09 — تفصيل مرئي كامل):
    إحصاءات المستويات والادعاءات كما بناها التقييم الصباحي + قائمتا قاعدة
    الإيقاف (مُثبَت/صامت) + اتجاه دقة الإنذارات يوماً-بيوم من السجل التراكمي.
    صفر نداءات — قراءة radar_log.json فقط."""
    log = load_json(RADAR_LOG_FILE, {})
    out = dict((log.get("meta") or {}).get("stats") or {})
    out["silenced"] = log.get("silenced") or []
    out["proven"] = log.get("proven") or []
    resolved = log.get("resolved") or []
    out["daily_warnings"] = _daily_warnings(resolved)
    # 🎛 REC-010: اتجاه شريحة دوريات المالك — الكتلة الموازية نفسها يبنيها
    # التقييم الصباحي في predict_v2؛ هنا يُضاف اتجاهها اليومي فقط (عرض).
    # السجلات القديمة بلا علامة `top` لا تدخل — الشريحة تمتلئ من لحظة التنفيذ.
    if isinstance(out.get("top_only"), dict):
        out["top_only"] = dict(out["top_only"])
        out["top_only"]["daily_warnings"] = _daily_warnings(
            [w for w in resolved if (w or {}).get("top")])
    # 📜 وجوه العدّادات (طلب المالك 2026-08-16 — «أريد أن أرى من هو من»):
    # آخر الإنذارات المُقيَّمة بأسمائها وتفاصيلها تُصدَّر للوحة كي لا يبقى
    # العدّاد رقماً بلا مباريات. شريحة عرض فقط (آخر 40) — السجل الكامل
    # غير المقصوص يبقى في radar_log.json (عقيدة عدم سقف القياس).
    out["recent"] = [{
        "date": w.get("date"), "home": w.get("home"), "away": w.get("away"),
        "league": w.get("league"), "level": w.get("level"),
        "minute": w.get("minute"), "score": w.get("score"),
        "pick": w.get("pick"), "conf": w.get("confidence"),
        "final": w.get("final_score"),
        "hit": bool(w.get("failed")),   # أصاب = التوقع المحذَّر منه سقط فعلاً
        "top": bool(w.get("top")),
    } for w in resolved[-40:]]
    return out


def main() -> None:
    state = load_json(STATE_FILE, {})
    store = load_json(PREDICTIONS_FILE, {"pending": {}, "resolved": [], "meta": {}})
    store_v2 = load_json(PREDICTIONS_V2_FILE, {})
    news = refresh_news()

    stats = (store.get("meta") or {}).get("stats") or {}

    data = {
        "updated": now_utc().isoformat(),
        "live": build_live(state, store, store_v2),
        "upcoming": build_upcoming(store),
        "recent_results": build_recent_results(store),
        # ⏳ فجوة ديفونبورت: المنتهية بانتظار تقييم الصباح — عرض فقط
        "awaiting": build_awaiting(store),
        # 🎛 REC-010: شريحة دوريات المالك للمحرك 1 — تُشتق هنا قراءةً من
        # predictions.json لأن المحرك 1 مجمّد ولا يُضاف إليه حساب (قاعدة 7)
        "accuracy": _with_top_only(stats, store),
        "news": news.get("items", []),
        # لوحة دقة الرادار S3: مستويات وادعاءات وحالة قاعدة الإيقاف واتجاه يومي
        "radar_acc": build_radar_accuracy(),
        # خط التحديث (طلب المالك 2026-08-02): متى صدرت توقعات المحرك 1
        "pred_updated": (store.get("meta") or {}).get("last_run", ""),
    }
    save_json(DATA_FILE, data)
    print(
        f"data.json: {len(data['live'])} حية، {len(data['upcoming'])} قادمة، "
        f"{len(data['news'])} خبراً."
    )
    for w in freshness_warnings(state):
        print("⚠️ حارس الطزاجة:", w)

    build_data_v2()


if __name__ == "__main__":
    main()
