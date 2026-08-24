#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🏆 تقرير «أين نتفوق؟» الأسبوعي (طلب المالك 2026-08-24).

يحسب لحظة التشغيل — لا رقم مكتوب يدوياً أبداً (قاعدة الأرقام المجمّدة) —
أبطال كل طبقة: S1 حسب الدوري، S2 حسب نوع الادعاء، S3 وقناتا الهاتف،
ثم يبني صفحة داكنة ويطبعها PDF بنفس مسار build_pdf.

صفر نداءات API-Football وصفر نداءات Claude — قراءة ملفات القياس فقط.
حارس العينة إلزامي: دوري تحت 30 مباراة لا يدخل ترتيب S1، وشريحة رادار
تحت 15 لا تدخل ترتيبه، وكل عدّاد خام يُعرض بجانب نسبته.
"""

import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

S1_MIN_LEAGUE = 30     # حد عينة ترتيب دوريات التوقع الصباحي
S3_MIN_LEAGUE = 15     # حد عينة ترتيب دوريات الرادار الأحمر

# نفس فئات REC-006 روحاً — مصنّف محلي حتى لا يستورد التقرير monitor بكامله
CLAIM_CATS = [
    ("يسجل الفريقان", ("يسجلان", "كلا الفريقين", "لا يسجل الفريقان")),
    ("فوق/تحت 2.5", ("أكثر من", "أقل من", "مجموع الأهداف", "2.5", "3.5", "1.5")),
    ("الركنيات", ("ركني",)),
    ("البطاقات", ("بطاق", "إنذار", "صفراء", "حمراء", "طرد")),
    ("الكرات الثابتة", ("ثابتة", "ركلة حرة", "ركلات حرة", "رمية")),
    ("نمط الشوطين", ("شوط",)),
    ("المسجل بالاسم", ("يسجل", "هدف من", "أول هدف")),
    ("النتيجة/الهامش", ("النتيجة المتوقعة", "يفوز", "فوز", "تعادل", "هامش", "فارق")),
]


def _load(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def _pct(c, n):
    return round(100 * c / n) if n else 0


def collect() -> dict:
    """كل أرقام التقرير — محسوبة من الملفات الأربعة، بحدود عيناتها."""
    p2 = [e for e in _load("predictions_v2.json")["resolved"]
          if isinstance(e, dict)]
    p1 = [e for e in _load("predictions.json")["resolved"]
          if isinstance(e, dict)]
    log = _load("radar_log.json")
    sc = _load("scenarios_v2.json").get("resolved") or []

    d = {"date": datetime.now(timezone.utc).strftime("%Y-%m-%d")}

    # ---- S1 ----
    c2, n2 = sum(1 for e in p2 if e.get("correct")), len(p2)
    c1, n1 = sum(1 for e in p1 if e.get("correct")), len(p1)
    b70 = [e for e in p2 if (e.get("confidence") or 0) >= 70]
    d["s1"] = {"v2": (c2, n2, _pct(c2, n2)), "v1": (c1, n1, _pct(c1, n1)),
               "b70": (sum(1 for e in b70 if e.get("correct")), len(b70),
                       _pct(sum(1 for e in b70 if e.get("correct")), len(b70)))}
    by = defaultdict(list)
    for e in p2:
        by[e.get("league") or "?"].append(e)
    rows = [(_pct(sum(1 for x in v if x.get("correct")), len(v)),
             sum(1 for x in v if x.get("correct")), len(v), k)
            for k, v in by.items() if len(v) >= S1_MIN_LEAGUE]
    rows.sort(reverse=True)
    d["s1_top"] = rows[:6]
    d["s1_bottom"] = sorted(rows)[:3]

    # ---- S2 ----
    cats = defaultdict(lambda: [0, 0])
    tot = [0, 0]
    for r in sc:
        for g in (r.get("grades") or []):
            claim, res = g.get("claim") or "", (g.get("result") or "")
            name = next((n for n, kws in CLAIM_CATS
                         if any(k in claim for k in kws)), "أخرى")
            cats[name][1] += 1
            tot[1] += 1
            if "صح" in res:
                cats[name][0] += 1
                tot[0] += 1
    d["s2_total"] = (tot[0], tot[1], _pct(*tot))
    d["s2_cats"] = sorted(
        ((_pct(*v), v[0], v[1], k) for k, v in cats.items() if v[1] >= 20),
        reverse=True)
    best = [(r, e) for e in sc
            for r in [_pct(e.get("correct") or 0, e.get("total") or 1)]
            if (e.get("total") or 0) >= 6]
    if best:
        r, e = max(best)
        d["s2_best"] = (r, e.get("correct"), e.get("total"),
                        f"{e.get('home')} × {e.get('away')}")

    # ---- S3 ----
    res = log.get("resolved") or []
    red = [w for w in res if w.get("level") == "red"]
    amb = [w for w in res if w.get("level") == "amber"]
    d["s3"] = {
        "red": (sum(1 for w in red if w.get("failed")), len(red),
                _pct(sum(1 for w in red if w.get("failed")), len(red))),
        "amber": (sum(1 for w in amb if w.get("failed")), len(amb),
                  _pct(sum(1 for w in amb if w.get("failed")), len(amb))),
    }
    byl = defaultdict(list)
    for w in red:
        byl[w.get("league") or "?"].append(w)
    lr = [(_pct(sum(1 for x in v if x.get("failed")), len(v)),
           sum(1 for x in v if x.get("failed")), len(v), k)
          for k, v in byl.items() if len(v) >= S3_MIN_LEAGUE]
    lr.sort(reverse=True)
    d["s3_leagues"] = lr[:5]

    sent = [w for w in res if w.get("alerted")]
    d["sent_total"] = (sum(1 for w in sent if w.get("failed")), len(sent),
                       _pct(sum(1 for w in sent if w.get("failed")), len(sent)))
    d["sent_bands"] = []
    for lo, hi, lbl in ((76, 85, "د76-85"), (61, 75, "د61-75"),
                        (0, 60, "قبل د60")):
        l = [w for w in sent
             if lo <= (w.get("alert_minute") or w.get("minute") or 0) <= hi]
        h = sum(1 for w in l if w.get("failed"))
        d["sent_bands"].append((lbl, h, len(l), _pct(h, len(l))))

    byk = defaultdict(list)
    for a in (log.get("alerts_resolved") or []):
        byk[a.get("key") or "?"].append(a)
    AR = {"equalizer": "إدراك التعادل", "next_goal": "الهدف القادم",
          "flip": "قلب النتيجة", "goal": "هدف المتأخر",
          "red_advantage": "أفضلية الطرد"}
    d["drama"] = sorted(
        ((_pct(sum(1 for a in v if a.get("hit")), len(v)),
          sum(1 for a in v if a.get("hit")), len(v), AR.get(k, k))
         for k, v in byk.items()), reverse=True)
    dr_all = [a for v in byk.values() for a in v]
    d["drama_total"] = (sum(1 for a in dr_all if a.get("hit")), len(dr_all),
                        _pct(sum(1 for a in dr_all if a.get("hit")), len(dr_all)))
    return d


def _bars(rows, cls="fill"):
    out = []
    for pct, c, n, name in rows:
        out.append(
            f'<div class="bar"><span class="lbl">{name}</span>'
            f'<span class="val"><b class="num">{pct}%</b> '
            f'<span class="num">{c}/{n}</span></span>'
            f'<span class="track"><span class="{cls}" '
            f'style="width:{pct}%"></span></span></div>')
    return "\n".join(out)


def render(d: dict) -> str:
    """الصفحة الداكنة — نفس هوية «أين نتفوق» المعتمدة 2026-08-24."""
    s1, s3 = d["s1"], d["s3"]
    css = """
:root{--bg:#0D1117;--card:#161C25;--card2:#1B2330;--line:#28313E;
--ink:#E8EDF4;--mut:#8DA0B3;--faint:#5D6B7A;--gold:#E3B341;
--gold-soft:rgba(227,179,65,.12);--teal:#53BEC9;--good:#3FBD86;--bad:#E06552}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);direction:rtl;
font-family:"IBM Plex Sans Arabic","SF Arabic",Tahoma,sans-serif;
font-size:15px;line-height:1.7}
.num{font-family:ui-monospace,Menlo,Consolas,monospace;
font-variant-numeric:tabular-nums;direction:ltr;display:inline-block}
main{max-width:780px;margin:0 auto;padding:24px 18px 60px;display:flex;
flex-direction:column;gap:26px}
header{border-bottom:2px solid var(--gold);padding-bottom:16px}
.eyebrow{font-size:11px;letter-spacing:.14em;color:var(--teal)}
h1{margin:6px 0 6px;font-size:28px;font-weight:700}
.sub{margin:0;color:var(--mut);font-size:13.5px}
section{display:flex;flex-direction:column;gap:11px}
h2{margin:0;font-size:19px;font-weight:700;display:flex;align-items:baseline;gap:10px}
h2 .tag{font-size:11.5px;color:var(--faint);font-weight:400}
h2::after{content:"";flex:1;height:1px;background:var(--line)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:15px 17px;display:flex;flex-direction:column;gap:9px}
.medal{background:var(--gold-soft);border:1px solid rgba(227,179,65,.45);
border-radius:12px;padding:13px 17px}
.medal b{color:var(--gold)}
p{margin:0}.mut{color:var(--mut);font-size:13px}
.small{font-size:12px;color:var(--faint)}
.bars{display:flex;flex-direction:column;gap:8px}
.bar{display:grid;grid-template-columns:1fr 84px;gap:10px;align-items:center}
.bar .lbl{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar .track{grid-column:1;height:8px;background:var(--card2);border-radius:6px;overflow:hidden}
.bar .fill{height:100%;background:var(--teal);border-radius:6px}
.bar .g{height:100%;background:var(--gold);border-radius:6px}
.bar .bad{height:100%;background:var(--bad);border-radius:6px}
.bar .val{grid-column:2;grid-row:1/span 2;text-align:left;font-size:12.5px;color:var(--mut)}
.bar .val b{color:var(--ink);font-size:14.5px}
.note{border-radius:10px;padding:10px 14px;font-size:13px;background:var(--card2);
border-right:3px solid var(--teal)}
.note.gold{border-right-color:var(--gold)}
table{width:100%;border-collapse:collapse;font-size:13px}
th{color:var(--mut);font-weight:500;text-align:right;padding:5px 8px;
border-bottom:1px solid var(--line)}
td{padding:6px 8px;border-bottom:1px solid var(--card2)}
tr:last-child td{border-bottom:0}
.tw{overflow-x:auto}
footer{color:var(--faint);font-size:11.5px;border-top:1px solid var(--line);
padding-top:12px;line-height:1.8}
@media print{.card,.medal,.note,.tw{break-inside:avoid}h2{break-after:avoid}}
"""
    top_league = d["s1_top"][0] if d["s1_top"] else (0, 0, 0, "—")
    s3_top = d["s3_leagues"][0] if d["s3_leagues"] else (0, 0, 0, "—")
    s2c = d["s2_cats"]
    s2_best_cat = s2c[0] if s2c else (0, 0, 0, "—")
    drama_rows = "\n".join(
        f'<tr><td>{name}</td><td class="num">{c}/{n}</td>'
        f'<td class="num">{pct}%</td></tr>'
        for pct, c, n, name in d["drama"])
    s2_rows = "\n".join(
        f'<tr><td>{name}</td><td class="num">{c}/{n}</td>'
        f'<td class="num">{pct}%</td></tr>'
        for pct, c, n, name in s2c)
    bands = "\n".join(
        f'<tr><td>{lbl}</td><td class="num">{c}/{n}</td>'
        f'<td class="num">{pct}%</td></tr>'
        for lbl, c, n, pct in d["sent_bands"])
    best_rep = d.get("s2_best")
    best_line = (f'أفضل تقرير حتى اليوم: <b>{best_rep[3]}</b> — '
                 f'<span class="num">{best_rep[1]}/{best_rep[2]} '
                 f'= {best_rep[0]}%</span>' if best_rep else "")
    return f"""<title>أين نتفوق؟</title>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>{css}</style>
<main>
<header>
<span class="eyebrow">INSIGHTMATCH · التقرير الأسبوعي · بيانات حتى <span class="num">{d['date']}</span></span>
<h1>🏆 أين نتفوق؟</h1>
<p class="sub">أبطال كل طبقة وكل قناة — كل رقم محسوب لحظة إصدار هذا الملف من سجلات القياس، وكل عينة صغيرة خارج الترتيب.</p>
</header>

<section><h2>🧠 S1 — التوقع الصباحي <span class="tag"><span class="num">{s1['v2'][1]:,}</span> مباراة مُقيَّمة</span></h2>
<div class="medal"><b>🥇 بطل الدوريات: {top_league[3]} — <span class="num">{top_league[0]}%</span> (<span class="num">{top_league[1]}/{top_league[2]}</span>)</b>
<p class="mut">المحرك 2 إجمالاً <b class="num">{s1['v2'][2]}%</b> (<span class="num">{s1['v2'][0]}/{s1['v2'][1]}</span>) مقابل المحرك 1 <span class="num">{s1['v1'][2]}%</span> · خانة الثقة 70%+ عند <b class="num">{s1['b70'][2]}%</b> (<span class="num">{s1['b70'][0]}/{s1['b70'][1]}</span>)</p></div>
<div class="card"><div class="bars">{_bars(d['s1_top'][:1], 'g')}{_bars(d['s1_top'][1:])}</div>
<p class="small">الأضعف: {' · '.join(f"{r[3]} <span class='num'>{r[0]}%</span> (<span class='num'>{r[1]}/{r[2]}</span>)" for r in d['s1_bottom'])} — للدوريات بعينة ≥<span class="num">{S1_MIN_LEAGUE}</span>.</p></div>
</section>

<section><h2>🎯 S2 — تقرير ما قبل المباراة <span class="tag"><span class="num">{d['s2_total'][1]:,}</span> بنداً</span></h2>
<div class="medal"><b>🥇 أقوى بند: {s2_best_cat[3]} — <span class="num">{s2_best_cat[0]}%</span></b>
<p class="mut">الإجمالي الصارم <span class="num">{d['s2_total'][2]}%</span> (<span class="num">{d['s2_total'][0]}/{d['s2_total'][1]}</span>) · {best_line}</p></div>
<div class="card"><div class="tw"><table>
<tr><th>نوع الادعاء</th><th>صح/الكل</th><th>الدقة</th></tr>{s2_rows}</table></div>
<p class="small">للأنواع بعينة ≥<span class="num">20</span>. بنود الركنيات والثابتة أُعيدت صياغتها ذرياً (prompt_rev:2) في <span class="num">2026-08-24</span> — شريحتها الجديدة تُقاس منفصلة.</p></div>
</section>

<section><h2>🛰 S3 — الرادار الحي <span class="tag"><span class="num">{s3['red'][1] + s3['amber'][1]:,}</span> إنذاراً مُقيَّماً</span></h2>
<div class="medal"><b>🥇 بطل المنظومة: الإنذار الأحمر — <span class="num">{s3['red'][2]}%</span> (<span class="num">{s3['red'][0]}/{s3['red'][1]}</span>)</b>
<p class="mut">الكهرماني <span class="num">{s3['amber'][2]}%</span> (شاشة فقط بحق) · أفضل دوري: {s3_top[3]} <span class="num">{s3_top[0]}%</span> (<span class="num">{s3_top[1]}/{s3_top[2]}</span>)</p></div>
<div class="card"><div class="bars">{_bars(d['s3_leagues'][:1], 'g')}{_bars(d['s3_leagues'][1:], 'fill')}</div>
<p class="small">للدوريات بعينة ≥<span class="num">{S3_MIN_LEAGUE}</span> إنذاراً أحمر.</p></div>
</section>

<section><h2>🔴 قناة هاتفك — الإنذار المبكر <span class="tag"><span class="num">{d['sent_total'][1]}</span> مُرسلاً مُقيَّماً</span></h2>
<div class="card"><div class="tw"><table>
<tr><th>دقيقة الإرسال</th><th>صح/الكل</th><th>الدقة</th></tr>{bands}</table></div>
<p class="small">الإجمالي <b class="num">{d['sent_total'][2]}%</b> (<span class="num">{d['sent_total'][0]}/{d['sent_total'][1]}</span>). القاعدة: بعد د<span class="num">75</span> خذه بجدية تامة؛ المبكر جداً تحذير لا حكم. القناة منذ <span class="num">2026-08-24</span> لدورياتك التسعة + مفضلاتك.</p></div>
</section>

<section><h2>⚡ تنبيهات الدراما <span class="tag">🧪 تجريبية · <span class="num">{d['drama_total'][1]}</span> مُقيَّماً</span></h2>
<div class="card"><div class="tw"><table>
<tr><th>الادعاء</th><th>صح/الكل</th><th>الدقة</th></tr>{drama_rows}</table></div>
<p class="small">الإجمالي <span class="num">{d['drama_total'][2]}%</span> — أضعف طبقة، وطبقة xG الحية تعمل في الظل لإنقاذها.</p></div>
</section>

<footer>المصادر: predictions_v2.json · predictions.json · radar_log.json · scenarios_v2.json — كلها لحظة إصدار الملف. القاعدة الثابتة: كل نسبة بجانبها عدّادها الخام، وما دون حدود العينات لا يدخل الترتيب.</footer>
</main>"""


def find_chrome() -> str:
    for c in ["/usr/bin/google-chrome", "/usr/bin/chromium-browser",
              "/usr/bin/chromium",
              "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"]:
        if Path(c).exists():
            return c
    raise SystemExit("لا متصفح لطباعة PDF")


PRINT_CSS = ('<style>@page{size:A4;margin:0}html,body{background:#0D1117;'
             '-webkit-print-color-adjust:exact;print-color-adjust:exact}'
             'main{max-width:none;padding:12mm 10mm}</style>')


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "InsightMatch-أين-نتفوق.pdf")
    html = render(collect())
    (HERE / "best-report.html").write_text(html, encoding="utf-8")
    tmp = HERE / "_best_print.html"
    tmp.write_text('<!doctype html><html lang="ar" dir="rtl"><head>'
                   + PRINT_CSS + "</head><body>" + html + "</body></html>",
                   encoding="utf-8")
    subprocess.run([find_chrome(), "--headless", "--disable-gpu",
                    "--no-sandbox", "--no-pdf-header-footer",
                    f"--print-to-pdf={out}", f"file://{tmp.resolve()}"],
                   check=True, capture_output=True)
    tmp.unlink(missing_ok=True)
    size = out.stat().st_size if out.exists() else 0
    if size < 30_000:
        raise SystemExit(f"الملف صغير بشكل مريب ({size} بايت) — طباعة فاشلة")
    print(f"✅ {out} — {size // 1024} KB")


if __name__ == "__main__":
    main()
