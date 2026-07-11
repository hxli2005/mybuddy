"""身体阈值穿越后的低频小布低语。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mybuddy.storage import enqueue, record_vpet_event

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from mybuddy.body.physio import PhysioEngine, PhysioSnapshot


# 这些是引擎侧审定台词,不在壳中;都遵守不索取、不愧疚化。
_TEXT = {
    "hunger": "肚子有点空了……不急,我先趴会儿。",
    "energy": "有点困了。我安静眯一下,你忙你的。",
    "mood": "今天想安静一点。靠近待着就好。",
}


def enqueue_crossed_murmurs(
    engine: Engine,
    physio: PhysioEngine,
    snapshot: PhysioSnapshot,
    *,
    server_flags: dict[str, Any],
    day_index: int,
) -> list[str]:
    """认领并入队本次阈值穿越;返回已入队 kind。"""
    approved = physio.claim_murmurs(snapshot.crossed)
    for kind in approved:
        enqueue(
            engine,
            source="body_murmur",
            content=_TEXT[kind],
            meta={"kind": kind, "physio": snapshot.to_dict()},
        )
        record_vpet_event(
            engine,
            event="body_murmur",
            context={"kind": kind, "physio": snapshot.to_dict()},
            server_flags=server_flags,
            day_index=day_index,
        )
    return approved
