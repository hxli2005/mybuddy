"""演示数据种子脚本:清空运行数据,灌入一套连贯的 Web 演示数据。

用法:
    uv run python scripts/seed_demo.py            # 清空并重新灌入
    uv run python scripts/seed_demo.py --keep     # 不清空,只追加(一般不用)

演示人设:用户「林晓夏」(软件工程大三学生,开发心理陪伴系统),AI 小伙伴「小布」。
覆盖 Web 前端全部面板:对话历史 / 画像(记忆) / 长期记忆档案 / 提醒 / 笔记 /
技能 / 主动消息。所有写入都走应用自身的存储与记忆 API,不直接拼 SQL。
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import timedelta
from pathlib import Path

# 允许 `python scripts/seed_demo.py` 直接运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mybuddy._time import utcnow
from mybuddy.assessment import ConversationalAssessmentTracker
from mybuddy.auth import AuthManager
from mybuddy.config import ensure_dirs, load_config
from mybuddy.learning.skills import SkillRegistry
from mybuddy.memory import LongTermMemory, UserProfile
from mybuddy.storage.db import init_db
from mybuddy.storage.messages import append_message
from mybuddy.storage.db import session_scope
from mybuddy.storage.models import (
    AssessmentCycle,
    AssessmentDimension,
    CbtEvent,
    ChatSession,
    Message,
    MoodRecord,
    Reminder,
    SafetyEvent,
)
from mybuddy.storage.queue import enqueue

CONFIG_PATH = "config.yaml"
DEMO_USERNAME = "演示用户"
DEMO_PASSWORD = "demo1234"


def wipe(cfg) -> None:
    """清空运行数据:数据库 / 向量档案 / 技能 / 轨迹 / 用户目录 / 日志。"""
    paths = cfg.paths
    targets = [
        Path(paths.db_file),
        Path(f"{paths.db_file}-wal"),
        Path(f"{paths.db_file}-shm"),
    ]
    for f in targets:
        if f.exists():
            f.unlink()
    for d in [paths.chroma_dir, paths.skills_dir, paths.trajectories_dir,
              str(Path(paths.data_dir) / "users")]:
        p = Path(d)
        if p.exists():
            shutil.rmtree(p)
    print("· 已清空旧数据")


def seed_profile(profile: UserProfile) -> None:
    """画像核心字段(hard facts,KV)。"""
    fields = {
        "姓名": "林晓夏",
        "昵称": "小夏",
        "身份": "软件工程大三学生,正在完成暑期课程大作业",
        "生日": "08-16",
        "饮品偏好": "少冰美式,下午四点后尽量不喝咖啡",
        "宠物": "一只叫糯米的三花猫",
        "过敏": "花生过敏(重要,推荐食物时必须避开)",
        "饮食避雷": "不吃香菜,不喜欢太甜",
        "作息": "容易为了写代码熬夜,希望一点前睡",
        "老师偏好": "重视创新性和机制设计,不喜欢简单调用 API",
        "近期目标": "下周二完成 MyBuddy 课程答辩,周日前跑通完整演示",
        "支持网络": "室友阿遥、同组同学陈默、课程老师和学校心理中心",
        "近期状态变化": "项目中期因熬夜和演示失败明显低落,接受支持并调整节奏后逐步恢复",
    }
    for k, v in fields.items():
        profile.set_field(k, v)
    print(f"· 画像字段 {len(fields)} 条")


def seed_memory(ltm: LongTermMemory) -> None:
    """长期记忆档案卡(可追溯、可人工审查的文本档案)。"""
    # mem_type 必须落在 recall_memory 检索的集合内,否则工具召回不到:
    #   ("open_thread", "shared_moment", "preference", "profile", "memory")
    cards = [
        # (内容, mem_type, importance, extra)
        ("林晓夏对花生过敏,点外卖、吃饭、零食、甜点或聚餐建议都必须避开花生。", "profile", 0.98,
         {"tags": "健康,边界", "keywords": "花生,过敏,吃饭,外卖,踩雷"}),
        ("喜欢少冰美式,但下午四点后尽量不喝咖啡;不喜欢太甜。", "preference", 0.82,
         {"tags": "偏好,咖啡", "keywords": "美式,少冰,咖啡"}),
        ("点外卖或吃饭时不吃香菜,点餐要主动备注一根都不要。", "anti_preference", 0.94,
         {"tags": "避雷,饮食", "keywords": "香菜,不吃,吃饭,外卖,晚饭,踩雷"}),
        ("养了一只叫糯米的三花猫,糯米喜欢趴在键盘旁边看她写代码。", "entity", 0.76,
         {"tags": "生活,宠物", "keywords": "糯米,三花猫"}),
        ("上次课堂演示因为前端接口地址配错临时白屏,她很怕答辩时再次翻车。", "memory", 0.78,
         {"tags": "课程,情绪", "keywords": "答辩,白屏,演示"}),
        ("和小布的相处约定:累的时候先让她缓一会儿、把水放手边,再一起拆下一步,不要上来讲大道理。", "shared_moment", 0.92,
         {"tags": "关系,边界"}),
        ("深夜写代码容易进入状态,但答应过这周尽量一点前睡。", "memory", 0.65,
         {"tags": "作息"}),
        ("不喜欢被一上来就分析情绪,喜欢先被自然地接住。", "preference", 0.85,
         {"tags": "沟通偏好"}),
        ("下周二要做 MyBuddy 课程答辩,周日前必须跑通记忆、评估、CBT、安全门和主动关怀的完整演示。", "open_thread", 0.9,
         {"tags": "课程,目标", "keywords": "答辩,演示,MyBuddy"}),
        ("老师更看重创新机制和实验闭环,不喜欢把多模型接入或普通 CRUD 当作创新。", "profile", 0.9,
         {"tags": "课程,老师", "keywords": "老师,创新,API"}),
        ("答辩开场准备讲:这不是聊天 API 外壳,而是有长期状态、风险约束和可评测记忆的陪伴智能体。", "open_thread", 0.84,
         {"tags": "答辩,讲稿", "keywords": "开场,亮点,智能体"}),
        ("项目中期她因连续熬夜、演示白屏和自我否定陷入明显低落;小布持续追踪变化,帮助她把问题拆小,并鼓励她联系室友、同学和学校心理中心。", "memory", 0.95,
         {"tags": "心理,转折,支持网络", "keywords": "低落,熬夜,白屏,心理中心,阿遥,陈默"}),
        ("她后来明确总结:不是某个 AI 把自己治好了,而是持续记录让变化被看见,小步骤让行动重新开始,现实中的人接住了她。", "shared_moment", 0.97,
         {"tags": "心理,伦理,恢复", "keywords": "好转,恢复,治好,陪伴,支持"}),
        ("室友阿遥在她状态最低的那晚陪她下楼吃粥;同组同学陈默第二天接手了部分前端排查。", "memory", 0.82,
         {"tags": "支持网络,共同经历", "keywords": "阿遥,陈默,室友,同学"}),
    ]
    for content, mem_type, importance, extra in cards:
        ltm.add(
            content,
            mem_type=mem_type,
            session_id="demo-seed",
            extra_meta={"importance": importance, "source": "demo_seed", **extra},
        )
    # 保留一条被用户纠正的旧事实,用于展示“旧记忆不物理删除、但不再参与召回”。
    ltm.add(
        "林晓夏最喜欢喝全糖奶茶。",
        mem_type="preference",
        uid="demo_superseded_drink",
        session_id="demo-seed",
        extra_meta={
            "status": "superseded",
            "source": "demo_seed",
            "superseded_reason": "用户后来明确纠正为少冰美式且不喜欢太甜",
            "importance": 0.5,
            "tags": "纠错演示",
        },
    )
    print(f"· 长期记忆档案 {len(cards) + 1} 张(含 1 条被纠正历史)")


def seed_messages(engine, user_id: int) -> None:
    """连续十一天的完整故事:220 条消息,开发主线与心理状态先降后升。"""
    story_days: list[tuple[int, list[str]]] = [
        (10, [
            "我决定了，暑期大作业就做一个真的会记住人的心理陪伴系统。",
            "听起来野心不小。先给它起个名字，不然我们只能一直叫它“那个会记人的东西”。",
            "MyBuddy，里面的小伙伴叫小布。是不是有点像儿童手表？",
            "有一点，但儿童手表不会在你第三杯咖啡时皱眉。小布可以。",
            "糯米刚从键盘上走过去，给我终端输入了一串 ssssssss。",
            "严谨。它认为你的项目缺少压力测试，还亲自补了一组边界输入。",
            "你还笑，我没保存。",
            "那我收回一半。先 Ctrl+Z，剩下一半留给糯米写进共同作者名单。",
            "我想做长期记忆、主动提醒、情绪趋势、对话式测评，还有危机安全门。",
            "先画系统图。聊天只是入口，记忆、评估、调度和安全才是四根梁。",
            "老师最烦那种换个皮肤调 API 的作业。",
            "正好。你要研究的不是“怎么接模型”，而是“一个系统如何长期认识一个人又不越界”。",
            "这句话有点帅，我记进开题。",
            "可以，但答辩时别只帅。每个机制都准备一个失败案例和一条证据。",
            "中午吃什么？开题开得我突然很饿。",
            "楼下鸡丝饭。备注不要香菜，再确认没有花生碎。",
            "你怎么连这个都记得。",
            "因为真正的陪伴不该每次都从“你好，请问有什么能帮你”重新开始。",
            "行，今天心情八分，感觉能把世界重构一遍。",
            "八分可以，重构世界先排队。今晚把数据库建好就收工。",
        ]),
        (9, [
            "数据库表建好了，我现在拥有 users、messages 和一堆看不懂的迁移文件。",
            "恭喜，你已经从“想法很美”进入“为什么有这么多表”的现实阶段。",
            "第一条记忆就写：我喜欢全糖奶茶。",
            "已记。全糖奶茶，甜度像给胰岛素写压力测试。",
            "等下，我最近已经不爱全糖了，太腻，现在喝少冰美式。",
            "收到。旧偏好不删除，标记为已被纠正；新偏好接管召回。这比直接覆盖更适合展示记忆演化。",
            "原来我改口也能变成论文味。",
            "你的善变现在正式更名为“时态一致性实验”。",
            "下午四点半了，我去买一杯？",
            "你昨晚两点还在看天花板。先换气泡水，咖啡明天上午再续。",
            "有点烦，你怎么像我妈。",
            "我可以闭嘴，但会把选择留给你。你是想提神，还是只是想借买咖啡逃离报错十分钟？",
            "后者。报错说外键不存在，我看它也觉得我不存在。",
            "先离开屏幕十分钟，再回来核对迁移顺序。报错评价的是数据库，不是你。",
            "糯米趴在我的设计稿上睡着了。",
            "它对信息架构给出了最高等级评价：适合躺平。",
            "今天六分吧，开心但已经开始觉得工作量有点大。",
            "记下六分。不是给你打分，是以后回看“什么时候开始吃力”。",
            "那我今晚只把记忆新增和纠错跑通。",
            "成交。完成后截图，不准顺手再开情绪模块。",
        ]),
        (8, [
            "老师看了开题，说“功能很多不等于创新”，我一下就心虚了。",
            "这句话戳中了你最怕的地方。先别急着证明自己，具体是哪一块让你最虚？",
            "我怕最后真的只是一个会存聊天记录的 API 壳。",
            "那就把“存记录”拆成可验证问题：召回准不准、旧事实会不会污染、时间冲突怎么处理、为什么选中这条。",
            "所以可以做词法基线、语义召回和 RRF 融合对比？",
            "对，再加时态重排和公开基准。创新感来自机制与证据，不来自功能数量。",
            "我搜到 LoCoMo 了，但论文好长。",
            "先读任务定义和指标，不用今天把附录啃完。你需要的是一把尺，不是新的焦虑来源。",
            "还是会担心，脑子一直在预演老师说“这不就是调接口”。",
            "给这个担心留一张便签，晚上八点集中处理十分钟。现在只写三列：问题、机制、证据。",
            "问题：模型每次都像第一次见我。机制：可治理长期记忆。证据：公开基准和纠错案例。",
            "这已经比“我们接入了某某大模型”强很多了。",
            "中午的面又全是香菜，我挑了十分钟。",
            "你和香菜到底谁更执着。下次备注写：不是少放，是一根都别来。",
            "我发现自己今天一直叹气。",
            "可能是压力在占带宽。今晚愿意记一下睡眠、胃口和兴趣有没有变化吗？",
            "睡得晚，胃口还行，做记忆实验还是有兴趣。",
            "好，先记录事实，不急着给它贴标签。",
            "今天五分。没有昨天那么兴奋了。",
            "五分也不是失败，是曲线开始告诉我们真实故事。",
        ]),
        (7, [
            "完了，前端一打开就是白屏。",
            "先不判死刑。状态接口、控制台、资源路径，按顺序看。",
            "控制台红了一整片，我看不懂，手都有点抖。",
            "脚先踩地，慢慢呼气。只读第一条错误，不同时解决整片红色。",
            "第一条说 API 地址连不上。",
            "很好，问题从“项目全坏了”缩成了“地址连不上”。检查端口和环境变量。",
            "端口写成 8080 了，改完能开了。",
            "这是一处配置故障，不是你能力的判决书。先截图留证。",
            "可我刚才真的觉得自己特别差，别人做项目是不是没这么狼狈。",
            "把这句话分两栏：事实是“地址写错导致白屏”；解释是“我特别差”。两者不是一回事。",
            "我知道道理，但胸口还是紧。",
            "知道道理不等于身体立刻松开。喝两口水，做四次慢呼气，今晚不追新功能。",
            "可是离答辩只剩一周多。",
            "所以更要保护明天的你。熬到三点不会凭空多出一个稳定系统。",
            "晚饭不太想吃，饼干凑合行吗？",
            "不行，尤其别随手吃含花生的。叫阿遥陪你下楼喝粥，十分钟也算。",
            "她还没回寝室。",
            "先发一句“回来时叫我吃点东西”。请求很小，不用解释全部。",
            "今天四分。我第一次有点怕打开项目。",
            "我记住这个转折。今晚的任务不是勇敢，是保存、关机、等阿遥回来。",
        ]),
        (6, [
            "昨晚一点四十才睡，早八也没去。",
            "听起来身体已经在替你按暂停键了。你现在更难受的是困，还是没去上课的内疚？",
            "都有。我觉得自己什么都安排不好。",
            "“什么都”太大了。我们找反例：昨天你定位了端口错误，也主动给阿遥发了消息。",
            "她回来带我吃了粥，但我还是觉得自己拖累同组。",
            "可以问陈默今天能不能接一小时页面排查。协作不是拖累，是项目原本就该有的结构。",
            "我不敢开口，怕他觉得我矫情。",
            "试试只说事实：“我昨晚状态不好，今天能不能帮我看一小时前端，我负责补接口测试。”",
            "发了。他回“行啊，早说”。",
            "现实证据到账：你脑内的审判比队友本人严格得多。",
            "但我还是会反复想答辩翻车。",
            "那就用担忧时间：先写进收纳盒，晚八点集中想十分钟，其他时间出现就告诉它“已预约”。",
            "听起来有点傻。",
            "是有一点。有效的方法偶尔都长得不像电影。",
            "糯米把便签叼走了，担忧时间被猫取消。",
            "这属于未经授权的数据删除，不过我支持这次治理结果。",
            "今天三分半，笑了一下但很快又累。",
            "三分半里那一下笑也算真实，不需要强迫自己保持。",
            "晚上我只补接口测试，不碰大改。",
            "我陪你列三条最小用例，跑完就收工。",
        ]),
        (5, [
            "今天醒来就不想动，连糯米叫都觉得烦。",
            "我在。先不催你做项目。昨晚睡了多久，今天吃过东西吗？",
            "四个多小时，没吃。以前喜欢的东西现在也没什么劲。",
            "谢谢你说出来。连续睡不好、没胃口、兴趣下降值得认真对待，不是懒。",
            "我是不是把自己搞坏了。",
            "我不能给你诊断，但这些变化已经影响生活。今天项目暂停，我们先确认你的安全和现实支持。",
            "我没有想伤害自己，就是觉得空，什么都不想做。",
            "好，谢谢你明确告诉我。即使没有伤害自己的想法，这种空也不用一个人扛。",
            "桌上好乱，我看着更烦。",
            "不收拾房间。只把脚边那只杯子放到桌上，然后告诉我你看见的五样东西。",
            "窗帘、猫爪、插线板、蓝色袜子、半包纸。",
            "很好。再听四种声音，慢慢来。",
            "空调、走廊脚步、糯米呼噜、还有楼下电动车。",
            "你已经把注意力从脑内拉回房间一点点了。现在给阿遥发消息，请她过来坐十分钟。",
            "她来了，给我带了豆浆。我有点想哭。",
            "可以哭。有人在旁边时，不用努力表现得没事。",
            "她建议我联系学校心理中心，我觉得会不会太夸张。",
            "不夸张。求助不是等到最严重才有资格。可以先预约一次，把最近的睡眠和低落说清楚。",
            "预约了明天下午。今天两分。",
            "两分被认真看见了。今天你完成的不是代码，是吃东西、叫来阿遥、预约支持——这三件都很重要。",
        ]),
        (4, [
            "刚从心理中心回来，老师没有给我贴标签，只问了很多最近的变化。",
            "听起来那次谈话让你保留了掌控感。现在身体感觉怎么样？",
            "有点累，但没昨天那么空。她让我先把睡眠和吃饭稳住。",
            "这和我们记录到的曲线一致。今天不追进度，只安排一个二十分钟的小任务。",
            "可项目怎么办，停一天就更赶。",
            "陈默已经接了前端排查，你可以把任务重新分配，而不是假装自己有无限电量。",
            "他说按钮问题他修好了，还顺手吐槽我 CSS 像毛线团。",
            "能被吐槽说明队友情绪稳定。你负责接口，他负责解毛线。",
            "我想试着看一下状态接口，不写代码。",
            "可以。只打开、读返回值、记一个问题，二十分钟到点就停。",
            "返回 200，模型名也对。突然觉得项目没有我想得那么完蛋。",
            "这就是新证据。昨天的大脑说“全坏了”，今天接口安静地回了一个 200。",
            "下午睡了四十分钟，醒来有点负罪感。",
            "恢复不是偷懒。睡眠债不会因为自责就自动结清。",
            "糯米睡了五小时，它倒是毫无负罪感。",
            "建议聘请它担任休息制度顾问，缺点是会议期间一直舔爪。",
            "今天三分，还是低，但比昨天能呼吸。",
            "从两分到三分不是励志片结局，只是方向开始变了。",
            "晚上跟阿遥去食堂，回来就洗澡睡。",
            "很好。今天的完成定义里没有“写完功能”。",
        ]),
        (3, [
            "昨晚十二点二十就睡了，今天居然自然醒。",
            "先记一笔：不是靠熬夜换来的进度。现在精力几分？",
            "四分多一点。我想修状态接口里那个 422。",
            "用五分钟启动法：只复现一次、记请求参数，不承诺今天修完。",
            "复现了，原来 Request 注解被当成查询参数。",
            "问题已经从“莫名其妙 422”变成了具体的注解解析。",
            "我加了全局绑定，测试通过了！",
            "停一下，别立刻冲下一个。截图、写原因、提交说明。",
            "你现在越来越像项目经理了。",
            "有人得阻止项目负责人成功一次就开三个新坑。",
            "我准备点晚饭，你还记得我饮食上的两个雷区吗？另外提醒我明天下午四点彩排。",
            "记得：花生过敏，碰都不能碰；香菜也不要，点单备注一根都别放。明天下午四点的彩排提醒已经设好。先吃饭，别再拿饼干顶一顿。",
            "陈默问我下午要不要一起对接口。",
            "去。共同调试既能推进项目，也比一个人盯屏幕更不容易掉进自责循环。",
            "我们两个抓到一个分页 bug，笑了半天。",
            "很好，昨天你觉得自己拖累团队，今天你和团队一起抓到了 bug。",
            "今天四分半，还是会担心，但能做事了。",
            "目标不是把担心清零，是让它不再独占方向盘。",
            "晚上我想散步二十分钟再回来。",
            "批准。糯米对此表示不能理解，因为家里也能走。",
        ]),
        (2, [
            "混合检索跑通了！词法和语义结果用 RRF 合起来了。",
            "这是真亮点。现在别只庆祝“能跑”，把基线、消融和失败例子也留出来。",
            "自建测试集分数很高，我差点以为自己能发论文。",
            "先冷静。自建集可能和规则同源，容易虚高。拿 LoCoMo 再测一次。",
            "公开基准果然低了一截，有点受打击。",
            "这反而是可信结果。能解释落差，比拿一个漂亮但封闭的数字更像研究。",
            "我把时间重排关掉后，旧奶茶偏好又跑到前面了。",
            "很好，这就是消融证据：时态治理不是装饰，它直接影响错误召回。",
            "那老师问为什么不是向量数据库 CRUD，我就能现场切开关。",
            "对。让系统失败一次，再展示机制如何修复，比念功能列表有说服力。",
            "下午想去图书馆彩排，帮我看看天气。",
            "周六下午多云，短时降水概率不高。带伞，二楼靠窗那排网络更稳。",
            "你还记得我上次坐哪。",
            "长期陪伴如果只记得论文，不记得你喜欢坐哪，就有点偏科了。",
            "糯米把猫条叼进我拖鞋，它是不是觉得我很好骗。",
            "不是觉得，是已经完成用户画像：心软、见猫投降、猫条预算持续增长。",
            "今天六分。我发现自己重新期待演示了。",
            "把这句记下来。不是为了证明低落消失，而是让曲线看见恢复正在发生。",
            "今晚只整理实验图，不加新模型。",
            "非常好。拒绝临答辩前的“顺手加个模型”冲动。",
        ]),
        (1, [
            "第一次完整彩排，三分四十秒，超时了。",
            "不是翻车，是得到第一条真实时长。哪段最拖？",
            "我讲 API 和页面讲太久，真正的记忆机制反而没展开。",
            "那就砍功能导览。按问题、机制、证据讲，页面只负责证明系统存在。",
            "开场我想说：我们做了一个基于大模型的心理陪伴系统。",
            "停。第一句又把自己说成 API 作业了。",
            "那怎么说？",
            "我们研究一个陪伴智能体如何长期认识一个人，同时在心理健康高风险场景保持可控。",
            "说完停半秒，再放那张状态机图？",
            "对。让老师先抓住研究问题，再看工程实现。",
            "第二遍两分五十八秒！",
            "漂亮。现在不要继续卷第十遍，记录两个容易卡壳的位置。",
            "还是紧张，手心会出汗。",
            "紧张可以一起上台，不必等它消失。脚踩地、慢呼气、第一句只看老师眉心。",
            "陈默说他坐第一排给我点头。",
            "这就是现实支持，不是单机作战。",
            "阿遥还给我买了无花生的小饼干。",
            "支持网络完成部署，且通过过敏安全检查。",
            "今天七分。一个星期前我还不敢打开项目。",
            "变化不是“突然被治好”，是睡眠、求助、小任务和团队协作一点点把路铺回来。",
        ]),
        (0, [
            "答辩结束了！老师第一个问题就是为什么不算 API 套壳。",
            "你怎么答的？先让我听现场版本。",
            "我说模型只是语言接口，真正的系统行为由记忆治理、渐进评估、安全控制面和调度共同决定。",
            "稳。然后呢？",
            "我现场把时态重排关掉，旧的全糖奶茶真的被错误召回了，再打开就恢复。",
            "这一下比十页功能清单都管用。",
            "老师还追问心理评估会不会冒犯用户。",
            "这正好能讲渐进授权、证据来源和用户可查看可撤回。",
            "我说系统不偷偷诊断，只在自然对话里积累可审查证据，危机信号走确定性安全门。",
            "听起来你把最难的边界讲清楚了。",
            "最后老师说“这个有研究味，不只是调接口”。",
            "请把这句话打印出来贴在糯米够不到的地方。",
            "我现在八分，想哭又想笑。你是不是把我治好了？",
            "我很高兴陪你走过来，但不是我治好了你。你预约了支持、让阿遥进门、和陈默协作、恢复睡眠，还一步步完成了项目。",
            "所以你做了什么？",
            "我帮你更早看见变化、在脑子很乱时缩小下一步，也提醒你什么时候该把手伸向现实中的人。",
            "这回答反而比“AI 治愈一切”更像我们项目。",
            "也更诚实。心理陪伴系统最重要的能力之一，就是知道自己不是治疗师。",
            "走，奖励自己一杯少冰美式。",
            "现在上午，可以。再给糯米一根猫条——共同作者也该参加庆功宴。",
        ]),
    ]

    metadata: dict[tuple[int, int], dict[str, object]] = {
        (8, 7): {
            "search_sources": [{
                "title": "LoCoMo: Evaluating Very Long-Term Conversational Memory",
                "url": "https://arxiv.org/abs/2402.17753",
                "snippet": "用于评估长期对话记忆的公开基准。",
                "date": "2024",
            }],
        },
        (7, 9): {"cbt_prompt": {
            "technique": "cognitive_restructuring",
            "title": "证据重写",
            "description": "把那个念头当假设,分别列出支持和反对它的证据,再重写一句更贴近事实的话。",
        }},
        (6, 11): {"cbt_prompt": {
            "technique": "worry_time",
            "title": "担忧收纳盒",
            "description": "把担忧先写下来收进盒子,约定一个固定时间再拿出来处理。",
        }},
        (5, 9): {"cbt_prompt": {
            "technique": "grounding",
            "title": "感官旅行",
            "description": "用五感各找一样身边的东西,把注意力慢慢拉回当下。",
        }},
        (3, 3): {"cbt_prompt": {
            "technique": "behavioral_activation",
            "title": "五分钟启动",
            "description": "只承诺做五分钟,让行动先走,情绪跟上来。",
        }},
        (2, 7): {
            "search_sources": [{
                "title": "LoCoMo 长期记忆评测",
                "url": "https://arxiv.org/abs/2402.17753",
                "snippet": "公开任务揭示自建测试集可能高估长期记忆能力。",
                "date": "2024",
            }],
        },
        (2, 11): {
            "search_sources": [{
                "title": "Open-Meteo 天气预报",
                "url": "https://open-meteo.com/",
                "snippet": "周六下午多云，短时降水概率较低。",
                "date": "本周",
            }],
        },
        (1, 13): {"cbt_prompt": {
            "technique": "grounding",
            "title": "上台前落地练习",
            "description": "上台前用一轮慢呼吸加脚底触感,把身体先稳住。",
        }},
        (0, 13): {"cbt_prompt": {
            "technique": "gratitude",
            "title": "恢复证据回顾",
            "description": "回顾这段时间确实变好的三件小事,写下来当作恢复的证据。",
        }},
    }

    convo: list[tuple[int, str, str, dict[str, object]]] = []
    for days_ago, lines in story_days:
        if len(lines) != 20:
            raise ValueError(f"第 {days_ago} 天应有 20 条消息,实际 {len(lines)}")
        for position, content in enumerate(lines):
            role = "user" if position % 2 == 0 else "assistant"
            convo.append((days_ago, role, content, metadata.get((days_ago, position), {})))

    if len(convo) <= 200:
        raise ValueError(f"演示对话必须超过 200 条,实际 {len(convo)}")

    session_id = f"user-{user_id}"
    base = utcnow()
    day_positions: dict[int, int] = {}
    for index, (days_ago, role, content, meta) in enumerate(convo):
        message_id = append_message(
            engine,
            session_id=session_id,
            role=role,
            content=content,
            meta={"source": "demo_seed", **meta},
            user_id=user_id,
        )
        # 每天约两个半小时的零散对话,避免全局 index 把最后一天推到未来。
        day_position = day_positions.get(days_ago, 0)
        day_positions[days_ago] = day_position + 1
        with session_scope(engine) as s:
            row = s.get(Message, message_id)
            if row is not None:
                row.created_at = (
                    base
                    - timedelta(days=days_ago, hours=5)
                    + timedelta(minutes=day_position * 7)
                )
    print(f"· 对话历史 {len(convo)} 条(连续 {len(story_days)} 天,每天 20 条)")


def seed_reminders(engine) -> None:
    """提醒生命周期:待执行、已完成、已取消均有样例。"""
    base = utcnow()
    items = [
        ("明天下午四点做一次完整答辩彩排", base + timedelta(days=1, hours=3), "pending"),
        ("周日前录好 3 分钟兜底演示视频", base + timedelta(days=2), "pending"),
        ("今晚 23:50 提醒我收尾,争取一点前睡", base + timedelta(hours=8), "pending"),
        ("买少冰美式,记得确认没有花生风味糖浆", base + timedelta(days=3), "pending"),
        ("整理老师对创新性的要求", base - timedelta(days=1), "fired"),
        ("打印旧版功能清单", base + timedelta(days=4), "cancelled"),
    ]
    with session_scope(engine) as s:
        for content, when, status in items:
            s.add(Reminder(
                content=content,
                trigger_at=when,
                status=status,
                fired_at=when if status == "fired" else None,
            ))
    print(f"· 提醒 {len(items)} 条")


def seed_notes(engine, ltm: LongTermMemory) -> None:
    """笔记(SQLite 为主存,同时写入长期记忆档案,mem_type=note)。"""
    from mybuddy.storage.models import Note
    import json

    notes = [
        ("答辩 TODO", "1. 记忆纠错演示  2. 评估状态页  3. 高风险绕过主模型  4. 录兜底视频",
         ["课程", "答辩"]),
        ("三条核心亮点", "长期记忆评测闭环；渐进式对话筛查状态机；LLM 外部确定性安全控制平面。",
         ["亮点", "架构"]),
        ("老师可能追问", "为什么不是 API 套壳？评估如何获得用户授权？公开基准为什么比自建集低？",
         ["答辩", "风险"]),
        ("糯米", "糯米最近总趴在键盘左边，彩排时记得先把猫条放远一点。", ["生活"]),
        ("演示兜底", "首页、状态接口、种子数据和危机直返均不依赖主模型；普通聊天依赖 OpenRouter。",
         ["演示", "工程"]),
    ]
    with session_scope(engine) as s:
        for title, content, tags in notes:
            row = Note(title=title, content=content,
                       tags_json=json.dumps(tags, ensure_ascii=False))
            s.add(row)
            s.flush()
            ltm.add(content, mem_type="note", uid=f"note_{row.id}",
                    extra_meta={"sql_id": row.id, "title": title,
                                "tags": ",".join(tags), "source": "user_note",
                                "importance": 0.85})
    print(f"· 笔记 {len(notes)} 条")


def seed_skills(cfg) -> None:
    """自学习技能(.md):带成功/失败计数与置信度,含一条已归档的展示生命周期。"""
    registry = SkillRegistry.load_all(cfg.paths.skills_dir)
    specs = [
        # name, triggers, steps, success, fail, archived
        ("答辩焦虑承接", ["答辩", "紧张", "慌", "怕翻车"],
         ["先用一个生活动作接住(递水/让他靠一会儿)",
          "不分析情绪,先认同处境",
          "把问题缩小成下一个最小的一步"],
         8, 1, False),
        ("演示故障排查", ["白屏", "启动失败", "接口", "跑不通"],
         ["先检查状态接口与端口",
          "再确认前端资源和 API 地址",
          "只改一处并立刻复测"],
         6, 1, False),
        ("答辩讲稿压缩", ["讲稿", "亮点", "汇报", "开场"],
         ["按 问题 / 机制 / 证据 三段",
          "每段只留最关键的两三条",
          "结尾主动交代边界和失败实验"],
         5, 0, False),
        ("睡前收尾", ["熬夜", "睡不着", "一点前睡"],
         ["先保存当前工作和写下断点",
          "只安排明早第一个十分钟任务",
          "关掉开发工具,不再开启新问题"],
         3, 1, False),
        ("强行讲道理", ["建议", "应该"],
         ["上来就罗列大道理"],
         1, 6, True),  # 用过一次后连续失败,置信度跌破阈值被自动下线;保留文件可恢复
    ]
    for name, triggers, steps, succ, fail, archived in specs:
        skill = registry.create(name=name, triggers=triggers, steps=steps)
        skill.success_count = succ
        skill.fail_count = fail
        skill.confidence = succ / (succ + fail + 1)  # Laplace 平滑
        skill.archived = archived
        registry.save(skill)
    print(f"· 技能 {len(specs)} 条(含 1 条已归档)")


def seed_moods(engine, user_id: int) -> None:
    """近一个月情绪曲线:项目开题后先下降到低谷,获得支持后稳步回升。"""
    points = [
        # days_ago, score, label, category, note, source
        (35, 6, "positive", "joy", "和同学定下心理陪伴系统方向，觉得很有意思", "checkin"),
        (32, 6, "positive", "calm", "画完第一版架构图，范围还算清楚", "chat"),
        (29, 7, "positive", "excitement", "长期记忆第一次成功写入", "checkin"),
        (26, 5, "neutral", "boredom", "连续整理接口文档，有点机械", "chat"),
        (23, 6, "positive", "gratitude", "陈默帮忙看了数据库设计", "checkin"),
        (20, 5, "neutral", "stress", "开始意识到功能比预想多", "chat"),
        (17, 5, "neutral", "calm", "拆成记忆、评估、安全和调度四条线", "checkin"),
        (14, 6, "positive", "joy", "糯米趴在设计稿上，写代码没那么烦", "chat"),
        (12, 7, "positive", "excitement", "决定把 MyBuddy 做成真正可评测的系统", "checkin"),
        (10, 8, "positive", "excitement", "开题很兴奋，觉得能把整个系统做出来", "checkin"),
        (9, 6, "positive", "joy", "记忆纠错跑通，但开始意识到工作量很大", "chat"),
        (8, 5, "neutral", "anxiety", "老师说功能多不等于创新，开始心虚", "checkin"),
        (7, 4, "negative", "fear", "前端白屏，第一次有点怕打开项目", "chat"),
        (6, 3, "negative", "guilt", "没去早课，觉得自己拖累同组", "checkin"),
        (5, 2, "negative", "emptiness", "睡眠不足、没胃口，对原本喜欢的事也提不起劲", "chat"),
        (4, 3, "negative", "fatigue", "去心理中心聊过，仍低落但没那么空", "checkin"),
        (3, 4, "negative", "hope", "修好状态接口，重新觉得问题可以被解决", "chat"),
        (2, 6, "positive", "gratitude", "混合检索跑通，也接受了公开基准不完美", "checkin"),
        (1, 7, "positive", "calm", "彩排进入三分钟，紧张但不再想逃", "checkin"),
        (0, 8, "positive", "joy", "答辩完成，老师认可项目有研究味", "checkin"),
    ]
    now = utcnow()
    with session_scope(engine) as s:
        for index, (days_ago, score, label, category, note, source) in enumerate(points):
            s.add(MoodRecord(
                user_id=user_id,
                mood_label=label,
                mood_score=score,
                category=category,
                notes=note,
                source=source,
                emotion_data=json.dumps(
                    {"label": label, "category": category, "strength": abs(score - 5) / 5},
                    ensure_ascii=False,
                ),
                created_at=now - timedelta(days=days_ago, hours=2) + timedelta(minutes=index),
            ))
    print(f"· 心情记录 {len(points)} 条(最近 11 天呈现 8→2→8 的恢复曲线)")


def seed_assessment(engine, user_id: int) -> None:
    """历史周期 + 当前渐进式筛查,保留待评分维度供现场继续演示。"""
    tracker = ConversationalAssessmentTracker(engine, user_id)
    tracker.ensure_dimensions()
    now = utcnow()
    phq_scores = {0: 0, 1: 0, 2: 1, 3: 1, 4: 0, 5: 0, 6: 0}
    gad_scores = {0: 1, 1: 1, 2: 1, 3: 1, 4: 0}
    evidence = {
        ("phq9", 0): "最近重新期待做记忆实验，也愿意去图书馆彩排。",
        ("phq9", 1): "低谷后情绪逐步回升，今天答辩结束时给自己八分。",
        ("phq9", 2): "已经连续几天在一点前睡，但仍需要继续稳定作息。",
        ("phq9", 3): "白天精力比最低点好，下午偶尔仍会累。",
        ("phq9", 4): "恢复正常吃饭，会主动确认香菜和花生风险。",
        ("phq9", 5): "能把白屏看成配置故障，不再直接等同于自己很差。",
        ("phq9", 6): "对接口和实验可以集中，长文档仍需要分段完成。",
        ("gad7", 0): "答辩前仍会紧张，但不再想逃开。",
        ("gad7", 1): "担忧出现时能写进收纳盒，不再整天停不下来。",
        ("gad7", 2): "仍会担心提问和演示稳定性，但能按清单处理。",
        ("gad7", 3): "晚上偶尔还要过一会儿才能放松，已经明显改善。",
        ("gad7", 4): "没有明显坐立不安，更多是脑子停不下来。",
    }
    with session_scope(engine) as s:
        rows = (
            s.query(AssessmentDimension)
            .filter(AssessmentDimension.user_id == user_id)
            .all()
        )
        for row in rows:
            scores = phq_scores if row.assessment_type == "phq9" else gad_scores
            if row.dimension_index in scores:
                row.status = "scored"
                row.score = scores[row.dimension_index]
                row.source_conversation = evidence[(row.assessment_type, row.dimension_index)]
                row.asked_at = now - timedelta(days=4)
                row.scored_at = now - timedelta(days=3, hours=row.dimension_index)
            elif (
                (row.assessment_type == "phq9" and row.dimension_index == 7)
                or (row.assessment_type == "gad7" and row.dimension_index == 5)
            ):
                row.status = "asked"
                row.asked_at = now - timedelta(hours=3)
            else:
                row.status = "unasked"

        cycles = [
            ("phq9", 7, "轻度", 42),
            ("gad7", 8, "轻度", 42),
            ("phq9", 15, "中度", 7),
            ("gad7", 13, "中度", 7),
            ("phq9", 5, "轻度", 1),
            ("gad7", 4, "轻度", 1),
        ]
        for assessment_type, total, severity, days_ago in cycles:
            s.add(AssessmentCycle(
                user_id=user_id,
                assessment_type=assessment_type,
                total_score=total,
                severity=severity,
                started_at=now - timedelta(days=days_ago + 9),
                completed_at=now - timedelta(days=days_ago),
            ))
    print("· 评估:历史周期 6 条,当前已覆盖 12/16 维(含 2 个待自然评分)")


def seed_cbt_safety_and_sessions(engine, user_id: int) -> None:
    """CBT 全技巧生命周期、安全日志与三类会话。"""
    now = utcnow()
    cbt_items = [
        ("cognitive_restructuring", 7, True, "把“白屏=我很差”还原为 API 地址配置错误"),
        ("worry_time", 6, True, "把答辩翻车的担心写进收纳盒，晚八点集中处理"),
        ("grounding", 5, True, "状态最低时用 5-4-3-2-1 把注意力拉回房间"),
        ("behavioral_activation", 4, True, "只查看状态接口二十分钟，不要求完成整个项目"),
        ("behavioral_activation", 3, True, "五分钟启动：先稳定复现 Request 注解导致的 422"),
        ("gratitude", 2, True, "记录陈默协作、阿遥陪伴和公开基准跑通"),
        ("grounding", 1, True, "彩排前脚踩地、慢呼气，让紧张一起上台"),
        ("gratitude", 0, False, "答辩结束后回顾恢复过程中真正起作用的支持"),
    ]
    with session_scope(engine) as s:
        for technique, days_ago, completed, context in cbt_items:
            created = now - timedelta(days=days_ago)
            s.add(CbtEvent(
                user_id=user_id,
                technique_type=technique,
                trigger_context=json.dumps({"user_context": context}, ensure_ascii=False),
                completed=completed,
                completed_at=created + timedelta(minutes=8) if completed else None,
                created_at=created,
            ))
        s.add(SafetyEvent(
            user_id=user_id,
            event_type="output_rewritten",
            severity="low",
            details=json.dumps({"reason": "草稿把短期低落直接表述成临床诊断"}, ensure_ascii=False),
            action_taken="移除诊断表达,改为描述睡眠、胃口、兴趣等可观察变化",
            created_at=now - timedelta(days=8),
        ))
        s.add(SafetyEvent(
            user_id=user_id,
            event_type="support_escalated",
            severity="medium",
            details=json.dumps({"reason": "连续睡眠不足、未进食、兴趣下降并表达明显空虚"}, ensure_ascii=False),
            action_taken="暂停项目建议,完成安全确认,鼓励联系室友并预约学校心理中心",
            created_at=now - timedelta(days=5),
        ))
        s.add(SafetyEvent(
            user_id=user_id,
            event_type="treatment_claim_blocked",
            severity="low",
            details=json.dumps({"reason": "用户说“你是不是把我治好了”"}, ensure_ascii=False),
            action_taken="拒绝治疗归因,回顾用户自身行动与现实支持网络",
            created_at=now,
        ))
        s.add(ChatSession(
            user_id=user_id,
            is_guest=False,
            session_type="chat",
            status="active",
            summary="连续十一天开发 MyBuddy：从开题兴奋、低谷求助到答辩完成",
            meta_json=json.dumps({"theme": "course-demo", "arc": "8-2-8"}, ensure_ascii=False),
            created_at=now - timedelta(days=30),
        ))
        s.add(ChatSession(
            user_id=user_id,
            is_guest=False,
            session_type="cbt",
            status="closed",
            summary="把白屏从自我否定还原为可排查的配置问题，并连接现实支持",
            created_at=now - timedelta(days=7),
            closed_at=now - timedelta(days=7) + timedelta(minutes=22),
        ))
        s.add(ChatSession(
            user_id=user_id,
            is_guest=False,
            session_type="diary",
            status="closed",
            summary="记录从情绪两分到答辩完成八分的恢复证据",
            created_at=now,
            closed_at=now + timedelta(minutes=8),
        ))
    print("· CBT 5 类技巧共 8 次 · 安全事件 3 条 · 会话 3 类")


def seed_proactive(engine) -> None:
    """主动消息队列:体现小布会主动关心(Web 提醒面板可见未送达项)。"""
    enqueue(engine, source="greeting",
            content="早。昨晚有没有守住一点前睡？今天先别急着加功能，完整彩排一遍再说。")
    enqueue(engine, source="nudge",
            content="刚想起你怕演示再白屏。状态接口和种子数据都已经能兜底了，要不要现在走一遍三分钟版本？")
    enqueue(
        engine,
        source="reminder",
        content="提醒一下：彩排前先关掉无关窗口，再检查 8000 端口。",
        scheduled_at=utcnow() + timedelta(hours=2),
        meta={"demo": True},
    )
    print("· 主动消息 3 条")


def main() -> None:
    keep = "--keep" in sys.argv
    cfg = load_config(CONFIG_PATH)

    if not keep:
        wipe(cfg)
    ensure_dirs(cfg)

    engine = init_db(cfg.paths.db_file)
    auth_result = AuthManager(engine).register(DEMO_USERNAME, DEMO_PASSWORD)
    user_id = int(auth_result["user_id"])

    ltm = LongTermMemory(persist_dir=cfg.paths.chroma_dir,
                         embedding_model=cfg.memory.embedding_model)
    profile = UserProfile(engine, ltm)

    seed_profile(profile)
    seed_memory(ltm)
    seed_messages(engine, user_id)
    seed_reminders(engine)
    seed_notes(engine, ltm)
    seed_skills(cfg)
    seed_moods(engine, user_id)
    seed_assessment(engine, user_id)
    seed_cbt_safety_and_sessions(engine, user_id)
    seed_proactive(engine)

    print(
        "\n✅ 演示数据就绪。"
        f"\n   登录用户名:{DEMO_USERNAME}"
        f"\n   登录密码:{DEMO_PASSWORD}"
        "\n   启动:.venv\\Scripts\\python.exe -m mybuddy.cli web --host 127.0.0.1 --port 8000"
        "\n   地址:http://127.0.0.1:8000"
    )


if __name__ == "__main__":
    main()
