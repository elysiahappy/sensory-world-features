"""
周期事件系统 —— 数据模型定义

定义事件配置、事件实例、分片组、事件阶段等核心数据结构。
使用 pydantic v2 做配置校验，dataclass 做运行时实例。
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ============================================================
# 枚举类型
# ============================================================

class EventType(str, enum.Enum):
    """事件类型"""
    GATHERING = "gathering"   # 聚集型：全城向特定地点汇聚
    SCENE = "scene"           # 场景型：特定地点触发 group_scene


class EventPhase(str, enum.Enum):
    """事件生命周期阶段"""
    PRE = "pre"               # 事件前：预告、情绪注入
    ACTIVE = "active"         # 事件中：群聊运行、私语增强
    POST = "post"             # 事件后：摘要、记忆写入


class TriggerType(str, enum.Enum):
    """触发规则类型"""
    CRON = "cron"             # 周期 cron 式（如每周六 20:00）
    DAILY = "daily"           # 每日固定时间


# ============================================================
# 配置模型（YAML 加载 → pydantic 校验）
# ============================================================

class TriggerRule(BaseModel):
    """
    事件触发规则。
    - cron 类型：指定 day_of_week（0=周一~6=周日）、hour、minute
    - daily 类型：仅指定 hour、minute
    """
    trigger_type: TriggerType = Field(default=TriggerType.DAILY)
    day_of_week: list[int] = Field(default_factory=list)  # 0=周一, 6=周日
    hour: int = Field(default=20, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)

    @field_validator("day_of_week")
    @classmethod
    def validate_days(cls, v: list[int]) -> list[int]:
        for d in v:
            if not 0 <= d <= 6:
                raise ValueError(f"day_of_week 值必须在 0-6 之间，收到: {d}")
        return v


class SliceConfig(BaseModel):
    """
    分片场地配置 —— 事件期间各子场地的分组规则。
    用于控制并发：每个分片独立拉起一个 group_scene，
    避免所有 NPC 同时进入同一群聊导致 LLM 并发尖峰。
    """
    slice_id: str                           # 分片标识（如 'main_stage', 'food_stalls'）
    location: str                           # 场地 ID（对应城市地点）
    display_name: str = ""                  # 展示名称（用于日记/记忆）
    capacity: int = Field(default=8, ge=1)  # 该分片最大 NPC 数


class ParticipationRule(BaseModel):
    """
    NPC 参与规则。
    - required_npcs: 必须到场的 NPC（如表演者）
    - optional_npcs: 按日程自然到场的 NPC
    - resident_probability: 普通居民汇聚概率 (0.0~1.0)
    """
    required_npcs: list[str] = Field(default_factory=list)
    optional_npcs: list[str] = Field(default_factory=list)
    resident_probability: float = Field(default=0.3, ge=0.0, le=1.0)


class EventConfig(BaseModel):
    """
    事件完整配置 —— 从 YAML 文件加载。
    一个 EventConfig 定义了一类周期事件的完整规则。
    """
    event_id: str                           # 事件唯一标识（如 'fireworks_night'）
    name: str                               # 事件名称（如 '周六烟火大会'）
    event_type: EventType                   # 事件类型
    trigger: TriggerRule                    # 触发规则
    main_venue: str                         # 主场地 ID
    slices: list[SliceConfig] = Field(default_factory=list)  # 分片场地列表
    participation: ParticipationRule = Field(default_factory=ParticipationRule)
    duration_minutes: int = Field(default=60, ge=1)  # 事件持续时间（游戏内分钟）
    pre_notice_minutes: int = Field(default=15, ge=0)  # 提前预告时间（游戏内分钟）
    chatter_boost: float = Field(default=0.2, ge=0.0, le=1.0)  # 事件期间私语概率提升
    chatter_topic_hint: str = ""            # 事件期间私语话题提示
    enabled: bool = Field(default=True)     # 是否启用

    @property
    def all_slice_ids(self) -> list[str]:
        """获取所有分片 ID"""
        return [s.slice_id for s in self.slices]

    @property
    def all_locations(self) -> list[str]:
        """获取所有涉及的场地（主场地 + 分片场地）"""
        locations = {self.main_venue}
        for s in self.slices:
            locations.add(s.location)
        return list(locations)


# ============================================================
# 运行时实例（事件运行期间使用）
# ============================================================

@dataclass
class SliceGroup:
    """
    运行时分片组 —— 跟踪一个分片内的 NPC 和群聊状态。
    """
    slice_id: str
    location: str
    display_name: str
    participant_ids: list[str] = field(default_factory=list)
    scene_run_id: str = ""          # group_scene 返回的运行 ID
    is_active: bool = False


@dataclass
class EventInstance:
    """
    运行时事件实例 —— 一次具体的事件执行。
    从 EventConfig 实例化而来，跟踪事件生命周期状态。
    """
    instance_id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:8]}")
    config: EventConfig | None = None
    phase: EventPhase = EventPhase.PRE
    scheduled_start: datetime | None = None     # 计划开始时间
    scheduled_end: datetime | None = None       # 计划结束时间
    actual_start: datetime | None = None        # 实际开始时间
    actual_end: datetime | None = None          # 实际结束时间
    slice_groups: list[SliceGroup] = field(default_factory=list)
    all_participants: set[str] = field(default_factory=set)
    summary: str = ""                           # 事件摘要
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def event_id(self) -> str:
        return self.config.event_id if self.config else ""

    @property
    def event_name(self) -> str:
        return self.config.name if self.config else ""

    @property
    def active_slices(self) -> list[SliceGroup]:
        """获取当前活跃的分片组"""
        return [s for s in self.slice_groups if s.is_active]

    @property
    def active_scene_count(self) -> int:
        """当前活跃的群聊场景数（用于并发控制）"""
        return len(self.active_slices)


# ============================================================
# 事件结果（用于记忆写入和日记记录）
# ============================================================

@dataclass
class EventMemoryPayload:
    """
    事件记忆载荷 —— 事件结束后写入每个参与者记忆库的内容。
    """
    event_name: str
    location: str
    participant_ids: list[str]
    impression: str             # 一句话印象（LLM 生成或模板兜底）
    timestamp: datetime
    tags: list[str] = field(default_factory=list)

    def format_memory_content(self, npc_id: str) -> str:
        """格式化为记忆条目文本"""
        others = [p for p in self.participant_ids if p != npc_id]
        others_str = "、".join(others[:5])  # 最多列5人
        if len(others) > 5:
            others_str += f"等{len(others)}人"
        return (
            f"参加了「{self.event_name}」，地点在{self.location}，"
            f"在场的还有{others_str}。{self.impression}"
        )
