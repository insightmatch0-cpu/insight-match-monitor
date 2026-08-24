# -*- coding: utf-8 -*-
"""
المحرك 2 (V2) — توقعات ما قبل المباراة بجيل أذكى
--------------------------------------------------
نفس مباريات الـ 24 ساعة التي يتوقعها المحرك 1 (نفس الاختيار والاستبعادات)
حتى تكون المقارنة بين المحركين عادلة، مع ترقيات:

1) النموذج: claude-fable-5 (أقوى من نموذج المحرك 1).
2) لمباريات الدوريات الكبرى: سياق إضافي من API-Football قبل التوقع —
   ترتيب الفريقين، آخر 5 مواجهات مباشرة، آخر 5 نتائج لكل فريق، والإصابات.
3) المخرجات: احتمالات فوز/تعادل/خسارة مجموعها 100، والاختيار = الأعلى.
4) ذاكرة مستقلة predictions_v2.json + دروس من الأخطاء في lessons_v2.json
   (تُملأ في المرحلة 3) تُحقن في كل توقع جديد.

لا تكتب أي مفتاح داخل هذا الملف — كل المفاتيح في GitHub Secrets.
استهلاك API-Football: نداءان لجلب المباريات + ≤3 للتسوية
+ سياق الدوريات الكبرى (بحد أقصى ENRICH_CALL_BUDGET نداء) — بعيد جداً عن حد 7,500/يوم.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import api_guard
import reminders
from api_guard import ApiRefused        # noqa: F401 — يُعاد تصديره للاختبارات

# ================== المفاتيح (تُقرأ من GitHub Secrets) ==================
API_FOOTBALL_KEY  = os.environ.get("API_FOOTBALL_KEY", "").strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
# بث اختياري لأجهزة/أشخاص إضافيين — انظر شرح الفصل في monitor.py
TELEGRAM_BROADCAST_IDS = os.environ.get("TELEGRAM_BROADCAST_IDS", "").strip()

# ================== الإعدادات ==================
PREDICTIONS_FILE      = Path("predictions_v2.json")   # ذاكرة المحرك 2 (مستقلة عن المحرك 1)
V1_PREDICTIONS_FILE   = Path("predictions.json")      # ذاكرة المحرك 1 (للمقارنة في الملخص فقط)
USER_PREDICTIONS_FILE = Path("predictions_user.json") # توقعات المالك (يسجلها عبر تيليجرام)
LESSONS_FILE          = Path("lessons_v2.json")       # دروس من الأخطاء (تُملأ في المرحلة 3)
HISTORY_FILE          = Path("history.json")          # الأرشيف الدائم: تقدم الجميع يوماً بيوم (لا يُقص أبداً)
NEWS_FILE             = Path("news.json")             # آخر عناوين الأخبار (سياق مشترك)
RADAR_LOG_FILE        = Path("radar_log.json")        # إنذارات الرادار (يكتبها monitor)
# سجلات القياس بلا أسقف — امتداد أمر المالك 2026-08-09 (اكتشف بنفسه في نفس
# اليوم أن سجل إنذارات الرادار مشبع عند 300/300 فأرقام اللوحة "شبه ثابتة" —
# نفس فئة نافذة الـ 1000 المنزلقة الصامتة). 0 = بلا سقف؛ للطوارئ أعد رقماً.
RADAR_RESOLVED_CAP    = 0     # كان 300 — سقف سجل الإنذارات المُقيَّمة
RADAR_DROP_DAYS       = 4     # إنذار بلا نتيجة بعد 4 أيام يُسقط (مباراة ملغاة/مؤجلة)

# أرشيف الموسم الكامل — أمر المالك 2026-08-09 بعد حادثة "أرقام 70%+ تتغير
# يومياً": سقف الـ 1000 القديم حوّل لوحة الدقة إلى نافذة متحركة ~7 أيام في
# حجم الموسم (~150 تقييماً/يوم)، فتسربت توقعات يوليو الذهبية من العرض بصمت.
# القرار: لا يُحذف أي سجل مُقيَّم أبداً إلا بأمر مالك صريح — القياس الكامل
# للموسم (لكل دوري/شهر/أسبوع/يوم) يتطلب كل الصفوف. RESOLVED_CAP=0 يعني بلا
# سقف؛ للتراجع الفوري عند أي طارئ حجم: أعد الرقم القديم 1000.
RESOLVED_CAP          = 0
# 🧮 حارس النزاهة اليومي — أمر المالك 2026-08-09 ("كيف نوقف تكرار الأرقام
# الخاطئة من الجذر؟"): ثلاث حوادث في أسبوع اكتشفها المالك بعينه لا النظام
# (تسريب WK-League، تضخم الحكام 2.6×، نافذة 70%+ المتحركة) — وكلها انتهاكات
# لقوانين حسابية بسيطة كان يمكن فحصها آلياً. المبدأ: القيد المزدوج المحاسبي —
# كل رقم مهم يُشتق من طريقين مستقلين كل صباح، وأي اختلاف إنذار تيليجرام فوري
# في نفس اليوم (لا بعد شهر). صفر نداءات، رياضيات محلية بحتة.
# قاعدة دائمة: كل حادثة أرقام جديدة = قانون جديد في السجل في نفس PR إصلاحها.
# التعطيل الفوري: False (يختفي الفحص وسطر النشرة معاً).
INTEGRITY_SENTINEL    = True
INTEGRITY_AGE_GRACE   = 2     # أيام سماح فوق كل مهلة قبل اعتبارها انتهاكاً
INTEGRITY_MAX_DETAILS = 5     # أقصى أمثلة تُعرض لكل قانون مكسور (ضد الإغراق)
# بداية موسم 2026-27 (انطلاق الدوري السعودي): عدّادات الموسم على اللوحة تبدأ
# من صفر عند هذا التاريخ — ما قبله يبقى محفوظاً كسجل تأسيس، لا يُعرض كموسم.
SEASON_START          = "2026-08-13"

# 🔬 يوم انطلاق تجربة xG الحي في الظل: السجلات الأقدم لا تحمل score_xg
# فتبقى خارج الشريحة — نفس منطق REC-010، الفلتر يمتلئ من لحظة التنفيذ.
XG_LIVE_START         = "2026-08-15"

# طول نافذة تجربة ظل xG بالأيام: 21 أصلاً، ثم +14 بأمر المالك 2026-08-14
# (الدوريات المشمولة لم تكن تلعب في معظم النافذة الأولى) — الحكم ~17 سبتمبر.
XG_SHADOW_DAYS        = 35

# 🎛 حارس العينة — REC-010 (قرار المالك 2026-08-13): أي عرض مفلتر على شريحة
# دوريات المالك عدده أقل من هذا الحد لا يُظهر نسبة مئوية إطلاقاً، بل نص
# "عينة غير كافية". السبب مسجّل في REJ-002: نسبة على ثلاث مباريات ("دورياتك،
# 0%") ضجيج يبدو كارثة، أو 100% من مباراتين فيبدو عبقرية — آلة استنتاجات
# خاطئة داخل نظام بُني كله لمنعها. الرقم يُقرأ من هنا في اللوحة أيضاً.
MIN_FILTERED_SAMPLE   = 20
RADAR_LOG_DEDUP       = True   # 🔧 REC-015: دمج صفوف الإنذار المكررة قبل التقييم

# قاعدة إيقاف تنبيهات الدراما — REC-005 (قرار المالك 2026-08-08): نهاية مكتوبة
# مسبقاً لتجربة قد تدور بلا نهاية، بقياس كل نوع ادعاء على حدة (قاعدة المالك ج —
# لا يُعاقب next_goal بذنب flip). عند بلوغ النوع 30 تنبيهاً مُقيَّماً تراكمياً:
# دقة < 40% → قائمة silenced (يُسجَّل ويظهر في تبويب الرادار فقط، بلا تيليجرام)؛
# دقة ≥ 50% → قائمة proven (يُرسل بلا وسم "🧪 تجريبي"). تحت 30: صفر تأثير.
# القائمتان تُعادان كتابتهما كل صباح من السجل التراكمي نفسه — لا حالة خفية،
# ونوع مُسكَت يستمر قياسه فيستطيع الخروج من الصمت إن تحسّن سجله.
# التعطيل الفوري: False هنا وفي monitor.py (تفرغ القوائم ويعود كل شيء كما كان).
RADAR_ALERT_STOP_RULE = True
RADAR_STOP_MIN_GRADED = 30    # الحد الأدنى من التنبيهات المُقيَّمة قبل أي حكم
RADAR_STOP_SILENCE_LT = 40    # دقة أقل من هذه (٪) → النوع صامت
RADAR_STOP_PROVEN_GTE = 50    # دقة من هذه (٪) فأعلى → النوع مُثبَت

CLAUDE_MODEL = "claude-fable-5"

MAX_PREDICTIONS_24H   = 150   # نفس حد المحرك 1 — نفس المباريات. رُفع من 60 (المالك
                              # 2026-07-18): 60 كانت تغطي أبكر مباريات اليوم فقط
                              # وتقطع مباريات المساء الأوروبية، فتظهر حية بلا توقع
                              # (ولا "حماية" على اللوحة). الدوريات الكبرى مضمونة
                              # دائماً (ترتيب كبرى-أولاً)؛ الرفع يوسّع تغطية البقية.
MAX_RESOLVE_CALLS     = 3     # أقصى نداءات API لتسوية نتائج الأيام السابقة
# رصيد API-Football (خطة Pro: 7500/يوم) مدفوع مسبقاً — نستخدمه بسخاء:
# كل المباريات تأخذ سياقاً إضافياً (الكبرى أولاً لأن القائمة مرتبة كبرى-أولاً)
MAX_ENRICHED_FIXTURES = 60    # كل مباريات اليوم (كانت 15 للكبرى فقط)
ENRICH_CALL_BUDGET    = 750   # سقف أمان لنداءات السياق الإضافي (~505 متوقعة مع الأودز والمدربين)
ENRICHED_BATCH_SIZE   = 4     # دفعات صغيرة للمباريات ذات السياق الغني
BASIC_BATCH_SIZE      = 12    # دفعات المباريات بدون سياق (مثل المحرك 1)
# توفير التكلفة (توجيه المالك 2026-07-17): السياق الغني (والنموذج المكلف عليه)
# للدوريات الكبرى فقط — بقية المباريات تُتوقّع بالنمط الخفيف. كل المباريات
# تبقى مُتوقَّعة ومُقيَّمة (التغطية كاملة، الدماغ والدروس لا يتأثران). للرجوع
# الفوري إلى تغطية غنية للجميع: اجعل هذه القيمة False.
ENRICH_TOP_ONLY       = True
# حارس مباريات الكأس/الإقصاء (توجيه المالك 2026-07-18): التعثّر الوحيد في خانة
# الثقة العالية (72% على كيري×شيلبورن في كأس أيرلندا انتهت 2-2) كان مباراة كأس.
# الفرق الصغيرة على أرضها تفاجئ الكبار بانتظام في الكأس، لذا نُلزم النموذج
# بحدّ أدنى للتعادل ونُسقّف الثقة حتى لا تدخل تخمينات الكأس خانة 70%+ العالية.
# لا يغيّر الطرف المُختار، ولا يمسّ التعلّم. للتعطيل الفوري: False.
CUP_GUARDRAIL         = True
CUP_CONF_CAP          = 65    # أقصى ثقة مسموحة في مباريات الكأس/الإقصاء
CUP_MIN_DRAW          = 25    # أدنى احتمال تعادل نفرضه في مباريات الكأس/الإقصاء
MAX_LESSONS_IN_PROMPT = 15    # أحدث الدروس التي تُحقن في كل توقع
MAX_LESSONS_STORED    = 100   # أقصى دروس محفوظة في lessons_v2.json
MAX_MISTAKES_PER_RUN  = 30    # كل أخطاء اليوم عملياً تُراجع لاستخلاص الدروس (نداء واحد)
CONSOLIDATE_THRESHOLD = 60    # عند تجاوز هذا العدد تُدمج الدروس المتشابهة
CONSOLIDATE_TARGET    = 30    # عدد المبادئ المركزة بعد الدمج

# معيار السوق — REC-003 (قرار المالك 2026-08-08): قياس تراكمي محايد يجيب على
# أهم سؤال في المشروع — هل يتفوق الإثراء المكلف على اتباع الأودز مجاناً؟
# كل صباح تُحسب دقة المحرك مقابل دقة "مرشّح السوق" (أعلى احتمال ضمني في
# الأودز) على نفس المباريات المُقيَّمة التي تحمل mkt_*، وتُخزَّن في
# meta.stats.market_bench. أداة حكم لا تحسين: صفر نداءات، صفر أثر على أي توقع.
# قاعدة القرار المسجّلة مسبقاً في سجل المعهد: إن كان المحرك أدنى من مرشّح
# السوق بأكثر من 3 نقاط على ≥150 مباراة كبرى، يُفتح ملف مراجعة الإثراء كاملاً.
# للتعطيل الفوري: False (يختفي الحساب وسطر النشرة معاً).
MARKET_BENCH          = True

# إفصاح الثقة — REC-004 (قرار المالك 2026-08-08): السقف العملي كان 65 بسلوك
# النموذج نفسه (تكدّس عند 65 رغم أن سجله عند ثقة ≥65 يبلغ ~96%) — شريحة 65-69
# مقوّمة بأقل من حقها بأكثر من 20 نقطة. العلاج: تعليمة موجّه تخبره بسجله
# الحقيقي وتأذن له بالإفصاح فوق 65 عند القناعة الحقيقية، مع سقف إفصاح 80
# يحتوي أي مبالغة. لا يغيّر أي اختيار (الاختيار = أعلى احتمال دائماً)، ولا
# يمسّ حارس الكؤوس (CUP_CONF_CAP=65 يعمل بعده ويبقى سيّد مباريات الكأس).
# للتعطيل الفوري: False (تسقط التعليمة ويعود سقف المحلل القديم 85).
CONF_DISCLOSURE       = True
CONF_DISCLOSURE_CAP   = 80    # أقصى ثقة معلنة بعد REC-004 — احتواء المبالغة

# قاعدة الحكام الذاتية (خطوة استكشاف 6): تتراكم من المباريات المُقيَّمة —
# معدل بطاقات الحكم يغذي تقارير ما قبل المباراة (بعض الحكام يشهرون بغزارة)
REFEREES_FILE = Path("referees.json")

# التقييم الذاتي لتقارير ما قبل المباراة (يكتبها monitor.py في scenarios_v2.json)
SCENARIOS_FILE = Path("scenarios_v2.json")
MAX_SCENARIO_GRADES_PER_RUN = 6    # نداء Claude لكل تقرير — قائمة التركيز صغيرة أصلاً
SCENARIO_MAX_AGE_DAYS = 4          # تقرير بلا بيانات نهائية بعد 4 أيام يُسقط (مؤجلة/ملغاة)
# كان 100 — سجل قياس دائم مثل بقية السجلات: بلا حذف أبداً (أمر المالك
# 2026-08-09؛ كان سيشبع خلال أيام عند 84/100 ويجمّد عدّاد مختبر الظل بصمت)
SCENARIOS_RESOLVED_CAP = 0

# التقييم اللحظي للتقارير — أمر المالك 2026-08-09 ("لماذا تنتظر اللوحة الغد؟"):
# فصل القياس عن التبليغ — القياس حدثي فور اكتمال البيانات النهائية (تظهر
# النتيجة على اللوحة خلال ~دورة مراقب من اكتمالها)، والتبليغ البشري يبقى
# مجمعاً في بطاقات الصباح على تيليجرام كما كان حرفياً (تفضيل المالك الصريح).
# بوابة تحقق صارمة قبل أي تقييم لحظي: مرور وقت كافٍ بعد الانطلاق + وجود
# الإحصائيات النهائية فعلاً — الناقص يُترك للدورة القادمة وللصباح كشبكة أمان.
# التعطيل الفوري: False (يعود كل التقييم صباحياً كما كان).
LIVE_SCENARIO_GRADING  = True
LIVE_GRADE_MIN_MINUTES = 150   # لا محاولة قبل ~ساعتين ونصف من الانطلاق
LIVE_GRADES_PER_CYCLE  = 2     # سقف تقييمات لكل دورة مراقب — توزيع التكلفة

SEND_TELEGRAM_DIGEST = True
DIGEST_TOP_ONLY      = True
# ⭐/⚡ قسما النشرة البارزان (طلب المالك 2026-08-21: «كل شيء مخلوط») — نفس
# مصطلحات البوابة حرفياً حتى يتطابق الهاتف مع الشاشة. عرض فقط، صفر نداءات
DIGEST_GOLD_MIN_CONF = 70          # عتبة الشريحة الذهبية — نفس رقم اللوحة
DIGEST_SECTIONS      = True        # مفتاح التراجع: False يعيد النشرة القديمة
DASHBOARD_URL = "https://insightmatch0-cpu.github.io/insight-match-monitor/"

TOP_LEAGUE_IDS = {
    1, 2, 3, 4, 9, 13, 15, 39, 40, 61, 71, 78, 88, 94, 128, 135, 140, 253, 307, 417, 542,
}

# 🎛 دوريات المالك التسعة حرفياً (قراره 2026-08-22: شريحة عرض ثالثة أضيق) —
# البريميرليغ والتشامبيونشيب والكالتشو والبوندسليغا واللاليغا والليغ آن
# والسعودي والبحريني والعراقي. **مجموعة عرض وقياس فقط**: لا تمس الإثراء ولا
# الأولوية ولا التنبيهات — تلك كلها تبقى على TOP_LEAGUE_IDS (قراره 2026-08-21
# «أبقِها كما هي» مسجَّل في REC-010). الاشتقاق بالمعرف حصراً، أبداً لا بالاسم.
OWNER_LEAGUE_IDS = {39, 40, 61, 78, 135, 140, 307, 417, 542}

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

FINAL_STATUSES = {"FT", "AET", "PEN"}
DEAD_STATUSES  = {"PST", "CANC", "ABD", "AWD", "WO"}


# ================== أدوات مساعدة ==================
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


def is_excluded(league: dict) -> bool:
    country = (league.get("country") or "").strip().lower()
    name = (league.get("name") or "").strip().lower()
    if country in EXCLUDED_COUNTRIES:
        return True
    return any(kw in name for kw in EXCLUDED_LEAGUE_KEYWORDS)


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



def api_football(path: str) -> list:
    """نداء API-Football محروساً (حادثة الصمت 2026-08-14).

    كان يرفع RuntimeError عاماً — يُفشل التشغيلة بلا أن يخبر أحداً. الآن
    يرفع ApiRefused مصنَّفاً **بعد** إرسال إنذار تيليجرام فوري، ويقرأ
    عدّاد الرصيد من كل رد. القائمة الفارغة تبقى مقبولة (القاعدة 5).
    للتراجع الفوري: API_REFUSAL_STRICT=0.
    """
    return api_guard.guarded_get(
        f"https://v3.football.api-sports.io/{path}",
        headers={"x-apisports-key": API_FOOTBALL_KEY},
        component="predict_v2.py (توقعات المحرك 2 الصباحية)",
    )


def send_telegram(text: str) -> None:
    """بث إلى المالك + كل معرّفات TELEGRAM_BROADCAST_IDS (انظر monitor.py).
    السرّ غائب/فارغ = السلوك القديم حرفياً. فشل مستقبِل لا يوقف البقية."""
    api_guard.send_telegram_multi(
        TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_BROADCAST_IDS, text
    )


def send_telegram_long(text: str) -> None:
    """يقسم الرسائل الطويلة (حد تيليجرام 4096 حرفاً)."""
    chunk = []
    size = 0
    for line in text.splitlines():
        if size + len(line) + 1 > 3800 and chunk:
            send_telegram("\n".join(chunk))
            chunk, size = [], 0
        chunk.append(line)
        size += len(line) + 1
    if chunk:
        send_telegram("\n".join(chunk))


# ================== سجل الدقة (التعلم الذاتي — مطابق للمحرك 1) ==================
def outcome_from_score(gh: int, ga: int) -> str:
    if gh > ga:
        return "home"
    if ga > gh:
        return "away"
    return "draw"


def _conf_bucket(conf) -> str:
    if conf >= 70:
        return "70+"
    if conf >= 60:
        return "60-69"
    if conf >= 50:
        return "50-59"
    return "<50"


def _accumulate(rows: list) -> dict:
    """يجمع (صح/مجموع) إجمالاً وحسب نوع الدوري وحسب شريحة الثقة لمجموعة صفوف."""
    acc = {
        "overall": {"correct": 0, "total": 0},
        "top_leagues": {"correct": 0, "total": 0},
        "other_leagues": {"correct": 0, "total": 0},
        "by_confidence": {
            "70+": {"correct": 0, "total": 0},
            "60-69": {"correct": 0, "total": 0},
            "50-59": {"correct": 0, "total": 0},
            "<50": {"correct": 0, "total": 0},
        },
    }
    for r in rows:
        ok = 1 if r.get("correct") else 0
        acc["overall"]["total"] += 1
        acc["overall"]["correct"] += ok
        key = "top_leagues" if r.get("top") else "other_leagues"
        acc[key]["total"] += 1
        acc[key]["correct"] += ok
        b = _conf_bucket(int(r.get("confidence", 0)))
        acc["by_confidence"][b]["total"] += 1
        acc["by_confidence"][b]["correct"] += ok
    return acc


def _stats_tree(rows: list) -> dict:
    """الشجرة الكاملة لمجموعة صفوف: إجمالي وشرائح ثقة ونوع دوري (من
    _accumulate) + آخر 30 يوماً + الاتجاه اليومي + كتلة "الموسم"
    (من SEASON_START) بعدّاداتها المستقلة.

    مستخرجة كدالة واحدة (REC-010) حتى تُحسب شريحة دوريات المالك بنفس
    الرياضيات حرفياً — لا نسخة ثانية من المنطق تنحرف عن الأولى بمرور الوقت."""
    stats = _accumulate(rows)
    stats["last30"] = {"correct": 0, "total": 0}
    stats["daily"] = {}
    cutoff = (now_utc() - timedelta(days=30)).strftime("%Y-%m-%d")
    for r in rows:
        ok = 1 if r.get("correct") else 0
        if r.get("date", "") >= cutoff:
            stats["last30"]["total"] += 1
            stats["last30"]["correct"] += ok
        d = stats["daily"].setdefault(r.get("date", "?"), {"correct": 0, "total": 0})
        d["total"] += 1
        d["correct"] += ok
    stats["daily"] = dict(sorted(stats["daily"].items())[-30:])
    # عدّادات الموسم: أصفار قبل 2026-08-13، ثم تتراكم بلا حذف حتى نهاية الموسم
    stats["season"] = _accumulate(
        [r for r in rows if r.get("date", "") >= SEASON_START])
    stats["season"]["start"] = SEASON_START
    return stats


def top_only_stats(resolved: list) -> dict:
    """🎛 شريحة دوريات المالك وحدها — REC-010 (قرار المالك 2026-08-13).

    الدليل الذي أنتج التوصية: من 1,525 مباراة مُقيَّمة 106 فقط (7%) من
    دوريات المالك التسعة — أي أن كل رقم يقرأه اليوم تحكمه كرة لا يتابعها.
    هذه شجرة موازية بنفس دوال الشجرة الكاملة على الصفوف الحاملة علامة
    `top` فقط: الإجمالي، شرائح الثقة، الاتجاه اليومي، كتلة الموسم، ومعيار
    السوق. **لا تمسّ أي توقع ولا أي ثقة ولا أي تنبيه** — عرض وقياس فقط.

    ملاحظة على الشكل: داخل هذه الشجرة يتساوى `top_leagues` مع `overall`
    ويبقى `other_leagues` أصفاراً — مقصود، فوحدة الشكل تسمح للوحة برسم
    الشريحتين بنفس الدالة بلا فرع خاص."""
    rows = [r for r in resolved if r.get("top")]
    tree = _stats_tree(rows)
    tree["market_bench"] = market_bench_stats(rows)
    # حارس العينة يسافر مع الأرقام: اللوحة تقرأ الحد من هنا لا من ثابت مكرر
    tree["min_sample"] = MIN_FILTERED_SAMPLE
    return tree


def mine_only_stats(resolved: list) -> dict:
    """🎛 الشريحة الثالثة: دوريات المالك التسعة حرفياً (قراره 2026-08-22).

    نفس بناء top_only_stats على الصفوف الحاملة علامة `mine` فقط. الصفوف
    الأقدم من يوم التنفيذ لا تحمل العلامة (الصفوف المُقيَّمة لا تخزن معرف
    الدوري، والاشتقاق بالاسم ممنوع بدرس WK-League) — فالشريحة تمتلئ من
    يوم التنفيذ فصاعداً، وحارس العينة يتكفل بعرضها الصادق حتى تكبر."""
    rows = [r for r in resolved if r.get("mine")]
    tree = _stats_tree(rows)
    tree["market_bench"] = market_bench_stats(rows)
    tree["min_sample"] = MIN_FILTERED_SAMPLE
    return tree


def compute_stats(resolved: list) -> dict:
    """يحسب دقة المحرك 2: إجمالي، آخر 30 يوماً، حسب مستوى الثقة، وحسب نوع
    الدوري — وكتلة "الموسم" (من SEASON_START) بعدّاداتها المستقلة التي تبدأ
    من صفر في 2026-08-13 (أمر المالك 2026-08-09) — وشجرتان موازيتان لا
    تغيّران أي رقم من أرقام "الكل": دوريات الصدارة (`top_only` — REC-010)
    ودورياته التسعة حرفياً (`mine_only` — قراره 2026-08-22)."""
    stats = _stats_tree(resolved)
    stats["top_only"] = top_only_stats(resolved)
    stats["mine_only"] = mine_only_stats(resolved)
    return stats


def pct(d: dict) -> str:
    if not d.get("total"):
        return "لا يوجد سجل بعد"
    return f"{round(100 * d['correct'] / d['total'])}% ({d['correct']}/{d['total']})"


def market_favorite(r: dict) -> str:
    """مرشّح السوق لصف مُقيَّم: الطرف صاحب أعلى احتمال ضمني في الأودز.
    عند التساوي يُحسم بترتيب ثابت (home ثم draw ثم away) حتى يبقى القياس حتمياً.
    يرجع '' إن كانت حقول السوق ناقصة."""
    try:
        probs = {k: int(r[f"mkt_{k}"]) for k in ("home", "draw", "away")}
    except (KeyError, TypeError, ValueError):
        return ""
    return max(("home", "draw", "away"), key=lambda k: probs[k])


def market_bench_stats(resolved: list) -> dict:
    """معيار السوق (REC-003): تراكمياً على كل الصفوف المُقيَّمة التي تحمل
    احتمالات السوق — كم أصاب المحرك وكم أصاب مرشّح السوق على نفس المباريات،
    وكم مباراة اختلفا فيها. صفر نداءات — قراءة محلية بحتة."""
    bench = {"n": 0, "engine_correct": 0, "market_correct": 0, "disagree": 0}
    for r in resolved:
        fav = market_favorite(r)
        actual = r.get("actual")
        if not fav or actual not in ("home", "draw", "away"):
            continue
        bench["n"] += 1
        bench["engine_correct"] += 1 if r.get("correct") else 0
        bench["market_correct"] += 1 if fav == actual else 0
        if fav != r.get("pick"):
            bench["disagree"] += 1
    return bench


def resolve_pending(store: dict):
    """يتحقق من نتائج التوقعات المنتظرة ويحوّل ما انتهى إلى سجل الدقة.
    يرجع (عدد المُسوَّى، قائمة المُسوَّى حديثاً) — القائمة تُستخدم لاستخلاص الدروس."""
    pending = store.get("pending", {})
    if not pending:
        return 0, []

    today = now_utc().strftime("%Y-%m-%d")
    dates = sorted({p.get("date", "") for p in pending.values() if p.get("date", "") <= today})
    dates = [d for d in dates if d][-MAX_RESOLVE_CALLS:]

    finals = {}
    for d in dates:
        try:
            for fx in api_football(f"fixtures?date={d}"):
                fid = str((fx.get("fixture") or {}).get("id"))
                status = (((fx.get("fixture") or {}).get("status")) or {}).get("short") or ""
                goals = fx.get("goals") or {}
                teams = fx.get("teams") or {}
                # عرف التوقعات العالمي: النتيجة بعد 90 دقيقة (score.fulltime) —
                # مباراة محسومة بالأشواط الإضافية تُقيَّم على نتيجة الوقت الأصلي
                ft = ((fx.get("score") or {}).get("fulltime")) or {}
                gh = ft.get("home") if ft.get("home") is not None else goals.get("home")
                ga = ft.get("away") if ft.get("away") is not None else goals.get("away")
                logos = {
                    "home_logo": (teams.get("home") or {}).get("logo", ""),
                    "away_logo": (teams.get("away") or {}).get("logo", ""),
                    "league_logo": (fx.get("league") or {}).get("logo", ""),
                }
                finals[fid] = (status, gh, ga, logos)
        except Exception as e:
            print(f"فشل سحب نتائج {d}:", e)

    resolved_now = 0
    newly_resolved = []
    drop_before = (now_utc() - timedelta(days=3)).strftime("%Y-%m-%d")
    for fid in list(pending.keys()):
        p = pending[fid]
        status, gh, ga, logos = finals.get(fid, ("", None, None, {}))
        if status in FINAL_STATUSES and gh is not None and ga is not None:
            actual = outcome_from_score(int(gh), int(ga))
            entry = {
                "fid": fid,
                "date": p.get("date"),
                "home": p.get("home"), "away": p.get("away"),
                "ar_home": p.get("ar_home"), "ar_away": p.get("ar_away"),
                "home_logo": p.get("home_logo") or logos.get("home_logo", ""),
                "away_logo": p.get("away_logo") or logos.get("away_logo", ""),
                "league_logo": p.get("league_logo") or logos.get("league_logo", ""),
                "league": p.get("league"), "ar_league": p.get("ar_league"),
                "top": p.get("top", False),
                # 🎛 REC-016: غياب المصدر يبقى None لا False — قيمة افتراضية
                # تتنكر كتصنيف هي بالضبط ما لوّث 65 صفاً يوم 2026-08-22
                "mine": p.get("mine"),
                "pick": p.get("pick"),
                "confidence": p.get("confidence"),
                "prob_home": p.get("prob_home"),
                "prob_draw": p.get("prob_draw"),
                "prob_away": p.get("prob_away"),
                # احتمالات السوق الضمنية (إن وُجدت) — تُحفظ مع النتيجة لقياس
                # "هل يتفوق المحرك على السوق حين يخالفه؟" لاحقاً
                "mkt_home": p.get("mkt_home"),
                "mkt_draw": p.get("mkt_draw"),
                "mkt_away": p.get("mkt_away"),
                # سطر "لماذا" (طلب المالك 2026-08-01): قراءة المحرك قبل المباراة
                # تبقى مع النتيجة — حين يخطئ التوقع نعرف ماذا كان يفكر
                "reason": p.get("reason", ""),
                "actual": actual,
                "score": f"{gh}-{ga}",
                "correct": p.get("pick") == actual,
            }
            store.setdefault("resolved", []).append(entry)
            newly_resolved.append(entry)
            del pending[fid]
            resolved_now += 1
        elif status in DEAD_STATUSES or (p.get("date", "") < drop_before):
            del pending[fid]

    # أرشيف الموسم الكامل (أمر المالك 2026-08-09): بلا سقف — لا يُحذف سجل
    # مُقيَّم أبداً إلا بأمر مالك صريح. RESOLVED_CAP>0 يعيد القص القديم للطوارئ.
    if RESOLVED_CAP:
        store["resolved"] = store.get("resolved", [])[-RESOLVED_CAP:]
    return resolved_now, newly_resolved


# ================== المرحلة 3: التعلم من الأخطاء ==================
def claude_request(system_prompt: str, user_text: str, max_tokens: int = 2000) -> str:
    """نداء Claude عام يرجع النص فقط (فارغ عند الفشل — لا يوقف التشغيلة)."""
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_text}],
            },
            timeout=180,
        )
        r.raise_for_status()
        return "".join(
            b.get("text", "")
            for b in r.json().get("content", [])
            if b.get("type") == "text"
        ).strip()
    except Exception as e:
        detail = ""
        resp = getattr(e, "response", None)
        if resp is not None:
            try: detail = " — " + resp.text[:300]
            except Exception: pass
        print(f"Claude error: {e}{detail}")
        claude_refusal_alert(detail or str(e), "المحرك 2")
        return ""


def claude_refusal_alert(detail: str, engine: str) -> None:
    """💳 رفض عائلة موت الحساب من Anthropic (رصيد/فوترة/مفتاح) — إنذار فوري
    للمالك من أول فشل بتهدئة 6 ساعات (درس 14 أغسطس: عتبة تحتاج N فشلاً
    يهزمها N-1). النداء الفاشل كان يُبتلع بهدوء فتخرج التشغيلة خضراء بصفر
    توقعات بلا أي صرخة — وهذا ما حدث حرفياً صبيحة 2026-08-17: 109 مرشحين،
    0 حُفظ، والحساب فارغ منذ الليل ولا رسالة. مفتاح تهدئة واحد للعائلة."""
    low = (detail or "").lower()
    if ("credit balance" in low or "billing" in low
            or "authentication" in low or "invalid x-api-key" in low):
        api_guard.alert_once(
            "claude_credit",
            f"💳 {engine} متوقف: Anthropic يرفض النداءات (رصيد/فوترة/مفتاح).\n"
            "لا توقعات ولا تقارير ولا دروس حتى يُعبَّأ الرصيد.\n"
            "الإجراء المطلوب منك: Plans & Billing في حساب Anthropic الممول — "
            "عبّئ الرصيد وتحقق لماذا لم تعمل التعبئة التلقائية.")


def parse_json_array(text: str) -> list:
    """يستخرج مصفوفة JSON من رد Claude بتسامح (أسوار، نص زائد)."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    if not text.startswith("["):
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if not m:
            return []
        text = m.group(0)
    try:
        items = json.loads(text)
    except Exception as e:
        print("JSON parse error:", e)
        return []
    return items if isinstance(items, list) else []


def generate_lessons(newly_resolved: list) -> int:
    """يستخلص درساً قابلاً للتطبيق من كل توقع خاطئ ويضيفه إلى lessons_v2.json.
    الدروس الأحدث تُحقن تلقائياً في كل توقع قادم (lessons_text)."""
    mistakes = [r for r in newly_resolved if not r.get("correct")][:MAX_MISTAKES_PER_RUN]
    if not mistakes:
        return 0

    payload = [
        {
            "match": f"{r.get('home')} vs {r.get('away')}",
            "league": r.get("league"),
            "my_pick": r.get("pick"),
            "my_probs_home_draw_away":
                f"{r.get('prob_home', '?')}/{r.get('prob_draw', '?')}/{r.get('prob_away', '?')}",
            "confidence": r.get("confidence"),
            "actual_outcome": r.get("actual"),
            "final_score": r.get("score"),
            "top_league": bool(r.get("top")),
        }
        for r in mistakes
    ]
    system_prompt = (
        "أنت المراجع الذاتي لمحرك توقعات كرة قدم. ستصلك توقعات خاطئة من الأمس "
        "مع النتائج الفعلية.\n"
        "استخلص من كل توقع خاطئ درساً واحداً قصيراً وقابلاً للتطبيق في توقعات "
        "قادمة: نمط عام يجب الانتباه له (مثل المبالغة في قوة صاحب الأرض، أو تجاهل "
        "احتمال التعادل بين متقاربين) — وليس مجرد وصف لما حدث في تلك المباراة.\n"
        "أرجع ردك بصيغة JSON فقط — مصفوفة واحدة بدون أي نص قبلها أو بعدها وبدون ```:\n"
        '[{"match":"...","lesson":"درس من سطر واحد بالعربي"}]\n'
        "استخدم الأرقام الإنجليزية (0-9) فقط ولا تستخدم الأرقام العربية (٠-٩) أبداً."
    )
    # حتى 30 درساً عربياً في رد واحد — يلزم سقف إخراج واسع وإلا يُقص الرد ويفشل التحليل
    raw = claude_request(system_prompt, json.dumps(payload, ensure_ascii=False),
                         max_tokens=6000)
    items = parse_json_array(raw)
    if not items:
        print("لم تُستخلص دروس (رد فارغ أو غير صالح) — أخطاء اليوم تبقى في السجل.")
        return 0

    data = load_json(LESSONS_FILE, {"lessons": []})
    data.setdefault("lessons", [])
    today = now_utc().strftime("%Y-%m-%d")
    added = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        lesson = str(it.get("lesson") or "").strip()
        if not lesson:
            continue
        data["lessons"].append({
            "date": today,
            "match": str(it.get("match") or "").strip(),
            "text": lesson,
        })
        added += 1
    if added:
        data["lessons"] = data["lessons"][-MAX_LESSONS_STORED:]
        save_json(LESSONS_FILE, data)
    return added


# REC-001: حارس عدم التكرار — نحتفظ بمعرفات المباريات المسجَّلة في قاعدة
# الحكام (مفتاح _meta.fids) فلا تُحتسب المباراة مرتين مهما أعيدت المحاولة.
# السقف يمنع نمو القائمة بلا حدود؛ إعادة المحاولة تحدث خلال أيام لا أشهر.
REFEREE_FIDS_CAP = 2000


def record_referee(name: str, yellows: int, reds: int, fid: str = "") -> None:
    """يراكم سجل الحكم في referees.json — قاعدة بيانات ذاتية تنمو مع كل
    مباراة مُقيَّمة (لا يوجد مصدر مجاني لإحصائيات الحكام — نبنيها بأنفسنا).
    REC-001: المعرف fid يمنع احتساب نفس المباراة مرتين — كانت تُسجَّل مع كل
    إعادة محاولة تقييم فتضخمت الأرقام بعامل ~2.3 (فينتشيتش: 1.00 طرد/مباراة)."""
    name = (name or "").strip()
    if not name:
        return
    db = load_json(REFEREES_FILE, {})
    meta = db.setdefault("_meta", {})
    fids = meta.setdefault("fids", [])
    fid = str(fid or "").strip()
    if fid:
        if fid in fids:
            return                     # سُجّلت من قبل — لا ازدواج
        fids.append(fid)
        meta["fids"] = fids[-REFEREE_FIDS_CAP:]
    rec = db.get(name) or {"matches": 0, "yellows": 0, "reds": 0}
    rec["matches"] += 1
    rec["yellows"] += max(0, yellows)
    rec["reds"] += max(0, reds)
    db[name] = rec
    save_json(REFEREES_FILE, db)


def actual_match_data(fid: str):
    """البيانات النهائية الحقيقية لمباراة منتهية: النتيجة + الإحصائيات
    (ركنيات، تسديدات، بطاقات، تصديات) + الأحداث (المسجلون والبطاقات بالأسماء).
    ترجع (النص، بيانات الحكم) — والنص '' إذا لم تنته المباراة بعد.
    3 نداءات API — الرصيد مدفوع مسبقاً.
    REC-001: لم يعد يسجّل الحكم بنفسه (كان يسجّله قبل نجاح التقييم فيتكرر مع
    كل إعادة محاولة) — يرجع بياناته للمستدعي ليسجّلها بعد نجاح التقييم فقط."""
    parts = []
    referee = ""
    ref_info = {"referee": "", "yellows": 0, "reds": 0}
    try:
        fx = api_football(f"fixtures?ids={fid}")
        if not fx:
            return "", ref_info
        status = (((fx[0].get("fixture") or {}).get("status")) or {}).get("short")
        if status not in ("FT", "AET", "PEN"):
            return "", ref_info
        referee = ((fx[0].get("fixture") or {}).get("referee")) or ""
        goals = fx[0].get("goals") or {}
        ft = (fx[0].get("score") or {}).get("fulltime") or {}
        gh = ft.get("home") if ft.get("home") is not None else goals.get("home")
        ga = ft.get("away") if ft.get("away") is not None else goals.get("away")
        parts.append(f"النتيجة النهائية (90 دقيقة): {gh}-{ga} — الحالة {status}")
        if referee:
            parts.append(f"الحكم: {referee}")
    except Exception as e:
        print("تقييم التقرير — فشل جلب النتيجة:", e)
        return "", ref_info
    yellows = reds = 0
    try:
        for side in api_football(f"fixtures/statistics?fixture={fid}"):
            name = (side.get("team") or {}).get("name", "?")
            vals = []
            for s in (side.get("statistics") or []):
                if s.get("value") is None:
                    continue
                vals.append(f"{s.get('type')}: {s.get('value')}")
                try:
                    if s.get("type") == "Yellow Cards":
                        yellows += int(s.get("value") or 0)
                    elif s.get("type") == "Red Cards":
                        reds += int(s.get("value") or 0)
                except Exception:
                    pass
            if vals:
                parts.append(f"إحصائيات {name} — " + ", ".join(vals))
    except Exception as e:
        print("تقييم التقرير — فشل الإحصائيات:", e)
    ref_info = {"referee": (referee or "").strip(),
                "yellows": yellows, "reds": reds}
    try:
        ev_lines = []
        for ev in api_football(f"fixtures/events?fixture={fid}"):
            minute = (ev.get("time") or {}).get("elapsed")
            team = (ev.get("team") or {}).get("name", "?")
            player = (ev.get("player") or {}).get("name") or ""
            ev_lines.append(f"{minute}' {ev.get('type')} ({ev.get('detail')}) "
                            f"{player} [{team}]")
        if ev_lines:
            parts.append("الأحداث:\n" + "\n".join(ev_lines))
    except Exception as e:
        print("تقييم التقرير — فشل الأحداث:", e)
    return "\n".join(parts), ref_info


def grade_scenario_report(entry: dict, actual: str) -> dict:
    """نداء Claude واحد: يقارن بنود التقرير المتوقعة بالبيانات النهائية،
    يرجع {'summary','grades':[{'claim','result'}],'lessons':[...]} أو {} عند الفشل."""
    system_prompt = (
        "أنت المقيّم الذاتي لتقارير ما قبل المباراة في محرك توقعات كرة قدم. "
        "ستصلك بنود تقرير كتبته قبل المباراة (نتيجة متوقعة، كلا الفريقين يسجلان، "
        "إجمالي الأهداف، مسجل محتمل، ركنيات، بطاقات، كرات ثابتة، نمط الشوطين) "
        "والبيانات النهائية الحقيقية.\n"
        "قيّم كل بند تحقق منه البيانات وأرجع JSON فقط بدون أي نص آخر وبدون ```:\n"
        '{"summary":"سطر واحد: كم أصاب التقرير من بنوده",'
        '"grades":[{"claim":"البند باختصار","result":"صح|خطأ|جزئي"}],'
        '"lessons":[{"lesson":"درس عام قابل للتطبيق في تقارير قادمة — سطر واحد"}]}\n'
        "الدروس تُستخلص من البنود الخاطئة فقط: نمط عام (مثل المبالغة في توقع "
        "الركنيات في المباريات المغلقة) وليس وصفاً لما حدث. إن لم توجد أخطاء "
        "أرجع lessons فارغة. استخدم الأرقام الإنجليزية (0-9) فقط."
    )
    user_text = json.dumps(
        {"match": f"{entry.get('home')} vs {entry.get('away')}",
         "league": entry.get("league"),
         "prematch_report": entry.get("report", ""),
         "actual_final_data": actual},
        ensure_ascii=False,
    )
    raw = claude_request(system_prompt, user_text, max_tokens=2500)
    if not raw:
        return {}
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except Exception:
        return {}
    return data if isinstance(data, dict) and data.get("grades") else {}


def _scenario_grade_order(pending: dict) -> list:
    """ترتيب تقارير السيناريوهات للتقييم: الأقدم أولاً (حسب موعد الانطلاق ثم
    التاريخ) لا حسب رقم المباراة. مع سقف MAX_SCENARIO_GRADES_PER_RUN وتدفق
    تقارير الظل اليومي (6/يوم)، كان الترتيب الأبجدي لأرقام المباريات يُجوّع
    الإدخالات الأقدم — رقم مباراة أكبر أبجدياً يُقيَّم أخيراً — فتُسقط بعد
    SCENARIO_MAX_AGE_DAYS دون تقييم، وتضيع إشارة التعلّم. نصرف ميزانية التقييم
    على الأقرب لانتهاء المهلة أولاً."""
    def key(fid):
        e = pending.get(fid) or {}
        ko = str(e.get("kickoff") or "")
        if not ko:
            d = str(e.get("date") or "")
            ko = d + "T00:00:00+00:00" if d else ""
        return (ko, str(fid))
    return sorted(pending.keys(), key=key)


def _grade_scenario_entry(scen: dict, fid: str, actual: str, ref_info: dict) -> bool:
    """جوهر تقييم تقرير واحد — مشترك بين تشغيلة الصباح والتقييم اللحظي.
    يقيّم بنداً-بنداً، يسجّل الحكم (REC-001: بعد نجاح التقييم فقط وبحارس fid)،
    يضيف الدروس لدفتر lessons_v2.json، يبني بطاقة التقييم ويخزنها مع السجل
    بوسم reported=False، وينقل التقرير إلى resolved. لا يرسل تيليجرام أبداً —
    التبليغ مسؤولية الصباح (فصل القياس عن التبليغ، أمر المالك 2026-08-09).
    يرجع False عند فشل التقييم (يُعاد لاحقاً)."""
    entry = scen["pending"][fid]
    result = grade_scenario_report(entry, actual)
    if not result:
        return False
    icon = {"صح": "✅", "خطأ": "❌", "جزئي": "🟡"}
    today = now_utc().strftime("%Y-%m-%d")
    # REC-001: تسجيل الحكم بعد نجاح التقييم فقط — إعادة المحاولة الفاشلة
    # لم تعد تحتسب المباراة، وحارس fid يمنع أي ازدواج مهما تكرر الاستدعاء
    if ref_info.get("referee"):
        record_referee(ref_info["referee"], ref_info.get("yellows", 0),
                       ref_info.get("reds", 0), fid)
    grades = [g for g in result.get("grades", []) if isinstance(g, dict)]
    correct = sum(1 for g in grades if g.get("result") == "صح")
    partial = sum(1 for g in grades if g.get("result") == "جزئي")
    h = entry.get("ar_home") or entry.get("home", "?")
    a = entry.get("ar_away") or entry.get("away", "?")
    shadow_tag = " (تقرير ظل — تدريب ذاتي)" if entry.get("shadow") else ""
    lines = [f"📋 تقييم تقرير المحرك 2{shadow_tag} — {h} 🆚 {a}",
             f"📊 أصاب {correct}/{len(grades)}"
             + (f" (+{partial} جزئياً)" if partial else "")]
    if result.get("summary"):
        lines.append(str(result["summary"]))
    for g in grades:
        lines.append(f"{icon.get(g.get('result'), '•')} {g.get('claim', '')}")
    # الدروس → نفس دفتر دروس المحرك 2 (يُحقن في التوقعات والتقارير القادمة)
    lessons = [str((it or {}).get("lesson") or "").strip()
               for it in result.get("lessons", []) if isinstance(it, dict)]
    lessons = [x for x in lessons if x]
    if lessons:
        ldata = load_json(LESSONS_FILE, {"lessons": []})
        ldata.setdefault("lessons", [])
        for text in lessons:
            ldata["lessons"].append({
                "date": today,
                "match": f"{entry.get('home')} vs {entry.get('away')} (تقرير)",
                "text": text,
            })
        ldata["lessons"] = ldata["lessons"][-MAX_LESSONS_STORED:]
        save_json(LESSONS_FILE, ldata)
        lines.append(f"📚 دروس جديدة من هذا التقرير: {len(lessons)}")
    entry["graded_on"] = today
    entry["correct"] = correct
    entry["total"] = len(grades)
    # التصحيح بنداً‑ببند يُحفظ مع التقرير — يظهر في مختبر الظل على اللوحة
    # (طلب المالك 2026-08-01: "أريد تفصيل ما نجح وما فشل، لا رقماً معلقاً")
    entry["grades"] = [{"claim": str(g.get("claim") or ""),
                        "result": str(g.get("result") or "")} for g in grades]
    if result.get("summary"):
        entry["grade_summary"] = str(result["summary"])
    # بطاقة التقييم تُخزن ولا تُرسل الآن — تبليغ الصباح يجمعها كما كان دائماً
    entry["scorecard"] = "\n".join(lines)
    entry["reported"] = False
    scen["resolved"].append(entry)
    del scen["pending"][fid]
    return True


def live_grade_scenarios(cap: int = None) -> int:
    """⚡ التقييم اللحظي (أمر المالك 2026-08-09): يقيّم تقارير المباريات
    المنتهية فور اكتمال بياناتها النهائية بدل انتظار الصباح — فتظهر النتيجة
    على اللوحة خلال ~دورة مراقب. بوابة تحقق صارمة: لا محاولة قبل
    LIVE_GRADE_MIN_MINUTES من الانطلاق، ولا تقييم بلا إحصائيات نهائية
    موجودة فعلاً — الناقص يُترك للدورة القادمة وللصباح كشبكة أمان.
    التبليغ البشري لا يتغير: البطاقات تصل تيليجرام مجمعة صباحاً كما كانت."""
    if not LIVE_SCENARIO_GRADING:
        return 0
    cap = LIVE_GRADES_PER_CYCLE if cap is None else cap
    scen = load_json(SCENARIOS_FILE, {"pending": {}, "resolved": []})
    scen.setdefault("pending", {})
    scen.setdefault("resolved", [])
    if not scen["pending"]:
        return 0
    graded = 0
    ready_after = now_utc() - timedelta(minutes=LIVE_GRADE_MIN_MINUTES)
    for fid in _scenario_grade_order(scen["pending"]):
        if graded >= cap:
            break
        entry = scen["pending"][fid]
        try:
            kickoff = datetime.fromisoformat(entry.get("kickoff", ""))
        except Exception:
            continue                  # بلا وقت انطلاق موثوق — يحسمه الصباح
        if kickoff > ready_after:
            continue                  # المباراة لم تنته/تكتمل بياناتها بعد
        actual, ref_info = actual_match_data(fid)
        # بوابة التحقق: نتيجة نهائية + إحصائيات موجودة فعلاً — وإلا ننتظر
        if not actual or "إحصائيات" not in actual:
            continue
        if _grade_scenario_entry(scen, fid, actual, ref_info):
            graded += 1
    if graded:
        save_json(SCENARIOS_FILE, scen)
        print(f"⚡ التقييم اللحظي: قُيّم {graded} تقريراً فور اكتمال بياناته.")
    return graded


def resolve_scenarios() -> int:
    """حلقة التعلم الذاتي للسيناريوهات: يقيّم كل تقرير ما قبل مباراة محفوظ
    مقابل البيانات النهائية، يرسل بطاقة التقييم للمالك، ويضيف الدروس إلى
    lessons_v2.json (فتُحقن تلقائياً في كل تقرير وتوقع قادم).
    مع التقييم اللحظي: ما قُيّم ليلاً لا يُعاد تقييمه — تُرسل بطاقته فقط."""
    scen = load_json(SCENARIOS_FILE, {"pending": {}, "resolved": []})
    scen.setdefault("pending", {})
    scen.setdefault("resolved", [])
    # بطاقات قُيّمت لحظياً ليلاً وتنتظر تبليغ الصباح — تُرسل حتى لو خلا pending
    unreported = [e for e in scen["resolved"]
                  if e.get("scorecard") and not e.get("reported")]
    if not scen["pending"] and not unreported:
        return 0
    graded = 0
    dirty = False
    for fid in _scenario_grade_order(scen["pending"]):
        if graded >= MAX_SCENARIO_GRADES_PER_RUN:
            break
        entry = scen["pending"][fid]
        try:
            kickoff = datetime.fromisoformat(entry.get("kickoff", ""))
        except Exception:
            kickoff = None
        if kickoff and kickoff > now_utc() - timedelta(hours=3):
            continue                              # لم تنته بعد — دورها لاحقاً
        actual, ref_info = actual_match_data(fid)
        if not actual:
            # لا بيانات نهائية: مؤجلة/ملغاة أو خلل — نسقطها بعد مهلة
            age_ok = (entry.get("date") or "9999") >= \
                (now_utc() - timedelta(days=SCENARIO_MAX_AGE_DAYS)).strftime("%Y-%m-%d")
            if not age_ok:
                del scen["pending"][fid]
                dirty = True
            continue
        if not _grade_scenario_entry(scen, fid, actual, ref_info):
            # فشل التقييم — إعادة غداً، لكن ليس إلى الأبد (علة "التقرير
            # العالق" 2026-08-09: تقرير 29 يوليو ظل يُعاد 11 يوماً لأن مهلة
            # الإسقاط كانت تُطبق على مسار غياب البيانات فقط لا مسار فشل
            # التقييم — فبقي معلقاً يستهلك محاولة يومية بلا نهاية)
            age_ok = (entry.get("date") or "9999") >= \
                (now_utc() - timedelta(days=SCENARIO_MAX_AGE_DAYS)).strftime("%Y-%m-%d")
            if not age_ok:
                print(f"إسقاط تقرير عالق تجاوز مهلة الإسقاط: "
                      f"{entry.get('home')} × {entry.get('away')}")
                del scen["pending"][fid]
                dirty = True
            continue
        graded += 1
        dirty = True
    # تبليغ الصباح (تفضيل المالك 2026-08-09): بطاقات كل التقارير المُقيَّمة —
    # لحظياً ليلاً أو في هذه التشغيلة — تُرسل الآن مجمعة كما كانت دائماً
    for e in scen["resolved"]:
        if e.get("scorecard") and not e.get("reported"):
            send_telegram_long(e["scorecard"])
            e["reported"] = True
            dirty = True
    if dirty:
        if SCENARIOS_RESOLVED_CAP:   # 0 = بلا حذف (أمر المالك 2026-08-09)
            scen["resolved"] = scen["resolved"][-SCENARIOS_RESOLVED_CAP:]
        save_json(SCENARIOS_FILE, scen)
    return graded


def consolidate_lessons() -> int:
    """عندما يتضخم دفتر الدروس، يدمج Claude الدروس المتشابهة في مبادئ عامة أقوى
    وأقل عدداً — فتبقى الدروس المحقونة في كل توقع مركزة بلا تكرار.
    يرجع عدد المبادئ بعد الدمج (0 = لم يحدث دمج)."""
    data = load_json(LESSONS_FILE, {"lessons": []})
    lessons = data.get("lessons") or []
    if len(lessons) <= CONSOLIDATE_THRESHOLD:
        return 0

    texts = []
    for it in lessons:
        t = it if isinstance(it, str) else (it.get("text") or "")
        t = str(t).strip()
        if t:
            texts.append(t)

    system_prompt = (
        "أنت محرر معرفة لمحرك توقعات كرة قدم. ستصلك قائمة دروس مستخلصة من أخطاء "
        f"سابقة، كثير منها متشابه أو متكرر. ادمجها في {CONSOLIDATE_TARGET} مبدأً "
        "عاماً أو أقل: اجمع المتشابه في مبدأ واحد أقوى وأوضح، واحذف المكرر، "
        "وحافظ على أي درس فريد مهم.\n"
        "أرجع ردك بصيغة JSON فقط — مصفوفة نصوص بدون أي شيء آخر وبدون ```:\n"
        '["مبدأ عام بالعربي من سطر واحد", ...]\n'
        "استخدم الأرقام الإنجليزية (0-9) فقط ولا تستخدم الأرقام العربية (٠-٩) أبداً."
    )
    raw = claude_request(system_prompt, json.dumps(texts, ensure_ascii=False), max_tokens=3000)
    items = parse_json_array(raw)
    principles = [str(x).strip() for x in items if str(x).strip() and isinstance(x, (str,))]
    if not principles:
        return 0   # فشل الدمج → نبقي الدروس كما هي (لا نخسر شيئاً أبداً)

    today = now_utc().strftime("%Y-%m-%d")
    data["lessons"] = [
        {"date": today, "match": "خلاصة مُجمّعة", "text": t}
        for t in principles[:CONSOLIDATE_TARGET]
    ]
    save_json(LESSONS_FILE, data)
    return len(data["lessons"])


# ================== سحب مباريات الـ 24 ساعة القادمة (مطابق للمحرك 1) ==================
def get_upcoming_24h() -> list:
    start = now_utc()
    end = start + timedelta(hours=24)
    days = {start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")}

    matches = []
    fetch_errors = []
    for d in sorted(days):
        try:
            matches.extend(api_football(f"fixtures?date={d}"))
        except Exception as e:
            print(f"فشل سحب مباريات {d}:", e)
            fetch_errors.append(str(e))
    if fetch_errors and not matches:
        raise RuntimeError(
            "تعذر سحب أي مباريات — غالباً مفتاح API_FOOTBALL_KEY في Secrets "
            "قديم أو خاطئ. آخر خطأ: " + fetch_errors[-1]
        )

    out = []
    seen = set()
    for fx in matches:
        fixture = fx.get("fixture") or {}
        league = fx.get("league") or {}
        teams = fx.get("teams") or {}
        fid = str(fixture.get("id"))
        if fid in seen:
            continue
        seen.add(fid)
        status = ((fixture.get("status") or {}).get("short")) or ""
        if status != "NS":
            continue
        if is_excluded(league):
            continue
        _t = fx.get("teams") or {}
        if is_womens_match((_t.get("home") or {}).get("name"), (_t.get("away") or {}).get("name")) \
                or is_youth_match((_t.get("home") or {}).get("name"), (_t.get("away") or {}).get("name")):
            continue
        try:
            kickoff = datetime.fromisoformat(fixture.get("date"))
        except Exception:
            continue
        if not (start <= kickoff <= end):
            continue
        out.append({
            "fid": fid,
            "kickoff": kickoff.isoformat(),
            "date": kickoff.strftime("%Y-%m-%d"),
            "home": (teams.get("home") or {}).get("name", "?"),
            "away": (teams.get("away") or {}).get("name", "?"),
            "home_id": (teams.get("home") or {}).get("id"),
            "away_id": (teams.get("away") or {}).get("id"),
            "home_logo": (teams.get("home") or {}).get("logo", ""),
            "away_logo": (teams.get("away") or {}).get("logo", ""),
            "league_logo": league.get("logo", ""),
            "league": f"{league.get('name', '?')} ({league.get('country', '?')})",
            "league_id": league.get("id"),
            "season": league.get("season"),
            "round": league.get("round") or "",
            "venue": ", ".join(x for x in (
                ((fixture.get("venue") or {}).get("name")),
                ((fixture.get("venue") or {}).get("city")),
                league.get("country"),
            ) if x),
            "top": league.get("id") in TOP_LEAGUE_IDS,
            "mine": league.get("id") in OWNER_LEAGUE_IDS,   # 🎛 الشريحة الثالثة
            "is_cup": is_cup_fixture(league.get("name"), league.get("round")),
        })

    out.sort(key=lambda m: (not m["top"], m["kickoff"]))
    return out[:MAX_PREDICTIONS_24H]


# ================== السياق الإضافي للدوريات الكبرى ==================
def _enrich_call(path: str, budget: dict) -> list:
    """نداء API ضمن سقف الأمان — يرجع [] عند تجاوز السقف أو أي فشل."""
    if budget["used"] >= ENRICH_CALL_BUDGET:
        return []
    budget["used"] += 1
    try:
        return api_football(path)
    except Exception as e:
        print(f"فشل نداء السياق {path}:", e)
        return []


def standings_context(m: dict, budget: dict, cache: dict) -> str:
    """ترتيب الفريقين في الدوري (نداء واحد لكل دوري، يُخزّن مؤقتاً)."""
    league_id, season = m.get("league_id"), m.get("season")
    if not league_id or not season:
        return ""
    key = f"{league_id}-{season}"
    if key not in cache:
        rows = {}
        for entry in _enrich_call(f"standings?league={league_id}&season={season}", budget):
            for group in ((entry.get("league") or {}).get("standings") or []):
                for row in group:
                    tid = ((row.get("team") or {}).get("id"))
                    if tid:
                        rows[tid] = row
        cache[key] = rows
    rows = cache[key]
    parts = []
    for side, tid in (("home", m.get("home_id")), ("away", m.get("away_id"))):
        row = rows.get(tid)
        if not row:
            continue
        allg = (row.get("all") or {})
        goals = (allg.get("goals") or {})
        parts.append(
            f"{m[side]}: rank {row.get('rank')}, {row.get('points')} pts, "
            f"played {allg.get('played')}, GF {goals.get('for')} GA {goals.get('against')}, "
            f"form {row.get('form') or '?'}"
        )
    return ("Standings — " + " | ".join(parts)) if parts else ""


def h2h_context(m: dict, budget: dict) -> str:
    """آخر 5 مواجهات مباشرة بين الفريقين."""
    hid, aid = m.get("home_id"), m.get("away_id")
    if not hid or not aid:
        return ""
    lines = []
    for fx in _enrich_call(f"fixtures/headtohead?h2h={hid}-{aid}&last=5", budget):
        teams = fx.get("teams") or {}
        goals = fx.get("goals") or {}
        date = (((fx.get("fixture") or {}).get("date")) or "")[:10]
        gh, ga = goals.get("home"), goals.get("away")
        if gh is None or ga is None:
            continue
        lines.append(
            f"{date}: {(teams.get('home') or {}).get('name', '?')} {gh}-{ga} "
            f"{(teams.get('away') or {}).get('name', '?')}"
        )
    return ("Head-to-head (last 5): " + "; ".join(lines)) if lines else ""


def form_context(team_id, team_name: str, budget: dict) -> str:
    """آخر 5 نتائج للفريق + أيام الراحة منذ آخر مباراة (من نفس النداء —
    الإرهاق وضغط الجدول عامل حقيقي، خطوة استكشاف البيانات 2)."""
    if not team_id:
        return ""
    lines = []
    last_dates = []
    for fx in _enrich_call(f"fixtures?team={team_id}&last=5", budget):
        teams = fx.get("teams") or {}
        goals = fx.get("goals") or {}
        try:
            last_dates.append(datetime.fromisoformat(
                ((fx.get("fixture") or {}).get("date") or "").replace("Z", "+00:00")))
        except Exception:
            pass
        gh, ga = goals.get("home"), goals.get("away")
        if gh is None or ga is None:
            continue
        home = (teams.get("home") or {})
        away = (teams.get("away") or {})
        at_home = home.get("id") == team_id
        mine, theirs = (gh, ga) if at_home else (ga, gh)
        opp = (away if at_home else home).get("name", "?")
        letter = "W" if mine > theirs else ("L" if mine < theirs else "D")
        lines.append(f"{letter} {mine}-{theirs} v {opp} ({'H' if at_home else 'A'})")
    if not lines:
        return ""
    rest = ""
    if last_dates:
        days = max(0, (now_utc() - max(last_dates)).days)
        rest = f" — أيام الراحة منذ آخر مباراة: {days}"
    return f"{team_name} last 5: " + ", ".join(lines) + rest


def injuries_context(m: dict, budget: dict) -> str:
    """الإصابات والغيابات المعلنة لهذه المباراة."""
    lines = []
    for item in _enrich_call(f"injuries?fixture={m['fid']}", budget)[:12]:
        player = (item.get("player") or {})
        team = (item.get("team") or {}).get("name", "?")
        name = player.get("name", "?")
        reason = player.get("reason") or player.get("type") or "?"
        lines.append(f"{name} ({team}: {reason})")
    return ("Injuries/absences: " + "; ".join(lines)) if lines else ""


def odds_context(m: dict, budget: dict) -> str:
    """أودز السوق (إجماع المراهنين) لنتيجة المباراة، مع الاحتمالات الضمنية
    بعد إزالة هامش الشركة — أقوى إشارة منفردة متاحة."""
    for entry in _enrich_call(f"odds?fixture={m['fid']}", budget):
        for bm in (entry.get("bookmakers") or []):
            for bet in (bm.get("bets") or []):
                if (bet.get("name") or "").lower() != "match winner":
                    continue
                vals = {v.get("value"): v.get("odd") for v in (bet.get("values") or [])}
                try:
                    oh = float(vals["Home"])
                    od = float(vals["Draw"])
                    oa = float(vals["Away"])
                except Exception:
                    continue
                inv = [1 / oh, 1 / od, 1 / oa]
                s = sum(inv)
                ph, pd, pa = (round(100 * x / s) for x in inv)
                # نخزّن احتمالات السوق على المباراة نفسها → تنتقل تلقائياً إلى
                # سجل pending (شريحة "المحرك ضد السوق" على اللوحة + قياس مستقبلي)
                m["mkt_home"], m["mkt_draw"], m["mkt_away"] = ph, pd, pa
                return (
                    f"Market odds ({bm.get('name', '?')}): home {oh} / draw {od} / away {oa}"
                    f" => implied probabilities {ph}% / {pd}% / {pa}%"
                )
    return ""


def api_prediction_context(m: dict, budget: dict) -> str:
    """توقع النموذج الإحصائي لـ API-Football (رأي ثانٍ مستقل)."""
    for entry in _enrich_call(f"predictions?fixture={m['fid']}", budget):
        pred = entry.get("predictions") or {}
        parts = []
        pct_ = pred.get("percent") or {}
        if pct_:
            parts.append(
                f"home {pct_.get('home', '?')}, draw {pct_.get('draw', '?')}, "
                f"away {pct_.get('away', '?')}"
            )
        comp = ((entry.get("comparison") or {}).get("total")) or {}
        if comp:
            parts.append(f"overall strength: home {comp.get('home', '?')} vs away {comp.get('away', '?')}")
        advice = (pred.get("advice") or "").strip()
        if advice:
            parts.append(f"advice: {advice}")
        if parts:
            return "Statistical model (API-Football): " + "; ".join(parts)
    return ""


def competition_context(m: dict) -> str:
    """سياق البطولة بلا أي نداء API: كأس أم دوري، أي جولة/مرحلة، ذهاب أم إياب.
    (خطوة استكشاف البيانات 2026-07-15 — المدرب وسياق البطولة يصنعان فرقاً.)"""
    rnd = (m.get("round") or "").strip()
    if not rnd:
        return ""
    return (f"سياق البطولة: {m.get('league', '')} — المرحلة: {rnd}. "
            "انتبه: مباريات الكؤوس والأدوار الإقصائية (ذهاب/إياب) لها منطق "
            "مختلف عن الدوري — الدوافع، التحفظ، وإدارة النتيجة.")


def travel_context(m: dict) -> str:
    """عبء السفر والبيئة بلا أي نداء API (خطوة استكشاف البيانات 2):
    ملعب المباراة ومدينته معروفان، والنموذج يعرف مواقع الفرق — فيُطلب منه
    تقدير مسافة سفر الضيف، فرق التوقيت، المناخ/الارتفاع، وأي عوامل لوجستية
    (رحلة طيران طويلة قبل المباراة تصنع فرقاً حقيقياً)."""
    venue = (m.get("venue") or "").strip()
    if not venue:
        return ""
    return (
        f"ملعب المباراة: {venue}. "
        f"من معرفتك بموقع فريق {m.get('away', 'الضيف')}: قدّر عبء سفره لهذه "
        "المباراة — مسافة الرحلة وعدد ساعات الطيران، فرق التوقيت، المناخ أو "
        "الارتفاع، وأي عوامل لوجستية أو بيئية أخرى تؤثر على الجاهزية."
    )


def coach_context(m: dict, budget: dict) -> str:
    """مدربا الفريقين (نداءان): اسم المدرب وجنسيته وعمره — شخصية المدرب
    وخبرته (مدرب كبير، بداية عهد جديدة، مغامر أم متحفظ) عامل مؤثر."""
    lines = []
    for team_id, team_name in ((m.get("home_id"), m["home"]),
                               (m.get("away_id"), m["away"])):
        if not team_id:
            continue
        try:
            coaches = _enrich_call(f"coachs?team={team_id}", budget)
        except Exception:
            continue
        if not coaches:
            continue
        c = coaches[0] or {}
        name = c.get("name") or ""
        if not name:
            continue
        extra = ", ".join(x for x in (c.get("nationality"),
                                      f"العمر {c.get('age')}" if c.get("age") else "")
                          if x)
        lines.append(f"{team_name}: المدرب {name}" + (f" ({extra})" if extra else ""))
    if not lines:
        return ""
    return ("المدربان (استخدم معرفتك بهما — أسلوبهما وخبرتهما وتأثيرهما):\n"
            + "\n".join(lines))



# طقس ساعة الانطلاق — Open-Meteo مجاني تماماً وبلا مفتاح (خطوة استكشاف 4)
WEATHER_GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
_GEO_CACHE: dict = {}   # مدينة → (خط عرض، خط طول) — مرة واحدة لكل تشغيلة


def weather_context(m: dict) -> str:
    """حرارة/أمطار/رياح ساعة الانطلاق في مدينة الملعب — المطر الغزير والرياح
    القوية والحر الشديد تغيّر أسلوب اللعب. أي فشل يرجع '' بصمت."""
    venue = m.get("venue") or ""
    parts = [x.strip() for x in venue.split(",")]
    city = parts[1] if len(parts) >= 2 else ""
    if not city:
        return ""
    try:
        if city not in _GEO_CACHE:
            r = requests.get(WEATHER_GEO_URL,
                             params={"name": city, "count": 1}, timeout=15)
            res = (r.json().get("results") or [])
            _GEO_CACHE[city] = (
                (res[0].get("latitude"), res[0].get("longitude")) if res else None
            )
        loc = _GEO_CACHE.get(city)
        if not loc:
            return ""
        kickoff = datetime.fromisoformat(m["kickoff"])
        day = kickoff.strftime("%Y-%m-%d")
        r = requests.get(WEATHER_URL, params={
            "latitude": loc[0], "longitude": loc[1],
            "hourly": "temperature_2m,precipitation,wind_speed_10m",
            "timezone": "UTC", "start_date": day, "end_date": day,
        }, timeout=15)
        hourly = r.json().get("hourly") or {}
        times = hourly.get("time") or []
        target = kickoff.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00")
        if target not in times:
            return ""
        i = times.index(target)
        temp = (hourly.get("temperature_2m") or [])[i]
        rain = (hourly.get("precipitation") or [])[i]
        wind = (hourly.get("wind_speed_10m") or [])[i]
        return (f"طقس ساعة الانطلاق في {city}: حرارة {temp}°م، أمطار {rain} ملم، "
                f"رياح {wind} كم/س — خذه بالحسبان إن كان مؤثراً على أسلوب اللعب.")
    except Exception:
        return ""


def transfers_context(m: dict, budget: dict) -> str:
    """انتقالات آخر 90 يوماً للفريقين (نداءان) — خطوة استكشاف 3: القادمون
    والمغادرون يغيّرون قوة الفريق قبل أن تعكسها النتائج."""
    cutoff = (now_utc() - timedelta(days=90)).strftime("%Y-%m-%d")
    lines = []
    for team_id, team_name in ((m.get("home_id"), m["home"]),
                               (m.get("away_id"), m["away"])):
        if not team_id:
            continue
        try:
            items = _enrich_call(f"transfers?team={team_id}", budget)
        except Exception:
            continue
        recent = []
        for it in items or []:
            player = ((it.get("player") or {}).get("name")) or "?"
            for tr in (it.get("transfers") or []):
                date = (tr.get("date") or "")[:10]
                if not date or date < cutoff:
                    continue
                t_in = (((tr.get("teams") or {}).get("in")) or {}).get("id")
                t_out = (((tr.get("teams") or {}).get("out")) or {}).get("id")
                if t_in == team_id:
                    recent.append(f"وصل {player} ({date})")
                elif t_out == team_id:
                    recent.append(f"غادر {player} ({date})")
        if recent:
            lines.append(f"{team_name}: " + "، ".join(sorted(recent, reverse=True)[:6]))
    if not lines:
        return ""
    return ("انتقالات آخر 90 يوماً (قد تغيّر قوة الفريق قبل أن تعكسها النتائج):\n"
            + "\n".join(lines))


def _team_news_titles(team: str) -> list:
    """عناوين مستهدفة للفريق من Google News RSS (مجاني، لا يكلّف رصيد API-Football).
    نفس نهج team_news_headlines في monitor.py — مصدر شرعي للأخبار الطازجة."""
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
        titles = re.findall(r"<title>(.*?)</title>", r.text)[1:5]
        return [html_mod.unescape(t).strip() for t in titles if t.strip()]
    except Exception as e:
        print("أخبار الفريق (المحرك 2) — فشل الجلب:", e)
        return []


def team_news_context(m: dict) -> str:
    """أخبار مستهدفة لكل فريق قبل التوقع اليومي (Google News RSS، مجاني) —
    توسيع خطوة الاستكشاف: كانت الأخبار المستهدفة تصل تقارير ما قبل المباراة
    فقط؛ الآن تغذّي كل مباراة من الدوريات ذات الأولوية (المالك 2026-07-18):
    غياب/انتقال/أزمة طازجة قبل أن تصل العناوين الكبرى — لرفع نسبة الحماية.
    خاص بالمحرك 2 (القاعدة 7)."""
    lines = []
    for team in (m.get("home"), m.get("away")):
        for title in _team_news_titles(team):
            lines.append(f"- {team}: {title}")
    if not lines:
        return ""
    return ("أخبار طازجة مستهدفة للفريقين (غياب/انتقال/أزمة قد لا تصل العناوين "
            "الكبرى بعد):\n" + "\n".join(lines))


def build_context(m: dict, budget: dict, standings_cache: dict) -> str:
    parts = [
        competition_context(m),
        travel_context(m),
        standings_context(m, budget, standings_cache),
        h2h_context(m, budget),
        form_context(m.get("home_id"), m["home"], budget),
        form_context(m.get("away_id"), m["away"], budget),
        injuries_context(m, budget),
        odds_context(m, budget),
        api_prediction_context(m, budget),
        coach_context(m, budget),
        transfers_context(m, budget),
        weather_context(m),
        team_news_context(m),
    ]
    return "\n".join(p for p in parts if p)


# ================== توقعات Claude (احتمالات، على دفعات) ==================
# REC-002 (قرار المالك 2026-08-08): شريحة ثقة عددها أقل من هذا الحد تُعرض
# بوسم "عينة غير كافية" بدل عرضها كحقيقة — سطر "70%+: 100% (5/5)" كان يقدَّم
# للنموذج كدليل معصومية وهو مبني على 5 مباريات فقط.
CALIBRATION_MIN_BUCKET = 20


def _confidence_line(label: str, d: dict) -> str:
    """سطر شريحة ثقة واحد في سجل المعايرة، مع وسم العينات الصغيرة."""
    line = f"- عندما كانت ثقتك {label}: {pct(d)}"
    if 0 < d.get("total", 0) < CALIBRATION_MIN_BUCKET:
        line += " (عينة غير كافية — لا تعتمد عليها)"
    return line


def calibration_text(stats: dict) -> str:
    # REC-002: الشريحة تحت 50 كانت غائبة عن السجل رغم أنها تضم أغلبية التوقعات
    # (70.5% وقت القرار) — أكبر كتلة تغذية راجعة للمعايرة لم تكن تصل للنموذج.
    if not stats["overall"]["total"]:
        return "لا يوجد سجل تاريخي بعد — كن متحفظاً في توزيع الاحتمالات."
    by_conf = stats["by_confidence"]
    return (
        f"سجل دقتك التاريخي الفعلي (استخدمه لمعايرة احتمالاتك):\n"
        f"- الإجمالي: {pct(stats['overall'])}\n"
        f"- آخر 30 يوماً: {pct(stats['last30'])}\n"
        f"- الدوريات الكبرى: {pct(stats['top_leagues'])} | البقية: {pct(stats['other_leagues'])}\n"
        + _confidence_line("70%+", by_conf["70+"]) + "\n"
        + _confidence_line("60-69%", by_conf["60-69"]) + "\n"
        + _confidence_line("50-59%", by_conf["50-59"]) + "\n"
        + _confidence_line("تحت 50%", by_conf["<50"]) + "\n"
        f"إذا كانت دقتك الفعلية أقل من ثقتك المعلنة فاخفض الاحتمال الأعلى، والعكس صحيح."
    )


def lessons_text() -> str:
    """أحدث الدروس المستخلصة من الأخطاء السابقة (تُملأ في المرحلة 3)."""
    data = load_json(LESSONS_FILE, {"lessons": []})
    lessons = data.get("lessons") or []
    lines = []
    for it in lessons[-MAX_LESSONS_IN_PROMPT:]:
        text = it if isinstance(it, str) else (it.get("text") or it.get("lesson") or "")
        text = str(text).strip()
        if text:
            lines.append(f"- {text}")
    if not lines:
        return ""
    return "دروس من أخطائك السابقة:\n" + "\n".join(lines)


def news_context() -> str:
    news = load_json(NEWS_FILE, {})
    items = news.get("items", [])[:10]
    if not items:
        return ""
    lines = [f"- {it.get('title', '')}" for it in items if it.get("title")]
    return "آخر عناوين الأخبار الكروية (قد تحتوي إصابات أو أخباراً مؤثرة):\n" + "\n".join(lines)


def claude_predict_batch(batch: list, stats: dict, enriched: bool) -> dict:
    """يرسل دفعة مباريات لـ Claude ويرجع {fid: توقع بالاحتمالات}."""
    payload = []
    for m in batch:
        item = {
            "id": m["fid"],
            "home": m["home"],
            "away": m["away"],
            "league": m["league"],
            "kickoff_utc": m["kickoff"],
        }
        if enriched and m.get("context"):
            item["context"] = m["context"]
        payload.append(item)

    extra = ""
    if enriched:
        extra = (
            "لكل مباراة حقل context يحتوي بيانات حقيقية محدثة: الترتيب، المواجهات "
            "المباشرة، آخر 5 نتائج لكل فريق، الإصابات، أودز السوق باحتمالاتها "
            "الضمنية، وتوقع نموذج إحصائي مستقل. اعتمد عليها أولاً قبل معرفتك العامة.\n"
            "أودز السوق إشارة قوية جداً — خذها مرجعاً أساسياً، لكنك لست مقلداً لها: "
            "ابتعد عنها فقط عندما تملك سبباً حقيقياً من البيانات أو من دروسك السابقة، "
            "واذكر السبب في reason.\n\n"
        )

    prompt_parts = [
        "أنت خبير توقع مباريات كرة قدم من الطراز الأول. ستصلك قائمة مباريات تقام خلال 24 ساعة.\n"
        "لكل مباراة وزّع احتمالات النتائج الثلاث (فوز المضيف / تعادل / فوز الضيف) "
        "بحيث يكون مجموعها 100 بالضبط.\n",
        extra,
        calibration_text(stats),
    ]
    # إفصاح الثقة (REC-004): النموذج كان يكبح نفسه عند 65 رغم سجل ~96% فوقها
    if CONF_DISCLOSURE:
        prompt_parts.append(
            "\nملاحظة معايرة مهمة: سجلك الفعلي عندما أعلنت ثقة 65% أو أكثر هو "
            "~96% — أنت أدق مما تعلن في هذه المنطقة. لا تتردد في إعلان احتمال "
            "أعلى من 65 عندما تكون قناعتك حقيقية ومبنية على البيانات — "
            f"ولا تتجاوز {CONF_DISCLOSURE_CAP} لأي نتيجة مهما بلغت قناعتك."
        )
    lessons = lessons_text()
    if lessons:
        prompt_parts.append("\n" + lessons)
    prompt_parts.append(
        "\nأرجع ردك بصيغة JSON فقط — مصفوفة واحدة بدون أي نص قبلها أو بعدها وبدون علامات ```:\n"
        '[{"id":"...","ar_home":"...","ar_away":"...","ar_league":"...",'
        '"prob_home":55,"prob_draw":25,"prob_away":20,"reason":"سطر واحد بالعربي"}]\n\n'
        "قواعد:\n"
        "- prob_home + prob_draw + prob_away = 100 بالضبط، أرقام صحيحة.\n"
        "- كرة القدم مليئة بالمفاجآت — لا تعطِ أي نتيجة احتمالاً أعلى من 85.\n"
        "- ar_home/ar_away/ar_league: الأسماء العربية الشائعة في الإعلام الرياضي، "
        "وإذا كان الاسم غير مشهور فاكتبه بحروف عربية.\n"
        "- استخدم الأرقام الإنجليزية (0-9) فقط ولا تستخدم الأرقام العربية (٠-٩) أبداً.\n"
        "- reason: مختصر وواضح بدون حشو، يذكر العامل الحاسم."
    )
    system_prompt = "".join(prompt_parts)

    user_text = json.dumps(payload, ensure_ascii=False)
    ctx = news_context()
    if ctx:
        user_text = ctx + "\n\nالمباريات:\n" + user_text

    body = {
        "model": CLAUDE_MODEL,
        "max_tokens": 3000,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_text}],
    }
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
            timeout=180,
        )
        r.raise_for_status()
        data = r.json()
        text = "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        ).strip()
        return parse_predictions_json(text)
    except Exception as e:
        detail = ""
        resp = getattr(e, "response", None)
        if resp is not None:
            try: detail = " — " + resp.text[:300]
            except Exception: pass
        print(f"Claude error: {e}{detail}")
        return {}


def parse_predictions_json(text: str) -> dict:
    """يحوّل رد Claude إلى {fid: توقع} — يطبّع الاحتمالات لمجموع 100 ويشتق الاختيار."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    if not text.startswith("["):
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if not m:
            return {}
        text = m.group(0)
    try:
        items = json.loads(text)
    except Exception as e:
        print("JSON parse error:", e)
        return {}
    out = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        fid = str(it.get("id", ""))
        if not fid:
            continue
        try:
            probs = {
                k: max(0, int(round(float(it.get(f"prob_{k}", 0)))))
                for k in ("home", "draw", "away")
            }
        except Exception:
            continue
        total = sum(probs.values())
        if total <= 0:
            continue
        if total != 100:
            scaled = {k: round(v * 100 / total) for k, v in probs.items()}
            kmax = max(scaled, key=scaled.get)
            scaled[kmax] += 100 - sum(scaled.values())
            probs = scaled
        pick = max(("home", "draw", "away"), key=lambda k: probs[k])
        # سقف الإفصاح (REC-004): 80 يحتوي مبالغة النموذج بعد فتح ما فوق 65 —
        # عند تعطيل المفتاح يعود السقف القديم 85. القص يمسّ الثقة المعلنة فقط،
        # لا الاحتمالات ولا الاختيار.
        conf_cap = CONF_DISCLOSURE_CAP if CONF_DISCLOSURE else 85
        conf = max(30, min(conf_cap, probs[pick]))
        out[fid] = {
            "pick": pick,
            "confidence": conf,
            "prob_home": probs["home"],
            "prob_draw": probs["draw"],
            "prob_away": probs["away"],
            "reason": (it.get("reason") or "").strip(),
            "ar_home": (it.get("ar_home") or "").strip(),
            "ar_away": (it.get("ar_away") or "").strip(),
            "ar_league": (it.get("ar_league") or "").strip(),
        }
    return out


# كلمات تكشف مباريات الكأس (بالاسم) والإقصاء/التصفيات (بالجولة)
_CUP_NAME_KW = ("cup", "coupe", "copa", "pokal", "beker", "taça", "taca",
                "cupa", "kupa", "cupen", "supercup", "كأس")
_CUP_ROUND_KW = ("qualif", "preliminary", "play-off", "playoff", "knockout",
                 "round of", "replay")


def is_cup_fixture(league_name: str, round_str: str) -> bool:
    """كأس/إقصاء؟ — الأودز غالباً مفقودة لهذه المباريات وهي كثيرة المفاجآت."""
    ln = (league_name or "").lower()
    rn = (round_str or "").lower()
    return (any(k in ln for k in _CUP_NAME_KW)
            or any(k in rn for k in _CUP_ROUND_KW))


def apply_cup_guardrail(entry: dict) -> None:
    """حارس مباريات الكأس/الإقصاء (توجيه المالك 2026-07-18).

    يعمل بعد النموذج على مباريات الكأس فقط: يرفع احتمال التعادل إلى حدّ أدنى
    (مفاجآت الكأس كثيرة) ثم يُسقّف الثقة عند CUP_CONF_CAP حتى لا تتسلّل تخمينات
    الكأس إلى خانة الثقة العالية (70%+). لا يغيّر الطرف المُختار (رفع التعادل
    لا يتجاوز المرشّح أبداً)، ولا يمسّ التعلّم — المعايرة والدروس تتعلّمان من
    النتيجة الحقيقية كالمعتاد. للتعطيل: CUP_GUARDRAIL=False."""
    if not (CUP_GUARDRAIL and entry.get("is_cup")):
        return
    try:
        ph = int(entry["prob_home"]); pd = int(entry["prob_draw"]); pa = int(entry["prob_away"])
    except (KeyError, TypeError, ValueError):
        return
    if pd < CUP_MIN_DRAW:
        need = CUP_MIN_DRAW - pd
        rest = ph + pa
        if rest > 0:
            ph -= int(round(need * ph / rest))
            pa -= int(round(need * pa / rest))
        pd = CUP_MIN_DRAW
        probs = {"home": max(0, ph), "draw": max(0, pd), "away": max(0, pa)}
        tot = sum(probs.values()) or 1
        if tot != 100:
            probs = {k: round(v * 100 / tot) for k, v in probs.items()}
            km = max(probs, key=probs.get)
            probs[km] += 100 - sum(probs.values())
        entry["prob_home"], entry["prob_draw"], entry["prob_away"] = (
            probs["home"], probs["draw"], probs["away"])
        entry["pick"] = max(("home", "draw", "away"), key=lambda k: probs[k])
    # سقّف الثقة (الطرف المُختار ثابت)
    entry["confidence"] = max(30, min(CUP_CONF_CAP, int(entry["prob_" + entry["pick"]])))


# ================== ملخص تيليجرام ==================
PICK_AR = {"home": "فوز {h}", "draw": "تعادل", "away": "فوز {a}"}


def pick_label(p: dict) -> str:
    h = p.get("ar_home") or p.get("home", "?")
    a = p.get("ar_away") or p.get("away", "?")
    return PICK_AR[p["pick"]].format(h=h, a=a)


def find_data_leaks(store: dict) -> list:
    """🚨 حارس البيانات النظيفة (درس 2026-08-01 — اكتشاف المالك بالصدفة ممنوع
    أن يتكرر): يفتش ما دخل الذاكرة فعلاً — الانتظار كله + المُقيَّم في آخر
    يومين — عن أنماط بيانات محظورة (سيدات بلاحقة W أو كلمة دالة في اسم
    الدوري المخزَّن). أي التقاط = رسالة تيليجرام صاخبة للمالك، لا صمت."""
    def bad(e):
        if is_womens_match(e.get("home"), e.get("away")):
            return True
        if is_youth_match(e.get("home"), e.get("away")):
            return True
        # 🔧 REC-016: الدولة مخزنة داخل «League (Country)» — نستخرجها فيرى
        # الحارس استبعادات الدول أيضاً بدل الكلمات المفتاحية وحدها
        lg = e.get("league") or ""
        country = lg.rsplit("(", 1)[1].rstrip(")").strip() if "(" in lg else ""
        return is_excluded({"name": lg, "country": country})
    leaks = []
    for p in (store.get("pending") or {}).values():
        if bad(p):
            leaks.append(f"{p.get('home', '?')} × {p.get('away', '?')} — {p.get('league', '?')} (انتظار)")
    recent = (now_utc() - timedelta(days=2)).strftime("%Y-%m-%d")
    for e in (store.get("resolved") or []):
        if (e.get("date") or "") >= recent and bad(e):
            leaks.append(f"{e.get('home', '?')} × {e.get('away', '?')} — {e.get('league', '?')} (مُقيَّمة)")
    return leaks


def post_grading_alerts(newly_resolved: list, store: dict) -> None:
    """حارسا ما بعد التقييم — يركضان كل صباح بعد التسوية مباشرة:
    (1) أي خطأ بثقة 70%+ يُبلَّغ للمالك فوراً بالاسم والتفاصيل (الشريحة
        الذهبية تحت مراقبته الشخصية — لا يكتشف تغيّرها من اللوحة صدفة)؛
    (2) فحص تسريب بيانات محظورة في ذاكرتي المحركين معاً."""
    lines = []
    hi_misses = [e for e in (newly_resolved or [])
                 if (e.get("confidence") or 0) >= 70 and not e.get("correct")]
    if hi_misses:
        lines.append("🚨 تنبيه القناعة العالية — أخطاء بثقة 70%+ هذا الصباح:")
        for e in hi_misses:
            lines.append(
                f"• {e.get('ar_home') or e.get('home')} × {e.get('ar_away') or e.get('away')}"
                f" — ثقة {e.get('confidence')}% — النتيجة {e.get('score')} ({e.get('league')})"
            )
            # قراءة المحرك قبل المباراة — تظهر مع الخطأ ليتضح أين كان التفكير الخاطئ
            if (e.get("reason") or "").strip():
                lines.append(f"  💭 كان يفكر: {e['reason'].strip()}")
    leaks = find_data_leaks(store) + find_data_leaks(load_json(V1_PREDICTIONS_FILE, {}))
    leaks = list(dict.fromkeys(leaks))
    if leaks:
        lines.append("🚨 حارس البيانات النظيفة — بيانات محظورة داخل الذاكرة، تحتاج تدخلاً:")
        lines += [f"• {x}" for x in leaks[:15]]
    if lines and TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        send_telegram_long("\n".join(lines))
    if lines:
        print("تنبيهات ما بعد التقييم:", len(lines))


def _radar_counts(warn_rows: list, alert_rows: list) -> tuple:
    """يبني عدّادَي الرادار من قائمتَي سجل: (المستويات، الادعاءات).
    يُستدعى مرتين بنفس المنطق حرفياً — مرة على السجل التراكمي ومرة على
    سجل الموسم المرشَّح بالتاريخ — فلا يفترق الحسابان أبداً."""
    levels = {}
    for lvl in ("red", "amber"):
        rows = [x for x in warn_rows if x.get("level") == lvl]
        levels[lvl] = {"fired": len(rows),
                       "hit": sum(1 for x in rows if x.get("failed"))}
    claims = {}
    for key in ("flip", "equalizer", "goal", "next_goal", "red_advantage"):
        rows = [x for x in alert_rows if x.get("key") == key]
        if rows:
            claims[key] = {"fired": len(rows),
                           "hit": sum(1 for x in rows if x.get("hit"))}
    return levels, claims


def resolve_radar_log(store: dict) -> int:
    """لوحة تقييم الرادار (طلب المالك 2026-08-01): كل إنذار كهرماني/أحمر
    سجّله الرادار ليلاً يُقارن صباحاً بالنتيجة الحقيقية — هل سقط التوقع الذي
    حذّر منه فعلاً؟ صفر نداءات API (النتائج تُقرأ من سجل resolved نفسه).
    يبني إحصاءات صدق الإنذار لكل مستوى — قاعدة معايرة أوزان الرادار لاحقاً."""
    log = load_json(RADAR_LOG_FILE, {})
    warnings = log.get("warnings") or []
    # 🔧 REC-015 (جلسة 2026-08-24): سباق نادر يترك صفين لنفس (المباراة،
    # المستوى، اليوم) فيُقيَّم الإنذار مرتين ويتضخم fired — نُدمج قبل التقييم:
    # الذروة الأعلى تبقى، وأعلام alerted/sent_* بالاتحاد (أول إرسال يفوز)
    if RADAR_LOG_DEDUP and warnings:
        merged, seen = [], {}
        for w in warnings:
            key = (str(w.get("fid")), w.get("date"), w.get("level"))
            if key not in seen:
                seen[key] = w
                merged.append(w)
                continue
            kept = seen[key]
            if (w.get("score") or 0) > (kept.get("score") or 0):
                for f in ("score", "minute", "factors"):
                    kept[f] = w.get(f)
            if w.get("alerted") and not kept.get("alerted"):
                kept["alerted"] = True
                kept["alert_minute"] = w.get("alert_minute")
                for f in ("sent_level", "sent_score", "sent_minute",
                          "sent_factors"):
                    if f in w:
                        kept[f] = w[f]
        if len(merged) != len(warnings):
            print(f"رادار: دُمجت {len(warnings) - len(merged)} صفوف مكررة قبل التقييم")
            warnings = merged
            log["warnings"] = merged
    alerts = log.get("alerts") or []
    if not warnings and not alerts:
        # قاعدة الإيقاف (REC-005): أول صباح بعد تفعيلها قد لا يكون فيه إنذار
        # منتظر — نكمل مرة واحدة لكتابة القائمتين من السجل المتراكم ثم نعود للصمت
        if not (RADAR_ALERT_STOP_RULE and log.get("alerts_resolved")
                and "silenced" not in log):
            return 0
    by_fid = {str(r.get("fid")): r for r in (store.get("resolved") or []) if r.get("fid")}
    today = now_utc().strftime("%Y-%m-%d")
    drop_before = (now_utc() - timedelta(days=RADAR_DROP_DAYS)).strftime("%Y-%m-%d")
    still, graded = [], 0
    resolved = log.get("resolved") or []
    for w in warnings:
        r = by_fid.get(str(w.get("fid")))
        if r is None:
            if (w.get("date") or today) >= drop_before:
                still.append(w)   # نتيجتها لم تصل بعد — تنتظر صباحاً آخر
            continue
        w["failed"] = not r.get("correct")   # الإنذار صادق إذا سقط التوقع فعلاً
        w["final_score"] = r.get("score")
        w["graded_on"] = today
        resolved.append(w)
        graded += 1
    log["warnings"] = still
    log["resolved"] = (resolved[-RADAR_RESOLVED_CAP:]
                       if RADAR_RESOLVED_CAP else resolved)

    # 🚨 تقييم تنبيهات الدراما (عقل S3): كل ادعاء يُحاكم بقاعدته الخاصة
    # على النتيجة النهائية فقط — هدف/تعادل/قلب نتيجة، صفر نداءات API
    a_still, a_resolved = [], log.get("alerts_resolved") or []
    for a in alerts:
        r = by_fid.get(str(a.get("fid")))
        if r is None:
            if (a.get("date") or today) >= drop_before:
                a_still.append(a)
            continue
        try:
            fg = [int(x) for x in (r.get("score") or "0-0").split("-")[:2]]
            ag = [int(x) for x in (a.get("score_at") or "0-0").split("-")[:2]]
        except ValueError:
            continue
        side = 0 if a.get("side") == "home" else 1
        opp = 1 - side
        scored = fg[side] > ag[side]                 # سجّل بعد التنبيه
        level_or_better = fg[side] >= fg[opp]        # أدرك التعادل على الأقل
        won = fg[side] > fg[opp]                     # قلبها فوزاً
        hit = {"goal": scored,
               "equalizer": scored and level_or_better,
               "flip": won,
               "next_goal": scored,
               # 🟥 REC-009: المستفيد من الطرد سجّل بعد لحظة التنبيه
               "red_advantage": scored}.get(a.get("key"), scored)
        a["hit"] = bool(hit)
        a["final_score"] = r.get("score")
        a["graded_on"] = today
        a_resolved.append(a)
        graded += 1
    log["alerts"] = a_still
    log["alerts_resolved"] = (a_resolved[-RADAR_RESOLVED_CAP:]
                              if RADAR_RESOLVED_CAP else a_resolved)

    # العدّاد التراكمي: السجل الكامل منذ أول يوم — لا يُمسّ ولا يُرشَّح
    stats, astats = _radar_counts(log["resolved"], log["alerts_resolved"])
    if astats:
        stats["alerts"] = astats

    # 📅 عدّاد الموسم (أمر المالك 2026-08-13): نفس أسلوب كتلة "الموسم" في
    # compute_stats — يُشتق بالترشيح على التاريخ لا بتصفير أي سجل
    # (قاعدة لا-أسقف-قياس). القائمتان أعلاه تبقيان كاملتين كما هما.
    season_warns = [x for x in log["resolved"]
                    if x.get("date", "") >= SEASON_START]
    season_alerts = [x for x in log["alerts_resolved"]
                     if x.get("date", "") >= SEASON_START]
    season, season_claims = _radar_counts(season_warns, season_alerts)
    season["alerts"] = season_claims
    season["start"] = SEASON_START
    stats["season"] = season

    # 🎛 شريحة دوريات المالك (REC-010، قرار المالك 2026-08-13): كتلة موازية
    # بنفس أسلوب كتلة الموسم أعلاه — ترشيح على علامة `top` التي تُكتب مع
    # السجل نفسه في monitor.py (مشتقة من TOP_LEAGUE_IDS بالمعرف، لا بالاسم).
    # السجلات القديمة بلا علامة تُعامل "غير مصنّفة" ولا تدخل الشريحة — منصوص
    # عليه في التوصية ومقبول من المالك: الفلتر يمتلئ من لحظة التنفيذ.
    #
    # ⛔ ينطبق هنا حرفياً تحذير كتلة الموسم أعلاه: لا تربط قاعدة الإيقاف
    # (REC-005) بهذه الكتلة مهما بدا ذلك منطقياً — شريحة دورياته أصغر من
    # التراكمي، فكل الأنواع تهبط تحت عتبة الـ 30 وتخرج من قائمة الإسكات
    # فتعود ترسل تيليجرام. الحكم يبقى على astats التراكمي؛ هذه عرض وقياس فقط.
    top_warns = [x for x in log["resolved"] if x.get("top")]
    top_alerts = [x for x in log["alerts_resolved"] if x.get("top")]
    top, top_claims = _radar_counts(top_warns, top_alerts)
    top["alerts"] = top_claims
    top_season, top_season_claims = _radar_counts(
        [x for x in top_warns if x.get("date", "") >= SEASON_START],
        [x for x in top_alerts if x.get("date", "") >= SEASON_START])
    top_season["alerts"] = top_season_claims
    top_season["start"] = SEASON_START
    top["season"] = top_season
    # حارس العينة (REC-010): الحد يسافر مع الأرقام حتى تقرأه اللوحة من مصدره
    top["min_sample"] = MIN_FILTERED_SAMPLE
    stats["top_only"] = top

    # 🎛 الشريحة الثالثة (دورياته التسعة — قراره 2026-08-22): نفس البناء على
    # الصفوف الحاملة `mine`، وتمتلئ من يوم التنفيذ فقط. نفس تحذير REC-005:
    # قاعدة الإيقاف تقرأ التراكمي وحده — لا تُربط بهذه الكتلة أبداً.
    mine_warns = [x for x in log["resolved"] if x.get("mine")]
    mine_alerts = [x for x in log["alerts_resolved"] if x.get("mine")]
    mine, mine_claims = _radar_counts(mine_warns, mine_alerts)
    mine["alerts"] = mine_claims
    mine_season, mine_season_claims = _radar_counts(
        [x for x in mine_warns if x.get("date", "") >= SEASON_START],
        [x for x in mine_alerts if x.get("date", "") >= SEASON_START])
    mine_season["alerts"] = mine_season_claims
    mine_season["start"] = SEASON_START
    mine["season"] = mine_season
    mine["min_sample"] = MIN_FILTERED_SAMPLE
    stats["mine_only"] = mine

    # 🔬 كتلة xG الموازية (تجربة الطبقة الحية): الدرجتان تُقيَّمان على **نفس**
    # النتائج الحقيقية وبنفس بنية العدّادات — قاعدة الحوكمة (ج): لوحة نتائج
    # منفصلة لكل وظيفة، لا خلط أبداً.
    #
    # ⛔ الشرط الجوهري: المقارنة تجري على المباريات التي توفّر لها xG **فقط**
    # (score_xg غير فارغ)، والكتلة تحمل أداء الدرجة الحالية على تلك الشريحة
    # نفسها. لولا ذلك لقارنّا مجتمعين مختلفين وسمّينا الفارق نتيجة.
    xg_rows = [x for x in log["resolved"] if x.get("score_xg") is not None]
    xg_levels = {}
    for lvl in ("red", "amber"):
        rows = [x for x in xg_rows if x.get("level_xg") == lvl]
        xg_levels[lvl] = {"fired": len(rows),
                          "hit": sum(1 for x in rows if x.get("failed"))}
    base_levels, _ = _radar_counts(xg_rows, [])
    stats["xg"] = {"xg": xg_levels, "base": base_levels, "n": len(xg_rows),
                   "start": XG_LIVE_START}

    # قاعدة الإيقاف (REC-005): تُعاد كتابة القائمتين كل صباح من السجل التراكمي
    # نفسه — كل نوع ادعاء يُحكم على حدة، ولا حكم قبل 30 تنبيهاً مُقيَّماً
    #
    # ⛔ لا تربط هذه الحلقة بعدّاد الموسم (stats["season"]) مهما بدا ذلك
    # منطقياً: عدّاد الموسم يبدأ من صفر في 2026-08-13، فلو قرأت الحلقة منه
    # لهبطت كل الأنواع تحت عتبة الـ 30 (RADAR_STOP_MIN_GRADED) ولخرجت من
    # القائمتين، فتعود الأنواع المكتومة ترسل تيليجرام من جديد — انحدار خطير.
    # الحكم يبقى على astats التراكمي؛ الموسم عرض وقياس فقط لا قرار إرسال.
    lists_changed = False
    if RADAR_ALERT_STOP_RULE:
        silenced, proven = [], []
        for key in sorted(astats):   # astats = التراكمي عمداً — اقرأ التحذير أعلاه
            s = astats[key]
            if s["fired"] < RADAR_STOP_MIN_GRADED:
                continue   # تحت 30: صفر تأثير
            acc = 100.0 * s["hit"] / s["fired"]
            if acc < RADAR_STOP_SILENCE_LT:
                silenced.append(key)
            elif acc >= RADAR_STOP_PROVEN_GTE:
                proven.append(key)
        lists_changed = (log.get("silenced") != silenced
                         or log.get("proven") != proven)
        log["silenced"], log["proven"] = silenced, proven

    log["meta"] = {"stats": stats, "updated": now_utc().isoformat()}
    if (graded or lists_changed
            or len(still) != len(warnings) or len(a_still) != len(alerts)):
        save_json(RADAR_LOG_FILE, log)
    return graded


# أسماء أنواع ادعاءات الدراما كما تُعرض للمالك (REC-005: كل نوع بسطره الخاص)
DRAMA_CLAIM_AR = {"next_goal": "الهدف القادم", "goal": "هدف المتأخر",
                  "equalizer": "إدراك التعادل", "flip": "قلب النتيجة",
                  "red_advantage": "أفضلية عددية (طرد) 🟥"}   # REC-009


# ================== 🧮 حارس النزاهة اليومي ==================
def integrity_check() -> list:
    """يعيد فحص قوانين النزاهة على ملفات البيانات الحية كما هي على القرص —
    القيد المزدوج المحاسبي (أمر المالك 2026-08-09). يرجع قائمة
    (اسم القانون، قائمة الانتهاكات) — قائمة انتهاكات فارغة = القانون سليم.
    صفر نداءات API وصفر نداءات Claude — رياضيات محلية بحتة."""
    today = now_utc().strftime("%Y-%m-%d")
    v2 = load_json(PREDICTIONS_FILE, {})
    v1 = load_json(V1_PREDICTIONS_FILE, {})
    user = load_json(USER_PREDICTIONS_FILE, {})
    hist = load_json(HISTORY_FILE, {})
    scen = load_json(SCENARIOS_FILE, {})
    radar = load_json(RADAR_LOG_FILE, {})
    refs = load_json(REFEREES_FILE, {})
    checks = []

    # 1) عدم النقصان: عدد السجلات المُقيَّمة لا ينقص أبداً (كشف أي حذف صامت)
    v = []
    prev = (hist.get("integrity") or {}).get("resolved_counts") or {}
    counts = {"v2": len(v2.get("resolved") or []),
              "v1": len(v1.get("resolved") or []),
              "user": len(user.get("resolved") or []),
              "history_days": len(hist.get("days") or {}),
              # امتداد 2026-08-09: سجلات القياس التي كانت أسقفها تقص بصمت
              # (اكتشاف المالك: الرادار مشبع عند 300/300 والأرقام "شبه ثابتة")
              "radar_resolved": len(radar.get("resolved") or []),
              "radar_alerts_resolved": len(radar.get("alerts_resolved") or []),
              "scen_resolved": len(scen.get("resolved") or [])}
    for key, n in counts.items():
        old = prev.get(key)
        if isinstance(old, int) and n < old:
            v.append(f"{key}: كان {old} وصار {n} — سجلات حُذفت")
    checks.append(("عدم النقصان في السجلات", v))
    hist.setdefault("integrity", {})["resolved_counts"] = counts
    hist["integrity"]["checked_on"] = today
    save_json(HISTORY_FILE, hist)

    # 2+3) تطابق الذاكرة والأرشيف الدائم: المجاميع اليومية وشرائح الثقة —
    # الطريق الثاني المستقل لنفس الرقم (درس نافذة 70%+ المتحركة)
    v_tot, v_bkt = [], []
    mem_days = {}
    for r in (v2.get("resolved") or []):
        d = r.get("date")
        if not d or d >= today:   # يوم غير مكتمل التقييم يُقارن غداً
            continue
        slot = mem_days.setdefault(d, {"total": 0, "buckets": {}})
        slot["total"] += 1
        b = _conf_bucket(int(r.get("confidence", 0)))
        bb = slot["buckets"].setdefault(b, {"correct": 0, "total": 0})
        bb["total"] += 1
        bb["correct"] += 1 if r.get("correct") else 0
    for d, slot in mem_days.items():
        arch = ((hist.get("days") or {}).get(d) or {}).get("v2") or {}
        if arch.get("total") is not None and arch["total"] != slot["total"]:
            v_tot.append(f"{d}: الذاكرة {slot['total']} ضد الأرشيف {arch['total']}")
        ab = arch.get("buckets")
        if isinstance(ab, dict):
            for b, bb in slot["buckets"].items():
                if ab.get(b) is not None and ab[b] != bb:
                    v_bkt.append(f"{d}/{b}: الذاكرة {bb} ضد الأرشيف {ab[b]}")
    checks.append(("تطابق الذاكرة والأرشيف (المجاميع اليومية)", v_tot))
    checks.append(("تطابق شرائح الثقة مع الأرشيف", v_bkt))

    # 4) الحكام 1:1 (درس التضخم 2.6×): مجموع المباريات = عدد المعرفات المسجلة
    v = []
    ref_matches = sum((r or {}).get("matches", 0) for k, r in refs.items()
                      if not str(k).startswith("_") and isinstance(r, dict))
    fids = ((refs.get("_meta") or {}).get("fids")) or []
    if refs and len(fids) < REFEREE_FIDS_CAP and ref_matches != len(fids):
        v.append(f"مجموع مباريات الحكام {ref_matches} ضد معرفات مسجلة {len(fids)}")
    checks.append(("تطابق الحكام 1:1", v))

    # 5-7) لا معلّق متجاوزاً مهلته (درس التقرير العالق 11 يوماً)
    def _stale(pending, limit_days, label):
        out = []
        cutoff = (now_utc() - timedelta(days=limit_days + INTEGRITY_AGE_GRACE)
                  ).strftime("%Y-%m-%d")
        for fid, e in (pending or {}).items():
            d = (e or {}).get("date") or ""
            if d and d < cutoff:
                out.append(f"{label} {fid} بتاريخ {d} تجاوز مهلة {limit_days} أيام")
        return out
    checks.append(("لا تقرير معلقاً فوق مهلته",
                   _stale(scen.get("pending"), SCENARIO_MAX_AGE_DAYS, "تقرير")))
    v = []
    cutoff = (now_utc() - timedelta(days=RADAR_DROP_DAYS + INTEGRITY_AGE_GRACE)
              ).strftime("%Y-%m-%d")
    for kind in ("warnings", "alerts"):
        for e in (radar.get(kind) or []):
            d = (e or {}).get("date") or ""
            if d and d < cutoff:
                v.append(f"{kind}:{e.get('fid')} بتاريخ {d}")
    checks.append(("لا إنذار رادار معلقاً فوق مهلته", v))
    v = []
    for label, store in (("v2", v2), ("v1", v1), ("user", user)):
        v += _stale(store.get("pending"), 3, f"توقع {label}")
    checks.append(("لا توقع معلقاً فوق مهلته", v))

    # 8) صحة عمود correct: يُعاد اشتقاقه من pick وactual لكل صف ويُقارن
    v = []
    for label, store in (("v2", v2), ("v1", v1), ("user", user)):
        for r in (store.get("resolved") or []):
            if r.get("pick") and r.get("actual"):
                if bool(r.get("correct")) != (r["pick"] == r["actual"]):
                    v.append(f"{label}:{r.get('fid')} correct مخالف لاشتقاقه")
    checks.append(("صحة عمود correct (إعادة اشتقاق)", v))

    # 9) الاحتمالات تجمع 100 (صفوف المحرك 2 الحاملة احتمالات)
    v = []
    for group in (list((v2.get("pending") or {}).values()),
                  v2.get("resolved") or []):
        for r in group:
            try:
                s = int(r["prob_home"]) + int(r["prob_draw"]) + int(r["prob_away"])
            except (KeyError, TypeError, ValueError):
                continue
            if s != 100:
                v.append(f"{r.get('fid')}: مجموع الاحتمالات {s}")
    checks.append(("الاحتمالات تجمع 100", v))

    # 10) الثقة داخل حدودها المعلنة [30, 85]
    v = []
    for label, store in (("v2", v2), ("v1", v1), ("user", user)):
        for group in (list((store.get("pending") or {}).values()),
                      store.get("resolved") or []):
            for r in group:
                try:
                    c = int(r.get("confidence"))
                except (TypeError, ValueError):
                    continue
                if not 30 <= c <= 85:
                    v.append(f"{label}:{r.get('fid')} ثقة {c}")
    checks.append(("الثقة ضمن الحدود", v))

    # 11) لا ازدواج معرفات: صف واحد لكل مباراة (درس ازدواج الحكام معمّماً)
    v = []
    for label, store in (("v2", v2), ("v1", v1), ("user", user)):
        seen = set()
        for r in (store.get("resolved") or []):
            fid = str(r.get("fid"))
            if fid in seen:
                v.append(f"{label}: المعرف {fid} مكرر في resolved")
            seen.add(fid)
        overlap = seen & set(map(str, (store.get("pending") or {}).keys()))
        for fid in overlap:
            v.append(f"{label}: المعرف {fid} في pending وresolved معاً")
    checks.append(("لا ازدواج معرفات", v))

    # 12) لا تسريب بيانات محظورة (نفس فاحص WK-League — يُضم للسجل الموحد)
    checks.append(("لا تسريب بيانات محظورة",
                   find_data_leaks(v2) + find_data_leaks(v1)))

    # 13) أسقف القياس معطلة — قانون انجراف الإعدادات (المسح الشامل 2026-08-09،
    # درس المالك: "لا أريد أن أكتشف بعد شهر"): أي سقف على سجل قياس أو قائمة
    # انتظار تقييم يُعاد تفعيله — بأي PR مستقبلي — يرن هنا أول صباح، لا بعد
    # أن يشبع السجل ويكتشفه المالك بعينه.
    v = []
    caps = {"predict_v2.RESOLVED_CAP": RESOLVED_CAP,
            "predict_v2.RADAR_RESOLVED_CAP": RADAR_RESOLVED_CAP,
            "predict_v2.SCENARIOS_RESOLVED_CAP": SCENARIOS_RESOLVED_CAP}
    try:
        import predict as _v1mod
        caps["predict.RESOLVED_CAP"] = getattr(_v1mod, "RESOLVED_CAP", 0)
    except Exception:
        pass
    try:
        import monitor as _monmod
        caps["monitor.RADAR_MAX_WARNINGS"] = getattr(_monmod,
                                                     "RADAR_MAX_WARNINGS", 0)
    except Exception:
        pass
    for name, val in caps.items():
        if val:
            v.append(f"{name}={val} — سقف قياس مفعّل يقص بيانات بصمت")
    checks.append(("أسقف القياس معطلة (انجراف الإعدادات)", v))
    return checks


def run_integrity_sentinel() -> str:
    """يشغّل الفحص، يرسل إنذاراً فورياً عند أي قانون مكسور، ويرجع سطر النشرة.
    أي فشل داخلي في الحارس نفسه يُبلَّغ ولا يقتل التشغيلة أبداً."""
    if not INTEGRITY_SENTINEL:
        return ""
    try:
        checks = integrity_check()
    except Exception as e:
        print("حارس النزاهة — فشل الفحص نفسه:", e)
        return "🧮 فحص النزاهة: تعذر التشغيل — راجع سجل التشغيلة"
    broken = [(name, v) for name, v in checks if v]
    ok = len(checks) - len(broken)
    if broken:
        lines = ["🧮🚨 حارس النزاهة — قوانين حسابية مكسورة اكتُشفت هذا الصباح:"]
        for name, v in broken:
            lines.append(f"❌ {name} ({len(v)} انتهاكاً):")
            lines += [f"   • {x}" for x in v[:INTEGRITY_MAX_DETAILS]]
            if len(v) > INTEGRITY_MAX_DETAILS:
                lines.append(f"   • … و{len(v) - INTEGRITY_MAX_DETAILS} غيرها")
        if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
            send_telegram_long("\n".join(lines))
        print("\n".join(lines))
        return (f"🧮 فحص النزاهة: {ok}/{len(checks)} — "
                f"⚠️ {len(broken)} قانون مكسور (التفاصيل في رسالة منفصلة)")
    return f"🧮 فحص النزاهة: {ok}/{len(checks)} ✓"


def drama_scoreboard_line() -> str:
    """سطر لوحة تنبيهات الدراما للملخص الصباحي (رؤية يومية إلزامية —
    درس 2026-08-02: السجل الأول 1/6 وُجد ولم يصل المالك إلا بسؤاله).
    يظهر فقط حين توجد تنبيهات مُقيَّمة؛ يفصّل أمس والإجمالي لكل شيء بشفافية.
    مع قاعدة الإيقاف (REC-005): تفصيل لكل نوع ادعاء على حدة — سجله، وحالته
    (مُثبَت/صامت)، أو عدّاد تقدمه نحو حكم الـ 30.
    أمر المالك 2026-08-13: أرقام الموسم أولاً، والتراكمي بين قوسين."""
    log = load_json(RADAR_LOG_FILE, {})
    resolved = log.get("alerts_resolved") or []
    if not resolved:
        return ""
    today = now_utc().strftime("%Y-%m-%d")
    fresh = [a for a in resolved if a.get("graded_on") == today]
    total_hit = sum(1 for a in resolved if a.get("hit"))
    # عدّاد الموسم: ترشيح بالتاريخ على نفس السجل الكامل — لا حذف ولا تصفير
    season = [a for a in resolved if a.get("date", "") >= SEASON_START]
    season_hit = sum(1 for a in season if a.get("hit"))
    head = f"{season_hit}/{len(season)} صحيحة" if season else "بدأ العدّ اليوم"
    line = (f"🧪 تنبيهات الدراما (تجريبية): الموسم {head} "
            f"(منذ البداية: {total_hit}/{len(resolved)})")
    if fresh:
        line += f" — اليوم {sum(1 for a in fresh if a.get('hit'))}/{len(fresh)}"
    if not RADAR_ALERT_STOP_RULE:
        return line
    lines = [line]
    silenced = set(log.get("silenced") or [])
    proven = set(log.get("proven") or [])
    for key, name in DRAMA_CLAIM_AR.items():
        rows = [a for a in resolved if a.get("key") == key]
        if not rows:
            continue
        hit, n = sum(1 for a in rows if a.get("hit")), len(rows)
        s_rows = [a for a in rows if a.get("date", "") >= SEASON_START]
        s_hit = sum(1 for a in s_rows if a.get("hit"))
        # الحالة وعدّاد الـ 30 يقرآن التراكمي (n) لا الموسم — قاعدة الإيقاف
        # مربوطة بالسجل الكامل عمداً (انظر التحذير في resolve_radar_log)
        if key in proven:
            status = "مُثبَت ✅ — يُرسل بلا وسم تجريبي"
        elif key in silenced:
            status = "صامت 🔇 — يُسجَّل بلا تيليجرام"
        else:
            status = (f"تجريبي — {min(n, RADAR_STOP_MIN_GRADED)}"
                      f"/{RADAR_STOP_MIN_GRADED} نحو الحكم")
        num = f"{s_hit}/{len(s_rows)}" if s_rows else "بدأ العدّ اليوم"
        lines.append(f"  • {name}: {num} (منذ البداية: {hit}/{n}) — {status}")
    return "\n".join(lines)


def xg_radar_line() -> str:
    """🔬 سطر مقارنة درجة الخطر بـxG مقابل الحالية (رؤية يومية إلزامية — هـ).

    يقرأ meta.stats.xg قراءةً فقط. النسبتان تُحسبان على **نفس** المباريات
    (الشريحة التي توفّر لها xG) فالمقارنة عادلة لا مُجمَّلة.

    حارس العينة نفسه المطبَّق في REC-010: تحت MIN_FILTERED_SAMPLE لا تُعرض
    نسبة مئوية إطلاقاً — نسبة على أربع مباريات تصنع استنتاجاً خاطئاً بثقة،
    وهذا نظام بُني كله لمنع ذلك (REJ-002).
    """
    log = load_json(RADAR_LOG_FILE, {})
    block = ((log.get("meta") or {}).get("stats") or {}).get("xg") or {}
    n = block.get("n") or 0
    if not n:
        return ""
    xg, base = block.get("xg") or {}, block.get("base") or {}
    def _pct(d):
        f = (d or {}).get("fired") or 0
        return f"{100 * (d.get('hit') or 0) / f:.0f}%" if f else "—"
    if n < MIN_FILTERED_SAMPLE:
        return (f"🔬 xG مقابل الحالي: عينة غير كافية "
                f"({n} من {MIN_FILTERED_SAMPLE}) — يُجمَع ولا يُحكم بعد")
    return (f"🔬 xG مقابل الحالي: أحمر {_pct(xg.get('red'))} ضد "
            f"{_pct(base.get('red'))} · كهرماني {_pct(xg.get('amber'))} ضد "
            f"{_pct(base.get('amber'))} (n={n}) — ظل، لا يؤثر على أي تنبيه")


def sportmonks_shadow_line() -> str:
    """🔬 رؤية ظل xG اليومية (قاعدة المالك هـ: تجربة نشطة = سطر يومي بلا سؤال).
    يقرأ sportmonks_shadow.json قراءةً فقط — يكتبه المجمّع المستقل
    sportmonks_shadow.py بعد هذه التشغيلة، فالسطر يعرض حصيلة الأمس
    (تأخير يوم واحد مقصود ومقبول — صفر تأثير على المحرك)."""
    shadow = load_json(Path("sportmonks_shadow.json"), {}) or {}
    meta = shadow.get("meta") or {}
    # المعيار هو **بدء التجربة** لا وجود بيانات (إصلاح 14 أغسطس): كان الشرط
    # `not meta.get("total")` فاختفى السطر تماماً طوال 13 أغسطس بينما التجربة
    # تجمع صفراً — فبدت كأنها لم تبدأ بعد. **اختفاء السطر هو ما أخفى العطل**؛
    # تجربة نشطة بصفر مباراة يجب أن تقول «0» بصوت عالٍ، لا أن تصمت.
    if not meta.get("started"):
        return ""
    try:
        started = datetime.strptime(meta.get("started", ""), "%Y-%m-%d")
        day_no = (now_utc().replace(tzinfo=None) - started).days + 1
    except ValueError:
        day_no = "?"
    line = (f"🔬 ظل xG — يوم {day_no} من ~{XG_SHADOW_DAYS}: أمس مطابقة "
            f"{meta.get('last_day_matched', 0)} ومُفلت "
            f"{meta.get('last_day_unmatched', 0)}؛ "
            f"الإجمالي {meta.get('total', 0)} مباراة موثقة")
    if not meta.get("total"):
        # صفر جمع لا يُقرأ كـ«يوم هادئ»: يُوسم صراحةً ومعه الخطوة التالية
        line += " ⚠️ صفر جمع — شغّل مسبار --probe"
        streak = meta.get("zero_streak") or 0
        if streak:
            line += f" ({streak} يوم متتالٍ)"
    xf = meta.get("xgform") or {}
    if xf.get("n"):
        line += f" | فورمة xG: {xf.get('correct', 0)}/{xf['n']}"
    return line


def v1_pending() -> dict:
    """توقعات المحرك 1 المنتظرة — للمقارنة جنباً إلى جنب في الملخص."""
    store = load_json(V1_PREDICTIONS_FILE, {})
    return store.get("pending") or {}


def update_history(v2_stats: dict, user_stats: dict, v2_resolved: list = None) -> int:
    """الأرشيف الدائم للتقدم: يدمج أرقام اليوم (صح/مجموع لكل طرف) في history.json.
    هذا الملف لا يُقص أبداً — سجل مسيرة المشروع الكامل يوماً بيوم.
    الدمج آمن التكرار (idempotent).
    منذ 2026-08-09 (حادثة أرقام 70%+): يحفظ أيضاً تفصيل شرائح الثقة يوماً-بيوم
    للمحرك 2، فسجل الخانة الذهبية الكامل محفوظ هنا للأبد مهما حدث للذاكرة."""
    hist = load_json(HISTORY_FILE, {"days": {}})
    days = hist.setdefault("days", {})
    v1_stats = (load_json(V1_PREDICTIONS_FILE, {}).get("meta") or {}).get("stats") or {}
    for key, st in (("v1", v1_stats), ("v2", v2_stats or {}), ("user", user_stats or {})):
        for d, row in ((st.get("daily") or {}) if st else {}).items():
            days.setdefault(d, {})[key] = {
                "correct": int(row.get("correct", 0)),
                "total": int(row.get("total", 0)),
            }
    # تفصيل شرائح الثقة يوماً-بيوم (المحرك 2): يُعاد حسابه من الصفوف نفسها
    # لكل يوم حاضر فيها ويُكتب فوق القديم — آمن التكرار مثل بقية الدمج
    day_buckets = {}
    for r in (v2_resolved or []):
        d = r.get("date")
        if not d:
            continue
        b = _conf_bucket(int(r.get("confidence", 0)))
        slot = day_buckets.setdefault(d, {}).setdefault(b, {"correct": 0, "total": 0})
        slot["total"] += 1
        slot["correct"] += 1 if r.get("correct") else 0
    for d, buckets in day_buckets.items():
        days.setdefault(d, {}).setdefault("v2", {"correct": 0, "total": 0})
        days[d]["v2"]["buckets"] = buckets
    lessons = load_json(LESSONS_FILE, {"lessons": []}).get("lessons") or []
    hist["meta"] = {
        "updated": now_utc().isoformat(),
        "lessons_stored": len(lessons),
    }
    save_json(HISTORY_FILE, hist)
    return len(days)


def race_line(user_stats: dict, v2_stats: dict) -> str:
    """سطر سباق الدقة الثلاثي: المالك ضد المحركين — يظهر متى وُجد سجل للمالك."""
    if not (user_stats and user_stats.get("overall", {}).get("total")):
        return ""
    v1_stats = (load_json(V1_PREDICTIONS_FILE, {}).get("meta") or {}).get("stats") or {}
    parts = [f"أنت: {pct(user_stats['overall'])}"]
    if v1_stats.get("overall", {}).get("total"):
        parts.append(f"المحرك 1: {pct(v1_stats['overall'])}")
    if v2_stats.get("overall", {}).get("total"):
        parts.append(f"المحرك 2: {pct(v2_stats['overall'])}")
    return "🏆 سباق الدقة — " + " | ".join(parts)


def digest_sections(new_preds: list) -> list:
    """⭐/⚡ القسمان البارزان في رأس النشرة (طلب المالك 2026-08-21).

    كانت الاختيارات الذهبية ومخالفات السوق مدفونة وسط قائمة الدوريات —
    «كل شيء مخلوط» بتعبير المالك. القسمان يستلان أثمن الصفوف ويضعانها
    أولاً بنفس مصطلحات البوابة حرفياً (الذهبية من كل الدوريات لأن شريحة
    الـ70%+ لا تعرف دورياً؛ ضد السوق من المباريات الغنية الحاملة لأودز).
    المباراة تبقى في قائمتها الأصلية أيضاً — هذان ملخصان لا نقل."""
    lines = []
    gold = [p for p in new_preds
            if (p.get("confidence") or 0) >= DIGEST_GOLD_MIN_CONF]
    if gold:
        lines.append(f"\n⭐ الاختيارات الذهبية — ثقة 70%+ ({len(gold)})")
        for p in gold:
            h = p.get("ar_home") or p["home"]
            a = p.get("ar_away") or p["away"]
            lines.append(f"• {h} 🆚 {a} — {pick_label(p)} ({p['confidence']}%)")
    contra = [p for p in new_preds
              if market_favorite(p) and p.get("pick")
              and market_favorite(p) != p["pick"]]
    if contra:
        lines.append(f"\n⚡ ضد السوق — المحرك يخالف المرشح ({len(contra)})")
        for p in contra:
            h = p.get("ar_home") or p["home"]
            a = p.get("ar_away") or p["away"]
            fav = market_favorite(p)
            fav_lbl = PICK_AR[fav].format(h=h, a=a)
            mkt_pct = p.get(f"mkt_{fav}")
            lines.append(
                f"• {h} 🆚 {a} — المحرك: {pick_label(p)} ({p['confidence']}%)"
                f" · السوق: {fav_lbl} ({mkt_pct}%)")
    return lines


def build_digest(new_preds: list, stats: dict, v1_preds: dict = None,
                 new_lessons: int = 0, user_stats: dict = None) -> str:
    lines = ["🤖 المحرك 2 — توقعات الـ 24 ساعة القادمة"]
    v1_preds = v1_preds or {}
    if DIGEST_SECTIONS:
        lines += digest_sections(new_preds)
    shown = [p for p in new_preds if p["top"]] if DIGEST_TOP_ONLY else new_preds
    rest = len(new_preds) - len(shown)

    current_league = None
    for p in shown:
        lg = p.get("ar_league") or p.get("league")
        if lg != current_league:
            lines.append(f"\n🏆 {lg}")
            current_league = lg
        t = ""
        try:
            ko = datetime.fromisoformat(p["kickoff"]).astimezone(
                timezone(timedelta(hours=3)))
            t = ko.strftime("%H:%M")
            # «غداً» حين تلعب المباراة بعد منتصف الليل بتوقيت السعودية —
            # الوقت وحده يوهم أنها الليلة (ملاحظة المالك 2026-08-21)
            today_ksa = datetime.now(timezone(timedelta(hours=3))).date()
            if ko.date() > today_ksa:
                t = f"غداً {t}"
        except Exception:
            pass
        h = p.get("ar_home") or p["home"]
        a = p.get("ar_away") or p["away"]
        lines.append(f"⏰ {t} — {h} 🆚 {a}")
        v1 = v1_preds.get(p["fid"])
        if v1 and v1.get("pick") in PICK_AR:
            lines.append(f"   المحرك 1: {pick_label(v1)} — ثقة {v1.get('confidence', '?')}%")
        lines.append(f"   المحرك 2: {pick_label(p)} — ثقة {p['confidence']}%")
        lines.append(
            f"   📊 {h} {p['prob_home']}% | تعادل {p['prob_draw']}% | {a} {p['prob_away']}%"
        )

    if not shown:
        lines.append("لا توجد مباريات في الدوريات الكبرى خلال 24 ساعة.")
    if rest > 0:
        lines.append(f"\n➕ {rest} مباراة أخرى بتوقعاتها على اللوحة:")
        lines.append(DASHBOARD_URL)
    if stats["last30"]["total"]:
        lines.append(f"\n📊 دقة المحرك 2 آخر 30 يوماً: {pct(stats['last30'])}")
    # معيار السوق (REC-003): يظهر فقط حين توجد مباريات مُقيَّمة تحمل أودز
    mb = stats.get("market_bench") or {}
    if MARKET_BENCH and mb.get("n"):
        lines.append(
            f"⚖️ معيار السوق: المحرك {mb['engine_correct']}/{mb['n']} "
            f"مقابل مرشّح السوق {mb['market_correct']}/{mb['n']} "
            f"— اختلفا في {mb['disagree']} مباراة"
        )
    if new_lessons:
        lines.append(f"📚 دروس جديدة من أخطاء الأمس: {new_lessons} — تدخل في توقعات اليوم.")
    race = race_line(user_stats, stats)
    if race:
        lines.append(race)
    lines.append("\n⭐ أرسل لي أسماء المباريات التي تهمك اليوم وسأركز تنبيهاتي عليها فقط.")
    lines.append("⚠️ توقعات تحليلية وليست ضمانات.")
    return "\n".join(lines)


# ================== المنطق الرئيسي ==================
def main() -> None:
    missing = [
        name
        for name, val in [
            ("API_FOOTBALL_KEY", API_FOOTBALL_KEY),
            ("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY),
        ]
        if not val
    ]
    if missing:
        print("مفاتيح ناقصة في Secrets:", ", ".join(missing))
        sys.exit(1)

    store = load_json(PREDICTIONS_FILE, {"pending": {}, "resolved": []})
    store.setdefault("pending", {})
    store.setdefault("resolved", [])

    # 1) تسوية نتائج الأيام السابقة (التعلم)
    resolved_now, newly_resolved = resolve_pending(store)
    stats = compute_stats(store["resolved"])
    print(f"المحرك 2: تمت تسوية {resolved_now} توقعاً. السجل: {pct(stats['overall'])}")
    # معيار السوق (REC-003): المحرك ضد مرشّح السوق على نفس المباريات — تراكمياً
    if MARKET_BENCH:
        stats["market_bench"] = market_bench_stats(store["resolved"])
        mb = stats["market_bench"]
        print(f"معيار السوق: n={mb['n']} — المحرك {mb['engine_correct']} / "
              f"السوق {mb['market_correct']} / اختلاف {mb['disagree']}")
    post_grading_alerts(newly_resolved, store)   # 🚨 حارسا القناعة العالية والتسريب

    # 1.5) المرحلة 3: استخلاص دروس من أخطاء الأمس (كلها)، ثم دمجها عند التضخم
    new_lessons = generate_lessons(newly_resolved)
    if new_lessons:
        print(f"دروس جديدة مستخلصة من الأخطاء: {new_lessons}")
    consolidated = consolidate_lessons()
    if consolidated:
        print(f"تم دمج الدروس في {consolidated} مبدأً عاماً.")

    # 1.52) تقييم إنذارات الرادار: هل صدق الإنذار المبكر؟ (صفر نداءات)
    radar_graded = resolve_radar_log(store)
    if radar_graded:
        print(f"قُيّم {radar_graded} من إنذارات الرادار مقابل النتائج الحقيقية.")

    # 1.55) التقييم الذاتي لتقارير ما قبل المباراة (سيناريوهات المحرك 2)
    scenario_graded = resolve_scenarios()
    if scenario_graded:
        print(f"قُيّمت {scenario_graded} من تقارير ما قبل المباراة مقابل البيانات النهائية.")

    # 1.6) تقييم توقعات المالك بنفس المنطق (سباق الدقة الثلاثي)
    user_store = load_json(USER_PREDICTIONS_FILE, {"pending": {}, "resolved": []})
    user_store.setdefault("pending", {})
    user_store.setdefault("resolved", [])
    user_stats = None
    user_resolved_now = 0
    if user_store["pending"] or user_store["resolved"]:
        user_resolved_now, _ = resolve_pending(user_store)
        user_stats = compute_stats(user_store["resolved"])
        user_store["meta"] = {"last_run": now_utc().isoformat(), "stats": user_stats}
        save_json(USER_PREDICTIONS_FILE, user_store)
        print(f"توقعات المالك: تم تقييم {user_resolved_now}. السجل: {pct(user_stats['overall'])}")

    # 2) مباريات الـ 24 ساعة القادمة (نفس اختيار المحرك 1) + إكمال الشعارات الناقصة
    fetched = get_upcoming_24h()
    for m in fetched:
        p = store["pending"].get(m["fid"])
        if p is not None and not p.get("home_logo"):
            for k in ("home_logo", "away_logo", "league_logo"):
                p[k] = m.get(k, "")
    upcoming = [m for m in fetched if m["fid"] not in store["pending"]]
    print(f"مباريات جديدة للتوقع: {len(upcoming)}")

    # 3) سياق إضافي لكل المباريات (القائمة مرتبة كبرى-أولاً فتأخذ الأولوية عند السقف)
    budget = {"used": 0}
    standings_cache = {}
    enriched, basic = [], []
    for m in upcoming:
        # النمط الغني (المكلف) للدوريات الكبرى فقط عند تفعيل التوفير — البقية
        # تبقى مُتوقَّعة بالنمط الخفيف (تغطية كاملة، تعلّم كامل، تكلفة أقل)
        want_rich = (m.get("top") or not ENRICH_TOP_ONLY)
        if want_rich and len(enriched) < MAX_ENRICHED_FIXTURES and budget["used"] < ENRICH_CALL_BUDGET:
            m["context"] = build_context(m, budget, standings_cache)
            enriched.append(m)
        else:
            basic.append(m)
    print(f"سياق إضافي: {len(enriched)} مباراة (غنية)، {len(basic)} خفيفة، {budget['used']} نداء API")

    # 4) توقعات Claude على دفعات
    new_preds = []
    groups = [(enriched, ENRICHED_BATCH_SIZE, True), (basic, BASIC_BATCH_SIZE, False)]
    for matches, batch_size, is_enriched in groups:
        for i in range(0, len(matches), batch_size):
            batch = matches[i:i + batch_size]
            results = claude_predict_batch(batch, stats, is_enriched)
            for m in batch:
                r = results.get(m["fid"])
                if not r:
                    continue
                entry = {k: v for k, v in m.items() if k != "context"}
                entry.update(r)
                apply_cup_guardrail(entry)   # سقف ثقة الكأس/الإقصاء
                store["pending"][m["fid"]] = entry
                new_preds.append(entry)

    store["meta"] = {
        "last_run": now_utc().isoformat(),
        "engine": "v2",
        "model": CLAUDE_MODEL,
        "stats": stats,
    }
    save_json(PREDICTIONS_FILE, store)
    print(f"تم حفظ {len(new_preds)} توقعاً جديداً للمحرك 2.")
    # ⛔ صفر توقعات مع مرشحين موجودين = رفض Claude شامل (رصيد غالباً) —
    # تشغيلة حمراء عمداً كي يظهر العطل في Actions لا أن يمر أخضر صامتاً
    # (فجوة صبيحة 2026-08-17). التقييم والحفظ أعلاه اكتملا قبل هذا السطر.
    if upcoming and not new_preds:
        raise SystemExit(
            f"⛔ {len(upcoming)} مرشحاً وصفر توقعات محفوظة — رفض Claude شامل")

    # الأرشيف الدائم للتقدم (كل الأطراف، لا يُقص أبداً)
    days_total = update_history(stats, user_stats, store["resolved"])
    print(f"الأرشيف الدائم: {days_total} يوماً مسجلاً.")

    # 4.5) 🧮 حارس النزاهة اليومي: يفحص ملفات القرص بعد اكتمال كتابتها كلها —
    # أي قانون مكسور يصل المالك تيليجرام فوراً، لا في مراجعة بعد شهر
    integrity_line = run_integrity_sentinel()
    if integrity_line:
        print(integrity_line)

    # 5) ملخص تيليجرام (مقارنة المحرك 1 + سباق الدقة الثلاثي مع المالك)
    if SEND_TELEGRAM_DIGEST and TELEGRAM_TOKEN and TELEGRAM_CHAT_ID and new_preds:
        digest = build_digest(new_preds, stats, v1_pending(), new_lessons, user_stats)
        drama = drama_scoreboard_line()
        if drama:
            digest += "\n" + drama
        # 🔬 سطر ظل xG (تجربة نشطة 13 أغسطس → ~3 سبتمبر — رؤية يومية إلزامية)
        xg_line = sportmonks_shadow_line()
        if xg_line:
            digest += "\n" + xg_line
        # 🔬 سطر xG الحي في الرادار — تجربة ثانية منفصلة، لوحة نتائج مستقلة
        # (قاعدة الحوكمة ج: كل وظيفة تُقاس وتُعرض وحدها، بلا خلط)
        xg_radar = xg_radar_line()
        if xg_radar:
            digest += "\n" + xg_radar
        # سطر النزاهة اليومي: المالك يرى كل صباح أن الحسبة سليمة، لا يفترض ذلك
        if integrity_line:
            digest += "\n" + integrity_line
        # 📊 عدّاد رصيد API (حادثة 2026-08-14): الاختناق يُرى قبل أن يقتل —
        # لو كان هذا السطر موجوداً لظهر السقف 100 بدل 7,500 صباح الحادثة.
        quota = api_guard.quota_line()
        if quota:
            digest += "\n" + quota
        # 📡 نبض التسليم (2026-08-15): النظام يتحقق أن رسائله وصلت فعلاً.
        # يُحسب من آخر بث مسجّل في state.json. «وصلت إلى الجهاز» لا «قُرئت» —
        # تيليجرام لا يمنح البوتات إيصال قراءة، ولن ندّعي ما لا نعرفه.
        delivery = api_guard.delivery_line()
        if delivery:
            digest += "\n" + delivery
        # 📅 كتلة المواعيد (قرار المالك 2026-08-14): كل تذكير يمرّ على تيليجرام
        # أيضاً، لا على Routines وحدها — الروتين يوقظ الوكيل ولا يصل الهاتف
        due_lines = reminders.reminder_lines()
        if due_lines:
            digest += "\n" + due_lines
        send_telegram_long(digest)

    # 🚨 آخر شيء في التشغيلة: لو فشل تسليم رسالة إلى المالك نفسه فلا قناة
    # تبليغ بديلة — نخرج بحالة فشل لتظهر التشغيلة حمراء. بعد حفظ كل شيء.
    api_guard.exit_if_owner_unreachable()


def resolve_only() -> int:
    """🌙 التمرير المسائي — تقييم فقط، بلا توقعات وبلا دروس وبلا نشرة.

    طلب المالك 2026-08-15: «مباراة انتهت، لماذا ننتظر الصباح لنعرف ✓/✗؟».
    الرفض كان لتقييم لحظي من النتيجة الحية — لأن التقييم يجري على نتيجة الـ90
    دقيقة (score.fulltime)، ومباراة كأس حُسمت في الوقت الإضافي **تُقيَّم
    تعادلاً**؛ النتيجة الحية في state تحمل نتيجة ما بعد التمديد، فالتقييم منها
    كان سيُفسد خانة الـ70%+ بالذات. هذا التمرير يحلّ المشكلة بلا تلك المقايضة:
    نفس نداء التسوية المجمَّع ونفس عرف الـ90 دقيقة، مرة إضافية في المساء.
    أسوأ انتظار ينزل من ~24 ساعة إلى ~12.

    ⛔ **لا يستدعي update_history() إطلاقاً.** تقدّم history.json هو قناة
    الإنذار التي يقرأها deadman.py ليعرف أن التقييم الصباحي جرى؛ لو حرّكه
    المساء لظنّ الحارس أن الصباح تم وسكت — وهذا بالضبط ثقب الصمت الذي كلّفنا
    19 ساعة في 14 أغسطس. الأرشيف الدائم يبقى من اختصاص الصباح وحده.

    ⛔ ولا يستدعي resolve_scenarios() ولا generate_lessons(): كلاهما نداء
    Claude لكل عنصر، وتشغيلهما مرتين يومياً يضاعف الفاتورة التي أسقطت المحرك
    ثلاث مرات في يوليو (القاعدة 4: أنفق API-Football بسخاء وClaude بحكمة).

    يرجع عدد ما قُيّم (المحرك 2 + توقعات المالك).
    """
    store = load_json(PREDICTIONS_FILE, {"pending": {}, "resolved": []})
    store.setdefault("pending", {})
    store.setdefault("resolved", [])
    resolved_now, newly_resolved = resolve_pending(store)
    if resolved_now:
        save_json(PREDICTIONS_FILE, store)
    stats = compute_stats(store["resolved"])
    print(f"🌙 التمرير المسائي: قُيّم {resolved_now} توقعاً. "
          f"السجل: {pct(stats['overall'])}")

    # حارسا ما بعد التقييم يركضان هنا **عمداً**: خطأ بثقة 70%+ يصل المالك
    # مساءً بدل صباح الغد. ولو تُركا للصباح لما أطلقا أصلاً — فهما يقرآن
    # newly_resolved، وهذه الصفوف ستكون قد سُوّيت هنا.
    if newly_resolved:
        post_grading_alerts(newly_resolved, store)

    # توقعات المالك بنفس المنطق (سباق الدقة الثلاثي)
    user_store = load_json(USER_PREDICTIONS_FILE, {"pending": {}, "resolved": []})
    user_store.setdefault("pending", {})
    user_store.setdefault("resolved", [])
    user_now = 0
    if user_store["pending"]:
        user_now, _ = resolve_pending(user_store)
        if user_now:
            save_json(USER_PREDICTIONS_FILE, user_store)
            print(f"🌙 توقعات المالك: قُيّم {user_now}.")

    # تقييم إنذارات الرادار — صفر نداءات API (يقرأ النتائج من resolved نفسه)
    try:
        radar_graded = resolve_radar_log(store)
        if radar_graded:
            print(f"🌙 قُيّم {radar_graded} من إنذارات الرادار.")
    except Exception as e:                       # pragma: no cover - دفاعي
        print("🌙 تعذر تقييم سجل الرادار:", type(e).__name__)

    return resolved_now + user_now


if __name__ == "__main__":
    if "--resolve-only" in sys.argv:
        resolve_only()
    else:
        main()
