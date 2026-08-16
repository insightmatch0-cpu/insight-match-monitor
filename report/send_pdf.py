#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""📤 إرسال ملف PDF إلى تيليجرام لكل أجهزة البث (طلب المالك 2026-08-16).

يعيد استخدام طبقة الحارس المشتركة بدل نسخ منطقها: `broadcast_ids` لقائمة
المستقبِلين (المالك أولاً وبلا تكرار)، `mask_id` لتقنيع المعرّفات في كل
سطر يُطبع (المستودع وسجلاته عامة — قاعدة الأسرار 3)، و`_delivery_reason`
لترجمة رفض تيليجرام إلى سبب مقروء.

**قاعدة الفشل الصاخب (درس 14 أغسطس):** فشل جهاز ثانوي يُطبع ولا يوقف
التشغيلة؛ أما فشل جهاز المالك نفسه فلا سبيل للتبليغ عنه عبر تيليجرام،
فتخرج التشغيلة **حمراء** في صفحة Actions. الصمت أسوأ من الخطأ الظاهر.
"""

import os
import sys
from pathlib import Path

import requests

import api_guard

TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
BROADCAST = os.environ.get("TELEGRAM_BROADCAST_IDS", "")
TIMEOUT = 120   # رفع ملف أبطأ من رسالة نصية — مهلة أوسع


def send_document(path: Path, caption: str = "") -> dict:
    """يرفع الملف لكل مستقبِل ويقرأ إيصال كل واحد على حدة."""
    result = {"sent": [], "failed": [], "total": 0, "delivered": 0}
    if not (TOKEN and CHAT_ID):
        print("مفاتيح تيليجرام ناقصة — تخطي الإرسال.")
        return result
    ids = api_guard.broadcast_ids(CHAT_ID, BROADCAST)
    owner = ids[0] if ids else ""
    result["total"] = len(ids)
    blob = path.read_bytes()
    for cid in ids:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendDocument",
                data={"chat_id": cid, "caption": caption},
                files={"document": (path.name, blob, "application/pdf")},
                timeout=TIMEOUT,
            )
            payload = {}
            try:
                payload = r.json()
            except Exception:
                pass
            ok = bool(payload.get("ok", r.status_code == 200))
        except Exception as e:
            ok, payload = False, {"description": api_guard.redact(str(e))}
        if ok:
            result["delivered"] += 1
            result["sent"].append(api_guard.mask_id(cid))
            print(f"📄 وصل الملف إلى {api_guard.mask_id(cid)}")
            continue
        reason, action = api_guard._delivery_reason(payload)
        result["failed"].append({"id": api_guard.mask_id(cid), "reason": reason,
                                 "action": action, "owner": cid == owner})
        print(f"⚠️ لم يصل الملف إلى {api_guard.mask_id(cid)} — {reason}")
    return result


def main() -> None:
    path = Path(sys.argv[1] if len(sys.argv) > 1
                else "InsightMatch-غرفة-التحكم.pdf")
    caption = sys.argv[2] if len(sys.argv) > 2 else "🛰 غرفة تحكم InsightMatch"
    if not path.exists():
        raise SystemExit(f"الملف غير موجود: {path}")
    res = send_document(path, caption)
    print(f"📡 التسليم: {res['delivered']} من {res['total']}")
    # فشل جهاز المالك = لا قناة تبليغ → التشغيلة حمراء عمداً
    if any(f.get("owner") for f in res["failed"]):
        raise SystemExit("⛔ لم يصل الملف إلى جهاز المالك — تشغيلة فاشلة عمداً")


if __name__ == "__main__":
    main()
