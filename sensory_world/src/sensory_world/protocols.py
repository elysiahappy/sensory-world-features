"""
协议接口定义 —— 对现有系统依赖的抽象

本模块定义了所有与现有城市模拟系统交互的 Protocol 接口。
主项目需实现这些 Protocol 并注入到各功能模块中。
所有接口均为异步设计，支持零配置兜底降级。

需主项目确认的接口：
  - WorldDiaryProtocol: 世界日记写入
  - NPCMemoryStoreProtocol: NPC 长期记忆读写
  - NPCEmotionProtocol: NPC 情绪状态注入
  - GroupSceneProtocol: 群聊场景拉起
  - ChatterProtocol: NPC 私语触发
  - ScheduleBookProtocol: NPC 日程查询与覆盖
  - GameClockProtocol: 游戏时间查询
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


# ============================================================
# 游戏时间协议
# ============================================================

@runtime_checkable
class GameClockProtocol(Protocol):
    """
    游戏时钟协议 —— 查询当前游戏内时间。
    需主项目确认：现有时间系统是否提供类似接口。
    """

    async def now(self) -> datetime:
        """返回当前游戏内时间（datetime 对象）"""
        ...

    async def weekday(self) -> int:
        """返回当前游戏内星期几（0=周一, 6=周日）"""
        ...


# ============================================================
# 世界日记协议
# ============================================================

@runtime_checkable
class WorldDiaryProtocol(Protocol):
    """
    世界日记协议 —— 向城市日记中写入条目。
    需主项目确认：对应现有 world_diary 模块的 add_npc 等接口。
    """

    async def write_entry(self, category: str, content: str, **metadata: Any) -> None:
        """
        写入一条世界日记。
        :param category: 条目类别（如 'event_preview', 'event_summary', 'birthday'）
        :param content: 条目正文
        :param metadata: 附加元数据
        """
        ...


# ============================================================
# NPC 记忆库协议
# ============================================================

@dataclass
class MemoryEntry:
    """记忆条目"""
    entry_id: str
    npc_id: str
    content: str
    timestamp: datetime
    confidence: float = 0.8
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class NPCMemoryStoreProtocol(Protocol):
    """
    NPC 长期记忆协议 —— 读写单个 NPC 的记忆库。
    需主项目确认：对应现有 npc_memory_store 模块。
    """

    async def add_entry(self, npc_id: str, entry: MemoryEntry) -> None:
        """向指定 NPC 的记忆库写入一条记忆"""
        ...

    async def recall(self, npc_id: str, query: str, top_k: int = 5) -> list[MemoryEntry]:
        """
        按语义查询召回 NPC 的记忆条目。
        :param npc_id: NPC 标识
        :param query: 查询文本
        :param top_k: 返回条数
        """
        ...

    async def get_entries_by_tag(
        self, npc_id: str, tag: str, limit: int = 10
    ) -> list[MemoryEntry]:
        """按标签检索记忆条目"""
        ...


# ============================================================
# NPC 情绪协议
# ============================================================

class EmotionType(str, enum.Enum):
    """情绪类型枚举（扩展用，主项目可忽略直接使用字符串）"""
    ANTICIPATION = "anticipation"    # 期待
    JOY = "joy"                      # 喜悦
    NOSTALGIA = "nostalgia"          # 怀旧
    SADNESS = "sadness"              # 悲伤
    EXCITEMENT = "excitement"        # 兴奋


@runtime_checkable
class NPCEmotionProtocol(Protocol):
    """
    NPC 情绪状态协议 —— 注入/查询 NPC 情绪。
    需主项目确认：对应现有 npc_emotion 模块。
    """

    async def inject_emotion(
        self, npc_id: str, emotion: str, intensity: float = 0.5,
        duration_seconds: float = 300.0, reason: str = ""
    ) -> None:
        """
        向 NPC 注入一种情绪状态。
        :param npc_id: NPC 标识
        :param emotion: 情绪类型
        :param intensity: 强度 (0.0~1.0)
        :param duration_seconds: 持续时间（游戏内秒）
        :param reason: 情绪产生原因
        """
        ...

    async def get_emotion(self, npc_id: str) -> dict[str, float]:
        """获取 NPC 当前情绪状态字典 {emotion_type: intensity}"""
        ...


# ============================================================
# 群聊场景协议
# ============================================================

@runtime_checkable
class GroupSceneProtocol(Protocol):
    """
    群聊场景协议 —— 拉起/管理 NPC 群聊。
    需主项目确认：对应现有 group_scene 机制。
    """

    async def start_scene(
        self,
        scene_id: str,
        location: str,
        participant_ids: list[str],
        topic: str = "",
        context: dict[str, Any] | None = None,
    ) -> str:
        """
        拉起一个群聊场景。
        :param scene_id: 场景唯一标识
        :param location: 场景地点
        :param participant_ids: 参与 NPC 列表
        :param topic: 话题/主题
        :param context: 附加上下文
        :return: 场景运行 ID
        """
        ...

    async def end_scene(self, scene_run_id: str) -> dict[str, Any]:
        """
        结束一个群聊场景，返回场景摘要。
        :return: 包含 summary, participants 等字段的字典
        """
        ...

    async def is_scene_active(self, scene_run_id: str) -> bool:
        """检查场景是否仍在运行"""
        ...


# ============================================================
# NPC 私语协议
# ============================================================

@runtime_checkable
class ChatterProtocol(Protocol):
    """
    NPC 私语协议 —— 触发两两私语。
    需主项目确认：对应现有 chatter 机制。
    """

    async def trigger_chatter(
        self,
        npc_a: str,
        npc_b: str,
        topic: str = "",
        boost_probability: float = 0.0,
    ) -> None:
        """
        触发两个 NPC 之间的私语。
        :param npc_a: 发起方 NPC
        :param npc_b: 接收方 NPC
        :param topic: 话题提示
        :param boost_probability: 概率提升值 (0.0~1.0)
        """
        ...

    async def trigger_topic(
        self,
        npc_id: str,
        topic: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """
        为单个 NPC 注入一个话题（用于回忆型私语、事件话题偏向等）。
        需主项目确认：阶段二相册"翻看相册"回忆私语使用。
        :param npc_id: NPC 标识
        :param topic: 话题内容
        :param context: 附加上下文（如 photo_id、memory_tags）
        """
        ...


# ============================================================
# NPC 日程协议
# ============================================================

@runtime_checkable
class ScheduleBookProtocol(Protocol):
    """
    NPC 日程协议 —— 查询/覆盖 NPC 日程。
    需主项目确认：对应现有 schedule_book 模块。
    """

    async def get_schedule(self, npc_id: str, game_time: datetime) -> dict[str, Any]:
        """
        查询 NPC 在指定时间的日程。
        :return: 包含 location, activity 等字段的字典
        """
        ...

    async def override_schedule(
        self, npc_id: str, start: datetime, end: datetime,
        location: str, activity: str, reason: str = ""
    ) -> None:
        """
        临时覆盖 NPC 日程（事件用）。
        事件结束后应自动恢复或被新日程覆盖。
        """
        ...

    async def get_location(self, npc_id: str) -> str:
        """获取 NPC 当前位置"""
        ...


# ============================================================
# LLM 客户端协议
# ============================================================

@runtime_checkable
class LLMClientProtocol(Protocol):
    """
    大语言模型客户端协议 —— 统一的 LLM 调用抽象。
    所有需要 LLM 的模块通过此接口调用，测试中使用 mock 实现。
    """

    async def chat(self, messages: list[dict[str, str]]) -> str:
        """
        发送对话请求，返回模型回复文本。
        :param messages: 消息列表，格式 [{"role": "system"|"user"|"assistant", "content": "..."}]
        :return: 模型回复文本
        :raises: 调用失败时抛出异常，调用方需做降级处理
        """
        ...
