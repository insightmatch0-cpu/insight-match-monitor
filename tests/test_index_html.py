# -*- coding: utf-8 -*-
"""اختبارات اللوحة (index.html) — صحة الجافاسكربت + حراسة إصلاحات الواجهة.

وميض "قيد الإنشاء" (بلاغ المالك 2026-07-18) عاد سببه أن الرسم سبق وصول
البيانات — هذه الاختبارات تضمن بقاء طبقات الحماية الثلاث للأبد:
الذاكرة المحلية، وحالة التحميل الثلاثية، وشرط الـ 404 الحقيقي.
"""

import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "index.html").read_text(encoding="utf-8")
SCRIPT = re.search(r"<script>([\s\S]*?)</script>", HTML).group(1)


class TestJavaScriptSyntax(unittest.TestCase):
    def test_inline_script_parses(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node غير متوفر")
        proof = "new Function(require('fs').readFileSync(0,'utf8'));console.log('OK')"
        r = subprocess.run([node, "-e", proof], input=SCRIPT.encode(),
                           capture_output=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr.decode()[:500])


class TestFlashFix(unittest.TestCase):
    """لوحة "قيد الإنشاء" تظهر فقط عند 404 حقيقي — لا وميض أثناء التحميل."""

    def test_local_cache_layers_exist(self):
        self.assertIn("im-cache-v1", SCRIPT)
        self.assertIn("im-cache-v2", SCRIPT)

    def test_three_state_loader(self):
        self.assertIn('v2state = "loading"', SCRIPT)
        self.assertIn('v2state = "missing"', SCRIPT)
        self.assertIn('v2state = "ok"', SCRIPT)

    def test_construction_gated_on_missing(self):
        gate = re.search(r'v2state === "missing"[\s\S]{0,120}renderConstruction', SCRIPT)
        self.assertIsNotNone(gate, "لوحة قيد الإنشاء يجب أن تكون خلف شرط missing")

    def test_404_check_before_missing(self):
        self.assertIn('"404"', SCRIPT)


class TestLiveCards(unittest.TestCase):
    """بطاقات LIVE تعرض توقع المحركين وتُخفي المباراة من قائمة الـ 24 ساعة."""

    def test_engine_chips_rendered(self):
        self.assertIn("livePredsRow", SCRIPT)
        self.assertIn("pred_v1", SCRIPT)
        self.assertIn("pred_v2", SCRIPT)

    def test_live_matches_filtered_from_upcoming(self):
        self.assertIn("liveSet[p.fid]", SCRIPT)

    def test_kicked_off_badge(self):
        self.assertIn("ko-live", SCRIPT)
        self.assertIn("liveBadge", SCRIPT)


class TestI18n(unittest.TestCase):
    """كل مفتاح ترجمة عربي له نظير إنجليزي — لا نص مكسور عند تبديل اللغة."""

    def test_ar_en_keys_match(self):
        m = re.search(r"ar:\s*\{([\s\S]*?)\n\s*\},\s*\n\s*en:\s*\{([\s\S]*?)\n\s*\}\s*\n\};",
                      SCRIPT)
        self.assertIsNotNone(m, "تعذر إيجاد قاموس الترجمة")
        keys = lambda s: set(re.findall(r"^\s*([A-Za-z]\w*)\s*:", s, re.M))
        ar, en = keys(m.group(1)), keys(m.group(2))
        self.assertEqual(ar, en, f"مفاتيح غير متطابقة: {ar ^ en}")


class TestGoldPicks(unittest.TestCase):
    """قسم الاختيارات الذهبية (ثقة 70%+) + تمييز شريحة الـ 70%+ في سجل الدقة
    (طلب المالك 2026-08-01 — رؤية أفضل لطبقة القناعة العالية للمحرك 2)."""

    def test_gold_section_exists(self):
        self.assertIn('id="gold-sec"', HTML)
        self.assertIn('id="gold-list"', HTML)
        self.assertIn('id="gold-count"', HTML)

    def test_gold_v2_only_and_70_threshold(self):
        gate = re.search(r'function renderGold[\s\S]{0,200}engine === "v2"', SCRIPT)
        self.assertIsNotNone(gate, "القسم الذهبي يجب أن يقتصر على تبويب المحرك 2")
        self.assertIn(">= 70", SCRIPT)

    def test_gold_hidden_in_construction_and_loading(self):
        # حالتا "قيد الإنشاء" و"التحميل" لا تمران عبر renderUpcoming — يجب إخفاء القسم فيهما
        for fn in ("renderConstruction", "renderLoadingState"):
            body = re.search(fn + r"\(\)\{([\s\S]*?)\n\}", SCRIPT)
            self.assertIsNotNone(body, fn)
            self.assertIn("gold-sec", body.group(1), f"{fn} يجب أن يخفي القسم الذهبي")

    def test_70_bucket_gold_highlight(self):
        self.assertIn('"70+"?" gold"', SCRIPT.replace("'", '"'))

    def test_shared_card_builder(self):
        # بطاقة واحدة مشتركة بين القائمتين — لا نسختين تتباعدان مع الزمن
        self.assertIn("function predCard", SCRIPT)
        self.assertTrue(SCRIPT.count("predCard(") >= 3)


class TestMarketChip(unittest.TestCase):
    """⚖️ شريحة "المحرك ضد السوق" (طلب المالك 2026-08-01): تظهر فقط عند توفر
    احتمالات السوق، وتتوهج حين يخالف المحرك مرشح السوق."""

    def test_mkt_row_exists_and_used_in_card(self):
        self.assertIn("function mktRow", SCRIPT)
        gate = re.search(r"probRow\(p\)\s*\+\s*mktRow\(p\)", SCRIPT)
        self.assertIsNotNone(gate, "البطاقة يجب أن تعرض شريحة السوق بعد الاحتمالات")

    def test_hidden_without_market_data(self):
        body = re.search(r"function mktRow\(p\)\{([\s\S]*?)\n\}", SCRIPT)
        self.assertIsNotNone(body)
        self.assertIn('p.mkt_home == null) return ""', body.group(1))

    def test_disagreement_highlight(self):
        self.assertIn('fav !== p.pick', SCRIPT)
        self.assertIn('"mkt', SCRIPT.replace("'", '"'))
        self.assertIn("mktDiff", SCRIPT)


class TestShadowLabPanel(unittest.TestCase):
    """🔬 مختبر الظل على اللوحة (طلب المالك 2026-08-01): تبويب المحرك 2 فقط،
    يختفي بلا بيانات، ويُرسم ضمن دورة renderAll."""

    def test_section_exists(self):
        self.assertIn('id="shadow-sec"', HTML)
        self.assertIn('id="shadow-list"', HTML)
        self.assertIn('id="shadow-summary"', HTML)

    def test_v2_only_gate(self):
        gate = re.search(r'function renderShadowLab[\s\S]{0,250}engine === "v2"', SCRIPT)
        self.assertIsNotNone(gate, "مختبر الظل يجب أن يقتصر على تبويب المحرك 2")

    def test_hidden_when_empty(self):
        """القسم يختفي حين لا تقارير ولا تجربة نشطة — ويبقى ظاهراً لتجربة
        نشطة بلا تقارير (قاعدة الحوكمة هـ: رؤية يومية إلزامية للتجارب)."""
        body = re.search(r"function renderShadowLab\(\)\{([\s\S]*?)\n\}", SCRIPT)
        self.assertIsNotNone(body)
        self.assertIn('(rows.length || expStrip) ? "" : "none"', body.group(1))
        # وبلا تقارير: الشريط وحده يُعرض في الملخص بدل مسحه
        self.assertIn('innerHTML = expStrip', body.group(1))

    def test_rendered_in_render_all(self):
        body = re.search(r"function renderAll\(\)\{([\s\S]*?)\n\}", SCRIPT)
        self.assertIsNotNone(body)
        self.assertIn("renderShadowLab()", body.group(1))

    def test_claim_breakdown_and_honest_states(self):
        """طلبات المالك 2026-08-01 (الصورة الثالثة): تفصيل البنود ✅/❌ داخل
        البطاقة، ولا "مباشر" زائف لمباراة انتهت منذ أيام."""
        self.assertIn("function labBreakdown", SCRIPT)
        self.assertIn("labNoBreakdown", SCRIPT)          # صدق مع التقارير القديمة
        self.assertIn("4*3600*1000", SCRIPT)             # النبضة الحمراء ≤ 4 ساعات فقط
        self.assertIn("labWaitData", SCRIPT)             # حالة "بانتظار البيانات" الصادقة

    def test_live_fold_and_collapsible_sections(self):
        """قتل التمرير الطويل: أول 12 بطاقة حية ثم زر عرض البقية، وكل قسم
        يُطوى بنقرة عنوانه مع حفظ الحالة."""
        self.assertIn("LIVE_VISIBLE", SCRIPT)
        self.assertIn("toggleLiveMore", SCRIPT)
        self.assertIn('id="live-more"', SCRIPT)
        self.assertIn("initSecToggles", SCRIPT)
        self.assertIn("im-sec-", SCRIPT)

    def test_live_search_bypasses_fold(self):
        """بحث القائمة الحية (طلب المالك 2026-08-01): يبحث في الفريقين والدوري
        عبر القائمة الكاملة، وأثناء البحث لا طي — كل المطابقات تظهر."""
        self.assertIn('id="live-search"', HTML)
        self.assertIn("onLiveSearch", SCRIPT)
        self.assertIn("liveCache", SCRIPT)
        body = re.search(r"function renderLive\(list\)\{([\s\S]*?)\n\}", SCRIPT)
        self.assertIsNotNone(body)
        self.assertIn("var fold = !q", body.group(1))
        self.assertIn("m.league", body.group(1))

    def test_ops_room(self):
        """🎯 غرفة العمليات (طلب المالك 2026-08-01): عدّاد "لو انتهت الآن" لكل
        محرك، وقائمة تركيز (توقع يخسر/نهاية متقلبة/ذهبية/مخالفة سوق)،
        والانطلاقات القريبة — إشارات لا زينة."""
        self.assertIn('id="ops-sec"', HTML)
        self.assertIn("function renderOpsRoom", SCRIPT)
        self.assertIn("function currentOutcome", SCRIPT)
        self.assertIn("opsLosing", SCRIPT)          # التوقع في خطر
        self.assertIn("opsVolatile", SCRIPT)        # دقيقة 75+ وفارق هدف
        self.assertIn(">= 75", SCRIPT)
        self.assertIn("3*3600*1000", SCRIPT)        # نافذة الانطلاقات القريبة
        body = re.search(r"function renderAll\(\)\{([\s\S]*?)\n\}", SCRIPT)
        self.assertIn("renderOpsRoom()", body.group(1))

    def test_collapsed_section_cannot_look_broken(self):
        """درس 2026-08-02: قسم مطوي بدا للمالك قسماً معطلاً — الآن سهم كهرماني
        + تلميح نصي واضح، وأي خطأ برمجي يظهر بلافتة حمراء لا بصمت."""
        self.assertIn('data-hint', SCRIPT)
        self.assertIn("collapsedHint", SCRIPT)
        self.assertIn("attr(data-hint)", HTML)
        self.assertIn('id="err-banner"', HTML)
        self.assertIn("window.onerror", SCRIPT)
        self.assertIn("jsError", SCRIPT)

    def test_team_names_follow_language(self):
        """طلب المالك 2026-08-02: عند اختيار EN تظهر أسماء الأندية بالإنجليزية
        في كل الأقسام — القائمة الحية، النتائج، مختبر الظل، غرفة العمليات،
        الرادار — عبر مبدّل واحد (tn) لا نسخاً متفرقة."""
        self.assertIn("function tn(o, side)", SCRIPT)
        body = re.search(r"function tn\(o, side\)\{([\s\S]*?)\n\}", SCRIPT)
        self.assertIsNotNone(body)
        self.assertIn('lang === "en"', body.group(1))
        self.assertIn('side+"_en"', body.group(1))
        # مطبق في كل مواضع العرض (بطاقات، صفوف، رادار، عمليات، مختبر...)
        self.assertGreaterEqual(SCRIPT.count("tn("), 18)
        # البحث يشمل الاسمين معاً
        self.assertIn('(m.home_en||"")', SCRIPT)
        self.assertIn('(r.home_en||"")', SCRIPT)

    def test_results_show_match_dates(self):
        """طلب المالك 2026-08-02: فاصل تاريخ لكل يوم في النتائج المُقيَّمة —
        يوضح متى لُعبت المباراة وأن القائمة تتحدث كل صباح."""
        body = re.search(r"function paintResults\(\)\{([\s\S]*?)\n\}", SCRIPT)
        self.assertIsNotNone(body)
        self.assertIn("res-day", body.group(1))
        self.assertIn("r.date", body.group(1))
        self.assertIn(".res-day", HTML)   # التنسيق موجود

    def test_why_line_on_results(self):
        """سطر "لماذا" (طلب المالك 2026-08-01): صف النتيجة المُقيَّمة يحمل
        قراءة المحرك قبل المباراة — 💭 مؤشر، نقرة تفتح النص، ولا شيء يظهر
        لإدخالات بلا سبب (المحرك 1 والسجل القديم)."""
        self.assertIn("function toggleWhy", SCRIPT)
        self.assertIn("res-why", SCRIPT)
        self.assertIn("whyLabel", SCRIPT)
        body = re.search(r"function paintResults\(\)\{([\s\S]*?)\n\}", SCRIPT)
        self.assertIsNotNone(body)
        self.assertIn("r.reason", body.group(1))
        self.assertIn("has-why", body.group(1))
        # بلا سبب: لا مؤشر ولا صندوق — الشرط ثلاثي في كلا الموضعين
        self.assertIn('why ? ', body.group(1))

    def test_v2_visual_upgrade(self):
        """v2 (طلب المالك 2026-08-01): شريط النسبة، عدّاد ✓/✗، النبضة الحمراء
        للمباريات الجارية، والتقرير الأصلي القابل للفتح."""
        self.assertIn("lab-bar", SCRIPT)                    # شريط أخضر/أحمر
        self.assertIn("lab-ticks", SCRIPT)                  # عدّاد ✓/✗
        self.assertIn("labpulse", HTML)                     # النبضة الحمراء
        self.assertIn("labWaitGrade", SCRIPT)               # حالة "جارية"
        self.assertIn('class="lab-report"', SCRIPT)         # التقرير الأصلي
        self.assertIn("lab.waiting", SCRIPT)                # المنتظرة تُعرض
        self.assertIn("labExplain", SCRIPT)                 # سطر الشرح الذاتي


class TestRadarTab(unittest.TestCase):
    """🛰 تبويب الرادار (طلب المالك 2026-08-01): شاشة إنذار مبكر مستقلة
    لعرض PC/TV — تبويب ثالث، شبكة خطر مرتبة، ولا ادعاء دقة بلا قياس."""

    def test_third_tab_and_section_exist(self):
        self.assertIn('id="tab-radar"', HTML)
        self.assertIn('id="radar-sec"', HTML)
        self.assertIn('id="radar-grid"', HTML)

    def test_proactive_funnel_replaces_count_tiles(self):
        """قمع الاستباق (طلب المالك 2026-08-02): بدل عدادات آمن/إنذار/خطر
        الوصفية — مراحل متضيقة نحو التنبيه + سطر "الأقرب للتنبيه" مع عدّ
        تنازلي لأهلية د75. العدادات القديمة "متأخرة" — القمع يستبق."""
        self.assertIn('id="radar-funnel"', HTML)
        self.assertIn('id="radar-next"', HTML)
        self.assertNotIn('id="radar-tiles"', HTML)   # العدادات القديمة أُزيلت
        for key in ("fTracked", "fBrewing", "fReady", "fSent",
                    "radarNextUp", "radarEligIn"):
            self.assertIn(key, SCRIPT)
        body = re.search(r"function renderRadar\(live, acc\)\{([\s\S]*?)\n\}", SCRIPT)
        self.assertIn("funnel.sent", body.group(1))
        self.assertIn("75 - liveMin(next.m", body.group(1))   # عدّ تنازلي بدقيقة مُقدَّمة

    def test_render_functions_exist(self):
        for fn in ("function renderRadar", "function radarCard",
                   "function setRadarMode", "function sparkRow"):
            self.assertIn(fn, SCRIPT)

    def test_radar_mode_in_render_all(self):
        body = re.search(r"function renderAll\(\)\{([\s\S]*?)\n\}", SCRIPT)
        self.assertIsNotNone(body)
        self.assertIn('engine === "radar"', body.group(1))
        self.assertIn("renderRadar(", body.group(1))

    def test_radar_mode_hides_other_sections(self):
        """شاشة نظيفة واحدة: وضع الرادار يخفي المباشر والأخبار وجسم المحرك."""
        body = re.search(r"function setRadarMode\(on\)\{([\s\S]*?)\n\}", SCRIPT)
        self.assertIsNotNone(body)
        for sec in ("live-sec", "news-sec", "engine-body", "radar-mode"):
            self.assertIn(sec, body.group(1))

    def test_sorted_by_danger(self):
        body = re.search(r"function renderRadar\(live, acc\)\{([\s\S]*?)\n\}", SCRIPT)
        self.assertIsNotNone(body)
        self.assertIn("b.radar.score", body.group(1))

    def test_accuracy_line_hidden_without_measurement(self):
        """لا نعرض "صدق الرادار" قبل وجود إنذارات مُقيَّمة فعلاً — لا تبجح فارغ."""
        body = re.search(r"function renderRadar\(live, acc\)\{([\s\S]*?)\n\}", SCRIPT)
        self.assertIn('display = "none"', body.group(1))

    def test_tv_wide_layout(self):
        """التبويب مصمم لشاشة PC/TV: عرض ممتد + شبكة 3 أعمدة على الشاشات الكبيرة."""
        self.assertIn("main.radar-mode{max-width", HTML)
        self.assertIn("@media(min-width:1380px){.radar-grid{grid-template-columns:1fr 1fr 1fr}}", HTML)

    def test_fast_lane_polling(self):
        """⚡ المسار السريع: اللوحة تقرأ فرع radar-live مباشرة كل 60 ثانية في
        وضع الرادار فقط، وتتقدم النسخة الحية على data.json حين تكون طازجة."""
        self.assertIn("RADAR_LIVE_URL", SCRIPT)
        self.assertIn("radar-live/radar-live.json", SCRIPT)
        self.assertIn("function pollRadarLive", SCRIPT)
        self.assertIn("setInterval(pollRadarLive, 60000)", SCRIPT)
        poll = re.search(r"function pollRadarLive\(\)\{([\s\S]*?)\n\}", SCRIPT)
        self.assertIsNotNone(poll)
        # 2026-08-02: الجلب صار دائماً — التغذية السريعة تخدم كل الشاشات
        self.assertIn("RADAR_LIVE_URL", poll.group(1))
        body = re.search(r"function renderRadar\(live, acc\)\{([\s\S]*?)\n\}", SCRIPT)
        self.assertIn("radarLive.matches", body.group(1))
        self.assertIn("15*60*1000", body.group(1))           # طزاجة 15 دقيقة أو تجاهل
        self.assertIn('id="radar-live-at"', HTML)

    def test_fast_scores_overlay_everywhere(self):
        """بلاغ المالك 2026-08-02 (0-3 والواقع 0-2 معروضة): النتيجة السريعة
        (~90 ثانية) تُطبق في كل مواضع العرض عبر fastOf، والجلب دائم لا في
        وضع الرادار فقط، وتُهمل النسخة الأقدم من 15 دقيقة."""
        self.assertIn("function fastOf", SCRIPT)
        body = re.search(r"function fastOf\(m\)\{([\s\S]*?)\n\}", SCRIPT)
        self.assertIsNotNone(body)
        self.assertIn("radarLive.scores", body.group(1))
        self.assertIn("15*60*1000", body.group(1))
        # الجلب لم يعد مقيداً بتبويب الرادار
        poll = re.search(r"function pollRadarLive\(\)\{([\s\S]*?)\n\}", SCRIPT)
        self.assertNotIn('engine !== "radar"', poll.group(1))
        # مواضع التطبيق: البطاقة الحية، غرفة العمليات (نتيجتان)، صف العمليات، الرادار
        self.assertGreaterEqual(SCRIPT.count("fastOf("), 7)
        # liveMin نفسها تقرأ التغذية السريعة
        lm = re.search(r"function liveMin\(m, fallbackIso\)\{([\s\S]*?)\n\}", SCRIPT)
        self.assertIn("fastOf(m)", lm.group(1))

    def test_self_update_and_stale_badge(self):
        """درس 2026-08-02 (الإصلاح وصل المستودع لا الشاشة): التبويب المفتوح
        يعيد تحميل نفسه عند نشر بناء أحدث، والشاشة تعترف بتقادم بياناتها."""
        self.assertIn("var IM_BUILD = ", SCRIPT)
        self.assertIn("function checkSelfUpdate", SCRIPT)
        self.assertIn("setInterval(checkSelfUpdate, 10*60*1000)", SCRIPT)
        body = re.search(r"function checkSelfUpdate\(\)\{([\s\S]*?)\n\}", SCRIPT)
        self.assertIsNotNone(body)
        self.assertIn("location.reload()", body.group(1))
        self.assertIn("> IM_BUILD", body.group(1))   # الأحدث فقط — لا حلقة تحميل
        # شارة التقادم: بيانات >20 دقيقة مع مباريات حية = تحذير كهرماني مرئي
        self.assertIn("staleWarn", SCRIPT)
        self.assertIn('classList.toggle("stale"', SCRIPT)
        self.assertIn("ageMin > 20", SCRIPT)

    def test_update_line(self):
        """خط التحديث (طلب المالك 2026-08-02): شريط زمني تحت توقعات الـ 24
        ساعة — آخر تحديث، القادم مع عدّ تنازلي، مؤشر "الآن"، إنذار تأخر،
        ويختفي بلا بيانات. النوافذ مصفوفة لتستوعب تشغيلاً ثانياً مستقبلاً."""
        self.assertIn('id="upd-line"', HTML)
        self.assertIn("function renderUpdLine", SCRIPT)
        self.assertIn("PRED_SCHED_UTC", SCRIPT)
        body = re.search(r"function renderUpdLine\(\)\{([\s\S]*?)\n\}", SCRIPT)
        self.assertIsNotNone(body)
        self.assertIn('display = "none"', body.group(1))     # بلا بيانات → مخفي
        self.assertIn("updLate", body.group(1))              # إنذار التأخر
        self.assertIn("upd-track", body.group(1))            # الشريط الزمني
        ra = re.search(r"function renderAll\(\)\{([\s\S]*?)\n\}", SCRIPT)
        self.assertIn("renderUpdLine()", ra.group(1))

    def test_live_minute_extrapolated(self):
        """بلاغ المالك 2026-08-02 (اللوحة 65 والواقع 84): الدقيقة المعروضة =
        المخزنة + ما مضى منذ رصدها، بسقوف واقعية لكل شوط، في كل مواضع العرض."""
        self.assertIn("function liveMin", SCRIPT)
        body = re.search(r"function liveMin\(m, fallbackIso\)\{([\s\S]*?)\n\}", SCRIPT)
        self.assertIsNotNone(body)
        for cap in ("50", "97", "125"):
            self.assertIn(cap, body.group(1))
        # مواضع الاستخدام: البطاقة الحية، غرفة العمليات، بطاقة الرادار، عدّ القمع
        self.assertGreaterEqual(SCRIPT.count("liveMin("), 6)
        # نسخة الرادار الحية تجدد لحظة الرصد عند الدمج
        self.assertIn("tgt.seen = lm.seen || radarLive.updated", SCRIPT)

    def test_drama_alerts_scoreboard(self):
        """🚨 لوحة عقل S3: تنبيهات الدراما المُقيَّمة تظهر في لوحة الدقة.
        (انتقل المنطق إلى radarAccPanel — لوحة S3 الكاملة 2026-08-09 —
        نفس الضمانة، وrenderRadar يستدعيها.)"""
        self.assertIn("radarDrama", SCRIPT)
        body = re.search(r"function radarAccPanel\(acc\)\{([\s\S]*?)\n\}", SCRIPT)
        self.assertIn("acc.alerts", body.group(1))
        render = re.search(r"function renderRadar\(live, acc\)\{([\s\S]*?)\n\}", SCRIPT)
        self.assertIn("radarAccPanel(", render.group(1))


if __name__ == "__main__":
    unittest.main()


class TestContraSection(unittest.TestCase):
    """⚡ قسم «ضد السوق» (طلب المالك 2026-08-21): قائمة مستقلة تحت الذهبية،
    ولا يجوز أبداً أن تُدمج اختيارات الخلاف في شريحة الـ70%+ نفسها."""

    def test_section_and_renderer_exist(self):
        self.assertIn('id="contra-sec"', HTML)
        self.assertIn("function renderContra(", HTML)
        self.assertIn("function mktFav(", HTML)

    def test_contra_never_widens_the_gold_bucket(self):
        # الذهبية تبقى مرشَّحة بالثقة وحدها — لا ذكر للسوق داخل renderGold
        gold = HTML.split("function renderGold(")[1].split("function ")[0]
        self.assertIn(">= 70", gold)
        self.assertNotIn("mkt", gold)

    def test_contra_filters_on_disagreement_only(self):
        contra = HTML.split("function renderContra(")[1].split("function ")[0]
        self.assertIn("f !== p.pick", contra)


if __name__ == "__main__":
    unittest.main()
