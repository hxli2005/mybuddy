"""用户画像:核心字段(hard facts)。

借鉴 Hermes Agent / Honcho 的用户建模,但只保留**明确事实**这一层:
姓名、生日、偏好、禁忌等,以 KV 形式存 SQLite。

  profile = UserProfile(engine)
  profile.set_field("名字", "小明")
  name = profile.get_field("名字")

早期版本还有一层"动态命题(soft claims)":对用户的推测性命题,带置信度、证据链、
冲突消解与晋升流程。实测中它几乎不触发(抽取被刻意压制、置信度门槛高),反馈回路也
未真正接通,养着只是空转。已整层移除——长期记忆只建立在用户**明确说过**的内容上,
不再做后台推测建模。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from mybuddy.memory.long_term import LongTermMemory

from mybuddy._time import utcnow
from mybuddy.storage import ProfileField, session_scope


class UserProfile:
    """用户画像:核心字段(KV 形式存 SQLite)。"""

    def __init__(self, engine: Engine, ltm: LongTermMemory | None = None) -> None:
        self._engine = engine
        # ltm 仅为向后兼容保留(画像现在只有 SQLite 核心字段,不再用到长期记忆)。
        self._ltm = ltm

    def set_field(self, key: str, value: str) -> None:
        """写入或更新一个核心字段。"""
        with session_scope(self._engine) as s:
            field = s.query(ProfileField).filter_by(key=key).one_or_none()
            if field is None:
                field = ProfileField(key=key, value=value)
                s.add(field)
            else:
                field.value = value
                field.updated_at = utcnow()

    def get_field(self, key: str) -> str | None:
        """读取单个字段值。"""
        with session_scope(self._engine) as s:
            field = s.query(ProfileField).filter_by(key=key).one_or_none()
            return field.value if field else None

    def get_all_fields(self) -> dict[str, str]:
        """返回所有核心字段的 KV 字典。"""
        with session_scope(self._engine) as s:
            return {f.key: f.value for f in s.query(ProfileField).all()}

    def delete_field(self, key: str) -> bool:
        """删除字段,返回是否删除成功。"""
        with session_scope(self._engine) as s:
            field = s.query(ProfileField).filter_by(key=key).one_or_none()
            if field is None:
                return False
            s.delete(field)
            return True
