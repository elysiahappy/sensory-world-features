"""
RealGroupSceneAdapter + RealChatterAdapter。

群聊勘测事实（core/group_scene，GroupSceneSystem.tick L241）：
  - 群聊由系统按 NPC **实时位置自动聚类**形成，每群 ≤ 5 人，每 tick 全局 ≤ 3 条台词；
  - **没有**"指定参与者+话题"的定向开群 API。
因此本适配器**不调用任何开群方法**，而是：
  1. 把事件参与者**移动到分片地点**（显式移动命令，见 schedule 适配注入的回调）；
  2. 主项目下一次 GroupSceneSystem.tick 即会把同地点的 NPC 自动聚成小群；
  3. 用"波浪状态机"控制节奏：每片 ≤5 人、同时分片群 ≤6、每 2~3 游戏分钟放一批。

闲聊 chatter 勘测事实：**无公开强制触发/话题注入 API**，话题偏置只能靠记忆正文
（零侵入）。故 RealChatterAdapter 通过给相关 NPC 写入"含事件名/话题"的高信度
记忆，让其大脑在下次私语/群聊时自然召回；同时可叠加情绪 record_proximity。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..protocols import GroupSceneProtocol
from .constants import (
    WAVE_GROUP_MAX_SIZE,
    WAVE_INTERVAL_MINUTES_MIN,
    WAVE_MAX_ACTIVE_GROUPS,
)

logger = logging.getLogger(__name__)

# 移动命令类型：async def move(npc_id, location_id, reason="") -> None
MoveFn = Callable[[str, str, str], Awaitable[None]]


@dataclass
class WaveSlice:
    """一个分片群：地点 + 该地点的参与者。"""

    location: str
    participant_ids: list[str]
    topic: str = ""
    scene_id: str = ""
    released_at_minutes: int = -1          # 何时被波浪放出（移动到地点）
    released: bool = False


@dataclass
class WavePlan:
    """一次事件的完整波浪分片计划。"""

    event_ref: str
    event_name: str
    slices: list[WaveSlice] = field(default_factory=list)
    cursor: int = 0                        # 下一个待放出的分片下标
    finished: bool = False
    last_batch_minutes: int = -1           # 上一批放出的游戏分钟（节拍控制）


class RealGroupSceneAdapter:
    """
    用"移动调度成群"模拟定向 group_scene，并用波浪状态机限峰。

    :param move_fn: 显式移动命令 async (npc_id, location_id, reason)。
        ⚠️ 主项目移动 API 名称需集成方在启动时绑定（勘测未给出函数名）。
    :param memory_adapter: 可选 RealMemoryAdapter，用于把话题写进参与者记忆正文。
    :param group_max_size: 单群人数上限（主项目自动聚类每群≤5）。
    :param max_active_groups: 同时活跃分片群上限（波浪窗口宽度，默认 6）。
    :param wave_interval_minutes: 波浪节拍（游戏分钟），默认 2。
    """

    def __init__(
        self,
        move_fn: MoveFn | None = None,
        memory_adapter: object | None = None,
        group_max_size: int = WAVE_GROUP_MAX_SIZE,
        max_active_groups: int = WAVE_MAX_ACTIVE_GROUPS,
        wave_interval_minutes: int = WAVE_INTERVAL_MINUTES_MIN,
    ) -> None:
        self._move = move_fn
        self._memory = memory_adapter
        self._group_max = group_max_size
        self._max_active = max_active_groups
        self._interval = wave_interval_minutes
        self._plans: dict[str, WavePlan] = {}

    # ---------- GroupSceneProtocol 兼容入口 ----------

    async def start_scene(
        self,
        scene_id: str,
        location: str,
        participant_ids: list[str],
        topic: str = "",
        context: dict[str, Any] | None = None,
    ) -> str:
        """
        功能模块按协议调用"开群"。适配层把它转成"分片 + 波浪移动"计划。

        - participant_ids 超过单群上限会被自动拆成多个分片群（≤5 人/片），
          首片在主 location，其余片在 context['extra_locations'] 轮取；
        - 返回 plan 运行 id（场景 id）。真正成群发生在后续 advance_wave()。
        """
        ctx = context or {}
        event_ref = str(ctx.get("event_ref") or scene_id)
        event_name = str(ctx.get("event_name") or topic or "城市活动")
        extra_locations: list[str] = list(ctx.get("extra_locations") or [])

        chunks = self._chunk_participants(participant_ids)
        slices: list[WaveSlice] = []
        for idx, chunk in enumerate(chunks):
            loc = location if idx == 0 else (
                extra_locations[idx - 1] if idx - 1 < len(extra_locations) else location
            )
            slices.append(
                WaveSlice(
                    location=loc,
                    participant_ids=chunk,
                    topic=topic,
                    scene_id=f"{scene_id}_s{idx}",
                )
            )
        plan = WavePlan(event_ref=event_ref, event_name=event_name, slices=slices)
        self._plans[event_ref] = plan
        # 立即放出第一批（不超过窗口宽度）
        await self._release_next_batches(self._current_minutes(ctx), plan)
        logger.info(
            "事件 %s 建立波浪计划：%d 片，参与者 %d 人",
            event_ref, len(slices), len(participant_ids),
        )
        return event_ref

    async def end_scene(self, scene_run_id: str) -> dict[str, Any]:
        """结束：标记计划完成，返回摘要（真实群聊摘要由主项目产出）。"""
        plan = self._plans.pop(scene_run_id, None)
        released = sum(1 for s in plan.slices if s.released) if plan else 0
        return {
            "scene_run_id": scene_run_id,
            "status": "ended",
            "slices_total": len(plan.slices) if plan else 0,
            "slices_released": released,
            "summary": f"{plan.event_name if plan else ''} 的分片群已散去",
        }

    async def is_scene_active(self, scene_run_id: str) -> bool:
        plan = self._plans.get(scene_run_id)
        return bool(plan and not plan.finished)

    # ---------- 波浪推进（由节流后的 tick 调用） ----------

    async def advance_wave(self, current_game_minutes: int) -> list[str]:
        """
        推进所有未完成计划的波浪。按节拍放出下一批分片。

        :return: 本次新放出的分片 scene_id 列表。
        """
        newly: list[str] = []
        for plan in list(self._plans.values()):
            if plan.finished:
                continue
            released = await self._release_next_batches(current_game_minutes, plan)
            newly.extend(released)
            if plan.cursor >= len(plan.slices):
                plan.finished = True
        return newly

    def pending_plan_count(self) -> int:
        return sum(1 for p in self._plans.values() if not p.finished)

    # ---------- 内部 ----------

    def _chunk_participants(self, participant_ids: list[str]) -> list[list[str]]:
        """按单群 ≤ group_max 人拆分分片。"""
        n = max(1, self._group_max)
        return [participant_ids[i:i + n] for i in range(0, len(participant_ids), n)]

    @staticmethod
    def _current_minutes(ctx: dict[str, Any]) -> int:
        try:
            return int(ctx.get("current_game_minutes", 0))
        except (TypeError, ValueError):
            return 0

    def _active_count(self, plan: WavePlan, now_minutes: int) -> int:
        """
        当前窗口内已放出且仍在"活跃窗口"内的分片数。

        一个分片放出后，经过 wave_interval 即视为窗口让出，可放入下一批。
        """
        count = 0
        for s in plan.slices:
            if s.released and now_minutes - s.released_at_minutes < self._interval:
                count += 1
        return count

    async def _release_next_batches(self, now_minutes: int, plan: WavePlan) -> list[str]:
        """
        按"批"波浪放出：每批最多 max_active_groups 片，相邻批间隔 wave_interval 分钟。

        - 首批（last_batch_minutes<0）立即放满一个窗口；
        - 之后只有当 now - last_batch >= interval 才放下一批（由 advance_wave 驱动）。
        """
        released_ids: list[str] = []
        first_batch = plan.last_batch_minutes < 0
        if not first_batch and now_minutes - plan.last_batch_minutes < self._interval:
            return released_ids  # 节拍未到
        batch = 0
        while plan.cursor < len(plan.slices) and batch < self._max_active:
            sl = plan.slices[plan.cursor]
            await self._release_slice(plan, sl, now_minutes)
            released_ids.append(sl.scene_id)
            plan.cursor += 1
            batch += 1
        if released_ids:
            plan.last_batch_minutes = now_minutes
        return released_ids

    async def _release_slice(self, plan: WavePlan, sl: WaveSlice, now_minutes: int) -> None:
        """把一个分片的参与者移动到分片地点，并注入话题记忆。"""
        for npc_id in sl.participant_ids:
            await self._move_npc(npc_id, sl.location, f"参加{plan.event_name}")
        # 话题走记忆正文（零侵入）：给在场者写含事件名+人名的记忆
        if self._memory is not None and hasattr(self._memory, "write_shared_memory"):
            try:
                for npc_id in sl.participant_ids:
                    self._memory.write_shared_memory(
                        npc_id,
                        event_name=plan.event_name,
                        location=sl.location,
                        present_npc_ids=sl.participant_ids,
                        kind="event",
                        importance=0.7,
                    )
            except Exception:  # noqa: BLE001
                logger.warning("分片话题记忆注入失败 event=%s", plan.event_ref, exc_info=True)
        sl.released = True
        sl.released_at_minutes = now_minutes

    async def _move_npc(self, npc_id: str, location: str, reason: str) -> None:
        """执行显式移动命令；未绑定移动函数则告警跳过（事件聚集将不生效）。"""
        if self._move is None:
            logger.warning("未绑定移动命令，无法聚集 NPC=%s 到 %s", npc_id, location)
            return
        try:
            result = self._move(npc_id, location, reason)
            if hasattr(result, "__await__"):
                await result
        except Exception:  # noqa: BLE001
            logger.warning("移动 NPC 失败 npc=%s loc=%s", npc_id, location, exc_info=True)


class RealChatterAdapter:
    """
    chatter 话题注入：零侵入，靠记忆正文 + 相处记录。

    主项目无强制私语/话题注入 API，故：
      - trigger_topic：给 NPC 写一条含话题的高信度记忆，大脑下次私语自然召回；
      - trigger_chatter：给两人各写话题记忆 + record_proximity 增进关系，
        让其自然发展出私语。
    """

    def __init__(self, memory_adapter: object | None = None, emotion_adapter: object | None = None) -> None:
        self._memory = memory_adapter
        self._emotion = emotion_adapter
        # 记录已触发话题，便于测试断言
        self.triggered_topics: list[dict[str, Any]] = []

    async def trigger_topic(
        self,
        npc_id: str,
        topic: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """为单个 NPC 注入话题（写进记忆正文，零侵入）。"""
        self.triggered_topics.append({"npc_id": npc_id, "topic": topic, "context": context or {}})
        if self._memory is not None and hasattr(self._memory, "append_high_confidence"):
            try:
                text = f"我心里一直惦记着一件事：{topic} 想找身边的人聊聊。"
                self._memory.append_high_confidence(
                    npc_id, text, importance=0.75,
                    tags=["topic", "chatter"],
                    metadata={"topic": topic, **(context or {})},
                )
            except Exception:  # noqa: BLE001
                logger.warning("话题记忆写入失败 npc=%s", npc_id, exc_info=True)

    async def trigger_chatter(
        self,
        npc_a: str,
        npc_b: str,
        topic: str = "",
        boost_probability: float = 0.0,  # noqa: ARG002 - 主项目无概率提升入口，忽略
    ) -> None:
        """引导两人私语：写话题记忆 + 记录相处。"""
        if topic:
            await self.trigger_topic(npc_a, topic, {"with": npc_b})
            await self.trigger_topic(npc_b, topic, {"with": npc_a})
        if self._emotion is not None and hasattr(self._emotion, "record_proximity"):
            try:
                await self._emotion.record_proximity(npc_a, npc_b, 1.0)
            except Exception:  # noqa: BLE001
                logger.warning("相处记录失败 %s-%s", npc_a, npc_b, exc_info=True)
