"""
RealEmotionAdapter —— 主项目 npc_emotion（全同步）→ NPCEmotionProtocol 的适配。

勘测事实（core/npc_emotion.py，全部同步）：
  - get_emotion(npc_id)                 L373
  - update_emotion(npc_id, emotion, intensity, reason)  L455
  - record_proximity(npc_a, npc_b, amount)  L525
功能模块协议是 async，这里做"同步方法的 async 薄 wrapper"（用 asyncio.to_thread
把同步调用移出事件循环线程，避免阻塞主循环；若调用本身极轻也可直接 await 结果）。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any

from ..protocols import NPCEmotionProtocol

logger = logging.getLogger(__name__)


class RealEmotionAdapter:
    """npc_emotion → NPCEmotionProtocol（同步方法异步包装）。"""

    def __init__(self, emotion_system: object) -> None:
        self._emo = emotion_system

    async def inject_emotion(
        self,
        npc_id: str,
        emotion: str,
        intensity: float = 0.5,
        duration_seconds: float = 300.0,  # noqa: ARG002 - 主项目 update_emotion 可能不支持时长
        reason: str = "",
    ) -> None:
        """
        注入情绪 → 映射到主项目 update_emotion。

        ⚠️ 主项目 update_emotion 无 duration 形参（勘测 L455），duration 在此忽略；
           情绪时长由主项目情绪系统自身衰减逻辑管理。
        """
        await self._run_sync(
            self._emo.update_emotion,  # type: ignore[attr-defined]
            npc_id,
            emotion,
            intensity,
            reason,
        )

    async def get_emotion(self, npc_id: str) -> dict[str, float]:
        """获取情绪状态 → get_emotion（L373）。"""
        result = await self._run_sync(self._emo.get_emotion, npc_id)  # type: ignore[attr-defined]
        return result if isinstance(result, dict) else {}

    async def record_proximity(self, npc_a: str, npc_b: str, amount: float = 1.0) -> None:
        """记录两 NPC 相处 → record_proximity（L525）。供私语/聚会增进关系。"""
        await self._run_sync(
            self._emo.record_proximity,  # type: ignore[attr-defined]
            npc_a,
            npc_b,
            amount,
        )

    # ---------- 内部 ----------

    async def _run_sync(self, fn, *args) -> Any:
        """
        调用一个主项目同步方法。

        - 若 fn 其实是协程函数（主项目未来异步化），直接 await；
        - 否则放到线程池执行，避免阻塞事件循环。
        任何异常都吞掉并告警（情绪注入失败不能影响主流程）。
        """
        try:
            if inspect.iscoroutinefunction(fn):
                return await fn(*args)
            return await asyncio.to_thread(fn, *args)
        except Exception as err:  # noqa: BLE001
            logger.warning("情绪系统调用失败 fn=%s args=%s: %s", getattr(fn, "__name__", "?"), args[:2], err)
            return None
