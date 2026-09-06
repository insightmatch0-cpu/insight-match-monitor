# -*- coding: utf-8 -*-
"""💾 نقطة حفظ بعد كل دفعة (حادثة 2026-09-06): تشغيلتا المحرك 2 #190 و#191
أُلغيتا (إعادة إطلاق من حارس النبض أثناء التشغيل) بعد ~50 دقيقة وضاعت كل
الدفعات المدفوعة لأن الحفظ كان في نهاية الحلقة. القاعدة: كل عمل مدفوع يصل
القرص لحظة اكتماله؛ الإطلاق التالي يكمل من حيث توقف (المعلّق يُتخطى)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import predict_v2 as P2

SRC = (ROOT / "predict_v2.py").read_text(encoding="utf-8")


class TestBatchCheckpoint(unittest.TestCase):
    def test_flag_exists_and_on(self):
        self.assertTrue(P2.CHECKPOINT_EVERY_BATCH)

    def test_checkpoint_sits_inside_the_batch_loop_before_final_save(self):
        body = SRC[SRC.index("def main("):]
        loop = body.index("results = claude_predict_batch(batch, stats, is_enriched)")
        ckpt = body.index("if CHECKPOINT_EVERY_BATCH and results:")
        final = body.index('store["meta"] = {', ckpt)
        self.assertLess(loop, ckpt)
        self.assertLess(ckpt, final)
        seg = body[ckpt:ckpt + 160]
        self.assertIn("save_json(PREDICTIONS_FILE, store)", seg)

    def test_resume_skips_already_pending(self):
        """بنيوي: المرشحون = ما ليس معلّقاً — فإعادة الإطلاق تكمل لا تكرر."""
        self.assertIn('upcoming = [m for m in fetched if m["fid"] not in store["pending"]]', SRC)


if __name__ == "__main__":
    unittest.main()
