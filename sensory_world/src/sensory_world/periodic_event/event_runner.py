"""
周期事件系统 —— 事件执行器

负责事件生命周期的三个阶段执行：
  - PRE（事件前）：写世界日记预告、向相关 NPC 注入期待情绪
  - ACTIVE（事件中）：按分片规则拉起 group_scene、上调私语概率
  - POST（事件后）：生成事件摘要、给参与者写入共同记忆

所有外部依赖通过 Protocol 注入，LLM 不可用时自动降级。
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime
from typing import Any

from sensory_world.llm_client import SafeLLMClient
from sensory_world.periodic_event.concurrency_guard import (
    ConcurrencyGuard,
    ConcurrencyRejectError,
)
from sensory_world.periodic_event.models import (
    EventConfig,
    EventInstance,
    EventMemoryPayload,
    EventPhase,
    EventType,
    SliceGroup,
)
from sensory_world.protocols import (
    ChatterProtocol,
    GameClockProtocol,
    GroupSceneProtocol,
    LLMClientProtocol,
    NPCMemoryStoreProtocol,
    NPCEmotionProtocol,
    Protocol,
    ScheduleBookProtocol,
    WorldDiaryProtocol,
    MemoryEntry,
)

logger = logging.getLogger(__name__)


# ============================================================
# 模板兜底文本（LLM 不可用时使用）
# ============================================================

_FALLBACK_IMPRESSIONS = [
    "那是一段愉快的时光。",
    "今天的氛围格外好。",
    "和大家在一起总是很开心。",
    "这个场景值得记住。",
    "平凡日子里的小确幸。",
]

_FALLBACK_SUMMARY_TEMPLATES = [
    "「{name}」在{location}如期举行，大家度过了愉快的时光。",
    "今天的「{name}」很热闹，{count}位居民参与了活动。",
    "「{name}」圆满结束，留下了美好的回忆。",
]


class EventRunner:
    """
    事件执行器 —— 处理事件前/中/后三阶段逻辑。

    依赖注入：
        llm: 安全的 LLM 客户端（含降级）
        diary: 世界日记
        memory: NPC 记忆库
        emotion: NPC 情绪系统
        group_scene: 群聊场景管理
        chatter: NPC 私语
        schedule: NPC 日程
        clock: 游戏时钟
        guard: 并发护栏
    """

    def __init__(
        self,
        llm: SafeLLMClient,
        diary: WorldDiaryProtocol,
        memory: NPCMemoryStoreProtocol,
        emotion: NPCEmotionProtocol,
        group_scene: GroupSceneProtocol,
        chatter: ChatterProtocol,
        schedule: ScheduleBookProtocol,
        clock: GameClockProtocol,
        guard: ConcurrencyGuard,
    ):
        self._llm = llm
        self._diary = diary
        self._memory = memory
        self._emotion = emotion
        self._group_scene = group_scene
        self._chatter = chatter
        self._schedule = schedule
        self._clock = clock
        self._guard = guard

    # ========================================================
    # PRE 阶段：事件预告
    # ========================================================

    async def run_pre_phase(self, instance: EventInstance) -> None:
        """
        事件前阶段：
        1. 向世界日记写入预告条目
        2. 向所有必到 NPC 注入期待情绪
        3. 为必到 NPC 覆盖日程到主场地
        """
        config = instance.config
        if not config:
            logger.error("事件实例缺少配置，跳过 PRE 阶段")
            return

        instance.phase = EventPhase.PRE
        logger.info("事件 PRE 阶段开始: %s", config.name)

        # 1. 写世界日记预告
        try:
            preview_text = await self._generate_preview(config)
            await self._diary.write_entry(
                category="event_preview",
                content=preview_text,
                event_id=config.event_id,
                event_name=config.name,
            )
            logger.info("已写入事件预告: %s", config.name)
        except Exception as e:
            logger.error("写入事件预告失败: %s", e)

        # 2. 向必到 NPC 注入期待情绪
        for npc_id in config.participation.required_npcs:
            try:
                await self._emotion.inject_emotion(
                    npc_id=npc_id,
                    emotion="anticipation",
                    intensity=0.6,
                    duration_seconds=config.pre_notice_minutes * 60,
                    reason=f"即将参加「{config.name}」",
                )
            except Exception as e:
                logger.warning("注入期待情绪失败 [%s]: %s", npc_id, e)

        # 3. 为必到 NPC 覆盖日程
        now = await self._clock.now()
        end_time = instance.scheduled_end or now
        for npc_id in config.participation.required_npcs:
            try:
                await self._schedule.override_schedule(
                    npc_id=npc_id,
                    start=now,
                    end=end_time,
                    location=config.main_venue,
                    activity=config.name,
                    reason=f"参加「{config.name}」",
                )
            except Exception as e:
                logger.warning("覆盖日程失败 [%s]: %s", npc_id, e)

        logger.info("事件 PRE 阶段完成: %s", config.name)

    async def _generate_preview(self, config: EventConfig) -> str:
        """生成事件预告文本（LLM 或模板兜底）"""
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一座虚拟城市的叙事者。请用一句简短、有画面感的话"
                    "预告即将发生的城市活动。不要超过 50 字。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"活动名称：{config.name}\n"
                    f"地点：{config.main_venue}\n"
                    f"主要参与者：{', '.join(config.participation.required_npcs)}\n"
                    f"请生成一句预告。"
                ),
            },
        ]
        return await self._llm.chat(messages)

    # ========================================================
    # ACTIVE 阶段：事件执行
    # ========================================================

    async def run_active_phase(self, instance: EventInstance) -> None:
        """
        事件中阶段：
        1. 确定各分片的 NPC 分配
        2. 按分片拉起 group_scene（受并发护栏保护）
        3. 上调事件期间 NPC 私语概率
        """
        config = instance.config
        if not config:
            logger.error("事件实例缺少配置，跳过 ACTIVE 阶段")
            return

        instance.phase = EventPhase.ACTIVE
        instance.actual_start = await self._clock.now()
        logger.info("事件 ACTIVE 阶段开始: %s", config.name)

        # 1. 确定参与者并分配到分片
        participants = await self._resolve_participants(config)
        instance.all_participants = set(participants)
        slice_assignments = self._assign_to_slices(config, participants)

        # 2. 按分片拉起 group_scene（受并发护栏保护）
        tasks = []
        for slice_group in instance.slice_groups:
            assigned_npcs = slice_assignments.get(slice_group.slice_id, [])
            if not assigned_npcs:
                logger.debug("分片 %s 无参与者，跳过", slice_group.slice_id)
                continue
            slice_group.participant_ids = assigned_npcs
            tasks.append(self._start_slice_scene(instance, slice_group, config))

        if tasks:
            # 并发启动所有分片场景（ConcurrencyGuard 内部控制并发数）
            await asyncio.gather(*tasks, return_exceptions=True)

        # 3. 上调私语概率
        await self._boost_chatter(config, participants)

        logger.info(
            "事件 ACTIVE 阶段完成: %s（%d 个分片活跃，%d 位参与者）",
            config.name, len(instance.active_slices), len(participants),
        )

    async def _resolve_participants(self, config: EventConfig) -> list[str]:
        """
        确定事件参与者列表。
        - 必到 NPC 全部加入
        - 可选 NPC 全部加入（他们有日程约束）
        - 普通居民按概率抽取（这里简化为从配置中读取）
        """
        participants = list(config.participation.required_npcs)
        participants.extend(config.participation.optional_npcs)

        # 普通居民汇聚：通过概率决定
        # 实际项目中应从城市居民列表中抽取
        # 这里预留接口，由调度器注入居民列表
        if hasattr(self, '_resident_pool') and self._resident_pool:
            prob = config.participation.resident_probability
            residents = [
                r for r in self._resident_pool
                if r not in participants and random.random() < prob
            ]
            participants.extend(residents)

        return participants

    def set_resident_pool(self, pool: list[str]) -> None:
        """设置普通居民池（由调度器调用）"""
        self._resident_pool = pool

    def _assign_to_slices(
        self, config: EventConfig, participants: list[str]
    ) -> dict[str, list[str]]:
        """
        将参与者分配到各分片。
        策略：
        - 必到 NPC 优先分配到主舞台
        - 其他 NPC 按分片容量轮转分配
        - 确保每个分片不超容量
        """
        assignments: dict[str, list[str]] = {
            s.slice_id: [] for s in config.slices
        }

        if not config.slices:
            return assignments

        # 必到 NPC → 第一个分片（主舞台）
        primary_slice = config.slices[0].slice_id
        for npc in config.participation.required_npcs:
            if npc in participants:
                assignments[primary_slice].append(npc)

        # 其他 NPC 按容量轮转分配
        remaining = [
            p for p in participants
            if p not in config.participation.required_npcs
        ]
        slice_idx = 0
        for npc in remaining:
            # 找到下一个有容量的分片
            attempts = 0
            while attempts < len(config.slices):
                sid = config.slices[slice_idx % len(config.slices)].slice_id
                cap = config.slices[slice_idx % len(config.slices)].capacity
                if len(assignments[sid]) < cap:
                    assignments[sid].append(npc)
                    break
                slice_idx += 1
                attempts += 1
            else:
                # 所有分片满了，塞到第一个分片（溢出容忍）
                assignments[primary_slice].append(npc)
                logger.warning("所有分片已满，NPC %s 溢出到主分片", npc)

        return assignments

    async def _start_slice_scene(
        self,
        instance: EventInstance,
        slice_group: SliceGroup,
        config: EventConfig,
    ) -> None:
        """在并发护栏保护下启动一个分片的群聊场景"""
        slot_name = f"{config.event_id}:{slice_group.slice_id}"

        try:
            slot = await self._guard.acquire(slot_name)
        except ConcurrencyRejectError:
            logger.warning(
                "并发护栏拒绝，跳过分片场景: %s", slot_name
            )
            return

        async with slot:
            try:
                scene_run_id = await self._group_scene.start_scene(
                    scene_id=f"{instance.instance_id}_{slice_group.slice_id}",
                    location=slice_group.location,
                    participant_ids=slice_group.participant_ids,
                    topic=config.chatter_topic_hint or config.name,
                    context={
                        "event_id": config.event_id,
                        "event_name": config.name,
                        "slice_id": slice_group.slice_id,
                    },
                )
                slice_group.scene_run_id = scene_run_id
                slice_group.is_active = True
                logger.info(
                    "分片场景已启动: %s @ %s（%d 人）",
                    slice_group.slice_id, slice_group.location,
                    len(slice_group.participant_ids),
                )
            except Exception as e:
                logger.error("启动分片场景失败 [%s]: %s", slice_group.slice_id, e)

    async def _boost_chatter(
        self, config: EventConfig, participants: list[str]
    ) -> None:
        """上调事件期间参与者的私语概率"""
        if not config.chatter_topic_hint:
            return

        # 随机配对参与者进行私语
        if len(participants) < 2:
            return

        pairs = []
        shuffled = list(participants)
        random.shuffle(shuffled)
        for i in range(0, len(shuffled) - 1, 2):
            pairs.append((shuffled[i], shuffled[i + 1]))

        for npc_a, npc_b in pairs[:len(participants) // 2]:
            try:
                await self._chatter.trigger_chatter(
                    npc_a=npc_a,
                    npc_b=npc_b,
                    topic=config.chatter_topic_hint,
                    boost_probability=config.chatter_boost,
                )
            except Exception as e:
                logger.warning("触发私语失败 [%s <-> %s]: %s", npc_a, npc_b, e)

    # ========================================================
    # POST 阶段：事件收尾
    # ========================================================

    async def run_post_phase(self, instance: EventInstance) -> None:
        """
        事件后阶段：
        1. 结束所有活跃的分片场景
        2. 生成事件摘要（LLM 或模板兜底）
        3. 写入世界日记
        4. 给每个参与者写入共同记忆
        """
        config = instance.config
        if not config:
            logger.error("事件实例缺少配置，跳过 POST 阶段")
            return

        instance.phase = EventPhase.POST
        instance.actual_end = await self._clock.now()
        logger.info("事件 POST 阶段开始: %s", config.name)

        # 1. 结束所有活跃的分片场景
        await self._end_all_scenes(instance)

        # 2. 生成事件摘要
        summary = await self._generate_summary(instance)
        instance.summary = summary

        # 3. 写入世界日记
        try:
            await self._diary.write_entry(
                category="event_summary",
                content=summary,
                event_id=config.event_id,
                event_name=config.name,
                participant_count=len(instance.all_participants),
            )
        except Exception as e:
            logger.error("写入事件摘要到日记失败: %s", e)

        # 4. 给每个参与者写入共同记忆
        impression = await self._generate_impression(config)
        memory_payload = EventMemoryPayload(
            event_name=config.name,
            location=config.main_venue,
            participant_ids=list(instance.all_participants),
            impression=impression,
            timestamp=instance.actual_end or await self._clock.now(),
            tags=["event", config.event_id],
        )

        for npc_id in instance.all_participants:
            try:
                content = memory_payload.format_memory_content(npc_id)
                entry = MemoryEntry(
                    entry_id=f"mem_{config.event_id}_{npc_id}_{instance.instance_id}",
                    npc_id=npc_id,
                    content=content,
                    timestamp=memory_payload.timestamp,
                    confidence=0.85,
                    tags=memory_payload.tags,
                    metadata={
                        "event_id": config.event_id,
                        "event_name": config.name,
                        "co_participants": [
                            p for p in instance.all_participants if p != npc_id
                        ],
                    },
                )
                await self._memory.add_entry(npc_id, entry)
            except Exception as e:
                logger.warning("写入共同记忆失败 [%s]: %s", npc_id, e)

        logger.info("事件 POST 阶段完成: %s", config.name)

    async def _end_all_scenes(self, instance: EventInstance) -> None:
        """结束所有活跃的分片场景"""
        for slice_group in instance.slice_groups:
            if slice_group.is_active and slice_group.scene_run_id:
                try:
                    result = await self._group_scene.end_scene(
                        slice_group.scene_run_id
                    )
                    slice_group.is_active = False
                    logger.debug(
                        "分片场景已结束: %s, 摘要: %s",
                        slice_group.slice_id,
                        result.get("summary", "")[:50],
                    )
                except Exception as e:
                    logger.warning(
                        "结束分片场景失败 [%s]: %s",
                        slice_group.slice_id, e,
                    )
                    slice_group.is_active = False

    async def _generate_summary(self, instance: EventInstance) -> str:
        """生成事件摘要（LLM 或模板兜底）"""
        config = instance.config
        if not config:
            return "事件已结束。"

        messages = [
            {
                "role": "system",
                "content": (
                    "你是一座虚拟城市的叙事者。请用 2-3 句话总结刚刚结束的城市活动。"
                    "要求有画面感、有温度，不超过 100 字。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"活动名称：{config.name}\n"
                    f"地点：{config.main_venue}\n"
                    f"参与者：{', '.join(instance.all_participants)}\n"
                    f"请生成活动摘要。"
                ),
            },
        ]
        return await self._llm.chat(messages)

    async def _generate_impression(self, config: EventConfig) -> str:
        """生成一句话印象（LLM 或模板兜底）"""
        messages = [
            {
                "role": "system",
                "content": "用一句话描述参加城市活动的感受，不超过 20 字。",
            },
            {
                "role": "user",
                "content": f"活动：{config.name}，地点：{config.main_venue}",
            },
        ]
        try:
            result = await self._llm.chat(messages)
            return result if result.strip() else random.choice(_FALLBACK_IMPRESSIONS)
        except Exception:
            return random.choice(_FALLBACK_IMPRESSIONS)


# ============================================================
# 逃课事件链（学园上课铃专用）
# ============================================================

class TruantEventChain:
    """
    逃课事件链 —— 学园上课铃的特殊子事件。

    流程：
    1. 每个学生按概率决定是否逃课
    2. 逃课学生前往游戏厅（shop_game_center）
    3. 学园长在上课 N 分钟后巡查
    4. 巡查按概率发现逃课学生，触发"一窝端"喜剧 group_scene
    """

    def __init__(
        self,
        llm: SafeLLMClient,
        group_scene: GroupSceneProtocol,
        chatter: ChatterProtocol,
        schedule: ScheduleBookProtocol,
        clock: GameClockProtocol,
        diary: WorldDiaryProtocol,
        guard: ConcurrencyGuard,
    ):
        self._llm = llm
        self._group_scene = group_scene
        self._chatter = chatter
        self._schedule = schedule
        self._clock = clock
        self._diary = diary
        self._guard = guard

    async def execute(
        self,
        student_ids: list[str],
        teacher_id: str,
        truant_probability: float = 0.25,
        truant_destination: str = "shop_game_center",
        patrol_delay_minutes: int = 30,
        patrol_discovery_probability: float = 0.7,
    ) -> dict[str, Any]:
        """
        执行逃课事件链。
        :return: 包含逃课学生列表、是否被发现等信息的字典
        """
        # 1. 决定谁逃课
        truants = [
            s for s in student_ids
            if random.random() < truant_probability
        ]

        if not truants:
            logger.info("学园事件：无人逃课")
            return {"truants": [], "discovered": False}

        logger.info("学园事件：逃课学生: %s", truants)

        # 2. 逃课学生前往游戏厅
        now = await self._clock.now()
        from datetime import timedelta
        end_time = now + timedelta(minutes=120)

        for student_id in truants:
            try:
                await self._schedule.override_schedule(
                    npc_id=student_id,
                    start=now,
                    end=end_time,
                    location=truant_destination,
                    activity="逃课打游戏",
                    reason="逃课了",
                )
            except Exception as e:
                logger.warning("逃课日程覆盖失败 [%s]: %s", student_id, e)

        # 3. 游戏厅内私语（逃课学生 + 老板）
        for student_id in truants:
            try:
                await self._chatter.trigger_chatter(
                    npc_a=student_id,
                    npc_b="bronya",  # 游戏厅老板
                    topic="逃课来打游戏的刺激感、游戏技巧",
                    boost_probability=0.3,
                )
            except Exception as e:
                logger.warning("逃课私语触发失败: %s", e)

        # 4. 学园长巡查
        discovered = random.random() < patrol_discovery_probability

        if discovered:
            logger.info("学园事件：学园长巡查发现逃课学生！")

            # 触发"一窝端"喜剧 group_scene
            try:
                slot = await self._guard.acquire("school_bell:truant_patrol")
                async with slot:
                    scene_id = await self._group_scene.start_scene(
                        scene_id=f"truant_patrol_{now.strftime('%Y%m%d_%H%M')}",
                        location=truant_destination,
                        participant_ids=[teacher_id] + truants + ["bronya"],
                        topic="学园长一窝端逃课学生",
                        context={"event_type": "truant_comedy"},
                    )

                # 写日记
                truants_str = "、".join(truants)
                await self._diary.write_entry(
                    category="event_comedy",
                    content=(
                        f"德丽莎学园长在游戏厅抓获了逃课的{truants_str}，"
                        f"上演了一出精彩的「一窝端」喜剧。"
                    ),
                    event_id="school_bell_truant",
                )
            except ConcurrencyRejectError:
                logger.warning("逃课巡查场景被并发护栏拒绝")
            except Exception as e:
                logger.error("逃课巡查场景失败: %s", e)

        return {
            "truants": truants,
            "discovered": discovered,
            "destination": truant_destination,
        }
