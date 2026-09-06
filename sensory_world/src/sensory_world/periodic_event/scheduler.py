"""
周期事件系统 —— 主调度器

PeriodicEventSystem 是阶段一的核心入口模块，负责：
  1. 加载事件配置
  2. 在每个游戏 tick 检查是否有事件需要触发
  3. 管理事件生命周期（PRE → ACTIVE → POST）
  4. 协调并发护栏、事件执行器、逃课事件链等子模块

集成方式：
    主项目在主循环中调用 await system.tick(game_time) 即可。
    每次 tick 系统会自动判断是否需要触发/推进/结束事件。

开关与降级：
    - 系统整体可通过 enabled 配置开关
    - LLM 不可用时所有文本生成自动降级为模板
    - 并发护栏防止推理槽位被打爆
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sensory_world.llm_client import FallbackLLMClient, SafeLLMClient
from sensory_world.periodic_event.concurrency_guard import ConcurrencyGuard
from sensory_world.periodic_event.event_config import (
    load_all_event_configs,
    load_event_configs_from_dicts,
)
from sensory_world.periodic_event.event_runner import EventRunner, TruantEventChain
from sensory_world.periodic_event.models import (
    EventConfig,
    EventInstance,
    EventPhase,
    SliceGroup,
    TriggerType,
)
from sensory_world.protocols import (
    ChatterProtocol,
    GameClockProtocol,
    GroupSceneProtocol,
    LLMClientProtocol,
    NPCMemoryStoreProtocol,
    NPCEmotionProtocol,
    ScheduleBookProtocol,
    WorldDiaryProtocol,
)

logger = logging.getLogger(__name__)


class PeriodicEventSystem:
    """
    周期事件系统 —— 城市生活的节拍器。

    用法：
        system = PeriodicEventSystem(
            llm=my_llm_client,
            diary=world_diary,
            memory=npc_memory_store,
            emotion=npc_emotion,
            group_scene=group_scene_manager,
            chatter=chatter_manager,
            schedule=schedule_book,
            clock=game_clock,
        )
        await system.initialize()

        # 在主循环中
        while True:
            game_time = await game_clock.now()
            await system.tick(game_time)
            await asyncio.sleep(tick_interval)
    """

    def __init__(
        self,
        llm: LLMClientProtocol,
        diary: WorldDiaryProtocol,
        memory: NPCMemoryStoreProtocol,
        emotion: NPCEmotionProtocol,
        group_scene: GroupSceneProtocol,
        chatter: ChatterProtocol,
        schedule: ScheduleBookProtocol,
        clock: GameClockProtocol,
        # 配置项
        config_dir: str | Path | None = None,
        max_concurrent_scenes: int = 4,
        acquire_timeout: float = 30.0,
        enabled: bool = True,
    ):
        """
        初始化周期事件系统。

        :param llm: LLM 客户端（将被 SafeLLMClient 包装）
        :param diary: 世界日记
        :param memory: NPC 记忆库
        :param emotion: NPC 情绪系统
        :param group_scene: 群聊场景管理
        :param chatter: NPC 私语
        :param schedule: NPC 日程
        :param clock: 游戏时钟
        :param config_dir: 事件配置目录（默认使用包内 configs）
        :param max_concurrent_scenes: 最大并发群聊场景数
        :param acquire_timeout: 并发槽获取超时（秒）
        :param enabled: 系统总开关
        """
        # 包装 LLM 客户端为安全版本
        self._safe_llm = SafeLLMClient(primary=llm, fallback=FallbackLLMClient())

        # 并发护栏
        self._guard = ConcurrencyGuard(
            max_concurrent=max_concurrent_scenes,
            acquire_timeout=acquire_timeout,
        )

        # 事件执行器
        self._runner = EventRunner(
            llm=self._safe_llm,
            diary=diary,
            memory=memory,
            emotion=emotion,
            group_scene=group_scene,
            chatter=chatter,
            schedule=schedule,
            clock=clock,
            guard=self._guard,
        )

        # 逃课事件链
        self._truant_chain = TruantEventChain(
            llm=self._safe_llm,
            group_scene=group_scene,
            chatter=chatter,
            schedule=schedule,
            clock=clock,
            diary=diary,
            guard=self._guard,
        )

        # 配置
        self._config_dir = config_dir
        self._enabled = enabled

        # 运行时状态
        self._configs: dict[str, EventConfig] = {}
        self._active_events: dict[str, EventInstance] = {}  # event_id → instance
        self._pre_noticed: set[str] = set()  # 已发送预告的 event_id（防重复）
        self._last_tick_time: datetime | None = None

    async def initialize(self) -> None:
        """
        初始化系统：加载事件配置。
        必须在首次 tick 前调用。
        """
        if not self._enabled:
            logger.info("周期事件系统已禁用")
            return

        configs = load_all_event_configs(self._config_dir)
        self._configs = {c.event_id: c for c in configs}
        logger.info(
            "周期事件系统初始化完成，加载 %d 个事件配置",
            len(self._configs),
        )

    async def initialize_from_dicts(self, data_list: list[dict]) -> None:
        """
        从字典列表初始化（用于测试或动态配置）。
        """
        configs = load_event_configs_from_dicts(data_list)
        self._configs = {c.event_id: c for c in configs}
        logger.info("从字典加载 %d 个事件配置", len(self._configs))

    def set_resident_pool(self, pool: list[str]) -> None:
        """设置城市居民池（用于普通居民概率汇聚）"""
        self._runner.set_resident_pool(pool)

    # ========================================================
    # 主 tick 入口
    # ========================================================

    async def tick(self, game_time: datetime) -> None:
        """
        系统主 tick —— 每个游戏时钟周期调用一次。

        处理流程：
        1. 检查是否有活跃事件需要推进或结束
        2. 检查是否有新事件需要触发预告
        3. 检查是否有事件到达开始时间

        :param game_time: 当前游戏时间
        """
        if not self._enabled:
            return

        self._last_tick_time = game_time

        # 1. 推进/结束活跃事件
        await self._tick_active_events(game_time)

        # 2. 检查新事件触发
        await self._check_new_events(game_time)

    async def _tick_active_events(self, game_time: datetime) -> None:
        """推进所有活跃事件的状态"""
        finished_ids: list[str] = []

        for event_id, instance in self._active_events.items():
            if instance.phase == EventPhase.PRE:
                # PRE → ACTIVE：检查是否到达开始时间
                if instance.scheduled_start and game_time >= instance.scheduled_start:
                    await self._runner.run_active_phase(instance)

                    # 学园上课铃特殊处理：逃课事件链
                    if event_id == "school_bell":
                        await self._handle_school_truant(instance, game_time)

            elif instance.phase == EventPhase.ACTIVE:
                # ACTIVE → POST：检查是否到达结束时间
                if instance.scheduled_end and game_time >= instance.scheduled_end:
                    await self._runner.run_post_phase(instance)
                    finished_ids.append(event_id)

        # 清理已结束的事件
        for eid in finished_ids:
            del self._active_events[eid]
            self._pre_noticed.discard(eid)
            logger.info("事件已从活跃列表移除: %s", eid)

    async def _check_new_events(self, game_time: datetime) -> None:
        """检查是否有新事件需要触发"""
        for event_id, config in self._configs.items():
            if not config.enabled:
                continue
            if event_id in self._active_events:
                continue  # 已在进行中

            # 检查是否到达预告时间
            trigger_time = self._next_trigger_time(config, game_time)
            if trigger_time is None:
                continue

            pre_notice_time = trigger_time - timedelta(
                minutes=config.pre_notice_minutes
            )

            # 预告阶段
            if (
                game_time >= pre_notice_time
                and game_time < trigger_time
                and event_id not in self._pre_noticed
            ):
                instance = self._create_instance(config, trigger_time, game_time)
                self._active_events[event_id] = instance
                self._pre_noticed.add(event_id)
                await self._runner.run_pre_phase(instance)
                logger.info("事件预告已发送: %s（预计 %s 开始）", config.name, trigger_time)

            # 直接到达开始时间（可能错过了预告窗口）
            elif game_time >= trigger_time and event_id not in self._active_events:
                instance = self._create_instance(config, trigger_time, game_time)
                self._active_events[event_id] = instance
                self._pre_noticed.add(event_id)
                # 跳过 PRE，直接进入 ACTIVE
                await self._runner.run_active_phase(instance)
                logger.info("事件直接开始（跳过预告）: %s", config.name)

    def _next_trigger_time(
        self, config: EventConfig, now: datetime
    ) -> datetime | None:
        """
        计算事件的下一个触发时间。
        返回今天或最近的触发时间点。
        """
        trigger = config.trigger
        today_trigger = now.replace(
            hour=trigger.hour, minute=trigger.minute, second=0, microsecond=0
        )

        if trigger.trigger_type == TriggerType.DAILY:
            # 每日事件：如果今天的触发时间已过，返回 None（不重复触发）
            if now <= today_trigger:
                return today_trigger
            return None

        elif trigger.trigger_type == TriggerType.CRON:
            # 周期事件：检查今天是否是触发日
            if now.weekday() in trigger.day_of_week:
                if now <= today_trigger:
                    return today_trigger
            return None

        return None

    def _create_instance(
        self, config: EventConfig, trigger_time: datetime, now: datetime
    ) -> EventInstance:
        """创建事件运行时实例"""
        end_time = trigger_time + timedelta(minutes=config.duration_minutes)

        # 初始化分片组
        slice_groups = [
            SliceGroup(
                slice_id=s.slice_id,
                location=s.location,
                display_name=s.display_name or s.slice_id,
            )
            for s in config.slices
        ]

        return EventInstance(
            config=config,
            phase=EventPhase.PRE,
            scheduled_start=trigger_time,
            scheduled_end=end_time,
            slice_groups=slice_groups,
        )

    async def _handle_school_truant(
        self, instance: EventInstance, game_time: datetime
    ) -> None:
        """处理学园上课铃的逃课事件链"""
        config = instance.config
        if not config:
            return

        # 从配置中读取逃课参数（YAML 中的 truant_config 字段）
        # 由于 EventConfig 模型没有 truant_config 字段，我们从原始配置获取
        truant_config = config.model_extra.get("truant_config", {}) if config.model_extra else {}
        if not truant_config or not truant_config.get("enabled", False):
            return

        # 检查是否到达巡查时间
        patrol_time = instance.actual_start + timedelta(
            minutes=truant_config.get("patrol_delay_minutes", 30)
        )
        if game_time < patrol_time:
            return

        # 避免重复触发
        if instance.metadata.get("truant_executed"):
            return

        student_ids = [
            npc for npc in config.participation.optional_npcs
            if npc not in config.participation.required_npcs
        ]
        # 实际上学生应该是 required_npcs 中除学园长外的
        teacher_id = "theresa"
        student_ids = [
            npc for npc in config.participation.required_npcs
            if npc != teacher_id
        ]

        result = await self._truant_chain.execute(
            student_ids=student_ids,
            teacher_id=teacher_id,
            truant_probability=truant_config.get("truant_probability", 0.25),
            truant_destination=truant_config.get("truant_destination", "shop_game_center"),
            patrol_delay_minutes=truant_config.get("patrol_delay_minutes", 30),
            patrol_discovery_probability=truant_config.get("patrol_discovery_probability", 0.7),
        )

        instance.metadata["truant_executed"] = True
        instance.metadata["truant_result"] = result
        logger.info("逃课事件链执行完毕: %s", result)

    # ========================================================
    # 状态查询接口
    # ========================================================

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def active_events(self) -> dict[str, EventInstance]:
        """获取当前活跃事件"""
        return dict(self._active_events)

    @property
    def event_configs(self) -> dict[str, EventConfig]:
        """获取所有事件配置"""
        return dict(self._configs)

    @property
    def concurrency_stats(self) -> dict[str, Any]:
        """获取并发护栏统计"""
        stats = self._guard.stats
        return {
            "active_count": stats.active_count,
            "max_concurrent": self._guard.max_concurrent,
            "total_acquired": stats.total_acquired,
            "total_released": stats.total_released,
            "total_rejected": stats.total_rejected,
        }

    def get_event_status(self, event_id: str) -> dict[str, Any] | None:
        """获取指定事件的状态"""
        instance = self._active_events.get(event_id)
        if not instance:
            return None
        return {
            "event_id": event_id,
            "phase": instance.phase.value,
            "scheduled_start": instance.scheduled_start,
            "scheduled_end": instance.scheduled_end,
            "actual_start": instance.actual_start,
            "actual_end": instance.actual_end,
            "participant_count": len(instance.all_participants),
            "active_slices": len(instance.active_slices),
            "summary": instance.summary,
        }

    def enable(self) -> None:
        """启用系统"""
        self._enabled = True
        logger.info("周期事件系统已启用")

    def disable(self) -> None:
        """禁用系统（不影响已活跃事件，但不再触发新事件）"""
        self._enabled = False
        logger.info("周期事件系统已禁用")
