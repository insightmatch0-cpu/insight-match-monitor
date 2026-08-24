# -*- coding: utf-8 -*-
"""
بوت مراقبة المباريات الحية — النسخة المجانية
--------------------------------------------
يسحب المباريات الجارية الآن من API-Football، يستبعد (الودية / الأفريقية /
الهند / باكستان / بنغلادش)، وعند أي حدث مهم (بداية مباراة، هدف، نهاية مباراة)
يحلل الموقف عبر Claude ويرسل تنبيهاً على تيليجرام.

لا تكتب أي مفتاح داخل هذا الملف — كل المفاتيح توضع في GitHub Secrets.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import api_guard
from api_guard import ApiRefused        # noqa: F401 — يُعاد تصديره للاختبارات

# 🔬 مجمّع xG — الاستيراد محروس عمداً: الطبقة الحية تجربة ظل، ويجب ألا يمنع
# غيابُها أو عطبُها تشغيلةَ المراقبة من العمل ولو لثانية (صفر تأثير).
try:
    import sportmonks_shadow
except Exception:                        # pragma: no cover - دفاعي
    sportmonks_shadow = None

# ================== المفاتيح (تُقرأ من GitHub Secrets) ==================
API_FOOTBALL_KEY  = os.environ.get("API_FOOTBALL_KEY", "").strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
# بث اختياري لأجهزة/أشخاص إضافيين (معرّفات مفصولة بفواصل). غائب أو فارغ =
# السلوك القديم حرفياً: المالك وحده. هذا هو مفتاح التراجع نفسه.
TELEGRAM_BROADCAST_IDS = os.environ.get("TELEGRAM_BROADCAST_IDS", "").strip()

# ================== الإعدادات ==================
STATE_FILE = Path("state.json")          # ذاكرة البوت بين التشغيلات
MAX_ANALYSES_PER_RUN = 20                # حد أقصى لتحليلات Claude في التشغيلة الواحدة
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

# ---- المحرك 2 المباشر (للدوريات الكبرى فقط) ----
# يسحب إحصائيات وأحداث وتشكيلات المباراة الحية (3 نداءات API لكل مباراة)
# ويحلل عبر النموذج الأقوى مع تفكير عميق ممتد قبل الإجابة، بتوقع كل
# السيناريوهات: هدف قادم، ركنيات، كرات ثابتة، اللاعب الأخطر، بطاقات.
# مقيد بعدد مباريات لكل تشغيلة حفاظاً على الرصيد.
CLAUDE_MODEL_V2 = "claude-fable-5"
MAX_LIVE_ENRICHED_PER_RUN = 12   # رصيد API-Football مدفوع مسبقاً — نرفع السقف بسخاء
LIVE_THINKING_BUDGET = 2048   # ميزانية التفكير العميق (توكنز) لتحليل المحرك 2 المباشر

# ---- نبض المحرك 2 (لمباريات قائمة التركيز فقط) ----
# بين الأحداث (لا هدف ولا بداية/نهاية) يفحص المحرك 2 المباراة كل تشغيلة:
# إن تشكل سيناريو خطر جديد (هدف قادم، كلا الفريقين يسجلان، موجة ركنيات،
# لاعب يهدد، بطاقة محتملة، انقلاب سيطرة) يرسل تنبيهاً — وإلا يبقى صامتاً.
MAX_PULSE_PER_RUN = 12           # حد نداءات Claude للنبض في التشغيلة الواحدة
PULSE_STATUSES = {"1H", "2H", "ET"}   # لا نبض في الاستراحة/الركلات الترجيحية

# ---- الرصد السريع (قائمة التركيز فقط): فحص كل ~90 ثانية بدل 10 دقائق ----
# بعد الجولة العادية تبقى التشغيلة مستيقظة وتفحص مباريات القائمة الحية كل
# 90 ثانية (طلب المالك: تنبيه خلال دقيقة إلى دقيقتين). نداء Claude يحدث فقط
# عند تحرك حقيقي في الأرقام (ركنية، تسديدة على المرمى، بطاقة...) — البصمة أدناه.
FOCUS_SWEEP_SECONDS = 90
# 6 دقائق (كانت 8 قبل إصلاح 2026-08-06): ميزانية 8 دقائق لا تتسع أصلاً داخل سقف
# التشغيلة أدناه، فكانت وعداً لا يتحقق. زمن الاستجابة لا يتأثر — تحدده فترة
# الفحص (90 ثانية) لا طول الحلقة؛ الفارق أربع كنسات بدل خمس.
FOCUS_LOOP_BUDGET_SECONDS = 6 * 60   # ثم نسلّم للتشغيلة التالية (كل 10 دقائق)

# ---- سقف زمن التشغيلة كاملة (إصلاح ازدحام 2026-08-06) ----
# العطل: في أمسية مزدحمة تستغرق الجولة العادية 6-7 دقائق، ثم يبدأ الرصد السريع
# ميزانيته الكاملة (8 دقائق) من تلك اللحظة — فتتجاوز التشغيلة الـ10 دقائق، ويتكوّن
# طابور، وكل تشغيلة جديدة تقتل السابقة قبل خطوة الحفظ. النتيجة: 7 ساعات و23 دقيقة
# بلا أي كتابة بيانات (2026-08-06 من 15:40 إلى 23:10 UTC).
# الإصلاح: تثبيت مهلة الرصد السريع على *بداية التشغيلة* لا على نهاية الجولة العادية،
# فلا يمكن للتشغيلة أن تتجاوز خانتها الزمنية مهما طالت الجولة العادية.
RUN_START = time.monotonic()          # لحظة بدء monitor.py
RUN_WALL_CLOCK_BUDGET_SECONDS = 7 * 60   # يترك ~3 دقائق للوحة والحارس والحفظ


def fast_watch_deadline() -> float:
    """المهلة المطلقة للمسارين السريعين — الأصغر بين:
    (أ) ميزانية الرصد السريع المعتادة من الآن، و(ب) سقف زمن التشغيلة من بدايتها.
    في ليلة هادئة تفوز (أ) فلا يتغير شيء؛ في أمسية مزدحمة تفوز (ب) فتنتهي
    التشغيلة داخل خانتها وتصل إلى خطوة الحفظ."""
    return min(
        time.monotonic() + FOCUS_LOOP_BUDGET_SECONDS,
        RUN_START + RUN_WALL_CLOCK_BUDGET_SECONDS,
    )
SIG_STATS = {
    "Corner Kicks", "Shots on Goal", "Total Shots",
    "Yellow Cards", "Red Cards", "Goalkeeper Saves",
}
SIG_THRESHOLDS = {
    "Corner Kicks": 2, "Shots on Goal": 2, "Total Shots": 3,
    "Yellow Cards": 1, "Red Cards": 1, "Goalkeeper Saves": 2,
}

# ---- تقرير ما قبل المباراة (قائمة التركيز فقط) ----
# قبل ~45 دقيقة من الانطلاق يرسل المحرك 2 تقرير سيناريوهات شاملاً لكل مباراة
# تركيز (طلب المالك 2026-07-15). يمكن توسيع النافذة مؤقتاً عبر متغير البيئة
# PREMATCH_WINDOW (زر التشغيل اليدوي في monitor.yml).
PREMATCH_REPORT_MINUTES = int(os.environ.get("PREMATCH_WINDOW", "").strip() or 45)

# REC-006 (قرار المالك 2026-08-08): تقرير ما قبل المباراة كان يكتب نسبه بلا أي
# تغذية راجعة عن أدائه (مبالغة ~17 نقطة في كل مستوى نسب، وعمى عن معدلات الأساس
# — BTTS المعلن 86% مقابل واقع 35%). صار السياق يُحقن فيه كشف حساب التقارير
# السابقة (من scenarios_v2.json) ومعدلات الأساس الحقيقية (من predictions_v2.json)
# — كسياق لا كأمر، وبصفر نداءات إضافية. False = تراجع فوري لسياق ما قبل التوصية.
PREMATCH_CALIBRATION_CTX = True
BASE_RATE_MIN_LEAGUE = 15   # عينة الدوري الواحد الدنيا قبل عرض معدلاته الخاصة

# ---- تقارير الظل (توجيه المالك 2026-07-18): تعلم عميق يومي بلا إزعاج ----
# المحرك 2 يكتب تقرير ما قبل المباراة لمباريات الدوريات الكبرى تلقائياً حتى
# لو لم تكن في قائمة التركيز، ويحفظه في scenarios_v2.json للتقييم الصباحي
# واستخلاص الدروس — دون إرسال أي رسالة تيليجرام (تيليجرام لقائمة التركيز فقط).
SHADOW_REPORTS_PER_DAY = 6   # سقف يومي — تكلفة Fable تبقى تحت السيطرة
# أولوية الذهب في الظل (أمر المالك 2026-08-09): يوم 8 أغسطس استهلكت المباريات
# الأبكر انطلاقاً حصص الظل الست كلها، فبقيت مباراتا 70%+ (أهم ما يراقبه
# المالك) بلا تقرير أصلاً. العلاج: (1) مباريات الثقة ≥70 تتقدم أي مباراة أخرى
# في الاختيار، و(2) إن نفدت الحصة اليومية تحصل على حصة إضافية خاصة بها —
# نادرة بالبناء وسقفها الخاص يحمي التكلفة. التعطيل الفوري: False.
SHADOW_GOLD_PRIORITY = True
GOLD_SHADOW_MIN_CONF = 70    # عتبة "الذهب" — نفس خانة المالك المقدسة
GOLD_SHADOW_EXTRA_PER_DAY = 4  # الحصة الإضافية للذهب بعد نفاد الحصة العادية
# الحجز المسائي (قرار المالك 2026-08-15 بعد حالة شيفيلد×برمنغهام): في سبتات
# التشامبيونشيب المزدحمة تستهلك مباريات الظهيرة الحصص الست كلها قبل أن تفتح
# نافذة مباريات المساء أصلاً — فتُحرم أمسيات إنجلترا والسعودية من تقرير الظل
# بانتظام لا مصادفة. الحل بلا كلفة: السقف يبقى 6، لكن ما قبل الحد المسائي
# لا يستهلك أكثر من (السقف − الحجز) إذا كانت في جدول اليوم مباريات كبرى
# تنطلق بعد الحد ولم تُلتقط. الحجز ديناميكي: لا مباريات مسائية = لا حجز
# ولا حصة مهدورة. الذهب فوق هذه القاعدة (لا يُترك بلا تقرير أبداً).
SHADOW_EVENING_FROM_UTC = 15   # حد "المساء": 15:00 UTC = 18:00 بتوقيت السعودية
SHADOW_EVENING_RESERVE = 2     # أقصى حصص تُحجز للمساء من السقف اليومي
# سقف الليل الأمريكي (اكتشاف صباح 2026-08-16): مباريات أمريكا الليلية تحمل
# تاريخ اليوم الأوروبي التالي بتوقيت UTC (انطلاق 00:15-02:30)، فالتقط الرادار
# ليلة السبت 6 تقارير MLS/أرجنتينية واستُهلكت حصة السبت كلها قبل أن تصحو
# إنجلترا أصلاً — نفس مرض شيفيلد من باب آخر، والحجز المسائي لا يراه لأن
# مباريات المساء لا تكون قد تُنبّئ بها بعد وقت الليل. العلاج بلا رفع سقف:
# الليل (قبل SHADOW_NIGHT_UNTIL_UTC) يُحتسب على حصة اليوم بحد أقصى
# SHADOW_NIGHT_MAX ولا يُلتقط منه أكثر من ذلك (الذهب فوق القاعدة كالعادة) —
# فما زاد ليلاً لا يسد نهار أوروبا، ويوم بلا ليل أمريكي لا يتغير فيه شيء.
SHADOW_NIGHT_UNTIL_UTC = 6     # ما قبل السادسة صباحاً UTC = ليل أمريكا
SHADOW_NIGHT_MAX = 2           # أقصى ما يُلتقط ويُحتسب من مباريات الليل

# ذاكرة تقارير السيناريوهات: كل تقرير ما قبل مباراة يُحفظ هنا، ويقيّمه
# predict_v2.py صباحاً مقابل البيانات النهائية الحقيقية ويستخلص دروساً
SCENARIOS_FILE = Path("scenarios_v2.json")
LESSONS_FILE = Path("lessons_v2.json")   # دروس المحرك 2 — تُحقن في تقرير ما قبل المباراة
REFEREES_FILE = Path("referees.json")    # قاعدة الحكام الذاتية (يبنيها predict_v2)

# ---- إعدادات التغطية العالمية ----
# ANALYZE_ALL = True  → كل مباراة تصلك تنبيهاتها تأتي مع تحليل
# ANALYZE_ALL = False → التحليل للدوريات الكبرى فقط
ANALYZE_ALL = True

# ---- قائمة التركيز (يديرها المستخدم عبر رسائل تيليجرام — watchlist.py) ----
# القائمة غير فارغة → التنبيهات لمباريات القائمة فقط (مع أولوية المحرك 2 المباشر).
# القائمة فارغة    → التنبيهات للدوريات الكبرى فقط (الوضع الافتراضي الهادئ).
# البيانات واللوحة تغطي كل المباريات دائماً — الفلترة على تيليجرام فقط.
WATCHLIST_FILE = Path("watchlist.json")

# ================== الرادار — إنذار مبكر رياضي بحت (طلب المالك 2026-08-01) ==================
# يقرأ أرقام كل مباراة حية عليها توقع للمحرك 2 كل دورة، يحسب درجة خطر شفافة
# (0-100) بلا أي نداء Claude، ويسجل الإنذارات لتقييمها صباحاً — لوحة "الرادار".
RADAR_FILE = Path("radar_log.json")   # سجل الإنذارات للتقييم الصباحي (predict_v2)
RADAR_PREDICTIONS_FILE = Path("predictions_v2.json")
RADAR_STATS_CAP = 20     # سقف نداءات الإحصائيات للرادار في التشغيلة (رصيد API وفير)
RADAR_SNAPS_KEEP = 12    # ~ ساعتا لقطات (كل 10 دقائق) — تكفي لاتجاهات المباراة كلها
RADAR_RED = 65           # عتبة الخطر الأحمر
RADAR_AMBER = 40         # عتبة الإنذار الكهرماني
# كان 400 — المسح الشامل للأسقف (أمر المالك 2026-08-09، والمالك نفسه توقع أن
# الدراما ستواجه نفس المشكلة): هذا القص يطال قائمتي الانتظار **قبل التقييم** —
# تنبيه أو إنذار يُقص هنا يضيع قياسه للأبد (ثقب بيانات لا نافذة عرض).
# القائمتان تنظفان نفسيهما طبيعياً (تقييم كل صباح + إسقاط بعد 4 أيام)
# فلا حاجة لأي قص. 0 = بلا سقف؛ للطوارئ أعد رقماً.
RADAR_MAX_WARNINGS = 0

# المسار السريع للرادار (طلب المالك 2026-08-01 — "الأسرع"): بعد الجولة العادية
# تبقى التشغيلة حية وتحدّث أخطر المباريات كل ~90 ثانية وتنشر النتيجة إلى فرع
# radar-live عبر GitHub API مباشرة (بلا commit محلي وبلا إعادة بناء Pages) —
# اللوحة تقرأ الملف الخام مباشرة فتصل السرعة ~90 ثانية أثناء حياة التشغيلة.
RADAR_FAST_CAP = 8            # أخطر 8 مباريات فقط في المسار السريع (ترشيد نداءات)
RADAR_LIVE_BRANCH = "radar-live"
RADAR_LIVE_PATH = "radar-live.json"
RADAR_SNAP_MIN_GAP_MIN = 8    # لقطة أحدث من نفس نافذة الـ 10 دقائق تستبدل الأخيرة
GH_TOKEN = os.environ.get("GH_TOKEN", "").strip()
GH_REPO = os.environ.get("GITHUB_REPOSITORY", "").strip() or "insightmatch0-cpu/insight-match-monitor"

# 🚨 تنبيهات الدراما (طلب المالك 2026-08-01 — عقل S3 الأول): من الدقيقة 75
# فصاعداً، إذا قالت الأرقام إن المتأخر سيسجل/يتعادل/يقلبها (أو من سيخطف
# فوزاً من تعادل) يصل تنبيه تيليجرام فوراً — بشرط المالك الصريح تُرسل هذه
# التنبيهات لكل مباريات الرادار بمعزل عن بوابة قائمة التركيز (نادرة بالبناء:
# د75+، عتبة إشارة، مرة لكل مباراة، سقف 5/تشغيلة). تُقيَّم صباحاً بلوحتها الخاصة.
RADAR_ALERT_MIN = 75          # شرط المالك: لا تنبيهات دراما قبل الدقيقة 75
# معايرة اليوم الأول (2026-08-02، السجل 1/6 صحيحة — قرار المالك: وضع تجريبي
# موسوم + تشديد): العتبات القديمة كانت تطلق على ضغط أواخر المباريات العادي
RADAR_ALERT_MAX = 85          # لا تنبيه بعد د85 — "هدف قادم" في د90 بلا قيمة
RADAR_ALERT_SIGNAL_MIN = 75   # كان 50 — الآن يلزم فوق موجة الضغط حصارُ حارس أو طرد
RADAR_ALERT_FLIP_MIN = 90     # قلب النتيجة: طرد أو إشارة كاسحة فقط
RADAR_ALERT_DRAW_MIN = 80     # حالة التعادل (الأكثر ضجيجاً 2026-08-02) أشد عتبةً
RADAR_ALERT_DRAW_GAP = 30     # هيمنة أوضح مطلوبة بين الطرفين (كان 20)
RADAR_ALERT_TRIAL = True      # وسم "🧪 تجريبي" على الرسائل حتى يثبت السجل الصباحي
RADAR_ALERT_CAP_PER_RUN = 5   # سقف تنبيهات الدراما في التشغيلة — ضد الضجيج
# قاعدة الإيقاف المسجّلة مسبقاً — REC-005 (قرار المالك 2026-08-08): التقييم
# الصباحي في predict_v2.py يكتب في radar_log.json قائمتين لكل نوع ادعاء بلغ
# 30 تنبيهاً مُقيَّماً: silenced (دقة <40% → يُسجَّل للتقييم بلا تيليجرام)
# وproven (دقة ≥50% → يُرسل بلا وسم "🧪 تجريبي"). تحت 30: القائمتان فارغتان
# وكل شيء كما كان. التعطيل الفوري: False (تُتجاهل القائمتان تماماً).
RADAR_ALERT_STOP_RULE = True
# 🟥 المسار السريع للطرد — REC-009 (قرار المالك 2026-08-10: "نفّذ من أي دقيقة"):
# طرد من فريق متعادل أو متقدم بهدف واحد = أفضلية عددية للخصم — تنبيه فوري
# **من أي دقيقة، بلا شرط د75 وبلا موجات الزخم** (استثناء المالك الصريح لهذا
# الادعاء وحده؛ بقية ادعاءات الدراما تبقى تحت شروطها كما هي). مرة واحدة لكل
# مباراة بعلم مستقل (red_alerted — لا يحجب سلم الدراما ولا يُحجب به)، يُسجَّل
# بمفتاح red_advantage ويُقيَّم صباحاً بعدّاده المستقل ويخضع لقاعدة الإيقاف
# REC-005 كأي ادعاء. المتوقع المسجّل مسبقاً: دقة 45-65% مقابل خط أساس 29%.
RADAR_RED_FAST_PATH = True    # مفتاح التراجع الفوري

# 🔴 إنذار الرادار الأحمر المبكر إلى تيليجرام (قرار المالك 2026-08-19 بعد
# قياس RND: الـ96% المعلنة للإنذار الأحمر مضلّلة — 196 من 223 إنذاراً تُطلق
# د86+ بعامل واحد «النتيجة الحالية تُسقط التوقع (د90)»، أي أنها تقرأ لوحة
# نتائج مباراة منتهية لا تتنبأ بشيء. الشريحة ذات القيمة هي المبكرة وحدها:
# ≤د85 = 27 إنذاراً بدقة 85% وبمعدل ~3/يوم — وقتٌ يسمح بالتصرف فعلاً.
# لذلك: المبكر وحده يرنّ الهاتف، وما بعد د85 يبقى شاشةً للأبد.
# يُرسل مرة واحدة لكل مباراة (علم warn_alerted مستقل عن الدراما والطرد)،
# ويُوسم صفه في السجل بـalerted:True فتُقاس الشريحة المُرسَلة وحدها صباحاً.
RED_WARN_ALERT = True          # مفتاح التراجع الفوري (False = شاشة فقط كالسابق)
# 🎛 قرار المالك 2026-08-24 (REC-014 خيار ج): الإرسال لدورياته التسعة حرفياً —
# فيضان 51/55 رسالة يومي 22-23 أغسطس مقابل تصميم ~3/يوم. الشاشة والقياس
# كاملان لكل العالم؛ الكبح على رسالة تيليجرام وحدها. False = عالمي كالسابق
RED_WARN_MINE_ONLY = True
# 🛗 REC-018 (قرار المالك 2026-08-24): سلّم تخفيف حمولة API-Football المسجَّل
# مسبقاً — ذروة مقاسة 88.5% من السقف يوم خميس تصفيات، ودور المجموعات قادم.
# تحت النسبة يقتصر الرادار على قائمة التركيز + دوريات الصدارة (المغمور أول
# ما يُضحى به، ودورياته والتقييم والحرس آخر ما يُمس). مفتاح التراجع LOAD_SHED
LOAD_SHED = True
LOAD_SHED_RATIO = 0.15
# 📵 قرار المالك 2026-08-24 مساءً (لقطة النشرة): تنبيهات الدراما تصل الهاتف
# لدورياته التسعة + مباريات قائمة التركيز (المفضلة) فقط — الشاشة والقياس
# يواصلان تغطية العالم (الصف يُسجَّل بوسم gated). False = عالمي كما كان
DRAMA_MINE_ONLY = True
# 📸 REC-015: حالة لحظة الإرسال تُجمَّد في حقول sent_* — الصف يخزن ذروة المسح
# وقد تكون أقدم/أدنى من قراءة الإرسال (21 صفاً أحمر أُرسل واحتُسب كهرمانياً)
SENT_SNAPSHOT = True
RED_WARN_ALERT_MAX = 85        # لا تنبيه بعد هذه الدقيقة — بعدها قراءة لوحة لا إنذار
RED_WARN_ALERT_CAP_PER_RUN = 3 # سقف مستقل حتى لا يزاحم تنبيهات الدراما
_ALERT_RANK = {"goal": 1, "equalizer": 2, "next_goal": 2, "flip": 3}

# معرفات الدوريات الكبرى في API-Football (تقدر تضيف عليها)
TOP_LEAGUE_IDS = {
    1,    # كأس العالم
    2,    # دوري أبطال أوروبا
    3,    # الدوري الأوروبي
    4,    # يورو
    9,    # كوبا أمريكا
    13,   # كوبا ليبرتادوريس
    15,   # كأس العالم للأندية
    39,   # الدوري الإنجليزي الممتاز
    40,   # دوري البطولة الإنجليزية (تشامبيونشيب)
    61,   # الدوري الفرنسي
    71,   # الدوري البرازيلي
    78,   # الدوري الألماني
    88,   # الدوري الهولندي
    94,   # الدوري البرتغالي
    128,  # الدوري الأرجنتيني
    135,  # الدوري الإيطالي
    140,  # الدوري الإسباني
    253,  # الدوري الأمريكي MLS
    307,  # دوري روشن السعودي
    417,  # الدوري البحريني الممتاز
    542,  # الدوري العراقي الممتاز
}

# الدول المستبعدة (أفريقيا + الهند وباكستان وبنغلادش)
EXCLUDED_COUNTRIES = {
    "india", "pakistan", "bangladesh",
    "algeria", "angola", "benin", "botswana", "burkina faso", "burkina-faso",
    "burundi", "cameroon", "cape verde", "cape-verde",
    "central african republic", "central-african-republic", "chad", "comoros",
    "congo", "congo dr", "congo-dr", "dr congo", "djibouti", "egypt",
    "equatorial guinea", "equatorial-guinea", "eritrea", "eswatini",
    "ethiopia", "gabon", "gambia", "ghana", "guinea", "guinea-bissau",
    "ivory coast", "ivory-coast", "kenya", "lesotho", "liberia", "libya",
    "madagascar", "malawi", "mali", "mauritania", "mauritius", "morocco",
    "mozambique", "namibia", "niger", "nigeria", "rwanda",
    "sao tome and principe", "sao-tome-and-principe", "senegal", "seychelles",
    "sierra leone", "sierra-leone", "somalia", "south africa", "south-africa",
    "south sudan", "south-sudan", "sudan", "tanzania", "togo", "tunisia",
    "uganda", "zambia", "zimbabwe",
}

# كلمات في اسم البطولة تؤدي للاستبعاد (الودية + بطولات أفريقيا القارية)
EXCLUDED_LEAGUE_KEYWORDS = [
    "friendl", "caf ", "africa", "afcon",
    # بيانات لا نبني عليها التعلم (توجيه المالك 2026-07-18): دوريات السيدات
    # والفئات السنية والرديف — ضجيج يبطئ بناء دماغ موثوق للموسم
    "women", "femen", "femin", "frauen", "ladies", "wsl", "girls",
    # دوريات سيدات لا تحمل أي كلمة دالة في اسمها — تسربت فعلياً حتى 2026-08-01
    # (WK-League الكورية أفسدت خانة 70%+) — أسماؤها الصريحة تُستبعد بالاسم
    "wk-league", "wk league", "kvinde", "damallsvenskan", "elitettan",
    "toppserien", "naisten", "vrouwen", "femmin", "northern super league",
    "u16", "u17", "u18", "u19", "u20", "u21", "u23",
    "youth", "primavera", "juvenil", "junioren", "reserve", "reserva",
    "academy",
]

# حالات المباراة الحية والمنتهية في API-Football
LIVE_STATUSES  = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE", "INT"}
FINAL_STATUSES = {"FT", "AET", "PEN"}


# ================== أدوات مساعدة ==================
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def is_excluded(league: dict) -> bool:
    country = (league.get("country") or "").strip().lower()
    name = (league.get("name") or "").strip().lower()
    if country in EXCLUDED_COUNTRIES:
        return True
    for kw in EXCLUDED_LEAGUE_KEYWORDS:
        if kw in name:
            return True
    return False


_W_TEAM_RE = re.compile(r"\s\(?W\)?$")


def is_womens_match(home_name: str, away_name: str) -> bool:
    """طبقة أمان نمطية (درس تسريب WK-League — 2026-08-01): فرق السيدات في
    API-Football تحمل لاحقة W في نهاية الاسم؛ إن حملها الفريقان معاً فهي
    مباراة سيدات حتى لو خلا اسم الدوري من أي كلمة دالة. القوائم تفشل
    بصمت — الأنماط تلتقط ما لم نتوقعه بعد."""
    return bool(_W_TEAM_RE.search((home_name or "").strip())) and \
           bool(_W_TEAM_RE.search((away_name or "").strip()))

_YOUTH_TEAM_RE = re.compile(r"\bU-?(1[6-9]|2[0-3])\b", re.I)


def is_youth_match(home_name: str, away_name: str) -> bool:
    """طبقة أمان نمطية ثانية (درس Costa Rica U21 — 2026-08-02، نفس عقيدة
    WK-League): فرق الشباب تحمل U16–U23 في اسم الفريق حتى لو خلا اسم
    الدوري من أي كلمة دالة. إن حملها الفريقان معاً فهي مباراة فئات سنية."""
    return bool(_YOUTH_TEAM_RE.search(home_name or "")) and \
           bool(_YOUTH_TEAM_RE.search(away_name or ""))



def should_analyze(league: dict, used: int) -> bool:
    """هل نطلب تحليل Claude لهذه المباراة؟"""
    if used >= MAX_ANALYSES_PER_RUN:
        return False
    if ANALYZE_ALL:
        return True
    return league.get("id") in TOP_LEAGUE_IDS


def load_json_file(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def load_watchlist_data() -> dict:
    return load_json_file(WATCHLIST_FILE, {})


def valid_watch_fids(data: dict) -> set:
    """معرفات مباريات قائمة التركيز الصالحة (غير منتهية الصلاحية)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
    return {
        fid for fid, e in (data.get("matches") or {}).items()
        if isinstance(e, dict) and (e.get("date") or "9999") >= cutoff
    }


def load_watchlist() -> set:
    return valid_watch_fids(load_watchlist_data())


def all_focus_finished(data: dict, watch: set) -> bool:
    """هل انتهت كل مباريات قائمة التركيز؟ (كل واحدة سجلت نتيجتها النهائية)"""
    matches = data.get("matches") or {}
    return bool(watch) and all((matches.get(f) or {}).get("result") for f in watch)


def build_focus_summary(matches: dict) -> str:
    """ملخص فوري عند انتهاء آخر مباراة في القائمة: نتائجك ضد المحركين.
    التقييم الرسمي (وسجل الدقة الدائم) يبقى لملخص الصباح."""
    pick_ar = {"home": "فوز {h}", "draw": "تعادل", "away": "فوز {a}"}
    stores = [
        ("أنت", (load_json_file(Path("predictions_user.json"), {}).get("pending") or {})),
        ("المحرك 1", (load_json_file(Path("predictions.json"), {}).get("pending") or {})),
        ("المحرك 2", (load_json_file(Path("predictions_v2.json"), {}).get("pending") or {})),
    ]
    tallies = {name: [0, 0] for name, _ in stores}
    lines = ["🏁 انتهت كل مباريات قائمة التركيز — النتائج السريعة:"]
    for fid, e in matches.items():
        score = e.get("result") or "?"
        try:
            gh, ga = (int(x) for x in score.split("-"))
        except Exception:
            continue
        outcome = "home" if gh > ga else ("away" if ga > gh else "draw")
        lines.append(f"\n• {e.get('label', '?')} — {score}")
        parts = []
        for name, pending in stores:
            p = pending.get(fid)
            if not p or p.get("pick") not in pick_ar:
                continue
            h = p.get("ar_home") or p.get("home", "?")
            a = p.get("ar_away") or p.get("away", "?")
            ok = p["pick"] == outcome
            tallies[name][0] += 1 if ok else 0
            tallies[name][1] += 1
            parts.append(f"{name}: {pick_ar[p['pick']].format(h=h, a=a)} {'✅' if ok else '❌'}")
        if parts:
            lines.append("   " + " | ".join(parts))
    totals = [f"{name} {c}/{t}" for name, (c, t) in tallies.items() if t]
    if totals:
        lines.append("\n📊 حصيلة اليوم: " + " | ".join(totals))
    lines.append("التقييم الرسمي والدروس في ملخص الصباح.")
    return "\n".join(lines)


def should_alert(league: dict, fid: str, watch: set) -> bool:
    """هل نرسل تنبيه تيليجرام حياً لهذه المباراة؟
    تنبيهات تيليجرام الحية = مباريات قائمة التركيز فقط (طلب المالك 2026-07-26).
    قائمة التركيز فارغة → لا تنبيهات حية إطلاقاً (صمت تام)؛ لا رجوع للدوريات الكبرى.
    ملاحظة: هذا يخصّ تنبيهات المراقبة الحية فقط — ملخصات الصباح وتقييماتها تُرسَل
    دائماً من predict.py / predict_v2.py ولا تمرّ بهذه الدالة، فتصل حتى لو كانت
    القائمة فارغة. التوقعات والتعلّم والداشبورد تغطي كل المباريات كالمعتاد."""
    if not watch:
        return False
    return fid in watch


def api_football(path: str) -> list:
    """نداء API-Football محروساً (حادثة الصمت 2026-08-14).

    قبل الإصلاح كانت هذه الدالة تطبع errors وتمضي، فيبدو رفض المزوّد
    كقائمة فارغة — أي "يوم هادئ" بالقاعدة 5. الآن: القائمة الفارغة تبقى
    مقبولة، أما الرفض فيُرفع ApiRefused ويصرخ على تيليجرام من أول مرة.
    للتراجع الفوري: API_REFUSAL_STRICT=0 (يعود الابتلاع القديم).
    """
    return api_guard.guarded_get(
        f"https://v3.football.api-sports.io/{path}",
        headers={"x-apisports-key": API_FOOTBALL_KEY},
        component="monitor.py (المراقبة الحية)",
    )


def get_live_fixtures() -> list:
    return api_football("fixtures?live=all")


# إحصائيات مهمة تُلخص لتحليل المحرك 2 المباشر
KEY_LIVE_STATS = {
    "Shots on Goal", "Shots off Goal", "Total Shots", "Blocked Shots",
    "Shots insidebox", "Shots outsidebox", "Ball Possession", "Corner Kicks",
    "Offsides", "Yellow Cards", "Red Cards", "expected_goals",
    "Goalkeeper Saves", "Fouls",
}


def get_live_details(fid: str) -> str:
    """3 نداءات API: إحصائيات المباراة الحية + أحداثها + التشكيلات،
    يرجع سياقاً نصياً مضغوطاً. أي فشل يرجع نصاً أقصر — لا يوقف التحليل أبداً."""
    parts = []
    try:
        team_lines = []
        for side in api_football(f"fixtures/statistics?fixture={fid}"):
            name = (side.get("team") or {}).get("name", "?")
            vals = [
                f"{s.get('type')}: {s.get('value')}"
                for s in (side.get("statistics") or [])
                if s.get("type") in KEY_LIVE_STATS and s.get("value") is not None
            ]
            if vals:
                team_lines.append(f"{name} — " + ", ".join(vals))
        if team_lines:
            parts.append("Live stats:\n" + "\n".join(team_lines))
    except Exception as e:
        print("فشل سحب الإحصائيات الحية:", e)
    try:
        ev_lines = []
        for ev in api_football(f"fixtures/events?fixture={fid}")[-15:]:
            minute = ((ev.get("time") or {}).get("elapsed"))
            team = (ev.get("team") or {}).get("name", "?")
            player = (ev.get("player") or {}).get("name") or ""
            etype = ev.get("type") or "?"
            detail = ev.get("detail") or ""
            ev_lines.append(f"{minute}' {etype} ({detail}) {player} [{team}]")
        if ev_lines:
            parts.append("Match events:\n" + "\n".join(ev_lines))
    except Exception as e:
        print("فشل سحب أحداث المباراة:", e)
    try:
        lu_lines = []
        for side in api_football(f"fixtures/lineups?fixture={fid}"):
            team = (side.get("team") or {}).get("name", "?")
            formation = side.get("formation") or "?"
            starters = [
                ((x.get("player") or {}).get("name") or "?")
                for x in (side.get("startXI") or [])
            ]
            if starters:
                lu_lines.append(f"{team} ({formation}): " + ", ".join(starters))
        if lu_lines:
            parts.append("Lineups:\n" + "\n".join(lu_lines))
    except Exception as e:
        print("فشل سحب التشكيلات:", e)
    return "\n".join(parts)


SYSTEM_PROMPT_BASIC = (
    "أنت محلل وخبير توقع مباريات كرة قدم. سيصلك وضع مباراة بأسماء إنجليزية. "
    "أرجع ردك بهذا الشكل بالضبط:\n"
    "الأسماء: [الفريق المضيف بالعربي] | [الفريق الضيف بالعربي] | [البطولة بالعربي (الدولة بالعربي)]\n"
    "ثم سطران إلى ثلاثة: تحليل مختصر مبني على معرفتك بالفريقين والنتيجة والدقيقة، "
    "ينتهي بسطر: التوقع: [اسم الفريق بالعربي أو تعادل] — ثقة X%\n"
    "استخدم الأسماء العربية الشائعة في الإعلام الرياضي "
    "(مثال: Real Madrid → ريال مدريد، Manchester City → مانشستر سيتي)، "
    "وإذا كان الاسم غير مشهور فاكتبه بحروف عربية. "
    "استخدم الأرقام الإنجليزية (0-9) فقط ولا تستخدم الأرقام العربية (٠-٩) أبداً."
)

# قائمة السيناريوهات الكاملة التي يغطيها المحرك 2 المباشر (طلب المالك —
# مستوحاة من أسواق التحليل الاحترافية). تُحقن في تحليل الأحداث وفي النبض.
SCENARIO_MENU_V2 = (
    "قائمة السيناريوهات التي تراقبها (اذكر فقط المرجح فعلاً الآن، أبرز 2-3):\n"
    "- الهدف القادم: أي فريق، ومن المرشح لتسجيله أو صناعته بالاسم\n"
    "- كلا الفريقين يسجلان؟ ومسار إجمالي الأهداف (مباراة مفتوحة أم مقفلة)\n"
    "- الركنيات: موجة ركنيات قادمة، وأي فريق يكسب أغلبها\n"
    "- الكرات الثابتة: ركلة حرة خطرة قادمة، رميات تماس طويلة قرب المنطقة، "
    "ركلات مرمى/تشتيت متكرر يدل على ضغط مستمر\n"
    "- البطاقات: لاعب مرشح للإنذار بالاسم (تدخلات وأخطاء متكررة)، بطاقة حمراء "
    "محتملة تغير المباراة، كلا الفريقين ينالان بطاقات\n"
    "- حارس تحت الحصار (تصديات متتالية)، تسلل متكرر يقتل هجمات فريق\n"
    "- شكل النهاية: هامش الفوز المرجح، أي شوط أغزر أهدافاً، انقلاب محتمل في "
    "النتيجة بين الشوطين، أو المتقدم يغلق المباراة\n"
    "- في مباريات الكأس/الإقصاء فقط: احتمال انتهاء الوقت الأصلي بالتعادل "
    "والامتداد للأشواط الإضافية أو ركلات الترجيح (%)، ومن الأوفر حظاً في "
    "الترجيح (خبرة الحارس والمنفذين) — درس كيري×شيلبورن 2026-07-18\n"
)

SYSTEM_PROMPT_LIVE_V2 = (
    "أنت محلل مباريات حية من الطراز الأول. سيصلك وضع مباراة جارية بأسماء إنجليزية، "
    "وقد يتضمن إحصائيات حية (تسديدات، استحواذ، ركنيات، xG، تسلل، تصديات، بطاقات) "
    "وقائمة أحداث (أهداف بأسماء المسجلين، بطاقات، تبديلات). اعتمد على هذه البيانات أولاً.\n"
    + SCENARIO_MENU_V2 +
    "أرجع ردك بهذا الشكل بالضبط:\n"
    "الأسماء: [الفريق المضيف بالعربي] | [الفريق الضيف بالعربي] | [البطولة بالعربي (الدولة بالعربي)]\n"
    "ثم قراءة مركزة في 2-3 أسطر: من يسيطر فعلياً (الاستحواذ وحده يخدع — اربطه بالخطورة)، "
    "وأثر أي طرد أو تبديل هجومي.\n"
    "ثم سطر يبدأ بـ: السيناريو: أخطر 2-3 سيناريوهات مرجحة من القائمة أعلاه بتفاصيلها "
    "(الفريق، اللاعب بالاسم إن دلت الأحداث عليه، ونسبة تقديرية لكل سيناريو).\n"
    "ثم سطر أخير: التوقع: [اسم الفريق بالعربي أو تعادل] — ثقة X%\n"
    "استخدم الأسماء العربية الشائعة في الإعلام الرياضي، وإذا كان الاسم غير مشهور "
    "فاكتبه بحروف عربية. "
    "استخدم الأرقام الإنجليزية (0-9) فقط ولا تستخدم الأرقام العربية (٠-٩) أبداً."
)


SYSTEM_PROMPT_PULSE = (
    "أنت عين حية على مباراة جارية من قائمة تركيز المستخدم. سيصلك وضع المباراة "
    "مع إحصائياتها وأحداثها الحية، وقراءتك السابقة قبل نحو 10 دقائق.\n"
    "مهمتك: هل يتشكل الآن سيناريو مهم جديد يستحق تنبيه المستخدم؟\n"
    + SCENARIO_MENU_V2 +
    "وكذلك أي انقلاب في السيطرة أو تغير جوهري في نمط المباراة.\n"
    "إن لم يكن هناك تغير حقيقي مهم عن قراءتك السابقة فأرجع سطراً واحداً فقط: لا جديد\n"
    "وإن وجد تغير مهم فأرجع 2-4 أسطر: أولها يبدأ بـ 🔮 وفيه خلاصة السيناريو في جملة، "
    "ثم التفاصيل (السيناريو المتوقع، اللاعب الأخطر بالاسم إن دلّت الأحداث عليه، "
    "ونسبة تقديرية للاحتمال).\n"
    "لا تخلط 'لا جديد' مع أي نص آخر. استخدم الأسماء العربية الشائعة في الإعلام "
    "الرياضي والأرقام الإنجليزية (0-9) فقط، لا الأرقام العربية (٠-٩)."
)


def analyze_with_claude(context_text: str, model: str = CLAUDE_MODEL,
                        system_prompt: str = SYSTEM_PROMPT_BASIC,
                        max_tokens: int = 400, thinking_budget: int = 0) -> str:
    """يرسل وضع المباراة لـ Claude ويرجع الأسماء بالعربي + توقعاً مختصراً.
    thinking_budget > 0 يفعّل التفكير العميق الممتد قبل الإجابة (للمحرك 2 المباشر)."""
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": context_text}],
    }
    if thinking_budget > 0:
        # نموذج المحرك 2 يفكر تلقائياً وبعمق افتراضياً — أي معامل تفكير إضافي
        # يُرفض (خطأ 400). نكتفي بنفس شكل الطلب البسيط الذي يعمل في predict_v2
        # مع متسع أكبر في max_tokens للتفكير + الرد النهائي.
        body["max_tokens"] = max(max_tokens, thinking_budget + 800)
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        text = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        ).strip()
        return text or "(تعذر الحصول على تحليل)"
    except Exception as e:
        # نص خطأ الـ API (لا يتضمن أي مفتاح) — ضروري لتشخيص أخطاء 400 من السجلات
        detail = ""
        resp = getattr(e, "response", None)
        if resp is not None:
            try:
                detail = " — " + resp.text[:300]
            except Exception:
                pass
        print(f"Claude error: {e}{detail}")
        return "(تعذر التحليل حالياً — تحقق من رصيد مفتاح Claude)"


def analyze_match(prompt_base: str, league: dict, fid: str, live_budget: dict,
                  watch: set = frozenset()):
    """يختار التحليل المناسب: المحرك 2 المباشر (لمباريات قائمة التركيز — من أي
    دوري — وللدوريات الكبرى) أو التحليل الأساسي.
    يرجع (نص التحليل، هل هو تحليل المحرك 2؟)."""
    vip = fid in watch or league.get("id") in TOP_LEAGUE_IDS
    if vip and live_budget["used"] < MAX_LIVE_ENRICHED_PER_RUN:
        live_budget["used"] += 1
        details = get_live_details(fid)
        text = prompt_base + (("\n\n" + details) if details else "")
        raw = analyze_with_claude(
            text, model=CLAUDE_MODEL_V2,
            system_prompt=SYSTEM_PROMPT_LIVE_V2, max_tokens=600,
            thinking_budget=LIVE_THINKING_BUDGET,
        )
        return raw, True
    return analyze_with_claude(prompt_base), False


def live_pulse(fid: str, home: str, away: str, league_line: str,
               score: str, minute, prev_pulse: str) -> str:
    """نبضة مراقبة بين الأحداث لمباراة من قائمة التركيز: يقرأ المحرك 2 الوضع
    الحي كاملاً ويقارنه بقراءته السابقة. يرجع نص التنبيه أو '' إذا لا جديد."""
    details = get_live_details(fid)
    ctx = (
        f"مباراة جارية: {home} ضد {away} — {league_line}. "
        f"النتيجة {score}، الدقيقة {minute}.\n"
        f"قراءتك السابقة:\n{prev_pulse or 'لا توجد قراءة سابقة (هذه أول نبضة).'}"
        + (("\n\n" + details) if details else "")
    )
    raw = analyze_with_claude(
        ctx, model=CLAUDE_MODEL_V2, system_prompt=SYSTEM_PROMPT_PULSE,
        max_tokens=600, thinking_budget=LIVE_THINKING_BUDGET,
    )
    if not raw or raw.strip().startswith("لا جديد") or raw.startswith("(تعذر"):
        return ""
    return raw.strip()


SYSTEM_PROMPT_PREMATCH = (
    "أنت محلل ما قبل المباراة للمحرك 2 — من الطراز الأول. سيصلك سياق مباراة "
    "تنطلق قريباً: توقعات المحركين واحتمالاتهما، وقد يتضمن تشكيلات معلنة، "
    "إصابات، أرقام سوق المراهنات، ومقارنة إحصائية للفريقين. اعتمد على البيانات "
    "أولاً ثم معرفتك بالفريقين.\n"
    + SCENARIO_MENU_V2 +
    "أرجع تقريراً عربياً منظماً بهذه البنود (سطر لكل بند، مع نسبة تقديرية):\n"
    "⚽ النتيجة المتوقعة وهامش الفوز\n"
    "🥅 كلا الفريقين يسجلان؟ وإجمالي الأهداف المتوقع (فوق/تحت 2.5)\n"
    "🎯 المسجل المحتمل بالاسم (وصانع اللعب الأخطر)\n"
    "🚩 أغلبية الركنيات: أي فريق يكسب ركنيات أكثر؟ (ادعاء واحد لا غير)\n"
    "🚩 نطاق الركنيات: إجمالي ركنيات المباراة أكثر أم أقل من 9.5؟\n"
    "🟨 البطاقات: لاعبون مرشحون بالاسم إن أمكن، واحتمال بطاقة حمراء\n"
    "⚡ هدف من كرة ثابتة (ركلة حرة/ركنية/رمية): نعم أم لا؟\n"
    "⏱ نمط الشوطين: أيهما أغزر أهدافاً، واحتمال انقلاب النتيجة\n"
    "🔑 مفتاح المباراة: المعركة الحاسمة التي تحسم اللقاء\n"
    "ثم سطر أخير: تذكير: هذه توقعات تحليلية وليست ضمانات.\n"
    "استخدم الأسماء العربية الشائعة في الإعلام الرياضي والأرقام الإنجليزية "
    "(0-9) فقط، لا الأرقام العربية (٠-٩)."
)


def team_news_headlines(team: str) -> list:
    """عناوين مستهدفة للفريق من Google News RSS (مجاني) — خطوة استكشاف 5:
    الأخبار الصغيرة (انتقال، أزمة، غياب) قبل أن تصل للعناوين الكبرى."""
    if not team:
        return []
    try:
        import html as html_mod
        r = requests.get(
            "https://news.google.com/rss/search",
            params={"q": f'"{team}" football', "hl": "en", "gl": "US",
                    "ceid": "US:en"},
            timeout=15,
        )
        titles = re.findall(r"<title>(.*?)</title>", r.text)[1:6]
        return [html_mod.unescape(t).strip() for t in titles if t.strip()]
    except Exception as e:
        print("أخبار الفريق — فشل الجلب:", e)
        return []


# REC-006: تصنيف بنود التقرير المُقيَّمة بنوع الادعاء — الترتيب مقصود:
# الكلمة الأدق أولاً حتى لا يبتلع نوعٌ عام ("النتيجة") بنداً أخص ("كرة ثابتة").
CLAIM_TYPE_KEYWORDS = [
    ("كلا الفريقين يسجلان", ("يسجلان", "كلا الفريقين")),
    ("الركنيات", ("ركني",)),
    ("البطاقات", ("بطاق", "إنذار", "صفراء", "حمراء", "طرد")),
    ("الكرات الثابتة", ("ثابتة", "ركلة حرة", "ركلات حرة", "رمية")),
    ("الأشواط", ("شوط",)),
    ("إجمالي الأهداف", ("2.5", "إجمالي الأهداف")),
    ("المسجل", ("مسجل", "هداف", "صانع")),
    ("النتيجة", ("فوز", "تعادل", "هامش", "نتيج", "انتصار")),
]


def claim_type(claim: str) -> str:
    """يرجع نوع الادعاء لبند تقرير مُقيَّم (أو 'أخرى' إن لم يُعرف نوعه)."""
    text = str(claim or "")
    for label, keys in CLAIM_TYPE_KEYWORDS:
        if any(k in text for k in keys):
            return label
    return "أخرى"


def scenario_scorecard_text() -> str:
    """REC-006 (أ): كشف حساب التقارير السابقة من scenarios_v2.json — صح/جزئي/خطأ
    لكل نوع ادعاء، وفجوة النسب المعلنة في البنود مقابل ما تحقق منها فعلاً.
    صفر نداءات API. يرجع '' عند غياب البيانات أو أي خلل — فشل صامت لا يقتل التقرير."""
    try:
        resolved = (load_json_file(SCENARIOS_FILE, {}) or {}).get("resolved") or []
        by_type = {}
        stated_pcts, stated_hits = [], []
        reports = graded_total = 0
        for entry in resolved:
            grades = entry.get("grades") if isinstance(entry, dict) else None
            if not isinstance(grades, list) or not grades:
                continue
            reports += 1
            for g in grades:
                if not isinstance(g, dict):
                    continue
                result = str(g.get("result") or "")
                if result not in ("صح", "خطأ", "جزئي"):
                    continue
                graded_total += 1
                t = by_type.setdefault(claim_type(g.get("claim")),
                                       {"صح": 0, "جزئي": 0, "خطأ": 0})
                t[result] += 1
                # فجوة النسب: البنود التي أعلنت نسبة صريحة مثل "(60%)"
                m = re.search(r"(\d{1,3})\s*%", str(g.get("claim") or ""))
                if m and 0 < int(m.group(1)) <= 100:
                    stated_pcts.append(int(m.group(1)))
                    stated_hits.append(1 if result == "صح" else 0)
        if not graded_total:
            return ""
        lines = [f"سجل أدائك الفعلي في التقارير السابقة "
                 f"({reports} تقريراً / {graded_total} بنداً مُقيَّماً):"]
        order = [label for label, _ in CLAIM_TYPE_KEYWORDS] + ["أخرى"]
        for label in order:
            t = by_type.get(label)
            if not t:
                continue
            total = t["صح"] + t["جزئي"] + t["خطأ"]
            lines.append(f"- {label}: صح {t['صح']} / جزئي {t['جزئي']} / "
                         f"خطأ {t['خطأ']} (من {total})")
        if stated_pcts:
            avg_stated = round(sum(stated_pcts) / len(stated_pcts))
            realized = round(100 * sum(stated_hits) / len(stated_hits))
            lines.append(
                f"- النسب التي أعلنتها داخل البنود: متوسطها {avg_stated}% "
                f"بينما تحقق منها فعلياً {realized}% "
                f"(على {len(stated_pcts)} بنداً) — عايِر نسبك على هذه الفجوة."
            )
        return "\n".join(lines)
    except Exception as e:
        print("كشف حساب التقارير — فشل الحساب (نكمل بدونه):", e)
        return ""


def base_rates_text(league_name: str) -> str:
    """REC-006 (ب): معدلات الأساس الحقيقية من المباريات المُقيَّمة في
    predictions_v2.json — نسبة "كلا الفريقين سجّلا" ونسبة "فوق 2.5 هدف" إجمالاً،
    وللدوري نفسه إذا كانت عينته ≥ BASE_RATE_MIN_LEAGUE مباراة.
    صفر نداءات API. يرجع '' عند أي خلل — فشل صامت لا يقتل التقرير."""
    try:
        resolved = (load_json_file(RADAR_PREDICTIONS_FILE, {}) or {}).get("resolved") or []
        overall = {"btts": 0, "over": 0, "total": 0}
        league = {"btts": 0, "over": 0, "total": 0}
        for r in resolved:
            m = re.match(r"^(\d+)-(\d+)$", str(r.get("score") or "").strip())
            if not m:
                continue
            gh, ga = int(m.group(1)), int(m.group(2))
            btts = 1 if (gh > 0 and ga > 0) else 0
            over = 1 if (gh + ga) >= 3 else 0
            overall["total"] += 1
            overall["btts"] += btts
            overall["over"] += over
            if league_name and r.get("league") == league_name:
                league["total"] += 1
                league["btts"] += btts
                league["over"] += over
        if not overall["total"]:
            return ""
        def rate(d, k):
            return round(100 * d[k] / d["total"])
        lines = [
            "معدلات الأساس الفعلية من مبارياتنا المُقيَّمة "
            f"({overall['total']} مباراة): كلا الفريقين سجّلا في "
            f"{rate(overall, 'btts')}% منها، وتجاوزت 2.5 هدف {rate(overall, 'over')}% منها."
        ]
        if league["total"] >= BASE_RATE_MIN_LEAGUE:
            lines.append(
                f"وفي هذا الدوري تحديداً ({league['total']} مباراة): "
                f"كلا الفريقين سجّلا في {rate(league, 'btts')}% "
                f"وفوق 2.5 في {rate(league, 'over')}%."
            )
        lines.append("هذه المعدلات الفعلية سياق لا أمر — إن خالفتها في بنود "
                     "الأهداف فاذكر سبباً محدداً في هذه المباراة يبرر الخروج عنها.")
        return "\n".join(lines)
    except Exception as e:
        print("معدلات الأساس — فشل الحساب (نكمل بدونه):", e)
        return ""


def build_prematch_context(fid: str, v2p: dict, v1p: dict, userp: dict) -> str:
    """يجمع سياق التقرير: توقعات المحركين والمالك + بيانات API قبل المباراة
    (تشكيلات إن أُعلنت، إصابات، أرقام السوق، التوقع الإحصائي) — 4 نداءات API."""
    p = v2p or v1p or {}
    lines = [
        f"مباراة تنطلق قريباً: {p.get('home', '?')} ضد {p.get('away', '?')} — "
        f"{p.get('league', '')}."
    ]
    if v2p:
        lines.append(
            f"توقع المحرك 2: {v2p.get('pick')} "
            f"(احتمالات {v2p.get('prob_home')}/{v2p.get('prob_draw')}/{v2p.get('prob_away')}) — "
            f"السبب: {v2p.get('reason', '')}"
        )
    if v1p:
        lines.append(f"توقع المحرك 1: {v1p.get('pick')} بثقة {v1p.get('confidence')}%")
    if userp:
        lines.append(f"توقع المالك: {userp.get('pick')}")
    try:
        lu = []
        for side in api_football(f"fixtures/lineups?fixture={fid}"):
            team = (side.get("team") or {}).get("name", "?")
            formation = side.get("formation") or "?"
            starters = [((x.get("player") or {}).get("name") or "?")
                        for x in (side.get("startXI") or [])]
            if starters:
                lu.append(f"{team} ({formation}): " + ", ".join(starters))
        if lu:
            lines.append("التشكيلات المعلنة:\n" + "\n".join(lu))
    except Exception as e:
        print("تقرير ما قبل المباراة — فشل التشكيلات:", e)
    try:
        inj = [f"{((i.get('player') or {}).get('name') or '?')} "
               f"({(i.get('team') or {}).get('name', '?')}: "
               f"{(i.get('player') or {}).get('reason', '?')})"
               for i in api_football(f"injuries?fixture={fid}")[:12]]
        if inj:
            lines.append("الإصابات/الغيابات: " + "، ".join(inj))
    except Exception as e:
        print("تقرير ما قبل المباراة — فشل الإصابات:", e)
    try:
        for bk in (api_football(f"odds?fixture={fid}") or [{}])[0].get("bookmakers", [])[:1]:
            for bet in bk.get("bets", []):
                if bet.get("name") == "Match Winner":
                    vals = {v.get("value"): v.get("odd") for v in bet.get("values", [])}
                    lines.append(f"أرقام السوق (1X2): {vals}")
    except Exception as e:
        print("تقرير ما قبل المباراة — فشل أرقام السوق:", e)
    try:
        for pr in api_football(f"predictions?fixture={fid}")[:1]:
            pred = pr.get("predictions") or {}
            pct = pred.get("percent") or {}
            lines.append(
                f"التوقع الإحصائي: {pct} — نصيحة: {pred.get('advice', '')} — "
                f"فوز مرجح: {((pred.get('winner') or {}).get('name'))}"
            )
            comp = pr.get("comparison") or {}
            if comp:
                lines.append(f"مقارنة الفريقين: {json.dumps(comp, ensure_ascii=False)[:600]}")
    except Exception as e:
        print("تقرير ما قبل المباراة — فشل التوقع الإحصائي:", e)
    # الحكم المعلن + سجله من قاعدتنا الذاتية (بعض الحكام يشهرون بغزارة)
    try:
        fx = api_football(f"fixtures?ids={fid}")
        referee = ((fx[0].get("fixture") or {}).get("referee") or "") if fx else ""
        if referee:
            rec = (load_json_file(REFEREES_FILE, {}) or {}).get(referee.strip())
            if rec and rec.get("matches"):
                avg_y = round(rec["yellows"] / rec["matches"], 1)
                lines.append(
                    f"الحكم: {referee} — من سجلنا: معدل {avg_y} بطاقة صفراء"
                    f" و{rec.get('reds', 0)} حمراء في {rec['matches']} مباراة."
                )
            else:
                lines.append(f"الحكم: {referee} (استخدم معرفتك بأسلوبه إن كان مشهوراً).")
    except Exception as e:
        print("تقرير ما قبل المباراة — فشل جلب الحكم:", e)
    # أخبار مستهدفة للفريقين (الأخبار الصغيرة تصنع فرقاً — توجيه المالك)
    news_lines = []
    for team in (p.get("home"), p.get("away")):
        for title in team_news_headlines(team):
            news_lines.append(f"- {title}")
    if news_lines:
        lines.append("أخبار حديثة مستهدفة للفريقين (استخدم المؤثر منها فقط):\n"
                     + "\n".join(news_lines[:10]))
    # REC-006: كشف الحساب ومعدلات الأساس — يُحقنان كسياق لا كأمر
    # (مفتاح التراجع PREMATCH_CALIBRATION_CTX أعلاه؛ الدالتان تفشلان بصمت)
    if PREMATCH_CALIBRATION_CTX:
        scorecard = scenario_scorecard_text()
        if scorecard:
            lines.append(scorecard)
        base_rates = base_rates_text(p.get("league") or "")
        if base_rates:
            lines.append(base_rates)
    # دروس المحرك 2 من تقييم تقاريره السابقة — حلقة التعلم الذاتي للسيناريوهات
    lessons = (load_json_file(LESSONS_FILE, {}).get("lessons") or [])[-15:]
    lesson_lines = [f"- {(it.get('text') or '').strip()}" for it in lessons
                    if isinstance(it, dict) and (it.get("text") or "").strip()]
    if lesson_lines:
        lines.append("دروس من أخطائك السابقة (طبقها في هذا التقرير):\n"
                     + "\n".join(lesson_lines))
    return "\n".join(lines)


def prematch_reports(wl_data: dict, watch: set) -> bool:
    """يرسل تقرير سيناريوهات المحرك 2 لكل مباراة تركيز تنطلق خلال
    PREMATCH_REPORT_MINUTES دقيقة (مرة واحدة لكل مباراة — علم prematch_sent).
    يحفظ watchlist.json فوراً عند الإرسال حتى لا يتكرر التقرير لو فشل ما بعده."""
    if not watch:
        return False
    v2_pending = load_json_file(Path("predictions_v2.json"), {}).get("pending") or {}
    v1_pending = load_json_file(Path("predictions.json"), {}).get("pending") or {}
    user_pending = load_json_file(Path("predictions_user.json"), {}).get("pending") or {}
    now = datetime.now(timezone.utc)
    dirty = False
    for fid in sorted(watch):
        entry = (wl_data.get("matches") or {}).get(fid)
        if entry is None or entry.get("prematch_sent") or entry.get("result"):
            continue
        p = v2_pending.get(fid) or v1_pending.get(fid) or {}
        try:
            kickoff = datetime.fromisoformat(p.get("kickoff", ""))
        except Exception:
            continue
        if kickoff <= now:                                   # بدأت — فات الوقت
            continue
        minutes_left = (kickoff - now).total_seconds() / 60
        if minutes_left > PREMATCH_REPORT_MINUTES:
            continue
        ctx = build_prematch_context(fid, v2_pending.get(fid),
                                     v1_pending.get(fid), user_pending.get(fid))
        report = analyze_with_claude(
            ctx, model=CLAUDE_MODEL_V2, system_prompt=SYSTEM_PROMPT_PREMATCH,
            max_tokens=900, thinking_budget=LIVE_THINKING_BUDGET,
        )
        if report.startswith("(تعذر"):
            continue                                          # نحاول في الجولة القادمة
        h = p.get("ar_home") or p.get("home", "?")
        a = p.get("ar_away") or p.get("away", "?")
        league = p.get("ar_league") or p.get("league", "")
        send_telegram(
            f"📋 تقرير المحرك 2 — ما قبل المباراة\n"
            f"🏆 {league}\n{h} 🆚 {a}\n"
            f"⏰ الانطلاق خلال ~{int(minutes_left)} دقيقة\n\n{report}"
        )
        entry["prematch_sent"] = True
        dirty = True
        # حفظ التقرير للتقييم الذاتي: predict_v2 يقارنه صباحاً بالبيانات
        # النهائية الحقيقية ويستخلص دروساً تتحسن بها التقارير القادمة
        scen = load_json_file(SCENARIOS_FILE, {"pending": {}, "resolved": []})
        scen.setdefault("pending", {})
        scen["pending"][fid] = {
            "fid": fid,
            "date": p.get("date") or (p.get("kickoff") or "")[:10],
            "kickoff": p.get("kickoff", ""),
            "home": p.get("home", "?"), "away": p.get("away", "?"),
            "ar_home": p.get("ar_home", ""), "ar_away": p.get("ar_away", ""),
            "league": p.get("ar_league") or p.get("league", ""),
            "report": report,
            "prompt_rev": 2,   # 🔬 REC-019: بنود ذرية — تُقاس شريحة مستقلة
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        SCENARIOS_FILE.write_text(
            json.dumps(scen, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    if dirty:
        WATCHLIST_FILE.write_text(
            json.dumps(wl_data, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return dirty


def select_shadow_fixtures(v2_pending: dict, scen_pending: dict, watch: set,
                           now: datetime, cap: int) -> list:
    """يختار مباريات الدوريات الكبرى التي تنطلق خلال نافذة ما قبل المباراة،
    لم يُلتقط لها تقرير بعد، وليست في قائمة التركيز (تلك لها التقرير العادي).
    ترجع fids بحد أقصى cap — الذهب (ثقة ≥70) أولاً ثم الأقرب انطلاقاً
    (أولوية الذهب: أمر المالك 2026-08-09 بعد ترك مباراتي 8 أغسطس الذهبيتين)."""
    out = []
    for fid, p in (v2_pending or {}).items():
        if fid in watch or fid in (scen_pending or {}):
            continue
        if not p.get("top"):
            continue
        try:
            kickoff = datetime.fromisoformat(p.get("kickoff", ""))
        except Exception:
            continue
        if kickoff <= now:
            continue
        if (kickoff - now).total_seconds() / 60 > PREMATCH_REPORT_MINUTES:
            continue
        gold = (SHADOW_GOLD_PRIORITY
                and (p.get("confidence") or 0) >= GOLD_SHADOW_MIN_CONF)
        out.append(((0 if gold else 1, kickoff), fid))
    out.sort()
    return [fid for _, fid in out[:max(0, cap)]]


def _shadow_is_early(kickoff_str: str) -> bool:
    """هل الانطلاق قبل الحد المسائي؟ (تعذر القراءة = مبكرة، فتخضع للحجز)."""
    try:
        return datetime.fromisoformat(kickoff_str or "").hour < SHADOW_EVENING_FROM_UTC
    except Exception:
        return True


def _shadow_hour(kickoff_str: str) -> int:
    """ساعة الانطلاق UTC — تعذر القراءة = 12 (نهار مبكر: الأكثر تحفظاً،
    يخضع لحجز المساء ولا يفلت من أي سقف)."""
    try:
        return datetime.fromisoformat(kickoff_str or "").hour
    except Exception:
        return 12


def evening_fixtures_ahead(v2_pending: dict, scen_pending: dict, watch: set,
                           now: datetime) -> int:
    """عدد مباريات الدوريات الكبرى التي تنطلق لاحقاً اليوم بعد الحد المسائي
    ولم يُلتقط لها تقرير بعد — هي من يُحجز الاحتياطي لأجلها."""
    today = now.strftime("%Y-%m-%d")
    count = 0
    for fid, p in (v2_pending or {}).items():
        if fid in watch or fid in (scen_pending or {}):
            continue
        if not p.get("top"):
            continue
        try:
            kickoff = datetime.fromisoformat(p.get("kickoff", ""))
        except Exception:
            continue
        if kickoff <= now or kickoff.strftime("%Y-%m-%d") != today:
            continue
        if kickoff.hour >= SHADOW_EVENING_FROM_UTC:
            count += 1
    return count


def shadow_reports(watch: set) -> None:
    """تقارير الظل: يلتقط تقرير سيناريوهات كاملاً لمباريات الدوريات الكبرى
    القادمة ويحفظه بعلامة shadow — بلا تيليجرام. يُقيَّم صباحاً كأي تقرير
    ويغذي دروس lessons_v2.json، فيتدرب المحرك يومياً لا فقط عند طلب المالك."""
    scen = load_json_file(SCENARIOS_FILE, {"pending": {}, "resolved": []})
    scen.setdefault("pending", {})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    todays = [e for e in list(scen["pending"].values())
              + list(scen.get("resolved") or [])
              if isinstance(e, dict) and e.get("shadow") and e.get("date") == today]
    # سقف الليل: ما زاد عن SHADOW_NIGHT_MAX من مباريات الليل الأمريكي لا
    # يُحتسب على حصة اليوم — فلا يسد ليلُ أمس نهارَ أوروبا (اكتشاف 2026-08-16)
    night_used = sum(1 for e in todays
                     if _shadow_hour(e.get("kickoff", "")) < SHADOW_NIGHT_UNTIL_UTC)
    night_counted = min(night_used, SHADOW_NIGHT_MAX)
    day_used = len(todays) - night_used
    budget = SHADOW_REPORTS_PER_DAY - day_used - night_counted
    # أولوية الذهب (أمر المالك 2026-08-09): بعد نفاد الحصة العادية تبقى
    # لمباريات الثقة ≥70 حصة إضافية خاصة — الذهب لا يُترك بلا تقرير أبداً
    gold_used = sum(1 for e in todays if e.get("gold"))
    gold_budget = (GOLD_SHADOW_EXTRA_PER_DAY - gold_used
                   if SHADOW_GOLD_PRIORITY else 0)
    if budget <= 0 and gold_budget <= 0:
        return
    v2_pending = load_json_file(Path("predictions_v2.json"), {}).get("pending") or {}
    v1_pending = load_json_file(Path("predictions.json"), {}).get("pending") or {}
    now = datetime.now(timezone.utc)
    # الحجز المسائي: كم حصة يجوز للمباريات المبكرة استهلاكها الآن —
    # (السقف − حجز ديناميكي بقدر المباريات المسائية الفعلية) − المستهلك مبكراً
    reserve = min(SHADOW_EVENING_RESERVE,
                  evening_fixtures_ahead(v2_pending, scen["pending"], watch, now))
    day_early_used = sum(1 for e in todays
                         if SHADOW_NIGHT_UNTIL_UTC
                         <= _shadow_hour(e.get("kickoff", ""))
                         < SHADOW_EVENING_FROM_UTC)
    early_budget = max(0, SHADOW_REPORTS_PER_DAY - reserve
                       - day_early_used - night_counted)
    for fid in select_shadow_fixtures(v2_pending, scen["pending"], watch, now,
                                      max(0, budget) + max(0, gold_budget)):
        p = v2_pending.get(fid) or {}
        gold = (SHADOW_GOLD_PRIORITY
                and (p.get("confidence") or 0) >= GOLD_SHADOW_MIN_CONF)
        hour = _shadow_hour(p.get("kickoff", ""))
        night = hour < SHADOW_NIGHT_UNTIL_UTC
        early = SHADOW_NIGHT_UNTIL_UTC <= hour < SHADOW_EVENING_FROM_UTC
        # سقف الليل: بعد بلوغه لا يُلتقط ليلٌ غير ذهبي إطلاقاً
        if night and not gold and night_used >= SHADOW_NIGHT_MAX:
            continue
        # مباراة مبكرة غير ذهبية لا تمس الحصص المحجوزة للمساء
        if (early or night) and not gold and early_budget <= 0:
            continue
        # الذهب يستهلك الحصة العادية أولاً؛ غير الذهب لا يمس الحصة الإضافية
        if budget > 0:
            budget -= 1
        elif gold and gold_budget > 0:
            gold_budget -= 1
        else:
            continue
        if night:
            night_used += 1
        if early or night:
            early_budget -= 1
        ctx = build_prematch_context(fid, v2_pending.get(fid),
                                     v1_pending.get(fid), None)
        report = analyze_with_claude(
            ctx, model=CLAUDE_MODEL_V2, system_prompt=SYSTEM_PROMPT_PREMATCH,
            max_tokens=900, thinking_budget=LIVE_THINKING_BUDGET,
        )
        if report.startswith("(تعذر"):
            continue                       # نحاول في الجولة القادمة
        scen["pending"][fid] = {
            "fid": fid,
            "date": p.get("date") or (p.get("kickoff") or "")[:10],
            "kickoff": p.get("kickoff", ""),
            "home": p.get("home", "?"), "away": p.get("away", "?"),
            "ar_home": p.get("ar_home", ""), "ar_away": p.get("ar_away", ""),
            "league": p.get("ar_league") or p.get("league", ""),
            "report": report,
            "prompt_rev": 2,   # 🔬 REC-019: بنود ذرية — تُقاس شريحة مستقلة
            "shadow": True,                # صامت — التقط للتعلم فقط
            "gold": gold,                  # ذهب (ثقة ≥70) — لعدّاد الحصة الإضافية
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        SCENARIOS_FILE.write_text(
            json.dumps(scen, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"تقرير ظل: {p.get('home', '?')} × {p.get('away', '?')}")


def live_signature(fid: str) -> dict:
    """بصمة أرقام المباراة (نداء API واحد): تحركها يعني حدثاً يستحق نبضة.
    ترجع {} عند أي فشل — فلا نخزنها كأساس."""
    sig = {}
    try:
        for side in api_football(f"fixtures/statistics?fixture={fid}"):
            team = (side.get("team") or {}).get("id")
            for s in side.get("statistics") or []:
                t, v = s.get("type"), s.get("value")
                if t in SIG_STATS and v is not None:
                    try:
                        sig[f"{team}:{t}"] = int(str(v).replace("%", ""))
                    except Exception:
                        pass
    except Exception as e:
        print("فشل سحب بصمة الإحصائيات:", e)
    return sig


def significant_delta(base: dict, now: dict) -> bool:
    """هل تحركت الأرقام بما يكفي منذ آخر نبضة؟ (ركنيتان، تسديدتان على المرمى،
    أي بطاقة، ...) — البوابة الحتمية التي توفر نداءات Claude في الرصد السريع."""
    for key, val in now.items():
        stat = key.split(":", 1)[-1]
        if val - base.get(key, 0) >= SIG_THRESHOLDS.get(stat, 10**9):
            return True
    return False


def focus_fast_watch(state: dict, wl_data: dict, watch: set,
                     live_budget: dict, pulses: dict) -> bool:
    """الرصد السريع لمباريات قائمة التركيز: بعد الجولة العادية تبقى التشغيلة
    مستيقظة حتى ~8 دقائق وتفحص مباريات القائمة كل ~90 ثانية — هدف/بداية/نهاية
    تُعلن فوراً، والنبض يعمل فقط عند تحرك الأرقام (significant_delta).
    يرجع True إذا سجل نتيجة نهائية في watchlist (تعديل يجب حفظه)."""
    if not watch:
        return False
    wl_dirty = False
    deadline = fast_watch_deadline()
    ids = "-".join(sorted(watch))
    while time.monotonic() < deadline:
        time.sleep(FOCUS_SWEEP_SECONDS)
        try:
            fixtures = api_football(f"fixtures?ids={ids}")
        except Exception as e:
            print("الرصد السريع: فشل السحب:", e)
            continue
        seen_now = datetime.now(timezone.utc).isoformat()
        keep = False
        for fx in fixtures:
            fixture = fx.get("fixture", {}) or {}
            fid = str(fixture.get("id"))
            if fid not in watch:
                continue
            league = fx.get("league", {}) or {}
            teams = fx.get("teams", {}) or {}
            goals = fx.get("goals", {}) or {}
            status = ((fixture.get("status") or {}).get("short")) or ""
            minute = ((fixture.get("status") or {}).get("elapsed")) or 0
            home = (teams.get("home") or {}).get("name", "?")
            away = (teams.get("away") or {}).get("name", "?")
            gh = goals.get("home") or 0
            ga = goals.get("away") or 0
            score = f"{gh}-{ga}"
            league_line = f"{league.get('name', '?')} ({league.get('country', '?')})"

            if status in LIVE_STATUSES:
                keep = True
            elif status == "NS":
                # لم تبدأ بعد — نواصل الانتظار إن كانت الانطلاقة قريبة
                try:
                    ko = datetime.fromisoformat(
                        (fixture.get("date") or "").replace("Z", "+00:00"))
                    if ko - datetime.now(timezone.utc) <= timedelta(minutes=12):
                        keep = True
                except Exception:
                    pass
                continue

            prev = state.get(fid)

            # بداية مباراة التقطها الرصد السريع قبل الجولة القادمة
            if prev is None and status in LIVE_STATUSES:
                raw, enriched = analyze_match(
                    f"مباراة حية بدأت الآن: {home} ضد {away} — {league_line}. "
                    f"النتيجة {score}، الدقيقة {minute}. "
                    f"أعطني توقعك النهائي لهذه المباراة.",
                    league, fid, live_budget, watch,
                )
                ar_names, analysis = parse_claude_reply(raw)
                h = ar_names["home"] if ar_names else home
                a = ar_names["away"] if ar_names else away
                l = ar_names["league"] if ar_names else league_line
                msg = f"⚽️ بدأت المباراة\n🏆 {l}\n{h} 🆚 {a}\n"
                if analysis:
                    label = "🤖 المحرك 2 (مباشر)" if enriched else "🤖 التوقع"
                    msg += f"\n{label}:\n{analysis}"
                send_telegram(msg)
                entry = {"score": score, "status": status, "minute": minute,
                         "home": home, "away": away, "league": league_line,
                         "home_logo": (teams.get("home") or {}).get("logo", ""),
                         "away_logo": (teams.get("away") or {}).get("logo", ""),
                         "league_logo": league.get("logo", ""), "seen": seen_now}
                if ar_names:
                    entry["ar"] = ar_names
                if enriched and analysis:
                    entry["pulse"] = analysis
                state[fid] = entry
                continue
            if prev is None:
                continue

            ar_names = prev.get("ar")
            h = ar_names["home"] if ar_names else home
            a = ar_names["away"] if ar_names else away
            l = ar_names["league"] if ar_names else league_line

            # هدف — تنبيه فوري مع تحليل المحرك 2 الكامل
            if score != prev.get("score") and status in LIVE_STATUSES:
                raw, enriched = analyze_match(
                    f"تحديث مباراة حية: {home} ضد {away} — {league_line}. "
                    f"النتيجة الآن {score} بعد هدف جديد، الدقيقة {minute}. "
                    f"هل يتغير توقعك؟ أعطني قراءة الموقف والتوقع النهائي.",
                    league, fid, live_budget, watch,
                )
                ar_new, analysis = parse_claude_reply(raw)
                if ar_new:
                    ar_names = ar_new
                    prev["ar"] = ar_new
                    h, a = ar_new["home"], ar_new["away"]
                    l = ar_new["league"]
                msg = f"🚨 هدف!\n🏆 {l}\n{h} {gh} - {ga} {a} (د{minute})\n"
                if analysis:
                    label = "🤖 المحرك 2 (مباشر)" if enriched else "🤖 قراءة المباراة الآن"
                    msg += f"\n{label}:\n{analysis}"
                send_telegram(msg)
                if enriched and analysis:
                    prev["pulse"] = analysis
                sig = live_signature(fid)
                if sig:
                    prev["sig"] = sig
                prev.update({"score": score, "status": status, "minute": minute,
                         "seen": seen_now})
                continue

            # نهاية المباراة
            if status in FINAL_STATUSES and prev.get("status") not in FINAL_STATUSES:
                send_telegram(f"🏁 انتهت المباراة\n🏆 {l}\n{h} {gh} - {ga} {a}")
                wl_entry = (wl_data.get("matches") or {}).get(fid)
                if wl_entry is not None and not wl_entry.get("result"):
                    wl_entry["result"] = f"{gh}-{ga}"
                    wl_dirty = True
                prev.update({"score": score, "status": status, "minute": minute,
                         "seen": seen_now})
                continue

            # لا حدث — نبضة مشروطة بتحرك الأرقام فقط (توفير نداءات Claude)
            if status in PULSE_STATUSES:
                sig_now = live_signature(fid)
                base = prev.get("sig")
                if sig_now and (not base or significant_delta(base, sig_now)) \
                        and pulses["used"] < MAX_PULSE_PER_RUN:
                    pulses["used"] += 1
                    alert = live_pulse(fid, home, away, league_line,
                                       score, minute, prev.get("pulse") or "")
                    if alert:
                        send_telegram(f"👁 عين المحرك 2 — {h} {gh} - {ga} {a} "
                                      f"(د{minute})\n\n{alert}")
                        prev["pulse"] = alert
                    prev["sig"] = sig_now
            prev.update({"score": score, "status": status, "minute": minute,
                         "seen": seen_now})

        if not keep:
            break
    return wl_dirty


# ================== الرادار: جمع الأرقام وحساب الخطر ==================
_RADAR_STAT_KEYS = {
    "Shots on Goal": "sog", "Total Shots": "shots", "Corner Kicks": "cor",
    "Yellow Cards": "yc", "Red Cards": "rc", "Goalkeeper Saves": "sv",
    "Ball Possession": "poss",
}


def radar_snapshot(fid: str, minute: int, gh: int, ga: int) -> dict:
    """نداء واحد: إحصائيات المباراة الحية → لقطة رقمية مضغوطة للرادار."""
    snap = {"minute": minute, "gh": gh, "ga": ga,
            "h": {}, "a": {}}
    sides = api_football(f"fixtures/statistics?fixture={fid}")
    for idx, side in enumerate(sides[:2]):
        box = snap["h"] if idx == 0 else snap["a"]
        for s in (side.get("statistics") or []):
            key = _RADAR_STAT_KEYS.get(s.get("type"))
            if not key:
                continue
            v = s.get("value")
            if isinstance(v, str):
                v = v.replace("%", "").strip()
            try:
                box[key] = int(float(v))
            except (TypeError, ValueError):
                box[key] = 0
    return snap


def _radar_outcome(gh: int, ga: int) -> str:
    return "home" if gh > ga else ("away" if ga > gh else "draw")


def _radar_delta(snaps: list, side: str, key: str) -> int:
    """فرق آخر لقطتين لمقياس معين — صفر إن لم تتوفر لقطتان."""
    if len(snaps) < 2:
        return 0
    last = (snaps[-1].get(side) or {}).get(key) or 0
    prev = (snaps[-2].get(side) or {}).get(key) or 0
    return max(0, last - prev)


def danger_score(pick: str, snaps: list, minute: int, gh: int, ga: int) -> dict:
    """درجة الخطر (0-100) على توقع المحرك 2 — رياضيات شفافة، صفر Claude.

    ثلاثة مكونات: لوحة النتيجة مقابل التوقع (موزونة بالدقيقة)، زخم الفريق
    الذي يهدد التوقع (فرق آخر لقطتين)، وإشارات الضغط (حارس محاصر، نقص عددي).
    الأرقام أوزان بداية صادقة — لوحة تقييم الرادار الصباحية ستعايرها بالوقائع."""
    score = 0
    factors = []
    out_now = _radar_outcome(gh, ga)

    # 1) لوحة النتيجة (حتى 70): توقع خاسر الآن يشتد خطره كلما اقترب الختام —
    #    خاسر متأخراً (د84+) أحمر من اللوحة وحدها، لكن 0-0 في د5 ليس خطراً
    #    (ملاحظة المالك 2026-08-02: البداية المبكرة كانت تصنع إنذارات ضجيج)
    if pick and out_now != pick:
        score += min(70, 15 + int(minute * 0.60))
        factors.append(f"النتيجة الحالية {gh}-{ga} تُسقط التوقع (د{minute})")
    elif pick and pick != "draw" and abs(gh - ga) == 1:
        score += 15
        factors.append("تقدم هش بفارق هدف واحد")
    elif pick == "draw" and minute >= 60:
        score += 10
        factors.append("التعادل صامد لكن أي هدف يقلبه")

    # 2) زخم الفريق المهدِّد (حتى ~26): من يضره هدفه القادم؟
    threat_sides = {"home": ["a"], "away": ["h"], "draw": ["h", "a"]}.get(pick, [])
    threat_names = {"h": "المضيف", "a": "الضيف"}
    best = 0
    best_factors = []
    for side in threat_sides:
        pts, fs = 0, []
        if _radar_delta(snaps, side, "sog") >= 2:
            pts += 12
            fs.append(f"موجة تسديد على المرمى من {threat_names[side]}")
        if _radar_delta(snaps, side, "cor") >= 2:
            pts += 8
            fs.append(f"موجة ركنيات لصالح {threat_names[side]}")
        if _radar_delta(snaps, side, "shots") >= 3:
            pts += 6
            fs.append(f"ضغط هجومي متصاعد من {threat_names[side]}")
        if pts > best:
            best, best_factors = pts, fs
    score += best
    factors += best_factors

    # 3) إشارات الضغط (حتى 18): حارس الطرف المُختار تحت الحصار + النقص العددي
    picked_side = {"home": "h", "away": "a"}.get(pick)
    if picked_side:
        if _radar_delta(snaps, picked_side, "sv") >= 2:
            score += 8
            factors.append("حارس الطرف المُختار تحت الحصار (تصديات متتالية)")
        if snaps and ((snaps[-1].get(picked_side) or {}).get("rc") or 0) > 0:
            score += 10
            factors.append("نقص عددي ضد الطرف المُختار (بطاقة حمراء)")

    score = max(0, min(100, score))
    level = "red" if score >= RADAR_RED else ("amber" if score >= RADAR_AMBER else "green")
    return {"score": score, "level": level, "factors": factors[:4]}


def danger_series(pick: str, snaps: list) -> list:
    """منحنى تصاعد الخطر: درجة الخطر عند كل لقطة محفوظة (طلب المالك
    2026-08-16 — «أين أرى هذا وهو يتصاعد؟»).

    البطاقة كانت تعرض الدرجة الحالية فقط، فلا يُرى الفرق بين خطر 70 هابط
    من 90 وخطر 70 صاعد من 40 — وهما حالتان متعاكستان تماماً.

    يُشتق من اللقطات المخزّنة نفسها في كل دورة (لا يُخزَّن مستقلاً): فيبقى
    مطابقاً لها حتماً حتى حين يستبدل المسار السريع آخر لقطة. صفر نداءات،
    وn ≤ 12 فالكلفة الحسابية مهملة.
    """
    out = []
    for i, s in enumerate(snaps):
        try:
            out.append(danger_score(pick, snaps[:i + 1], s.get("minute") or 0,
                                    s.get("gh") or 0, s.get("ga") or 0)["score"])
        except Exception:
            out.append(0)
    return out


def danger_score_xg(pick: str, snaps: list, minute: int, gh: int, ga: int) -> dict:
    """🔬 درجة الخطر البديلة — نفس لوحة النتائج، لكن الزخم بـxG بدل عدّ التسديدات.

    ⛔ **تُحسب وتُسجَّل ولا تُقرأ في أي قرار تنبيه.** التنبيهات تبقى على
    danger_score() وحدها. السبب: لو أثّرت فوراً لفقدنا القدرة على معرفة هل
    حسّنت أم أضرّت، وصارت كل أرقامنا بلا معنى (عقيدة الظل أولاً، البند 7).

    لماذا هذه التجربة أصلاً — المبرر المقاس من radar_log.json (591 إنذاراً):
    الإنذارات المصحوبة بإشارات الزخم أسوأ دقةً من لوحة النتائج وحدها
    (أحمر 41/54 = 75.9% مقابل 278/301 = 92.4%؛ كهرماني 5/28 = 17.9% مقابل
    82/208 = 39.4%). السبب الجذري: تسديدة من 30 متراً وتسديدة من داخل
    المرمى الصغير كلتاهما «تسديدة على المرمى» في عدّادنا. xG يفرّق بينهما.

    المكوّن 1 (لوحة النتائج) و3 (إشارات الضغط) منسوخان كما هما من
    danger_score لأنهما الجزء المثبت — المتغيّر الوحيد هو المكوّن 2.
    """
    score = 0
    factors = []
    out_now = _radar_outcome(gh, ga)

    # 1) لوحة النتيجة (حتى 70) — مطابق لـdanger_score حرفياً (الجزء المثبت)
    if pick and out_now != pick:
        score += min(70, 15 + int(minute * 0.60))
        factors.append(f"النتيجة الحالية {gh}-{ga} تُسقط التوقع (د{minute})")
    elif pick and pick != "draw" and abs(gh - ga) == 1:
        score += 15
        factors.append("تقدم هش بفارق هدف واحد")
    elif pick == "draw" and minute >= 60:
        score += 10
        factors.append("التعادل صامد لكن أي هدف يقلبه")

    # 2) طبقة الزخم مستبدلة: فارق xG الحي لصالح الطرف الذي يهدد التوقع.
    #    الفارق (لا المجموع) هو المقياس: 2.1 مقابل 0.3 خطرٌ على من اختار
    #    الطرف الثاني، بينما 2.1 مقابل 2.0 مباراة مفتوحة لا تهديد موجّه.
    xh, xa = _last_xg(snaps)
    if xh is not None:
        threat_gap = {"home": xa - xh, "away": xh - xa,
                      "draw": abs(xh - xa)}.get(pick)
        if threat_gap is not None and threat_gap > 0:
            pts = min(26, int(threat_gap * 20))
            if pts:
                score += pts
                side = ("الضيف" if pick == "home" else
                        "المضيف" if pick == "away" else
                        ("المضيف" if xh > xa else "الضيف"))
                factors.append(
                    f"أفضلية xG لصالح {side} ({xh:.2f}-{xa:.2f})")

    # 3) إشارات الضغط (حتى 18) — مطابق لـdanger_score حرفياً
    picked_side = {"home": "h", "away": "a"}.get(pick)
    if picked_side:
        if _radar_delta(snaps, picked_side, "sv") >= 2:
            score += 8
            factors.append("حارس الطرف المُختار تحت الحصار (تصديات متتالية)")
        if snaps and ((snaps[-1].get(picked_side) or {}).get("rc") or 0) > 0:
            score += 10
            factors.append("نقص عددي ضد الطرف المُختار (بطاقة حمراء)")

    score = max(0, min(100, score))
    level = "red" if score >= RADAR_RED else ("amber" if score >= RADAR_AMBER else "green")
    # has_xg يفصل «حُسبت بـxG» عن «حُسبت بلا xG»: المقارنة الصباحية تُجرى على
    # المباريات التي توفر لها xG فقط — وإلا قارنّا الدرجة الحالية بنسخة من
    # نفسها وسمّينا التطابق نجاحاً.
    return {"score": score, "level": level, "factors": factors[:4],
            "has_xg": xh is not None}


def _last_xg(snaps: list) -> tuple:
    """آخر قيمتَي xG موثقتين في اللقطات — (None, None) حين لا xG إطلاقاً.

    غياب xG ليس عطلاً: مباراة خارج تغطية الباقة تمرّ ببساطة بلا الطبقة
    الثانية، فتصير درجتها لوحةَ نتائج وضغطاً فقط. لا كسر، ولا ادعاء بلا بيانات.
    """
    for s in reversed(snaps or []):
        xh, xa = s.get("xg_h"), s.get("xg_a")
        if xh is not None and xa is not None:
            return float(xh), float(xa)
    return None, None


def merge_fast_snap(snaps: list, snap: dict) -> list:
    """يحافظ على تباعد ~10 دقائق بين اللقطات: لقطة أحدث داخل نفس النافذة
    تستبدل الأخيرة (فيبقى الزخم مقاساً على ~10 دقائق حقيقية لا على 90 ثانية)،
    ولقطة بعد فجوة كافية تُلحق كالمعتاد."""
    if snaps and (snap.get("minute", 0) - (snaps[-1].get("minute") or 0)) < RADAR_SNAP_MIN_GAP_MIN:
        return snaps[:-1] + [snap]
    return (snaps + [snap])[-RADAR_SNAPS_KEEP:]


def _radar_trend(snaps: list, dscores: list = None) -> dict:
    return {
        "min": [s.get("minute", 0) for s in snaps],
        # 📈 منحنى تصاعد الخطر (طلب المالك 2026-08-16)
        "danger": list(dscores or []),
        "h_sog": [(s.get("h") or {}).get("sog", 0) for s in snaps],
        "a_sog": [(s.get("a") or {}).get("sog", 0) for s in snaps],
        "h_cor": [(s.get("h") or {}).get("cor", 0) for s in snaps],
        "a_cor": [(s.get("a") or {}).get("cor", 0) for s in snaps],
    }


def radar_live_payload(state: dict) -> dict:
    """لقطة الرادار الحية كاملة — نفس شكل بطاقات data.json ليعاد استخدام الرسم."""
    matches = []
    for fid, e in state.items():
        if not isinstance(e, dict) or e.get("status") not in LIVE_STATUSES:
            continue
        r = e.get("radar") or {}
        if r.get("score") is None:
            continue
        ar = e.get("ar") or {}
        matches.append({
            "fid": fid,
            "home": ar.get("home") or e.get("home", "?"),
            "away": ar.get("away") or e.get("away", "?"),
            "home_en": e.get("home", "?"), "away_en": e.get("away", "?"),
            "league": ar.get("league") or e.get("league", ""),
            "home_logo": e.get("home_logo", ""), "away_logo": e.get("away_logo", ""),
            "score": e.get("score", "0-0"), "minute": e.get("minute", 0),
            "status": e.get("status", ""), "seen": e.get("seen", ""),
            "radar": {"score": r.get("score"), "level": r.get("level"),
                      "factors": r.get("factors") or [], "pick": r.get("pick"),
                      "confidence": r.get("confidence"),
                      "drama": r.get("drama"), "alerted": r.get("alerted"),
                      "trend": _radar_trend(r.get("snaps") or [],
                                            r.get("dscores") or [])},
        })
    # النتائج السريعة لكل المباريات الحية (لا الرادار فقط) — القائمة الحية
    # وغرفة العمليات تقرآنها أيضاً فيصل الهدف خلال ~90 ثانية لكل الشاشات
    scores = {}
    for fid, e in state.items():
        if isinstance(e, dict) and e.get("status") in LIVE_STATUSES:
            scores[fid] = {"score": e.get("score", "0-0"),
                           "minute": e.get("minute", 0),
                           "status": e.get("status", ""),
                           "seen": e.get("seen", "")}
    return {"updated": datetime.now(timezone.utc).isoformat(),
            "matches": matches, "scores": scores}


def publish_radar_live(state: dict) -> bool:
    """ينشر لقطة الرادار إلى فرع radar-live عبر GitHub API — بلا commit محلي
    وبلا إعادة بناء Pages (اللوحة تقرأ raw مباشرة). فشله صامت تماماً:
    النشر السريع رفاهية، لا يجوز أن يوقف المراقبة أو يفشّل التشغيلة."""
    if not GH_TOKEN:
        return False
    try:
        import base64
        api = f"https://api.github.com/repos/{GH_REPO}"
        hdrs = {"Authorization": f"Bearer {GH_TOKEN}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"}
        sha = None
        r = requests.get(f"{api}/contents/{RADAR_LIVE_PATH}?ref={RADAR_LIVE_BRANCH}",
                         headers=hdrs, timeout=15)
        if r.status_code == 200:
            sha = (r.json() or {}).get("sha")
        elif r.status_code == 404:
            # الفرع قد لا يكون موجوداً بعد — يُنشأ مرة واحدة من رأس main
            mr = requests.get(f"{api}/git/ref/heads/main", headers=hdrs, timeout=15)
            if mr.status_code == 200:
                requests.post(f"{api}/git/refs", headers=hdrs, timeout=15,
                              json={"ref": f"refs/heads/{RADAR_LIVE_BRANCH}",
                                    "sha": ((mr.json() or {}).get("object") or {}).get("sha")})
        payload = json.dumps(radar_live_payload(state), ensure_ascii=False)
        body = {"message": "radar live [skip ci]",
                "content": base64.b64encode(payload.encode("utf-8")).decode("ascii"),
                "branch": RADAR_LIVE_BRANCH}
        if sha:
            body["sha"] = sha
        put = requests.put(f"{api}/contents/{RADAR_LIVE_PATH}",
                           headers=hdrs, json=body, timeout=20)
        return put.status_code in (200, 201)
    except Exception as e:
        print("نشر الرادار الحي: فشل صامت:", e)
        return False


def maybe_red_warning_alert(fid: str, e: dict, verdict: dict, minute: int,
                            pick: str, conf, budget: dict, log: dict = None,
                            watch: set = None) -> bool:
    """🔴 إنذار الرادار الأحمر **المبكر** إلى تيليجرام (قرار المالك 2026-08-19).

    لماذا المبكر وحده: قياس 223 إنذاراً أحمر منذ انطلاق الموسم أظهر أن 196
    منها (88%) تُطلق د86+ بعامل واحد «النتيجة الحالية تُسقط التوقع (د90)» —
    أي أنها تصف لوحة نتائج مباراة منتهية، فدقتها 97% بلا قيمة استباقية.
    الشريحة ≤د85 (27 إنذاراً، دقة 85%، ~3/يوم) هي الوحيدة التي تصل والوقت
    ما زال يسمح بالتصرف. لذلك السقف الزمني ليس تحفظاً بل هو جوهر الفكرة.

    مرة واحدة لكل مباراة بعلم مستقل (warn_alerted) لا يحجب الدراما ولا يُحجب
    بها، وبسقف مستقل لكل تشغيلة. يُرجع True متى أُرسل فعلاً."""
    if not RED_WARN_ALERT or verdict.get("level") != "red":
        return False
    if (minute or 0) > RED_WARN_ALERT_MAX:
        return False
    # 🎛 بوابة التسعة + المفضلة (قرارا المالك 2026-08-24 صباحاً ومساءً):
    # رسالة الهاتف لدورياته أو لمباراة على قائمة تركيزه؛ الإنذار يبقى
    # مسجلاً ومقيساً للجميع على الشاشة
    if RED_WARN_MINE_ONLY and not radar_phone_worthy(fid, e, watch):
        return False
    radar = e.get("radar") or {}
    if radar.get("warn_alerted") or budget["used"] >= RED_WARN_ALERT_CAP_PER_RUN:
        return False
    # 🔒 بوابة ثانية دائمة (حادثة Drukpa 2026-08-21: تنبيهان لنفس المباراة
    # بفارق دقيقتين). العلم warn_alerted يعيش في state.json الذي **يُحفظ في
    # نهاية التشغيلة**، بينما radar_log.json يُحفظ داخلها — فتشغيلة أُجهضت
    # بعد الإرسال وقبل الحفظ تُسلّم الرسالة وتفقد علمها، فتعيدها التشغيلة
    # التالية. السجل هو السِّجل الدائم لما أُرسل فعلاً، فهو الحكم.
    own_log = log is None
    if own_log:
        log = load_json_file(RADAR_FILE, {}) or {}
    if any(str(w.get("fid")) == str(fid) and w.get("alerted")
           for w in (log.get("warnings") or [])):
        radar["warn_alerted"] = True   # أعِد بناء العلم المفقود في الذاكرة
        e["radar"] = radar
        return False
    budget["used"] += 1
    ar = e.get("ar") or {}
    h = ar.get("home") or e.get("home", "?")
    a = ar.get("away") or e.get("away", "?")
    threatened = h if pick == "home" else (a if pick == "away" else "التعادل")
    trial = " (🧪 تجريبي — قيد المعايرة)" if RADAR_ALERT_TRIAL else ""
    send_telegram(
        f"🔴 إنذار الرادار{trial} — د{minute}\n"
        f"{h} {e.get('score') or '?'} {a}\n"
        f"التوقع المهدَّد: {threatened}"
        + (f" ({conf}%)" if conf else "")
        + f" — درجة الخطر {verdict.get('score')}/100\n"
        f"الأسباب: {'، '.join(verdict.get('factors') or []) or 'ضغط متصاعد'}"
    )
    radar["warn_alerted"] = True
    e["radar"] = radar
    # 📌 قاعدة 2026-08-21 (تكرار Drukpa ثم Cracovia): سجل «ما أُرسل» يُكتب
    # **لحظة الإرسال** لا في نهاية الجولة — تشغيلة تُجهض بعد التسليم كانت
    # تفقد علمها وصفّها معاً فتعيد التالية الإرسال. في المسار السريع (كان
    # لا يكتب صفاً إطلاقاً) نرفع الصف ونحفظ الملف هنا فوراً؛ في المسح تُرفع
    # العلامة على النسخة الحية ويتكفل المسح بالحفظ الفوري بعد ندائه مباشرة.
    _upsert_warn_row(log, fid, e, verdict, minute, pick, conf)
    if own_log:
        _flush_radar_log(log)
    return True


def _upsert_warn_row(log: dict, fid: str, e: dict, verdict: dict,
                     minute: int, pick: str, conf) -> None:
    """يرفع صف الإنذار الموسوم alerted إلى السجل الحي (ينشئه إن لم يوجد).
    نفس مخطط صفوف المسح حرفياً حتى يقيَّم صباحاً كأي صف آخر."""
    log.setdefault("warnings", [])
    w = next((w for w in log["warnings"] if str(w.get("fid")) == str(fid)), None)
    sent = ({"sent_level": verdict.get("level"),
             "sent_score": verdict.get("score"),
             "sent_minute": minute,
             "sent_factors": verdict.get("factors") or []}
            if SENT_SNAPSHOT else {})
    if w is None:
        log["warnings"].append({
            "fid": str(fid),
            "date": e.get("date")
                    or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "home": e.get("home"), "away": e.get("away"),
            "league": e.get("league"), "top": radar_is_top(e),
            "mine": radar_is_mine(e),
            "pick": pick, "confidence": conf,
            "level": verdict.get("level"), "score": verdict.get("score"),
            "minute": minute, "factors": verdict.get("factors") or [],
            "alerted": True, "alert_minute": minute,
            "score_xg": None, "level_xg": None,
            **sent,
        })
    else:
        w["alerted"] = True
        w["alert_minute"] = minute
        # 📸 REC-015: قراءة لحظة الإرسال حمراء بالبوابة، والصف قد يخزن ذروة
        # مسح كهرمانية أقدم — نرقّيه لقراءة الإرسال (نفس شرط المسح) ونجمّد
        # حقول sent_* فلا تمحوها أي إعادة كتابة لاحقة
        if (verdict.get("score") or 0) > (w.get("score") or 0):
            w.update({"level": verdict.get("level"),
                      "score": verdict.get("score"),
                      "minute": minute,
                      "factors": verdict.get("factors") or []})
        if SENT_SNAPSHOT and "sent_level" not in w:
            w.update(sent)


def _flush_radar_log(log: dict) -> None:
    """كتابة فورية للسجل — تُستدعى لحظة إرسال أي تنبيه (قاعدة 2026-08-21).
    فشلها لا يمنع التنبيه ولا يكسر التشغيلة."""
    try:
        RADAR_FILE.write_text(
            json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as exc:
        print("تعذر الحفظ الفوري لسجل الرادار:", exc)


def radar_fast_watch(state: dict, watch: set, deadline: float,
                     alert_budget: dict = None, warn_budget: dict = None) -> int:
    """المسار السريع (طلب المالك 2026-08-01): يحدّث أخطر مباريات الرادار كل
    ~90 ثانية فيما تبقى من ميزانية التشغيلة وينشر كل جولة إلى radar-live.
    ليالي قائمة التركيز يستهلك رصدها السريع الميزانية أولاً — لا ازدواج،
    فمباريات القائمة هي نفسها أولوية الرادار وتتحدث عبر الدورة العادية."""
    v2_pending = (load_json_file(RADAR_PREDICTIONS_FILE, {}) or {}).get("pending") or {}
    if alert_budget is None:
        alert_budget = {"used": 0}
    if warn_budget is None:   # 🔧 توحيد السقف (جلسة 2026-08-24): يُمرَّر من main
        warn_budget = {"used": 0}
    published = 0
    while time.monotonic() < deadline:
        # ما دامت هناك أي مباراة حية نواصل — النتائج السريعة لكل المباريات
        # (بلاغ المالك 2026-08-02: هدف تأخر ~10 دقائق على القائمة الحية)
        if not any(e.get("status") in LIVE_STATUSES for e in state.values()
                   if isinstance(e, dict)):
            break
        cands = []
        for fid, e in state.items():
            if e.get("status") in LIVE_STATUSES and (e.get("radar") or {}).get("score") is not None:
                cands.append(((0 if fid in watch else 1,
                               -((e["radar"].get("score")) or 0)), fid))
        targets = [f for _, f in sorted(cands)][:RADAR_FAST_CAP]
        time.sleep(FOCUS_SWEEP_SECONDS)
        # نداء واحد يجلب كل المباريات الحية عالمياً: يحدّث نتيجة/دقيقة/حالة
        # كل مباراة معروفة في الذاكرة (المستبعدات ليست في الذاكرة أصلاً)
        try:
            fixtures = api_football("fixtures?live=all")
        except Exception as e:
            print("الرادار السريع: فشل السحب:", e)
            continue
        seen_now = datetime.now(timezone.utc).isoformat()
        for fx in fixtures:
            fid = str((fx.get("fixture") or {}).get("id"))
            e = state.get(fid)
            if not e:
                continue
            st = (((fx.get("fixture") or {}).get("status")) or {}).get("short") or ""
            minute = (((fx.get("fixture") or {}).get("status")) or {}).get("elapsed") or 0
            goals = fx.get("goals") or {}
            gh = goals.get("home") or 0
            ga = goals.get("away") or 0
            e.update({"status": st, "minute": minute, "score": f"{gh}-{ga}",
                      "seen": seen_now})
        # اللقطات الإحصائية العميقة لأخطر مباريات الرادار فقط (ترشيد نداءات)
        for fid in targets:
            e = state.get(fid)
            if not e or e.get("status") not in LIVE_STATUSES:
                continue
            try:
                gh, ga = (int(x) for x in (e.get("score") or "0-0").split("-")[:2])
            except ValueError:
                continue
            minute = e.get("minute") or 0
            try:
                snap = radar_snapshot(fid, minute, gh, ga)
            except Exception as ex:
                print("الرادار السريع: فشل لقطة", fid, ex)
                continue
            radar = e.get("radar") or {}
            snaps = merge_fast_snap(radar.get("snaps") or [], snap)
            p = v2_pending.get(fid) or {}
            verdict = danger_score(p.get("pick") or radar.get("pick"),
                                   snaps, minute, gh, ga)
            d = drama_signal(snaps, gh, ga)
            radar.update({"snaps": snaps, "score": verdict["score"],
                          "dscores": danger_series(
                              p.get("pick") or radar.get("pick"), snaps),
                          "level": verdict["level"], "factors": verdict["factors"],
                          # 🎛 REC-010: العلامة تُحدَّث في المسار السريع أيضاً
                          "top": bool(p.get("top", radar.get("top"))),
                          "mine": p.get("mine", radar.get("mine")),
                          "drama": {"signal": (d or {}).get("signal", 0),
                                    "side": (d or {}).get("side"),
                                    "ready": bool(d and d["dominant"]
                                                  and d["signal"] >= RADAR_ALERT_SIGNAL_MIN)}})
            e["radar"] = radar
            # 🚨 عقل S3 في المسار السريع: التنبيه يصل خلال ~90 ثانية من الإشارة
            maybe_radar_alert(fid, e, alert_budget, watch=watch)
            # 🟥 REC-009: مسار الطرد المستقل — من أي دقيقة
            maybe_red_alert(fid, e, alert_budget, watch=watch)
            # 🔴 الإنذار الأحمر المبكر — ميزانيته مستقلة عن الدراما
            maybe_red_warning_alert(fid, e, verdict, minute,
                                    p.get("pick") or radar.get("pick"),
                                    p.get("confidence") or radar.get("confidence"),
                                    warn_budget, watch=watch)
        if publish_radar_live(state):
            published += 1
    return published


def _side_momentum(snaps: list, side: str, opp: str):
    """نقاط زخم طرف واحد في اللحظات الأخيرة + أسبابها بالكلمات."""
    pts, reasons = 0, []
    if _radar_delta(snaps, side, "sog") >= 2:
        pts += 30
        reasons.append("موجة تسديد على المرمى")
    if _radar_delta(snaps, side, "cor") >= 2:
        pts += 20
        reasons.append("موجة ركنيات")
    if _radar_delta(snaps, side, "shots") >= 3:
        pts += 15
        reasons.append("ضغط هجومي متصاعد")
    if snaps and ((snaps[-1].get(opp) or {}).get("rc") or 0) > 0:
        pts += 35
        reasons.append("نقص عددي عند الخصم (طرد)")
    if _radar_delta(snaps, opp, "sv") >= 2:
        pts += 20
        reasons.append("حارس الخصم تحت الحصار")
    return pts, reasons


def drama_signal(snaps: list, gh: int, ga: int):
    """إشارة الدراما الخام (0-95) بمعزل عن شرط الدقيقة — وقود قمع الاستباق
    على اللوحة: يظهر من يختمر ومن جاهز قبل أن يصل التنبيه نفسه."""
    margin = abs(gh - ga)
    if not snaps or margin > 2:
        return None
    if margin == 0:
        ph, rh = _side_momentum(snaps, "h", "a")
        pa, ra = _side_momentum(snaps, "a", "h")
        side, pts, reasons, other = ("home", ph, rh, pa) if ph >= pa else ("away", pa, ra, ph)
        # إصلاح REC-009: red كانت مثبّتة False نصاً في التعادل — تُحسب الآن
        # من لقطة خصم الطرف المهيمن كما في فرع التأخر
        o = "a" if side == "home" else "h"
        red = bool(((snaps[-1].get(o) or {}).get("rc") or 0) > 0)
        # في التعادل نطلب هيمنة واضحة لطرف واحد — لا دراما على شد وجذب متكافئ
        return {"side": side, "signal": max(0, min(95, pts)), "reasons": reasons,
                "margin": 0, "red": red,
                "dominant": (pts - other) >= RADAR_ALERT_DRAW_GAP}
    side = "home" if gh < ga else "away"
    s, o = ("h", "a") if side == "home" else ("a", "h")
    pts, reasons = _side_momentum(snaps, s, o)
    if margin == 2:
        pts -= 15   # العودة من هدفين أصعب — نطلب إشارة أقوى
    red = bool(((snaps[-1].get(o) or {}).get("rc") or 0) > 0)
    return {"side": side, "signal": max(0, min(95, pts)), "reasons": reasons,
            "margin": margin, "red": red, "dominant": True}


def evaluate_comeback(snaps: list, minute: int, gh: int, ga: int):
    """عقل S3 للحظات الحاسمة (سيناريوهات المالك 2026-08-01): من الدقيقة 75،
    هل تقول الأرقام إن المتأخر سيسجل / يتعادل / يقلب النتيجة؟ وفي التعادل:
    من يضغط لخطف الفوز؟ يرجع الادعاء والإشارة والأسباب — أو None (صمت)."""
    if minute < RADAR_ALERT_MIN or minute > RADAR_ALERT_MAX:
        return None   # قبل 75 مبكر (شرط المالك)، بعد 85 متأخر بلا قيمة (معايرة 2026-08-02)
    d = drama_signal(snaps, gh, ga)
    if not d or not d["dominant"] or d["signal"] < RADAR_ALERT_SIGNAL_MIN:
        return None
    if d["margin"] == 0:
        # التعادل أثبت أنه الأكثر ضجيجاً — عتبة أشد وهيمنة أوضح
        if d["signal"] < RADAR_ALERT_DRAW_MIN:
            return None
        key, claim = "next_goal", "الهدف القادم — وربما خطف الفوز"
    elif d["margin"] == 1 and (d["red"] or d["signal"] >= RADAR_ALERT_FLIP_MIN):
        key, claim = "flip", "تعادل قريب — وقلب النتيجة وارد"
    elif d["margin"] == 1:
        key, claim = "equalizer", "هدف التعادل قادم"
    else:
        key, claim = "goal", "هدف يقلّص الفارق قادم"
    return {"key": key, "side": d["side"], "signal": d["signal"],
            "reasons": d["reasons"], "claim": claim}


def radar_claim_lists() -> tuple:
    """قائمتا قاعدة الإيقاف (REC-005) كما كتبهما التقييم الصباحي في
    radar_log.json — (silenced, proven). غيابهما = مجموعتان فارغتان (صفر تأثير)."""
    log = load_json_file(RADAR_FILE, {}) or {}
    return set(log.get("silenced") or []), set(log.get("proven") or [])


def maybe_radar_alert(fid: str, e: dict, budget: dict, log: dict = None,
                      watch: set = None) -> bool:
    """يرسل تنبيه الدراما مرة واحدة لكل مباراة (يُرقّى فقط لادعاء أقوى —
    مثال: تنبيه تعادل ثم طرد يرفعه لقلب نتيجة)، ويسجله في radar_log.json
    ليقيَّم صباحاً على النتيجة الحقيقية — عقل S3 له لوحة صدق خاصة به.

    ⚠️ log: النسخة الحية من السجل عند النداء من داخل radar_sweep — إلزامية
    هناك (سباق 2026-08-15: الجولة كانت تحمل نسختها في بدايتها، فكل تنبيه
    كُتب للقرص أثناءها دُهس بحفظها الختامي — تنبيها Mito وAlverca وصلا
    تيليجرام واختفيا من سجل القياس). كاتب واحد لكل ملف داخل الجولة."""
    try:
        gh, ga = (int(x) for x in (e.get("score") or "0-0").split("-")[:2])
    except ValueError:
        return False
    radar = e.get("radar") or {}
    verdict = evaluate_comeback(radar.get("snaps") or [], e.get("minute") or 0, gh, ga)
    if not verdict:
        return False
    prev = radar.get("alerted")
    if prev and _ALERT_RANK.get(verdict["key"], 0) <= _ALERT_RANK.get(prev, 0):
        return False
    # قاعدة الإيقاف (REC-005): نوع مُسكَت يُسجَّل للتقييم الصباحي بلا تيليجرام
    # (فيستمر قياسه ويستطيع الخروج من الصمت)، ونوع مُثبَت يفقد وسم 🧪.
    silenced_keys, proven_keys = (radar_claim_lists() if RADAR_ALERT_STOP_RULE
                                  else (set(), set()))
    silent = verdict["key"] in silenced_keys
    # 📵 بوابة التسعة + المفضلة (قرار المالك 2026-08-24 مساءً): خارجها
    # يُسجَّل التنبيه للقياس بوسم gated ولا يُرسل — كالإسكات تماماً
    gated = DRAMA_MINE_ONLY and not radar_phone_worthy(fid, e, watch)
    if not silent and not gated:
        if budget["used"] >= RADAR_ALERT_CAP_PER_RUN:
            return False
        budget["used"] += 1
        ar = e.get("ar") or {}
        h = ar.get("home") or e.get("home", "?")
        a = ar.get("away") or e.get("away", "?")
        target = h if verdict["side"] == "home" else a
        # وسم المرحلة التجريبية (قرار المالك 2026-08-02): يستلم التنبيه ويعلم أنه
        # قيد المعايرة — الوسم يُرفع فقط حين يثبت السجل الصباحي جدارة الادعاء
        trial = ("" if verdict["key"] in proven_keys
                 else (" (🧪 تجريبي — قيد المعايرة)" if RADAR_ALERT_TRIAL else ""))
        send_telegram(
            f"⚡🚨 تنبيه دراما{trial} — د{e.get('minute')}\n"
            f"{h} {gh} - {ga} {a}\n"
            f"التوقع: {verdict['claim']} لصالح {target} (إشارة {verdict['signal']}%)\n"
            f"الأسباب: " + "، ".join(verdict["reasons"])
        )
    radar["alerted"] = verdict["key"]
    e["radar"] = radar
    if log is None:   # نداء خارج الجولة (المسار السريع) — لا نسخة حية مشتركة
        log = load_json_file(RADAR_FILE, {}) or {}
    log.setdefault("alerts", []).append({
        "fid": fid, "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "minute": e.get("minute"), "score_at": f"{gh}-{ga}",
        "side": verdict["side"], "key": verdict["key"],
        "signal": verdict["signal"], "home": e.get("home"), "away": e.get("away"),
        "league": e.get("league"),
        # 🎛 REC-010: علامة دوريات المالك تُختم مع السجل عند الكتابة —
        # التقييم الصباحي يبني منها شريحة موازية بلا أي مطابقة أسماء
        "top": radar_is_top(e),
        "mine": radar_is_mine(e),
        # قاعدة الإيقاف (REC-005): وسم الإسكات يُحفظ مع التنبيه — تبويب الرادار
        # يعرف أنه لم يُرسل، والتقييم الصباحي يقيسه كالمعتاد
        "silenced": silent,
        "gated": gated or None,   # 📵 خارج التسعة/المفضلة — مُقاس غير مُرسل
        # حزمة الأدلة (طلب المالك 2026-08-02 — تحقق الجذر): آخر لقطات الأرقام
        # التي بُني عليها التنبيه تُحفظ معه، فأي خطأ مستقبلي يُشرَّح لأرقامه
        "evidence": (radar.get("snaps") or [])[-3:],
    })
    if RADAR_MAX_WARNINGS:   # 0 = بلا قص لقائمة الانتظار (المسح الشامل 2026-08-09)
        log["alerts"] = log["alerts"][-RADAR_MAX_WARNINGS:]
    RADAR_FILE.write_text(
        json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    return True


def evaluate_red_advantage(snaps: list, gh: int, ga: int):
    """🟥 REC-009: هل لدى أحد الطرفين بطاقة حمراء (rc في أحدث لقطة) بينما
    فريقه متعادل أو متقدم بهدف واحد؟ يرجع المستفيد (الخصم) أو None — من أي
    دقيقة بقرار المالك الصريح (2026-08-10): الطرد حدث خشن عالي المعلومة لا
    ضجيج زخم، فلا شرط د75 ولا موجات. المتأخر المطرود منه تغطيه ادعاءات
    الدراما القائمة، والمتقدم بهدفين+ طرده لا يصنع دراما."""
    if not snaps:
        return None
    last = snaps[-1]
    red_h = ((last.get("h") or {}).get("rc") or 0) > 0
    red_a = ((last.get("a") or {}).get("rc") or 0) > 0
    if red_h == red_a:
        return None   # لا طرد — أو طرد متبادل يلغي الأفضلية العددية
    margin = (gh - ga) if red_h else (ga - gh)   # فارق الفريق المطرود منه
    if margin not in (0, 1):
        return None
    return {"side": "away" if red_h else "home"}


def maybe_red_alert(fid: str, e: dict, budget: dict, log: dict = None,
                    watch: set = None) -> bool:
    """🟥 تنبيه الأفضلية العددية (REC-009) — مسار مستقل تماماً عن سلم ادعاءات
    الدراما: علمه الخاص red_alerted (لا يستخدم alerted حتى لا يحجب أحدهما
    الآخر)، مرة واحدة لكل مباراة، يُسجَّل بمفتاح red_advantage بنفس بنية
    التنبيهات القائمة ويُقيَّم صباحاً بعدّاده المستقل — وقاعدة الإيقاف
    REC-005 تحكمه تلقائياً كأي ادعاء.
    log: النسخة الحية من السجل عند النداء من داخل radar_sweep (سباق
    2026-08-15 — انظر maybe_radar_alert)."""
    if not RADAR_RED_FAST_PATH:
        return False
    radar = e.get("radar") or {}
    if radar.get("red_alerted"):
        return False
    try:
        gh, ga = (int(x) for x in (e.get("score") or "0-0").split("-")[:2])
    except ValueError:
        return False
    v = evaluate_red_advantage(radar.get("snaps") or [], gh, ga)
    if not v:
        return False
    silenced_keys, proven_keys = (radar_claim_lists() if RADAR_ALERT_STOP_RULE
                                  else (set(), set()))
    silent = "red_advantage" in silenced_keys
    gated = DRAMA_MINE_ONLY and not radar_phone_worthy(fid, e, watch)  # 📵
    if not silent and not gated:
        if budget["used"] >= RADAR_ALERT_CAP_PER_RUN:
            return False
        budget["used"] += 1
        ar = e.get("ar") or {}
        h = ar.get("home") or e.get("home", "?")
        a = ar.get("away") or e.get("away", "?")
        target = h if v["side"] == "home" else a
        carded = a if v["side"] == "home" else h
        trial = ("" if "red_advantage" in proven_keys
                 else (" (🧪 تجريبي — قيد المعايرة)" if RADAR_ALERT_TRIAL else ""))
        send_telegram(
            f"🟥 أفضلية عددية{trial} — د{e.get('minute')}\n"
            f"{h} {gh} - {ga} {a}\n"
            f"طرد في صفوف {carded} — الأفضلية لصالح {target}"
        )
    radar["red_alerted"] = True
    e["radar"] = radar
    if log is None:   # نداء خارج الجولة (المسار السريع) — لا نسخة حية مشتركة
        log = load_json_file(RADAR_FILE, {}) or {}
    log.setdefault("alerts", []).append({
        "fid": fid, "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "minute": e.get("minute"), "score_at": f"{gh}-{ga}",
        "side": v["side"], "key": "red_advantage",
        "home": e.get("home"), "away": e.get("away"),
        "league": e.get("league"),
        "top": radar_is_top(e),   # 🎛 REC-010
        "mine": radar_is_mine(e),
        "silenced": silent,
        "gated": gated or None,   # 📵 خارج التسعة/المفضلة — مُقاس غير مُرسل
        # حزمة الأدلة (نهج 2026-08-02): آخر لقطات الأرقام تُحفظ مع التنبيه
        "evidence": (radar.get("snaps") or [])[-3:],
    })
    if RADAR_MAX_WARNINGS:   # 0 = بلا قص لقائمة الانتظار (المسح الشامل 2026-08-09)
        log["alerts"] = log["alerts"][-RADAR_MAX_WARNINGS:]
    RADAR_FILE.write_text(
        json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    return True


def radar_live_xg(state: dict) -> tuple:
    """🔬 يجلب خريطة xG الحي للدورة كلها — نداء واحد، وفشله لا يكلّف شيئاً.

    الرصيد المتبقي يُخزَّن في state (`xg_live.remaining`) ويُعاد تمريره في
    الدورة التالية، فالكبح الذاتي ينجو بين التشغيلات — عملية monitor.py تموت
    كل 10 دقائق، ومتغيّر في الذاكرة كان سينسى الكبح فوراً.

    صفر تأثير: أي عطل (لا وحدة، لا مفتاح، رفض، شبكة) يرجع خريطة فارغة،
    والرادار يكمل دورته بالضبط كما كان قبل هذه الطبقة.
    """
    if sportmonks_shadow is None:
        return {}, "الوحدة غير متاحة"
    box = state.setdefault("xg_live", {}) if isinstance(state, dict) else {}
    try:
        xg_map, remaining, note = sportmonks_shadow.live_xg_map(
            last_remaining=box.get("remaining"))
    except Exception as ex:                      # pragma: no cover - دفاعي
        return {}, f"عطل: {type(ex).__name__}"
    box["remaining"] = remaining
    box["note"] = note
    box["matches"] = len(xg_map)
    if note:
        print("🔬 xG الحي:", note)
    return xg_map, note


def xg_lookup(xg_map: dict, e: dict):
    """xG مباراة بعينها من خريطة الدورة — أسماء state.json لاتينية فتُطابَق مباشرة."""
    if not xg_map or sportmonks_shadow is None:
        return None
    try:
        return sportmonks_shadow.live_xg_for(xg_map, e.get("home"), e.get("away"))
    except Exception:                            # pragma: no cover - دفاعي
        return None


def radar_is_top(e: dict) -> bool:
    """🎛 هل هذه المباراة من دوريات المالك؟ — REC-010 (قرار المالك 2026-08-13).

    المصدر الوحيد: علامة `top` المحفوظة في كتلة رادار المباراة، وهي منسوخة
    من صف توقع المحرك 2 الذي اشتقها بالمعرف من TOP_LEAGUE_IDS
    (`league.get("id") in TOP_LEAGUE_IDS`) — نفس المنطق الذي يرتّب به
    select_radar_fixtures أولوية المباريات.

    ⛔ ممنوع قطعياً اشتقاق هذه العلامة بمطابقة اسم الدوري نصاً: ذلك بالضبط
    نمط "قائمة الحظر التي تفشل مفتوحة" الذي سبّب حادثة الدوريات النسائية
    (2026-08-01) — اسم لا يحمل الكلمة المفتاحية يمرّ بصمت. المعرف الرقمي
    لا يفشل مفتوحاً."""
    return bool((e.get("radar") or {}).get("top"))


def radar_phone_worthy(fid, e: dict, watch=None) -> bool:
    """📵 من يستحق رسالة هاتف من قنوات الرادار؟ (قرار المالك 2026-08-24):
    دورياته التسعة أو مباراة على قائمة تركيزه — الاثنان بالمعرف حصراً."""
    return radar_is_mine(e) or bool(watch and str(fid) in watch)


def radar_is_mine(e: dict) -> bool:
    """🎛 الشريحة الثالثة (قرار المالك 2026-08-22): دورياته التسعة حرفياً.
    نفس عقيدة radar_is_top سطراً بسطر: المصدر الوحيد علامة `mine` المنسوخة
    من صف توقع المحرك 2 (المشتقة بالمعرف من OWNER_LEAGUE_IDS) — ومطابقة
    اسم الدوري نصاً ممنوعة قطعياً بنفس درس WK-League."""
    return bool((e.get("radar") or {}).get("mine"))


def load_shed_active(state: dict) -> bool:
    """🛗 REC-018: هل الرصيد تحت عتبة التخفيف؟ يقرأ آخر قراءة سقف حقيقية
    (api_guard يخزنها من ترويسات كل رد). غياب القراءة = لا تخفيف — الحارس
    لا يخنق النظام على جهل."""
    if not LOAD_SHED:
        return False
    q = (state.get("api_quota") or {}) if isinstance(state, dict) else {}
    try:
        remaining, limit = int(q.get("remaining")), int(q.get("limit"))
    except (TypeError, ValueError):
        return False
    return limit > 0 and (remaining / limit) < LOAD_SHED_RATIO


def note_load_shed(state: dict) -> None:
    """يسجّل تفعيل اليوم (عدّاد أيام + آخر تفعيل) ويُنذر المالك مرة كل 6h."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    box = state.setdefault("load_shed", {"days": 0, "last": ""})
    if box.get("last") != today:
        box["days"] = int(box.get("days") or 0) + 1
        box["last"] = today
    try:
        api_guard.alert_once(
            "load_shed",
            "🛗 تخفيف حمولة API مفعّل\n"
            "الرصيد تحت 15% من السقف — الرادار يقتصر الآن على قائمة التركيز "
            "ودوريات الصدارة، والدوريات المغمورة تتوقف إحصاءاتها مؤقتاً حتى "
            "يتصفّر الرصيد منتصف الليل UTC.\n"
            "دورياتك والتقييم الصباحي والحراس لا يُمسون.")
    except Exception as exc:
        print("تعذر إنذار التخفيف:", exc)


def select_radar_fixtures(state: dict, v2_pending: dict, watch: set) -> list:
    """اختيار مباريات الرادار تحت السقف: قائمة التركيز أولاً، ثم الدوريات
    الكبرى، ثم الأعلى ثقة — الأهم للمالك لا يُزاحم أبداً.
    🛗 وتحت عتبة REC-018 يُسقط المغمور كلياً (أول درجات السلّم)."""
    shed = load_shed_active(state)
    if shed:
        note_load_shed(state)
        print("🛗 تخفيف الحمولة: رادار قائمة التركيز ودوريات الصدارة فقط")
    cands = []
    for fid, e in state.items():
        if e.get("status") not in LIVE_STATUSES:
            continue
        p = v2_pending.get(fid)
        if not p:
            continue
        if shed and fid not in watch and not p.get("top"):
            continue   # 🛗 المغمور يُضحى به أولاً — القائمة والصدارة محفوظتان
        rank = (0 if fid in watch else (1 if p.get("top") else 2),
                -(p.get("confidence") or 0))
        cands.append((rank, fid))
    return [fid for _, fid in sorted(cands)][:RADAR_STATS_CAP]


def radar_sweep(state: dict, watch: set, alert_budget: dict = None,
                warn_budget: dict = None) -> int:
    """دورة الرادار: لقطة أرقام + درجة خطر لكل مباراة حية عليها توقع، وتسجيل
    الإنذارات (كهرماني/أحمر) في radar_log.json ليقيَّم صدقها صباحاً.
    أي فشل لمباراة واحدة لا يوقف البقية — والفشل الكامل لا يوقف التشغيلة."""
    v2_pending = (load_json_file(RADAR_PREDICTIONS_FILE, {}) or {}).get("pending") or {}
    targets = select_radar_fixtures(state, v2_pending, watch)
    if not targets:
        return 0
    if alert_budget is None:
        alert_budget = {"used": 0}
    if warn_budget is None:   # 🔧 توحيد السقف (جلسة 2026-08-24): يُمرَّر من main
        warn_budget = {"used": 0}
    log = load_json_file(RADAR_FILE, {}) or {}
    log.setdefault("warnings", [])
    log.setdefault("resolved", [])
    log_dirty = False
    swept = 0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # 🔬 xG الحي (ظل): نداء واحد لكل الدورة قبل الحلقة، بكبح ذاتي على الرصيد.
    # فشله يترك الخريطة فارغة ولا يمس شيئاً — الرادار يعمل كما كان تماماً.
    xg_map, xg_note = radar_live_xg(state)
    xg_hits = 0          # كم من مبارياتنا وجد لها xG فعلاً — رقم التغطية الحقيقي
    for fid in targets:
        e = state[fid]
        try:
            gh, ga = (int(x) for x in (e.get("score") or "0-0").split("-")[:2])
        except ValueError:
            gh = ga = 0
        minute = e.get("minute") or 0
        try:
            snap = radar_snapshot(fid, minute, gh, ga)
        except Exception as ex:
            print("الرادار: فشل لقطة", fid, ex)
            continue
        radar = e.get("radar") or {}
        # 🔬 xG الحي يُختم على اللقطة قبل الدمج — حقل مستقل بجانب الإحصائيات
        xg = xg_lookup(xg_map, e)
        if xg:
            snap["xg_h"], snap["xg_a"] = xg
            xg_hits += 1
        snaps = merge_fast_snap(radar.get("snaps") or [], snap)
        p = v2_pending.get(fid) or {}
        verdict = danger_score(p.get("pick"), snaps, minute, gh, ga)
        # 🔬 الدرجة الموازية — تُحسب دائماً وتُسجَّل، ولا تدخل أي قرار تنبيه
        verdict_xg = danger_score_xg(p.get("pick"), snaps, minute, gh, ga)
        d = drama_signal(snaps, gh, ga)
        e["radar"] = {
            "snaps": snaps,
            "score": verdict["score"], "level": verdict["level"],
            # 📈 منحنى التصاعد: الدرجة عند كل لقطة — يُشتق من snaps فيطابقها دوماً
            "dscores": danger_series(p.get("pick"), snaps),
            "factors": verdict["factors"],
            "pick": p.get("pick"), "confidence": p.get("confidence"),
            # 🎛 REC-010: علامة دوريات المالك تُنسخ من صف التوقع (مشتقة
            # بالمعرف من TOP_LEAGUE_IDS) لتُختم بها سجلات الرادار عند الكتابة
            "top": bool(p.get("top", radar.get("top"))),
            "mine": p.get("mine", radar.get("mine")),
            # قمع الاستباق: الإشارة الخام + الجاهزية قبل شرط الدقيقة 75
            "drama": {"signal": (d or {}).get("signal", 0),
                      "side": (d or {}).get("side"),
                      "ready": bool(d and d["dominant"]
                                    and d["signal"] >= RADAR_ALERT_SIGNAL_MIN)},
            # علم "أُرسل التنبيه" يجب أن ينجو من إعادة البناء — وإلا تكرر التنبيه
            "alerted": radar.get("alerted"),
            # 🟥 REC-009: علم الطرد مستقل عن سلم الدراما — ينجو هو الآخر
            "red_alerted": radar.get("red_alerted"),
            # 🔴 علم الإنذار الأحمر المبكر — ينجو بدوره وإلا تكرر كل 10 دقائق
            "warn_alerted": radar.get("warn_alerted"),
        }
        swept += 1
        # 🚨 عقل S3: هل تتشكل دراما اللحظات الأخيرة؟ (د75+، مرة لكل مباراة)
        # ⚠️ تمرير النسخة الحية log إلزامي (سباق 2026-08-15): بدونه يكتب
        # التنبيه للقرص ثم يدهسه حفظ الجولة الختامي بنسخته القديمة
        sent_now = False
        if maybe_radar_alert(fid, e, alert_budget, log, watch=watch):
            log_dirty = sent_now = True
        # 🟥 REC-009: مسار الطرد المستقل — من أي دقيقة
        if maybe_red_alert(fid, e, alert_budget, log, watch=watch):
            log_dirty = sent_now = True
        # 🔴 الإنذار الأحمر المبكر (≤د85) — قرار المالك 2026-08-19.
        # يُحسب هنا لأن verdict جاهز، ويُوسم صفه أدناه بـalerted ليُقاس وحده.
        warn_sent = maybe_red_warning_alert(
            fid, e, verdict, minute, p.get("pick"), p.get("confidence"),
            warn_budget, log, watch=watch)
        # يُسجَّل الصف متى أنذرت **إحدى** الدرجتين، لا الحالية وحدها.
        # لولا ذلك لاستحال قياس الحالة التي تُجرى التجربة من أجلها أصلاً:
        # xG يرى خطراً لا تراه عدّادات الحجم (0.4 مقابل 2.8 بينما لوحة النتائج
        # مطمئنة). تسجيل الحالية وحدها كان سيقيس تنازلات xG فقط ويعمى عن مكاسبه.
        #
        # ⛔ ولا يلوّث ذلك اللوحة الحالية: صف درجته الحالية "green" لا يُحتسب
        # في red ولا amber داخل _radar_counts، فعدّادات المالك تبقى كما هي حرفياً.
        if verdict["level"] in ("amber", "red") or (
                verdict_xg["has_xg"] and verdict_xg["level"] in ("amber", "red")):
            w = next((w for w in log["warnings"] if str(w.get("fid")) == fid), None)
            if w is None:
                log["warnings"].append({
                    "fid": fid, "date": p.get("date") or today,
                    "home": e.get("home"), "away": e.get("away"),
                    "league": e.get("league"),
                    "top": radar_is_top(e),   # 🎛 REC-010
                    "mine": radar_is_mine(e),
                    "pick": p.get("pick"), "confidence": p.get("confidence"),
                    "level": verdict["level"], "score": verdict["score"],
                    "minute": minute, "factors": verdict["factors"],
                    # 🔴 وسم الشريحة المُرسَلة إلى تيليجرام — تُقاس وحدها صباحاً.
                    # ودقيقة الإرسال تُحفظ منفصلة عن minute: الأخيرة تُحدَّث
                    # لاحقاً كلما ارتفعت الدرجة (نقيس أقصى ما رآه الرادار)،
                    # فلو قرأنا البوابة منها بدت إنذارات د90 وقد أُرسلت ≤د85
                    # فعلاً — حقل يكذب على مراجعه لا يُصلَح بالشرح بل بحقل ثانٍ.
                    "alerted": warn_sent or None,
                    "alert_minute": minute if warn_sent else None,
                    # 🔬 الحقل الموازي — فارغ حين لا xG (غيابه لا يكسر شيئاً)
                    "score_xg": (verdict_xg["score"] if verdict_xg["has_xg"]
                                 else None),
                    "level_xg": (verdict_xg["level"] if verdict_xg["has_xg"]
                                 else None),
                })
                if RADAR_MAX_WARNINGS:   # 0 = بلا قص (المسح الشامل 2026-08-09)
                    log["warnings"] = log["warnings"][-RADAR_MAX_WARNINGS:]
                log_dirty = True
            else:
                if verdict["score"] > (w.get("score") or 0):
                    # يُرقّى الإنذار لأعلى درجة بلغها — نقيس أقصى ما رآه الرادار
                    w.update({"level": verdict["level"],
                              "score": verdict["score"],
                              "minute": minute, "factors": verdict["factors"]})
                if warn_sent:
                    w["alerted"] = True
                    w["alert_minute"] = minute   # دقيقة الإرسال لا دقيقة الذروة
                    log_dirty = True
                # 🔬 ذروة الدرجة الموازية تُتابَع **مستقلة**: الدرجتان تبلغان
                # ذروتيهما في لحظتين مختلفتين، وربطُ ترقية إحداهما بالأخرى كان
                # سيقيس الدرجة الموازية عند لحظة ذروة غيرها لا ذروتها هي.
                if verdict_xg["has_xg"] and \
                        verdict_xg["score"] > (w.get("score_xg") or 0):
                    w.update({"score_xg": verdict_xg["score"],
                              "level_xg": verdict_xg["level"]})
                    log_dirty = True
        # 📌 قاعدة 2026-08-21: أي تنبيه غادر إلى تيليجرام في هذه اللفة يُحفظ
        # سجله إلى القرص الآن — لا عند نهاية الجولة التي قد لا تأتي أبداً
        # (إجهاض الأمسيات المزدحمة هو ما كرّر إنذاري Drukpa وCracovia)
        if sent_now or warn_sent:
            _flush_radar_log(log)
    if log_dirty:
        RADAR_FILE.write_text(
            json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    # 🔬 رقم التغطية الحقيقي: كم من مباريات الرادار وجد لها xG. صفر متكرر هنا
    # يعني أن الباقة لا تغطي ما نراقبه — وهو الدرس المستفاد من عطل الجمع
    # الصباحي (90 معروضة، 0 مطابقة). يُطبع كل دورة كي لا يُكتشف متأخراً.
    if targets:
        box = state.get("xg_live") if isinstance(state, dict) else None
        if isinstance(box, dict):
            box["radar_hits"] = xg_hits
            box["radar_targets"] = len(targets)
        print(f"🔬 xG الحي: {xg_hits} من {len(targets)} مباراة رادار لها xG")
    return swept


def parse_claude_reply(text: str):
    """يفصل سطر الأسماء العربية عن نص التحليل. يرجع (dict أو None, التحليل)."""
    names = None
    body = []
    for line in text.splitlines():
        s = line.strip()
        if names is None and s.startswith("الأسماء:"):
            parts = [p.strip() for p in s[len("الأسماء:"):].split("|")]
            if len(parts) == 3 and all(parts):
                names = {"home": parts[0], "away": parts[1], "league": parts[2]}
            continue
        if s:
            body.append(s)
    return names, "\n".join(body)


def send_telegram(text: str) -> None:
    """بث إلى المالك + كل معرّفات TELEGRAM_BROADCAST_IDS.

    فصل البث عن التحكم (طلب المالك 2026-08-14): الخارج يتوسّع ليصل إلى
    2-3 أجهزة إضافية، أما الداخل (أوامر watchlist.py وأزرار التوقع) فيبقى
    من محادثة المالك حصراً — سجل توقعاته في predictions_user.json يجب ألا
    يتلوث. فشل مستقبِل واحد لا يمنع البقية ولا يكسر التشغيلة.
    """
    api_guard.send_telegram_multi(
        TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_BROADCAST_IDS, text
    )


# ================== المنطق الرئيسي ==================
def prune_dead_matches(state: dict, live_ids) -> None:
    """تنظيف الذاكرة: حذف المباريات التي لم تعد حية — بالنمط الرقمي فقط.

    مفاتيح المباريات كلها أرقام (fid = str(fixture.id))، أما المفاتيح
    المحجوزة (api_alerts، api_quota، deadman، delivery، xg_live...) فليست
    مباريات ولا تُحذف هنا أبداً. النسخة القديمة كانت تحذف «كل ما ليس حياً»
    فمحت ذاكرة تهدئة الإنذارات ونبض التسليم وعلم deadman كل 10 دقائق
    (عطل 2026-08-15) — قائمة سوداء تفشل مفتوحة؛ الحذف بالنمط الرقمي
    يفشل مغلقاً: أي مفتاح محجوز مستقبلي ينجو تلقائياً بلا تحديث قائمة.
    """
    for fid in list(state.keys()):
        if str(fid).isdigit() and fid not in live_ids:
            del state[fid]


def main() -> None:
    missing = [
        name
        for name, val in [
            ("API_FOOTBALL_KEY", API_FOOTBALL_KEY),
            ("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY),
            ("TELEGRAM_TOKEN", TELEGRAM_TOKEN),
            ("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID),
        ]
        if not val
    ]
    if missing:
        print("مفاتيح ناقصة في Secrets:", ", ".join(missing))
        sys.exit(1)

    state = load_state()
    # نربط قاموس الحالة الحي بالحارس: يعدّله ويحفظه فوراً (متانة ضد موت
    # التشغيلة) ويبقى حفظنا الأخير متسقاً معه — بلا هذا الربط يمحو حفظ
    # نهاية التشغيلة علمَ مانع التكرار فيعود إغراق الرسائل.
    api_guard.attach_state(state, save_state)
    analyses_used = 0
    pulses = {"used": 0}            # عداد نبضات المحرك 2 في هذه التشغيلة
    live_budget = {"used": 0}       # عداد مباريات المحرك 2 المباشر في هذه التشغيلة
    wl_data = load_watchlist_data() # قائمة التركيز — تتحكم بمن يستحق تنبيه تيليجرام
    watch = valid_watch_fids(wl_data)
    wl_dirty = False

    # تقرير سيناريوهات ما قبل المباراة (قبل ~45 دقيقة من انطلاق مباريات القائمة)
    if prematch_reports(wl_data, watch):
        wl_dirty = True

    # تقارير الظل: تدريب يومي صامت على مباريات الدوريات الكبرى (بلا تيليجرام)
    try:
        shadow_reports(watch)
    except Exception as e:
        print("تقارير الظل — خطأ غير متوقع:", e)

    # ⚡ التقييم اللحظي للتقارير (أمر المالك 2026-08-09): مباراة انتهت
    # واكتملت بياناتها → تقييمها يظهر على اللوحة خلال دورة، وتيليجرام يبقى
    # صباحياً كما هو. أي فشل هنا لا يوقف المراقب أبداً.
    try:
        import predict_v2 as p2
        p2.live_grade_scenarios()
    except Exception as e:
        print("التقييم اللحظي — خطأ غير متوقع (نكمل):", e)

    try:
        fixtures = get_live_fixtures()
    except Exception as e:
        print("فشل سحب المباريات:", e)
        sys.exit(0)  # لا نفشّل التشغيلة، نحاول في الجولة القادمة
    # طابع لحظة الرصد: اللوحة تُقدّم الدقيقة بما مضى منذها — لا شاشة متجمدة
    # (بلاغ المالك 2026-08-02: اللوحة 65 والواقع 84)
    seen_now = datetime.now(timezone.utc).isoformat()

    live_ids = set()

    for fx in fixtures:
        league = fx.get("league", {}) or {}
        if is_excluded(league):
            continue
        _t = fx.get("teams") or {}
        if is_womens_match((_t.get("home") or {}).get("name"), (_t.get("away") or {}).get("name")) \
                or is_youth_match((_t.get("home") or {}).get("name"), (_t.get("away") or {}).get("name")):
            continue

        fixture = fx.get("fixture", {}) or {}
        teams = fx.get("teams", {}) or {}
        goals = fx.get("goals", {}) or {}

        fid = str(fixture.get("id"))
        status = ((fixture.get("status") or {}).get("short")) or ""
        minute = ((fixture.get("status") or {}).get("elapsed")) or 0
        home = (teams.get("home") or {}).get("name", "?")
        away = (teams.get("away") or {}).get("name", "?")
        home_logo = (teams.get("home") or {}).get("logo", "")
        away_logo = (teams.get("away") or {}).get("logo", "")
        league_logo = league.get("logo", "")
        gh = goals.get("home")
        ga = goals.get("away")
        gh = 0 if gh is None else gh
        ga = 0 if ga is None else ga
        score = f"{gh}-{ga}"
        league_line = f"{league.get('name', '?')} ({league.get('country', '?')})"

        live_ids.add(fid)
        prev = state.get(fid)
        alert_ok = should_alert(league, fid, watch)

        # --- حدث 1: مباراة جديدة بدأت ---
        if prev is None and status in LIVE_STATUSES:
            ar_names = None
            analysis = ""
            enriched = False
            # التحليل يُطلب فقط لمباراة سنرسل تنبيهها (توفير Claude)
            if alert_ok and should_analyze(league, analyses_used):
                raw, enriched = analyze_match(
                    f"مباراة حية بدأت الآن: {home} ضد {away} — {league_line}. "
                    f"النتيجة {score}، الدقيقة {minute}. "
                    f"أعطني توقعك النهائي لهذه المباراة.",
                    league, fid, live_budget, watch,
                )
                analyses_used += 1
                ar_names, analysis = parse_claude_reply(raw)
            h_disp = ar_names["home"] if ar_names else home
            a_disp = ar_names["away"] if ar_names else away
            l_disp = ar_names["league"] if ar_names else league_line
            if alert_ok:
                msg = (
                    f"⚽️ بدأت المباراة\n"
                    f"🏆 {l_disp}\n"
                    f"{h_disp} 🆚 {a_disp}\n"
                )
                if analysis:
                    label = "🤖 المحرك 2 (مباشر)" if enriched else "🤖 التوقع"
                    msg += f"\n{label}:\n{analysis}"
                send_telegram(msg)
            entry = {
                "score": score, "status": status, "minute": minute,
                "home": home, "away": away, "league": league_line,
                "home_logo": home_logo, "away_logo": away_logo, "league_logo": league_logo,
                "seen": seen_now,
            }
            if ar_names:
                entry["ar"] = ar_names
            state[fid] = entry
            continue

        if prev is None:
            # مباراة بحالة غير حية (توقف/تأجيل) — نسجلها بدون تنبيه
            state[fid] = {
                "score": score, "status": status, "minute": minute,
                "home": home, "away": away, "league": league_line,
                "home_logo": home_logo, "away_logo": away_logo, "league_logo": league_logo,
                "seen": seen_now,
            }
            continue

        # --- حدث 2: تغير النتيجة (هدف) ---
        ar_names = prev.get("ar")
        pulse_text = prev.get("pulse") or ""
        if score != prev.get("score") and status in LIVE_STATUSES and alert_ok:
            analysis = ""
            enriched = False
            if should_analyze(league, analyses_used):
                raw, enriched = analyze_match(
                    f"تحديث مباراة حية: {home} ضد {away} — {league_line}. "
                    f"النتيجة الآن {score} بعد هدف جديد، الدقيقة {minute}. "
                    f"هل يتغير توقعك؟ أعطني قراءة الموقف والتوقع النهائي.",
                    league, fid, live_budget, watch,
                )
                analyses_used += 1
                ar_new, analysis = parse_claude_reply(raw)
                if ar_new:
                    ar_names = ar_new
                if enriched and analysis:
                    pulse_text = analysis   # قراءة الهدف تصبح المرجع لنبضات ما بعده
            h_disp = ar_names["home"] if ar_names else home
            a_disp = ar_names["away"] if ar_names else away
            l_disp = ar_names["league"] if ar_names else league_line
            msg = (
                f"🚨 هدف!\n"
                f"🏆 {l_disp}\n"
                f"{h_disp} {gh} - {ga} {a_disp} (د{minute})\n"
            )
            if analysis:
                label = "🤖 المحرك 2 (مباشر)" if enriched else "🤖 قراءة المباراة الآن"
                msg += f"\n{label}:\n{analysis}"
            send_telegram(msg)

        # --- حدث 3: نهاية المباراة ---
        if status in FINAL_STATUSES and prev.get("status") not in FINAL_STATUSES:
            if alert_ok:
                h_disp = ar_names["home"] if ar_names else home
                a_disp = ar_names["away"] if ar_names else away
                l_disp = ar_names["league"] if ar_names else league_line
                send_telegram(
                    f"🏁 انتهت المباراة\n"
                    f"🏆 {l_disp}\n"
                    f"{h_disp} {gh} - {ga} {a_disp}"
                )
            # تسجيل النتيجة النهائية لمباراة من قائمة التركيز (لملخص نهاية اليوم)
            wl_entry = (wl_data.get("matches") or {}).get(fid)
            if fid in watch and wl_entry is not None and not wl_entry.get("result"):
                wl_entry["result"] = f"{gh}-{ga}"
                wl_dirty = True

        # --- نبض المحرك 2: مراقبة مستمرة لمباريات قائمة التركيز بين الأحداث ---
        # (لا هدف هذه الجولة — لكن هل يتشكل سيناريو خطر؟ ركنيات، هدف قادم،
        #  كلا الفريقين يسجلان، لاعب يهدد، بطاقة... يرسل فقط عند وجود جديد)
        sig_val = prev.get("sig")
        if (alert_ok and fid in watch and status in PULSE_STATUSES
                and score == prev.get("score")
                and pulses["used"] < MAX_PULSE_PER_RUN):
            pulses["used"] += 1
            alert = live_pulse(fid, home, away, league_line, score, minute, pulse_text)
            if alert:
                h_disp = ar_names["home"] if ar_names else home
                a_disp = ar_names["away"] if ar_names else away
                send_telegram(
                    f"👁 عين المحرك 2 — {h_disp} {gh} - {ga} {a_disp} (د{minute})\n\n{alert}"
                )
                pulse_text = alert
            # بصمة الأرقام الآن = الأساس الذي يقيس عليه الرصد السريع تحرك المباراة
            new_sig = live_signature(fid)
            if new_sig:
                sig_val = new_sig

        entry = {
            "score": score, "status": status, "minute": minute,
            "home": home, "away": away, "league": league_line,
                "home_logo": home_logo, "away_logo": away_logo, "league_logo": league_logo,
            "seen": seen_now,
        }
        if ar_names:
            entry["ar"] = ar_names
        if pulse_text:
            entry["pulse"] = pulse_text
        if sig_val:
            entry["sig"] = sig_val
        if prev.get("radar"):
            entry["radar"] = prev["radar"]   # ذاكرة الرادار تنجو من إعادة البناء
        state[fid] = entry

    # تنظيف الذاكرة: نحذف المباريات التي لم تعد حية (المفاتيح المحجوزة تنجو)
    prune_dead_matches(state, live_ids)

    # الرادار: إنذار مبكر رياضي لكل توقعات المحرك 2 الحية (صفر Claude)
    radar_count = 0
    radar_alerts = {"used": 0}   # سقف تنبيهات الدراما مشترك بين الدورة والمسار السريع
    # 🔧 جلسة 2026-08-24: سقف الإنذار المبكر مشترك أيضاً — كان يُنشأ مرتين
    # فيتضاعف فعلياً إلى 6/تشغيلة بدل 3 المعلنة
    radar_warns = {"used": 0}
    try:
        radar_count = radar_sweep(state, watch, radar_alerts, radar_warns)
        publish_radar_live(state)   # أول نسخة حية لهذه الدورة (فشلها صامت)
    except Exception as e:
        print("الرادار — خطأ غير متوقع:", e)

    # الرصد السريع: تبقى التشغيلة مستيقظة وتفحص مباريات قائمة التركيز
    # كل ~90 ثانية حتى تسليم الجولة التالية (تنبيه خلال دقيقة إلى دقيقتين)
    fast_deadline = fast_watch_deadline()
    if fast_deadline <= time.monotonic():
        print("المسار السريع: تخطٍّ — الجولة العادية استهلكت خانة التشغيلة "
              f"({int(time.monotonic() - RUN_START)}s)، نُسلّم للتشغيلة التالية")
    if focus_fast_watch(state, wl_data, watch, live_budget, pulses):
        wl_dirty = True

    # المسار السريع للرادار: ما تبقى من ميزانية التشغيلة يُنفق على تحديث
    # أخطر المباريات كل ~90 ثانية ونشرها الحي (طلب المالك 2026-08-01)
    radar_published = 0
    try:
        radar_published = radar_fast_watch(state, watch, fast_deadline, radar_alerts, radar_warns)
    except Exception as e:
        print("الرادار السريع — خطأ غير متوقع:", e)

    # ملخص نهاية اليوم: يُرسل فور انتهاء آخر مباراة في قائمة التركيز (مرة واحدة)
    if watch and not wl_data.get("results_sent") and all_focus_finished(wl_data, watch):
        focus_matches = {
            f: e for f, e in (wl_data.get("matches") or {}).items() if f in watch
        }
        send_telegram(build_focus_summary(focus_matches))
        wl_data["results_sent"] = True
        wl_dirty = True
    if wl_dirty:
        WATCHLIST_FILE.write_text(
            json.dumps(wl_data, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    save_state(state)
    print(
        f"تم: {len(live_ids)} مباراة حية (بعد الفلترة)، تحليلات مستخدمة: {analyses_used}، "
        f"منها بالمحرك 2 المباشر: {live_budget['used']}، نبضات: {pulses['used']}، "
        f"قائمة التركيز: {len(watch)}، لقطات الرادار: {radar_count}، "
        f"نشرات الرادار السريعة: {radar_published}"
    )

    # 🚨 بعد حفظ الحالة وطباعة الملخص: لو فشل تسليم رسالة إلى المالك نفسه
    # فلا سبيل للتبليغ عبر تيليجرام — نخرج بحالة فشل لتظهر التشغيلة حمراء
    # في صفحة Actions. الخطوات التالية في monitor.yml تحمل if: always() فلا
    # يضيع شيء. الفشل الصاخب أفضل من الصمت (درس 14 أغسطس).
    api_guard.exit_if_owner_unreachable()


if __name__ == "__main__":
    main()
