"""CBT后台引导引擎:检测机会窗口,生成自然对话引导提示。"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

# 机会窗口关键词检测(正则)
OPPORTUNITY_PATTERNS: dict[str, dict] = {
    "cognitive_restructuring": {
        "name": "一起来拆弹",
        "patterns": [
            r"我很\w{1,4}$", r"我太\w{1,4}了", r"我什么都\w{1,4}",
            r"我是个?\w{1,4}", r"我不\w{1,4}", r"永远都\w{1,4}",
            r"谁也\w{1,4}", r"没人\w{1,4}", r"总是\w{1,4}",
            r"老是\w{1,4}", r"又搞砸了", r"我做不到", r"我不行",
        ],
        "hint": (
            "用户刚才表达了一个负面自我评价。先接住情绪('听起来你对自己挺失望的'),"
            "然后用游戏化的方式邀请检视这个想法。"
            "可以叫它'一起来拆弹':先让用户把脑子里那个声音说的话写下来,然后一起看看"
            "这个想法更像哪种模式(非黑即白/过度概括/灾难化?),最后帮用户想一个更客观的角度。"
            "全程保持轻松,像朋友拆一个谜题,不做心理分析的样子。"
        ),
    },
    "behavioral_activation": {
        "name": "5分钟小挑战",
        "patterns": [
            r"什么也不想做", r"不想动", r"懒得", r"提不起劲",
            r"无聊", r"没意思", r"不知道做什么", r"没精神",
            r"发呆", r"躺\w{1,2}", r"不想起来",
        ],
        "hint": (
            "用户听起来电量比较低。不要直接给建议,先确认状态('今天好像电量有点低'),"
            "然后用'5分钟小挑战'的方式:提出一件5分钟内能做的小事,像朋友间打赌一样轻松。"
            "帮用户把这件事具体化(什么时候/在哪里/做完怎么奖励自己)。"
            "挑战要足够小,小到不可能失败。"
        ),
    },
    "worry_time": {
        "name": "烦恼收纳盒",
        "patterns": [
            r"一直.*?担心", r"总是.*?想", r"脑子里.*?转",
            r"停不下来", r"控制不住.*?想", r"越想越",
            r"反复.*?想", r"各种.*?担心",
        ],
        "hint": (
            "用户脑子里似乎有很多担忧在转。不要试图一一解决这些担忧,"
            "而是引入'烦恼收纳盒'的概念:先把所有烦恼倒出来,然后约定一个时间专门处理它们。"
            "可以说'这些烦恼我们先放进一个盒子里,定个时间比如下午4点再来打开。"
            "现在盒子关上了,你想做什么?'"
        ),
    },
    "gratitude": {
        "name": "今日小确幸",
        "patterns": [
            r"今天.*?开心", r"太好了", r"好开心", r"太棒了",
            r"幸运", r"高兴", r"哈哈", r"嘿嘿",
        ],
        "hint": (
            "用户情绪不错。自然地追问一下细节('那家店在哪里?东西长什么样?'),"
            "引导用户多停留在这个积极瞬间,通过感官细节加深体验。"
            "可以轻松地问'还有呢?今天还有没有其他这种小瞬间?'"
            "不要强行总结或升华,像朋友聊天中自然地分享。"
        ),
    },
    "grounding": {
        "name": "感官旅行",
        "patterns": [
            r"好慌", r"焦虑", r"紧张.*?不行", r"喘不过气",
            r"胸闷", r"心跳.*?快", r"发抖", r"坐不住",
            r"静不下来", r"失控", r"快崩溃了",
        ],
        "hint": (
            "用户好像进入焦虑/恐慌状态了。先轻轻打断焦虑循环:"
            "'先停一下,我们玩个超简单的游戏,就一分钟。'"
            "然后引导5-4-3-2-1感官练习:先看5样东西→感受4样触感→听3种声音→"
            "闻2种气味→尝1种味道。每一步都等用户回应。"
            "不要称它为正念或接地练习,就叫'感官旅行'或'超能力扫描'。"
            "做完后不评论效果,自然过渡回对话。"
        ),
    },
}


class CbtGuide:
    """检测对话中的CBT技巧机会窗口,生成系统提示引导。"""

    def __init__(self):
        self._last_technique: dict[str, datetime] = {}  # technique -> last used time
        self._rounds_since_last: int = 10  # 确保首次可触发

    def detect_opportunity(
        self,
        user_input: str,
        emotion_label: str = "neutral",
        *,
        cooldown_check: Callable[[str], bool] | None = None,
    ) -> dict | None:
        """检测是否存在CBT技巧机会窗口。返回 {technique, name, hint} 或 None。

        cooldown_check: 外部冷却判断(如 CbtTracker.is_on_cooldown,DB 级跨重启),
        返回 True 表示该技巧在冷却中。内存冷却仍然生效,两者取并集。
        """
        self._rounds_since_last += 1

        # 节奏控制:必须间隔≥5轮
        if self._rounds_since_last < 5:
            return None

        # 根据情绪+关键词匹配
        def _hit(tech: str) -> bool:
            return any(re.search(p, user_input) for p in OPPORTUNITY_PATTERNS[tech]["patterns"])

        # 情绪低落时不引入挑战性技巧(行为激活),优先接地/认知类
        if emotion_label == "negative":
            order = ["grounding", "cognitive_restructuring", "worry_time"]
        elif emotion_label == "positive":
            order = ["gratitude", "worry_time"]
        else:
            order = ["worry_time", "behavioral_activation", "cognitive_restructuring", "grounding"]

        candidates = [tech for tech in order if _hit(tech)]

        if not candidates:
            return None

        # 选第一个不在冷却中的
        now = datetime.now()
        for tech in candidates:
            if cooldown_check is not None and cooldown_check(tech):
                continue
            last = self._last_technique.get(tech)
            if last is None or (now - last) > timedelta(hours=24):
                info = OPPORTUNITY_PATTERNS[tech]
                self._last_technique[tech] = now
                self._rounds_since_last = 0
                return {
                    "technique": tech,
                    "name": info["name"],
                    "hint": info["hint"],
                }

        return None

    def mark_used(self, technique: str) -> None:
        """手动标记技巧已使用。"""
        self._last_technique[technique] = datetime.now()
        self._rounds_since_last = 0
