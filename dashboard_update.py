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

STATE_FILE       = Path("state.json")
PREDICTIONS_FILE = Path("predictions.json")
NEWS_FILE        = Path("news.json")
DATA_FILE        = Path("data.json")

NEWS_MAX_AGE_HOURS = 3     # لا نحدّث الأخبار قبل مرور هذه المدة
NEWS_MAX_ITEMS     = 15

# خلاصات مجانية وغير محدودة — أضف أو احذف ما تريد
NEWS_FEEDS = [
    ("BBC عربي",     "https://feeds.bbci.co.uk/arabic/sports/rss.xml"),
    ("BBC Sport",    "https://feeds.bbci.co.uk/sport/football/rss.xml"),
    ("Sky Sports",   "https://www.skysports.com/rss/12040"),
    ("The Guardian", "https://www.theguardian.com/football/rss"),
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
def build_live(state: dict) -> list:
    live = []
    for fid, e in state.items():
        if not isinstance(e, dict):
            continue
        if e.get("status") not in LIVE_STATUSES:
            continue
        ar = e.get("ar") or {}
        live.append({
            "fid": fid,
            "home": ar.get("home") or e.get("home", "?"),
            "away": ar.get("away") or e.get("away", "?"),
            "league": ar.get("league") or e.get("league", ""),
            "score": e.get("score", "0-0"),
            "minute": e.get("minute", 0),
            "status": e.get("status", ""),
        })
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
        upcoming.append({
            "fid": fid,
            "kickoff": p.get("kickoff"),
            "home": p.get("ar_home") or p.get("home", "?"),
            "away": p.get("ar_away") or p.get("away", "?"),
            "home_en": p.get("home", "?"),
            "away_en": p.get("away", "?"),
            "league": p.get("ar_league") or p.get("league", ""),
            "top": bool(p.get("top")),
            "pick": p.get("pick"),
            "confidence": p.get("confidence"),
            "reason": p.get("reason", ""),
        })
    upcoming.sort(key=lambda m: (not m["top"], m["kickoff"]))
    return upcoming


def build_recent_results(store: dict) -> list:
    out = []
    for r in (store.get("resolved") or [])[-20:]:
        out.append({
            "date": r.get("date"),
            "home": r.get("ar_home") or r.get("home", "?"),
            "away": r.get("ar_away") or r.get("away", "?"),
            "league": r.get("ar_league") or r.get("league", ""),
            "pick": r.get("pick"),
            "confidence": r.get("confidence"),
            "score": r.get("score"),
            "correct": bool(r.get("correct")),
        })
    out.reverse()
    return out


def main() -> None:
    state = load_json(STATE_FILE, {})
    store = load_json(PREDICTIONS_FILE, {"pending": {}, "resolved": [], "meta": {}})
    news = refresh_news()

    stats = (store.get("meta") or {}).get("stats") or {}

    data = {
        "updated": now_utc().isoformat(),
        "live": build_live(state),
        "upcoming": build_upcoming(store),
        "recent_results": build_recent_results(store),
        "accuracy": stats,
        "news": news.get("items", []),
    }
    save_json(DATA_FILE, data)
    print(
        f"data.json: {len(data['live'])} حية، {len(data['upcoming'])} قادمة، "
        f"{len(data['news'])} خبراً."
    )


if __name__ == "__main__":
    main()
