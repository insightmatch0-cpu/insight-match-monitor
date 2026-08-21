# -*- coding: utf-8 -*-
"""🧪 رسائل توضيحية عن قنوات الرادار — زر يدوي، بلا أي نداء API-Football أو Claude.

طلب المالك 2026-08-21: «أعطني مثالاً عبر تيليجرام حتى أعرف أي رسالة أي قناة».

كل الأرقام والأمثلة تُحسب لحظة التشغيل من `radar_log.json` نفسه — لا رقم
مكتوب يدوياً (قاعدة الأرقام المجمّدة). كل رسالة تبدأ بوسم «مثال توضيحي»
حتى لا تُخلط أبداً بتنبيه حقيقي.
"""
import json
import os

import api_guard

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_BROADCAST_IDS = os.environ.get("TELEGRAM_BROADCAST_IDS", "")

RADAR_FILE = "radar_log.json"
TAG = "🧪 مثال توضيحي — ليس تنبيهاً حقيقياً"


def _pct(hit, total):
    """نسبة مئوية بأرقام لاتينية، أو شرطة حين لا عيّنة."""
    return f"{round(100 * hit / total)}%" if total else "—"


def load_rows():
    """الصفوف المُقيَّمة من سجل الرادار (قد يكون الملف غائباً)."""
    try:
        with open(RADAR_FILE, encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("resolved") or []
    except (OSError, ValueError):
        return []


def slices(rows):
    """الشرائح الثلاث التي سأل عنها المالك: المُرسل كله، لوحة النتائج، الزخم."""
    sent = [w for w in rows if w.get("alerted")]
    board = [w for w in sent if len(w.get("factors") or []) == 1]
    momentum = [w for w in sent if len(w.get("factors") or []) > 1]
    return sent, board, momentum


def example_line(rows):
    """أحدث مثال حقيقي من الشريحة، أو نص صريح حين لا مثال بعد."""
    if not rows:
        return None
    w = rows[-1]
    verdict = "✅ الإنذار كان صحيحاً — التوقع سقط" if w.get("failed") \
        else "❌ الإنذار كان خطأ — التوقع نجا"
    pick = {"home": w.get("home"), "away": w.get("away")}.get(w.get("pick"), "التعادل")
    return (
        f"⚽️ {w.get('home')} × {w.get('away')}\n"
        f"🔮 توقع المحرك 2: {pick} ({w.get('confidence')}%)\n"
        f"⏱ دقيقة الإنذار: د{w.get('alert_minute') or w.get('minute')}\n"
        f"🎯 درجة الخطر: {w.get('score')} من 100\n"
        f"📋 العوامل: {' + '.join(w.get('factors') or [])}\n"
        f"🏁 النتيجة النهائية: {w.get('final_score')}\n"
        f"{verdict}"
    )


def build_messages(rows):
    """يبني الرسائل الثلاث: خريطة القنوات، ثم مثال لكل نوع عامل."""
    sent, board, momentum = slices(rows)
    msgs = [
        f"{TAG}\n\n"
        "🗺 خريطة قنوات الرادار — أيها يصل هاتفك؟\n\n"
        f"✅ يصلك: الإنذار الأحمر المبكر (د85 فأقل) — {len(sent)} مُقيَّماً، "
        f"دقته {_pct(sum(1 for w in sent if w.get('failed')), len(sent))}\n"
        f"   ├ منه بعامل لوحة النتائج وحده: {len(board)} — "
        f"{_pct(sum(1 for w in board if w.get('failed')), len(board))}\n"
        f"   └ منه مع عوامل زخم: {len(momentum)} — "
        f"{_pct(sum(1 for w in momentum if w.get('failed')), len(momentum))}\n\n"
        "✅ يصلك أيضاً: ⚡🚨 تنبيه الدراما (قناة مستقلة، عنوانها مختلف)\n\n"
        "🚫 لا يصلك (شاشة البوابة فقط): الأحمر بعد د85، وكل الكهرماني.\n\n"
        "الصفان الثاني والثالث ليسا قناتين منفصلتين — هما تقسيم للقناة نفسها "
        "حسب سبب الإنذار. مجموعهما = العدد الأول."
    ]
    for title, rows_ in (
        ("1️⃣ إنذار أحمر مبكر — عامل «لوحة النتائج» وحده", board),
        ("2️⃣ إنذار أحمر مبكر — مع عوامل «زخم»", momentum),
    ):
        body = example_line(rows_) or "لا مثال مُقيَّم في هذه الشريحة بعد."
        msgs.append(f"{TAG}\n\n{title}\n\n{body}")
    return msgs


def main():
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        print("لا مفاتيح تيليجرام — توقف نظيف.")
        return
    for text in build_messages(load_rows()):
        api_guard.send_telegram_multi(
            TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_BROADCAST_IDS, text)
    api_guard.exit_if_owner_unreachable()


if __name__ == "__main__":
    main()
