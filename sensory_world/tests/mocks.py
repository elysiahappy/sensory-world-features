"""
测试 Mock 实现 —— 所有外部依赖的模拟对象

所有测试均可离线运行，不依赖真实 LLM 服务或数据库。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sensory_world.protocols import MemoryEntry


# ============================================================
# Mock LLM 客户端
# ============================================================

class MockLLMClient:
    """模拟 LLM 客户端 —— 返回预设文本"""

    def __init__(self, responses: dict[str, str] | None = None):
        self._responses = responses or {}
        self._default = "这是一段模拟的 LLM 回复。"
        self.call_count = 0
        self.call_history: list[list[dict[str, str]]] = []

    async def chat(self, messages: list[dict[str, str]]) -> str:
        self.call_count += 1
        self.call_history.append(messages)
        user_msg = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_msg = msg.get("content", "")
                break
        for keyword, response in self._responses.items():
            if keyword in user_msg:
                return response
        return self._default


class FailingLLMClient:
    """模拟永远失败的 LLM 客户端"""

    def __init__(self, error_msg: str = "LLM 服务不可用"):
        self._error_msg = error_msg

    async def chat(self, messages: list[dict[str, str]]) -> str:
        raise ConnectionError(self._error_msg)


# ============================================================
# Mock 世界日记
# ============================================================

class MockWorldDiary:
    """模拟世界日记"""

    def __init__(self):
        self.entries: list[dict[str, Any]] = []

    async def write_entry(self, category: str, content: str, **metadata: Any) -> None:
        self.entries.append({
            "category": category,
            "content": content,
            **metadata,
        })


# ============================================================
# Mock NPC 记忆库
# ============================================================

class MockNPCMemoryStore:
    """模拟 NPC 记忆库"""

    def __init__(self):
        self._entries: dict[str, list[MemoryEntry]] = {}

    async def add_entry(self, npc_id: str, entry: MemoryEntry) -> None:
        if npc_id not in self._entries:
            self._entries[npc_id] = []
        self._entries[npc_id].append(entry)

    async def recall(self, npc_id: str, query: str, top_k: int = 5) -> list[MemoryEntry]:
        entries = self._entries.get(npc_id, [])
        return entries[:top_k]

    async def get_entries_by_tag(
        self, npc_id: str, tag: str, limit: int = 10
    ) -> list[MemoryEntry]:
        entries = self._entries.get(npc_id, [])
        return [e for e in entries if tag in e.tags][:limit]

    def get_all_entries(self, npc_id: str) -> list[MemoryEntry]:
        """测试辅助：获取 NPC 的所有记忆"""
        return self._entries.get(npc_id, [])


# ============================================================
# Mock NPC 情绪系统
# ============================================================

class MockNPCEmotion:
    """模拟 NPC 情绪系统"""

    def __init__(self):
        self._emotions: dict[str, dict[str, float]] = {}
        self.injection_history: list[dict[str, Any]] = []

    async def inject_emotion(
        self, npc_id: str, emotion: str, intensity: float = 0.5,
        duration_seconds: float = 300.0, reason: str = ""
    ) -> None:
        if npc_id not in self._emotions:
            self._emotions[npc_id] = {}
        self._emotions[npc_id][emotion] = intensity
        self.injection_history.append({
            "npc_id": npc_id,
            "emotion": emotion,
            "intensity": intensity,
            "reason": reason,
        })

    async def get_emotion(self, npc_id: str) -> dict[str, float]:
        return self._emotions.get(npc_id, {})


# ============================================================
# Mock 群聊场景
# ============================================================

class MockGroupScene:
    """模拟群聊场景管理"""

    def __init__(self):
        self._scenes: dict[str, dict[str, Any]] = {}
        self._counter = 0
        self.start_history: list[dict[str, Any]] = []

    async def start_scene(
        self,
        scene_id: str,
        location: str,
        participant_ids: list[str],
        topic: str = "",
        context: dict[str, Any] | None = None,
    ) -> str:
        self._counter += 1
        run_id = f"scene_run_{self._counter}"
        self._scenes[run_id] = {
            "scene_id": scene_id,
            "location": location,
            "participant_ids": participant_ids,
            "topic": topic,
            "context": context or {},
            "active": True,
        }
        self.start_history.append(self._scenes[run_id])
        return run_id

    async def end_scene(self, scene_run_id: str) -> dict[str, Any]:
        scene = self._scenes.get(scene_run_id, {})
        scene["active"] = False
        return {"summary": "场景已结束", "participants": scene.get("participant_ids", [])}

    async def is_scene_active(self, scene_run_id: str) -> bool:
        scene = self._scenes.get(scene_run_id, {})
        return scene.get("active", False)


# ============================================================
# Mock NPC 私语
# ============================================================

class MockChatter:
    """模拟 NPC 私语"""

    def __init__(self):
        self.chatter_history: list[dict[str, Any]] = []
        self.topics: list[dict[str, Any]] = []

    async def trigger_chatter(
        self,
        npc_a: str,
        npc_b: str,
        topic: str = "",
        boost_probability: float = 0.0,
    ) -> None:
        self.chatter_history.append({
            "npc_a": npc_a,
            "npc_b": npc_b,
            "topic": topic,
            "boost_probability": boost_probability,
        })

    async def trigger_topic(
        self,
        npc_id: str,
        topic: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.topics.append({"npc_id": npc_id, "topic": topic, "context": context or {}})


# ============================================================
# Mock NPC 日程
# ============================================================

class MockScheduleBook:
    """模拟 NPC 日程"""

    def __init__(self):
        self._locations: dict[str, str] = {}
        self._overrides: list[dict[str, Any]] = []

    async def get_schedule(self, npc_id: str, game_time: datetime) -> dict[str, Any]:
        return {
            "npc_id": npc_id,
            "location": self._locations.get(npc_id, "home"),
            "activity": "idle",
        }

    async def override_schedule(
        self, npc_id: str, start: datetime, end: datetime,
        location: str, activity: str, reason: str = ""
    ) -> None:
        self._locations[npc_id] = location
        self._overrides.append({
            "npc_id": npc_id,
            "start": start,
            "end": end,
            "location": location,
            "activity": activity,
            "reason": reason,
        })

    async def get_location(self, npc_id: str) -> str:
        return self._locations.get(npc_id, "home")


# ============================================================
# Mock 游戏时钟
# ============================================================

class MockGameClock:
    """模拟游戏时钟 —— 支持手动推进时间"""

    def __init__(self, start_time: datetime | None = None):
        self._time = start_time or datetime(2024, 1, 6, 19, 0)  # 默认周六 19:00
        # 城市纪元起点（total_days=0）
        self._epoch = datetime(2024, 1, 1, 0, 0)

    async def now(self) -> datetime:
        return self._time

    async def weekday(self) -> int:
        return self._time.weekday()

    async def total_days(self) -> int:
        """自城市纪元起点经过的整天数（第 1 天为 0）"""
        return (self._time - self._epoch).days

    def advance(self, minutes: int = 1) -> None:
        """手动推进时间（测试用）"""
        self._time += timedelta(minutes=minutes)

    def advance_days(self, days: int = 1) -> None:
        """手动推进整天数（测试用）"""
        self._time += timedelta(days=days)

    def set_time(self, dt: datetime) -> None:
        """手动设置时间（测试用）"""
        self._time = dt
