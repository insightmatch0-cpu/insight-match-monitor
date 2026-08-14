"""حارس تغطية البث (ثغرة 2026-08-15).

الميزة كانت مبنية ومدموجة، لكن `predict.yml` و`scan.yml` لم يمررا السرّ،
و`predict.py` و`scan.py` كانا يرسلان إلى المالك وحده — فلو أضاف المالك
الرقم لما وصل الجهاز الثاني نشرة المحرك 1 ولا المسح الحي، **بلا أي خطأ
ظاهر**. عطل صامت من نفس فئة انقطاع 14 أغسطس.

القاعدة المحروسة هنا: كل مسار يرسل تيليجرام يجب أن يمر بالبث المشترك،
وكل workflow يشغّل مرسِلاً يجب أن يمرر السرّ.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# كل سكربت يرسل تيليجرام + الـ workflow الذي يشغّله
SENDERS = {
    "monitor.py": "monitor.yml",
    "predict.py": "predict.yml",
    "predict_v2.py": "predict_v2.yml",
    "scan.py": "scan.yml",
}


class TestBroadcastCoverage(unittest.TestCase):

    def test_senders_use_shared_broadcast(self):
        """لا سكربت يرسل إلى chat_id واحد مباشرة — الكل عبر البث المشترك."""
        for script in SENDERS:
            src = (ROOT / script).read_text(encoding="utf-8")
            with self.subTest(script=script):
                self.assertIn(
                    "send_telegram_multi", src,
                    f"{script} لا يستخدم البث المشترك — الجهاز الثاني لن يستقبل منه",
                )

    def test_no_direct_sendmessage_post(self):
        """ممنوع نداء sendMessage مباشرةً بـ chat_id مفرد خارج api_guard."""
        pattern = re.compile(r'"chat_id"\s*:\s*TELEGRAM_CHAT_ID')
        for script in SENDERS:
            src = (ROOT / script).read_text(encoding="utf-8")
            with self.subTest(script=script):
                self.assertIsNone(
                    pattern.search(src),
                    f"{script} يرسل إلى المالك وحده — يتجاوز قائمة البث",
                )

    def test_workflows_pass_broadcast_secret(self):
        """كل workflow يشغّل مرسِلاً يمرر TELEGRAM_BROADCAST_IDS."""
        for script, wf in SENDERS.items():
            text = (ROOT / ".github" / "workflows" / wf).read_text(encoding="utf-8")
            with self.subTest(workflow=wf):
                self.assertIn(
                    "TELEGRAM_BROADCAST_IDS", text,
                    f"{wf} لا يمرر سرّ البث — {script} سيرسل للمالك وحده صامتاً",
                )

    def test_absent_secret_keeps_owner_only(self):
        """بلا السرّ: مستقبِل واحد فقط — سلوك ما قبل الميزة حرفياً."""
        import api_guard
        self.assertEqual(api_guard.broadcast_ids("111", ""), ["111"])
        self.assertEqual(api_guard.broadcast_ids("111", "   "), ["111"])

    def test_extra_ids_are_added_without_duplicating_owner(self):
        import api_guard
        self.assertEqual(api_guard.broadcast_ids("111", "222"), ["111", "222"])
        self.assertEqual(api_guard.broadcast_ids("111", "111,222"), ["111", "222"])


if __name__ == "__main__":
    unittest.main()
