"""演示数据种子脚本:清空运行数据,灌入一套连贯的 Web 演示数据。

用法:
    uv run python scripts/seed_demo.py            # 清空并重新灌入
    uv run python scripts/seed_demo.py --keep     # 不清空,只追加(一般不用)

演示人设:用户「李昊翔」(在读研究生,做 AI 方向),AI 小伙伴「小布」。
覆盖 Web 前端全部面板:对话历史 / 画像(记忆) / 长期记忆档案 / 提醒 / 笔记 /
技能 / 主动消息。所有写入都走应用自身的存储与记忆 API,不直接拼 SQL。
"""

from __future__ import annotations

import shutil
import sys
from datetime import timedelta
from pathlib import Path

# 允许 `python scripts/seed_demo.py` 直接运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mybuddy._time import utcnow
from mybuddy.config import ensure_dirs, load_config
from mybuddy.learning.skills import SkillRegistry
from mybuddy.memory import LongTermMemory, UserProfile
from mybuddy.storage.db import init_db
from mybuddy.storage.messages import append_message
from mybuddy.storage.models import Reminder
from mybuddy.storage.db import session_scope
from mybuddy.storage.queue import enqueue
from mybuddy.storage.users import ensure_local_user, set_user_status

CONFIG_PATH = "config.yaml"
SESSION = "web-demo"


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
        "姓名": "李昊翔",
        "昵称": "昊翔",
        "身份": "在读研究生 · AI 方向,正在赶毕业论文",
        "生日": "11-05",
        "咖啡偏好": "手冲,浅烘,最爱耶加雪菲",
        "宠物": "一只叫团子的橘猫",
        "过敏": "海鲜过敏(重要,别推荐海鲜)",
        "作息": "习惯熬夜写代码,导师希望早点睡",
        "导师": "张老师,每周三上午组会",
        "近期目标": "6 月底把论文投出去",
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
        ("李昊翔对海鲜过敏,任何聚餐/点单都要避开海鲜。", "profile", 0.95,
         {"tags": "健康,边界", "keywords": "海鲜,过敏"}),
        ("偏爱手冲咖啡,浅烘,最爱耶加雪菲;不喝速溶。", "preference", 0.8,
         {"tags": "偏好,咖啡"}),
        ("养了一只叫团子的橘猫,上周生了点小病去过医院,现在好了。", "profile", 0.7,
         {"tags": "生活,团子", "keywords": "团子,猫,宠物"}),
        ("上周三组会被张老师追问 baseline 复现对不上的事,压力比较大。", "memory", 0.75,
         {"tags": "科研,情绪", "keywords": "组会,baseline"}),
        ("和小布的相处约定:累的时候先让他靠一会儿、把水放手边,再谈下一步,不要一上来讲道理。", "shared_moment", 0.9,
         {"tags": "关系,边界"}),
        ("深夜写代码效率最高,但答应过要尽量在两点前睡。", "memory", 0.6,
         {"tags": "作息"}),
        ("不喜欢被一上来就分析情绪,喜欢先被自然地接住。", "preference", 0.85,
         {"tags": "沟通偏好"}),
        ("目标是 6 月底前把论文投出去,最近在补 baseline 对齐实验和重画 figure3。", "open_thread", 0.8,
         {"tags": "科研,目标", "keywords": "论文,目标"}),
    ]
    for content, mem_type, importance, extra in cards:
        ltm.add(
            content,
            mem_type=mem_type,
            session_id=SESSION,
            extra_meta={"importance": importance, "source": "demo_seed", **extra},
        )
    print(f"· 长期记忆档案 {len(cards)} 张")


def seed_messages(engine) -> None:
    """一段此前的对话历史,体现小布的人设(短句、先接住、再给一小步)。"""
    convo = [
        ("user", "在吗"),
        ("assistant", "在的,刚把桌上便签收了一半。怎么啦?"),
        ("user", "今天组会又被问住了,有点烦"),
        ("assistant", "先别急着自责。组会被问住太正常了,你昨天还熬到两点改实验呢。"
                      "先喝口水——卡住的那个点发我,我们只看下一个最小的一步。"),
        ("user", "嗯…就是 baseline 复现对不上"),
        ("assistant", "那多半不是你的问题。baseline 对不齐,九成出在随机种子和数据划分上。"
                      "别先推翻自己,我陪你把它的配置一行行对一遍。"),
        ("user", "好 谢谢你"),
        ("assistant", "跟我还客气。对了,团子今天乖不乖?"),
    ]
    for role, content in convo:
        append_message(engine, session_id=SESSION, role=role, content=content)
    print(f"· 对话历史 {len(convo)} 条")


def seed_reminders(engine) -> None:
    """即将到期的提醒(pending)。"""
    base = utcnow()
    items = [
        ("周三上午组会,记得带实验结果", base + timedelta(days=1, hours=2)),
        ("给张老师发本周周报", base + timedelta(days=2)),
        ("买耶加雪菲咖啡豆,快喝完了", base + timedelta(days=3)),
        ("今晚别太晚,争取两点前睡", base + timedelta(hours=10)),
    ]
    with session_scope(engine) as s:
        for content, when in items:
            s.add(Reminder(content=content, trigger_at=when, status="pending"))
    print(f"· 提醒 {len(items)} 条")


def seed_notes(engine, ltm: LongTermMemory) -> None:
    """笔记(SQLite 为主存,同时写入长期记忆档案,mem_type=note)。"""
    from mybuddy.storage.models import Note
    import json

    notes = [
        ("论文 TODO", "1. 补 baseline 对齐实验  2. 重画 figure3  3. related work 再补两篇",
         ["论文", "工作"]),
        ("团子", "团子病好了,医生说别喂太多,定时定量。", ["生活"]),
        ("灵感", "把记忆置信度的衰减做成一条可视化曲线,组会上正好能讲清楚记忆治理。",
         ["灵感", "科研"]),
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
        ("深夜情绪安抚", ["烦", "累", "丧", "撑不住"],
         ["先用一个生活动作接住(递水/让他靠一会儿)",
          "不分析情绪,先认同处境",
          "把问题缩小成下一个最小的一步"],
         6, 0, False),
        ("baseline 复现排查", ["baseline", "复现", "对不上", "跑不通"],
         ["先排随机种子与数据划分",
          "逐行比对配置与超参",
          "锁定差异后再改一处验证一处"],
         4, 1, False),
        ("周报草拟", ["周报", "汇报", "进展"],
         ["按 本周做了什么 / 卡在哪 / 下周计划 三段",
          "每段只留最关键的两三条",
          "结尾给导师一个明确的待确认点"],
         2, 0, False),
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


def seed_proactive(engine) -> None:
    """主动消息队列:体现小布会主动关心(Web 提醒面板可见未送达项)。"""
    enqueue(engine, source="greeting",
            content="早上好呀。昨晚几点睡的?今天组会别空着肚子去,我猜你又想跳过早饭了。")
    enqueue(engine, source="nudge",
            content="刚想起你昨天说有点丧。现在好点没?要是还堵着,就把那件最小的事先丢给我。")
    print("· 主动消息 2 条")


def main() -> None:
    keep = "--keep" in sys.argv
    cfg = load_config(CONFIG_PATH)

    if not keep:
        wipe(cfg)
    ensure_dirs(cfg)

    engine = init_db(cfg.paths.db_file)
    user = ensure_local_user(engine)
    set_user_status(engine, user.id, "active")

    ltm = LongTermMemory(persist_dir=cfg.paths.chroma_dir,
                         embedding_model=cfg.memory.embedding_model)
    profile = UserProfile(engine, ltm)

    seed_profile(profile)
    seed_memory(ltm)
    seed_messages(engine)
    seed_reminders(engine)
    seed_notes(engine, ltm)
    seed_skills(cfg)
    seed_proactive(engine)

    print("\n✅ 演示数据就绪。启动:uv run mybuddy web  →  http://127.0.0.1:8000")


if __name__ == "__main__":
    main()
