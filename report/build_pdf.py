#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""📄 بناء نسخة PDF داكنة من صفحة غرفة التحكم (طلب المالك 2026-08-16).

يقرأ `report/control-room.html` — وهي جسم صفحة بلا هيكل (تُغلَّف عند النشر) —
فيبني منها مستنداً كاملاً بالوضع الداكن مهيّأً للورق، ثم يطبعه بـChrome بلا
رأس ولا تذييل.

**لماذا الداكن إلزامي هنا**: المالك طلبه صراحةً («make it darken, not this
white»)، والصفحة تُقرأ على الجوال ليلاً. السمة `data-theme="dark"` هي مفتاح
الوضع الداكن في تعريفات الألوان — بدونها يخرج الملف أبيض.

**ولماذا هامش الصفحة صفر**: خلفية الوضع الداكن تُرسم من `body`؛ أي هامش
صفحة يترك إطاراً أبيض حول المحتوى. الهوامش الحقيقية داخل `main`.
"""

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "control-room.html"

# سلسلة بحث عن المتصفح: منفّذ Actions يحمل google-chrome جاهزاً، وبيئة
# التطوير تحمل chromium من playwright. أول موجود يُستخدم.
CHROME_CANDIDATES = [
    os.environ.get("CHROME_BIN", ""),
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
]

PRINT_CSS = """
<style>
/* ===== تهيئة الورق — الوضع الداكن ===== */
@page { size: A4; margin: 0; }
html, body { background: var(--bg);
  -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { font-size: 12.5px; line-height: 1.55; }
main { max-width: none; padding: 12mm 10mm; gap: 16px; }
h1 { font-size: 22px; } h2 { font-size: 16px; } h3 { font-size: 13.5px; }
/* الجداول مصممة للانزلاق الأفقي باللمس — على الورق تُفرد بالكامل */
.tw { overflow: visible !important; }
.tw.wide table, table { min-width: 0 !important; width: 100%; font-size: 11px; }
.tw.wide th, .tw.wide td { white-space: normal !important; }
th, td { padding: 5px 7px; }
.plot { overflow: visible !important; }
/* لا تُقطع بطاقة ولا جدول بين صفحتين */
.card, .chart, .note, .tw, .mock, .fstep, .rung {
  break-inside: avoid; page-break-inside: avoid; }
h2, h3 { break-after: avoid; page-break-after: avoid; }
tr { break-inside: avoid; }
.card, .chart { box-shadow: none; }
</style>
"""


def find_chrome() -> str:
    for c in CHROME_CANDIDATES:
        if c and Path(c).exists():
            return c
    raise SystemExit("لم يُعثر على متصفح لطباعة PDF (جرّب ضبط CHROME_BIN)")


def build_document(src: str) -> str:
    """يغلّف جسم الصفحة بمستند كامل داكن مهيّأ للطباعة."""
    head, body = src.split("<main>", 1)
    return ('<!doctype html>\n<html lang="ar" dir="rtl" data-theme="dark">\n'
            '<head>\n<meta charset="utf-8">\n' + head + PRINT_CSS +
            "\n</head>\n<body>\n<main>" + body + "\n</body>\n</html>\n")


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1
               else "InsightMatch-غرفة-التحكم.pdf")
    if not SOURCE.exists():
        raise SystemExit(f"المصدر مفقود: {SOURCE}")
    tmp = HERE / "_print.html"
    tmp.write_text(build_document(SOURCE.read_text(encoding="utf-8")),
                   encoding="utf-8")
    subprocess.run([find_chrome(), "--headless", "--disable-gpu", "--no-sandbox",
                    "--no-pdf-header-footer", f"--print-to-pdf={out}",
                    f"file://{tmp.resolve()}"], check=True, capture_output=True)
    tmp.unlink(missing_ok=True)
    size = out.stat().st_size if out.exists() else 0
    # حارس المخرج: ملف صغير جداً = طباعة فاشلة صامتة (صفحة فارغة أو خطأ)
    if size < 50_000:
        raise SystemExit(f"الملف الناتج صغير بشكل مريب ({size} بايت) — طباعة فاشلة")
    print(f"✅ {out} — {size // 1024} KB")


if __name__ == "__main__":
    main()
