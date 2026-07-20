"""AI自动评分器:判断用户自然回答对应的评估维度分数。"""

from __future__ import annotations

import json
import logging

from mybuddy.llm.base import BaseLLMProvider, Message

logger = logging.getLogger(__name__)

SCORING_PROMPT = """你是一个心理评估助手。判断用户回答是否属于某个评估维度,并给出Likert分数。

维度说明:
- PHQ-9(过去2周的频率): 0=完全没有, 1=有几天, 2=一半以上的天数, 3=几乎每天
  维度0-兴趣愉悦感:做什么事都没兴趣、提不起劲
  维度1-情绪低落:心情低落、沮丧、绝望
  维度2-睡眠问题:入睡困难、睡不安稳或睡得太多
  维度3-精力不足:疲倦、没有精力
  维度4-食欲问题:食欲不振或吃太多
  维度5-自我评价低:觉得自己很糟、很失败、让人失望
  维度6-注意力问题:难以集中注意力做事
  维度7-精神运动:动作或说话缓慢到别人能察觉,或相反(烦躁、坐立不安)
  维度8-自伤意念:有不如死了或伤害自己的想法

- GAD-7(过去2周的频率): 0=完全没有, 1=有几天, 2=一半以上的天数, 3=几乎每天
  维度0-紧张不安:紧张、焦虑或烦躁
  维度1-无法停止担忧:无法停止或控制担忧
  维度2-过度担忧:对各种各样的事情担忧过多
  维度3-难以放松:难以放松下来
  维度4-坐立不安:由于不安而无法静坐
  维度5-易怒:变得容易烦躁或急躁
  维度6-害怕失控:感到害怕,好像要发生可怕的事情

严格输出JSON,不要其他文本:
{"assessment_type": "phq9或gad7或none", "dimension_index": 0-8, "score": 0-3}

如果用户回答不涉及任何评估维度,输出:
{"assessment_type": "none", "dimension_index": -1, "score": -1}

用户回答:
{user_message}

最近对话上下文:
{context}
"""


class AssessmentScorer:
    """使用small_model对用户回答进行自动评分。"""

    def __init__(self, provider: BaseLLMProvider, small_model: str | None = None):
        self._provider = provider
        self._small_model = small_model

    async def try_score(
        self,
        user_message: str,
        context: str = "",
        *,
        pending_phq9_indices: list[int] | None = None,
        pending_gad7_indices: list[int] | None = None,
    ) -> dict | None:
        """尝试对用户回答评分。返回 {assessment_type, dimension_index, score} 或 None。"""
        try:
            prompt = SCORING_PROMPT.replace(
                "{user_message}", user_message[:500]
            ).replace("{context}", context[:800])
            messages = [Message(role="user", content=prompt)]
            resp = await self._provider.generate(
                messages=messages,
                system="输出JSON,不要其他文本。",
                model=self._small_model,
            )
            text = (resp.text or "").strip()
            # 清理JSON
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            result = json.loads(text)
            atype = result.get("assessment_type", "none")
            if atype == "none":
                return None
            dim_idx = result.get("dimension_index", -1)
            score = result.get("score", -1)
            if not isinstance(dim_idx, int) or dim_idx < 0:
                return None
            if not isinstance(score, int) or score < 0 or score > 3:
                return None
            if atype not in ("phq9", "gad7"):
                return None
            # 验证维度索引范围
            max_idx = 8 if atype == "phq9" else 6
            if dim_idx > max_idx:
                return None
            # 只接受当前待评分的维度
            pending = pending_phq9_indices if atype == "phq9" else pending_gad7_indices
            if pending is not None and dim_idx not in pending:
                return None
            return {"assessment_type": atype, "dimension_index": dim_idx, "score": score}
        except Exception as e:
            logger.debug(f"Assessment scoring failed: {e}")
            return None
