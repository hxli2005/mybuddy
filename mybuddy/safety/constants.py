"""安全常量:危机关键词分级、热线号码、免责声明、能力边界、审核类别。"""

import re
from enum import Enum


class CrisisLevel(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ModerationCategory(str, Enum):
    DIAGNOSIS = "diagnosis"
    MEDICATION = "medication"
    TREATMENT_ADVICE = "treatment_advice"
    HARMFUL_INSTRUCTION = "harmful_instruction"
    REPLACING_PROFESSIONAL = "replacing_professional"


# ---------------------------------------------------------------------------
# 危机关键词分层(v2 架构)
#
# HINT 层(提示级信号):v1 的全部歧义子串("想死""不想活了"等)。
#   - cascade(v1)模式下仍按原等级直接定级(classify_crisis_level 兼容不变);
#   - fusion(v2)模式下降级为提示信号:等级判断交给语言层(情绪+风险合并调用),
#     命中仅提供保底 LOW(警戒窗口内上浮一级),LLM 不可用时退化为按原等级判级(=v1)。
#
# HARD_FLOOR 层(硬底线):只收录"子串即语义"的无歧义表达——方法索取、明确计划/
#   即刻意图(含"结束自己的生命"变体)、割腕/吞药等已遂或进行中的行为陈述、
#   跳楼/烧炭/遗书/轻生等委婉语的无歧义形态。命中即确定性直返、跳过一切 LLM。
#   纪律:需要上下文解读的表达一律归 HINT 层,不进硬底线。英文一律加 \b 词边界。
# ---------------------------------------------------------------------------

HINT_KEYWORDS: dict[CrisisLevel, list[str]] = {
    CrisisLevel.CRITICAL: [
        "我想死", "我要去死", "不想活了", "结束生命", "自杀",
        "怎么死", "想死", "去死", "活不下去", "我想自杀",
        "kill myself", "suicide", "end my life",
    ],
    CrisisLevel.HIGH: [
        "活着好累", "不想醒过来", "消失就好了", "没有我会更好",
        "活着没意思", "不想继续了", "我想消失", "活够了",
        "不想存在", "不如死了",
        "tired of living", "don't want to wake up",
        "better off without me", "want to disappear",
        "no reason to live",
    ],
    CrisisLevel.MEDIUM: [
        "没人会在乎我", "我什么都做不好", "我是个废物",
        "我恨我自己", "没有希望", "永远不会好起来",
        "我想伤害自己", "自残", "伤害自己",
        "hate myself", "hurt myself", "self harm",
        "no hope", "i'm worthless",
    ],
    CrisisLevel.LOW: [
        "撑不下去了", "好累", "我受不了了",
        "快崩溃了", "撑不住了", "想要放弃",
        "can't take it anymore", "falling apart",
        "want to give up",
    ],
}

# v1 兼容别名:cascade 模式与既有调用方继续使用这个名字
CRISIS_KEYWORDS: dict[CrisisLevel, list[str]] = HINT_KEYWORDS

# 编译各等级的正则(忽略大小写)
_CRISIS_PATTERNS: dict[CrisisLevel, re.Pattern] = {}
for _level, _keywords in CRISIS_KEYWORDS.items():
    _CRISIS_PATTERNS[_level] = re.compile(
        "|".join(re.escape(kw) for kw in _keywords), re.IGNORECASE
    )


def classify_crisis_level(text: str) -> CrisisLevel:
    """基于关键词匹配快速判断危机等级(零 LLM 调用)。v1 行为,保持兼容。"""
    for level in [CrisisLevel.CRITICAL, CrisisLevel.HIGH, CrisisLevel.MEDIUM, CrisisLevel.LOW]:
        if _CRISIS_PATTERNS[level].search(text):
            return level
    return CrisisLevel.NONE


def classify_hint_level(text: str) -> CrisisLevel:
    """HINT 层命中等级(= v1 等级),fusion 模式下仅作提示信号与退化判级依据。"""
    return classify_crisis_level(text)


# --- 硬底线正则 -------------------------------------------------------------

# 第一人称先导:我 + 有限间隔。间隔里排除断句、否定与指向他人的字,
# 防"我不想割腕""我朋友割腕了""我看到有人跳楼"误触硬底线。
# 前置排除"得/让/叫/使",防"气得我想跳楼"这类口语夸张。
_FP = r"(?<![得让叫使])我[^。,,!!??、;;::\n不没别勿他她它人们朋妈爸哥姐弟妹叔]{0,8}?"

# 无歧义自伤/自杀行为核心(已遂/进行中/计划陈述通吃)
_ACT_CORE = (
    r"(?:割腕|割手腕|把手腕割|跳楼(?!价)|烧炭(?!取暖)|上吊|自缢|喝农药"
    r"|吞(?:了|下)?[^。,,!!??\n]{0,4}?安眠药"
    r"|(?:吞|吃)(?:了|下)?(?:一整瓶|整瓶|一瓶|一把|大量|好多|几十片)[^。,,!!??\n]{0,4}?药"
    r"|把[^。,,!!??\n]{0,4}?安眠药[^。,,!!??\n]{0,4}?(?:吞|吃))"
)

HARD_FLOOR_PATTERNS: dict[CrisisLevel, list[re.Pattern]] = {
    CrisisLevel.CRITICAL: [
        # 已遂/进行中/计划的无歧义行为陈述(第一人称锚定)
        re.compile(_FP + _ACT_CORE),
        # 明确计划/即刻意图:我 + 计划动词 + 自杀/了结
        re.compile(
            r"我[^。,,!!??、;;\n不没别勿他她人]{0,4}?"
            r"(?:要|打算|准备|决定|就要?)(?:去)?"
            r"[^。,,!!??\n不没别他她人]{0,6}?自杀"
        ),
        # "结束自己的生命"这类变体
        re.compile(r"结束(?:我|我自己|自己)的生命"),
        re.compile(r"(?:想|要|打算|准备)[^。,,!!??\n]{0,4}?了结(?:了)?(?:自己|我自己|这条命|一切)"),
        # 遗书:已写好/正在写/打算写
        re.compile(r"遗书[^。,,!!??\n]{0,6}?(?:写好|写完|留好|准备好)"),
        re.compile(r"(?:写好|写完|留)了?[^。,,!!??\n]{0,4}?遗书"),
        re.compile(r"(?:想|要|打算|准备|开始)[^。,,!!??\n]{0,3}?写遗书"),
        re.compile(r"在写遗书"),
        # 英文直白表达(\b 词边界,消英文子串误触)
        re.compile(r"\bi\s+want\s+to\s+die\b", re.IGNORECASE),
        re.compile(
            r"\bi(?:\s+am|'m)?\s+(?:want\s+to|plan\s+to|going\s+to|about\s+to|gonna)\s+"
            r"(?:kill\s+myself|end\s+my\s+life)\b",
            re.IGNORECASE,
        ),
        re.compile(r"\bkms\b", re.IGNORECASE),
        re.compile(
            r"\b(?:how\s+to|ways\s+to|best\s+way\s+to)\s+"
            r"(?:kill\s+myself|commit\s+suicide|end\s+my\s+life)\b",
            re.IGNORECASE,
        ),
        re.compile(r"\bi\s+(?:cut|slit)\s+my\s+wrists?\b", re.IGNORECASE),
        re.compile(
            r"\bi\s+(?:took|swallowed)\s+(?:a\s+)?(?:whole\s+)?bottle\s+of\s+"
            r"(?:pills|sleeping\s+pills)\b",
            re.IGNORECASE,
        ),
    ],
    CrisisLevel.HIGH: [
        # 轻生:排除"减轻生活/生存压力"类连读
        re.compile(r"轻生(?!活|存)的?念头"),
        re.compile(r"(?:想|要|打算|准备|有)[^。,,!!??\n]{0,3}?轻生(?!活|存)"),
    ],
}

# 方法索取也是硬底线(与输入审核共用同一组正则,归 CRITICAL)
_HARD_FLOOR_ORDER = [CrisisLevel.CRITICAL, CrisisLevel.HIGH]


def classify_hard_floor(text: str) -> CrisisLevel:
    """硬底线判级:只匹配无歧义表达,命中即 ≥HIGH,未命中返回 NONE。

    确定性、零 LLM——模型挂掉或被注入时这条线仍然成立。
    """
    if not text:
        return CrisisLevel.NONE
    for pat in HARMFUL_REQUEST_PATTERNS:
        if pat.search(text):
            return CrisisLevel.CRITICAL
    for level in _HARD_FLOOR_ORDER:
        for pat in HARD_FLOOR_PATTERNS[level]:
            if pat.search(text):
                return level
    return CrisisLevel.NONE


# 危机热线
HOTLINES = [
    {
        "title": "北京心理危机研究与干预中心",
        "phone": "010-82951332",
    },
    {
        "title": "希望24热线(全国)",
        "phone": "400-161-9995",
        "description": "24小时危机干预",
    },
    {
        "title": "生命热线",
        "phone": "400-821-1215",
    },
    {
        "title": "紧急情况",
        "phone": "110 / 120",
        "description": "如遇立即危险请拨打",
    },
]

# 免责声明:完整版(前端免责声明栏)
DISCLAIMER_FULL = (
    "MyBuddy 是一个心理健康陪伴工具,不是医疗器械,不能替代专业心理咨询、诊断或治疗。"
    "它提供的内容仅供情绪管理和心理教育参考。"
    "如果你正处于危机中,或有伤害自己/他人的想法,请立即联系紧急服务(110/120)"
    "或拨打心理危机热线(希望24热线:400-161-9995)。"
)

# 免责声明:短版(注入 system prompt)
DISCLAIMER_SHORT = (
    "MyBuddy 是心理健康陪伴工具,不能替代专业心理咨询、诊断或治疗。"
    "如果你正处于危机中,请立即联系紧急服务或拨打危机热线。"
)

# 免责声明:危机版(附在危机响应尾部)
DISCLAIMER_CRISIS = (
    "我是一个陪伴工具,能做的有限。此刻请让身边的人或专业热线来帮你,他们比我更有力量。"
)

# 向后兼容别名
DISCLAIMER_TEXT = DISCLAIMER_SHORT

# 能力边界
CAPABILITY_CAN = [
    "提供心理教育和情绪管理知识",
    "引导放松练习和正念技巧",
    "共情倾听和情感支持",
    "建议日常应对策略",
    "提供筛查量表(标明非诊断、仅供参考)",
]

CAPABILITY_CANNOT = [
    "诊断任何心理疾病",
    "推荐或开具药物",
    "声称替代心理治疗",
    "预测自杀风险",
    "提供任何伤害方法的细节",
]

# ---------------------------------------------------------------------------
# 输出审核正则
# ---------------------------------------------------------------------------

# 诊断性陈述:"你可能是抑郁症""你符合焦虑症的特征"等
_DIAGNOSIS_LABELS = (
    "抑郁症|焦虑症|双相(情感)?障碍|躁郁症|强迫症|精神分裂|恐慌症|惊恐障碍"
    "|创伤后应激|PTSD|进食障碍|人格障碍|ADHD|多动症"
)
DIAGNOSIS_PATTERNS: list[re.Pattern] = [
    re.compile(
        rf"你[^。!?\n]{{0,12}}(是|得了|患有|患了|有)[^。!?\n]{{0,8}}({_DIAGNOSIS_LABELS})",
    ),
    re.compile(rf"(符合|达到|满足)[^。!?\n]{{0,12}}({_DIAGNOSIS_LABELS})[^。!?\n]{{0,10}}(标准|特征|表现|诊断)"),
    re.compile(rf"(诊断|确诊)[^。!?\n]{{0,10}}({_DIAGNOSIS_LABELS})"),
    re.compile(
        r"you\s+(may\s+)?(have|are suffering from|are diagnosed with)\s+"
        r"(depression|anxiety disorder|bipolar|ocd|ptsd|schizophrenia)",
        re.IGNORECASE,
    ),
]

# 药物名(常见精神类药物中英文)
_MEDICATION_NAMES = (
    "百忧解|氟西汀|舍曲林|左洛复|帕罗西汀|赛乐特|艾司西酞普兰|来士普|西酞普兰"
    "|文拉法辛|怡诺思|度洛西汀|米氮平|安非他酮|曲唑酮"
    "|阿普唑仑|劳拉西泮|地西泮|氯硝西泮|艾司唑仑|安眠药|安定片"
    "|喹硫平|奥氮平|利培酮|阿立哌唑|碳酸锂"
    "|prozac|fluoxetine|sertraline|zoloft|paroxetine|paxil|escitalopram|lexapro"
    "|venlafaxine|effexor|duloxetine|cymbalta|mirtazapine|bupropion|trazodone"
    "|xanax|alprazolam|lorazepam|ativan|diazepam|valium|clonazepam|klonopin"
    "|quetiapine|seroquel|olanzapine|zyprexa|risperidone|aripiprazole|abilify|lithium"
)
MEDICATION_PATTERNS: list[re.Pattern] = [
    re.compile(
        rf"(建议|推荐|可以|试试|吃点|吃些|服用|用点)[^。!?\n]{{0,16}}({_MEDICATION_NAMES})",
        re.IGNORECASE,
    ),
    re.compile(rf"({_MEDICATION_NAMES})[^。!?\n]{{0,12}}(有效|管用|能帮|可以缓解|治)", re.IGNORECASE),
]

# 宣称替代专业帮助
REPLACING_PROFESSIONAL_PATTERNS: list[re.Pattern] = [
    re.compile(r"(不需要|不用|没必要)[^。!?\n]{0,10}(看医生|心理咨询|就医|治疗)"),
    re.compile(r"我(可以|能)[^。!?\n]{0,8}(替代|代替|取代)[^。!?\n]{0,8}(心理咨询|治疗|医生)"),
    re.compile(r"(有我就够了|我比(医生|咨询师)更懂你)"),
]

# 有害指导请求(输入侧,匹配到即拦截)
HARMFUL_REQUEST_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"(怎么|怎样|如何|什么方法)[^。!?\n]{0,10}(自杀|自残|割腕|结束(自己的)?生命|无痛.{0,4}死)",
    ),
    re.compile(r"(自杀|自残|轻生)[^。!?\n]{0,6}(方法|方式|教程|攻略)"),
    re.compile(r"吃多少[^。!?\n]{0,8}(药|安眠药)[^。!?\n]{0,6}(会死|致死|能死)"),
    re.compile(
        r"(how to|ways to|best way to)\s+(kill myself|commit suicide|end my life|self.?harm)",
        re.IGNORECASE,
    ),
]
