"""translate 工具。

不接外部翻译服务,直接复用 MyBuddy 已装配的 LLM provider(small_model)。
system prompt 强制"只输出译文",避免 LLM 寒暄把结果污染。
"""

from __future__ import annotations

import logging

from mybuddy.llm import Message, Role

from .context import get_config, get_provider
from .registry import tool

logger = logging.getLogger(__name__)


TRANSLATE_SYSTEM = (
    "你是专业翻译助手。严格遵守:\n"
    "1. 只输出译文,不要解释、不要寒暄、不要加引号。\n"
    "2. 保留原文的格式(换行、列表、代码块)。\n"
    "3. 若原文已是目标语言,原样返回。\n"
)


@tool(
    name="translate",
    description="把一段文本翻译成指定语言(默认英文)。使用 LLM 小模型完成。",
)
async def translate(text: str, target_lang: str = "英文") -> dict:
    """翻译文本。

    参数:
      text: 原文
      target_lang: 目标语言,如 "英文" / "日文" / "Chinese"
    """
    if not text or not text.strip():
        return {"ok": False, "error": "原文为空"}

    provider = get_provider()
    cfg = get_config()

    user_prompt = f"请把下面的文本翻译成{target_lang}:\n\n{text}"
    try:
        resp = await provider.generate(
            messages=[Message(role=Role.USER, content=user_prompt)],
            system=TRANSLATE_SYSTEM,
            temperature=0.2,
            model=cfg.llm.small_model or None,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("translate 失败")
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    translated = (resp.text or "").strip()
    return {
        "ok": True,
        "text": text,
        "target_lang": target_lang,
        "translated": translated,
    }
