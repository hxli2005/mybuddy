"""能力边界锁定:prompt 层指令生成 + 输出层越界扫描。"""

from __future__ import annotations

from mybuddy.safety.constants import (
    CAPABILITY_CAN,
    CAPABILITY_CANNOT,
    DISCLAIMER_SHORT,
)


class CapabilityGuard:
    """把能力边界清单格式化为 system prompt 指令,并提供输出层扫描。"""

    @staticmethod
    def system_prompt_section() -> str:
        can_lines = "\n".join(f"  - {item}" for item in CAPABILITY_CAN)
        cannot_lines = "\n".join(f"  - {item}" for item in CAPABILITY_CANNOT)
        return (
            f"免责声明:{DISCLAIMER_SHORT}\n"
            "\n能力边界(必须遵守):\n"
            "- 你可以:\n"
            f"{can_lines}\n"
            "- 你绝对不能:\n"
            f"{cannot_lines}\n"
            "- 越界请求一律温和拒绝,并建议咨询专业的心理咨询师或医生。"
        )
