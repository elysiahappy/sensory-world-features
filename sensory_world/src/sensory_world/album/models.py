"""
相册系统（PhotoAlbumSystem）—— 数据模型

承载 NPC：三月七（march7th），热爱拍照的少女。
照片是"记忆的物证"：每张照片给所有在场者写入同一条共享记忆，
形成跨 NPC 的记忆交集。
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ============================================================
# 枚举
# ============================================================

class PhotoSceneType(str, Enum):
    """照片场景类型"""
    PERIODIC_EVENT = "periodic_event"  # 周期事件（阶段一）
    CASUAL_GATHERING = "casual"        # NPC 自发聚会
    BIRTHDAY = "birthday"              # 生日合照（阶段三）
    NEW_SHOP = "new_shop"              # 新店开张（阶段三）
    DAILY = "daily"                    # 日常随机


# ============================================================
# 照片实体
# ============================================================

class Photo(BaseModel):
    """
    一张照片记录。

    每张照片给【所有在场者】的记忆库写入同一条共享记忆，
    这是相册系统的核心价值：未来两个 NPC 私语时可召回共同在场的事件。
    """
    photo_id: str = Field(default_factory=lambda: f"photo_{uuid.uuid4().hex[:10]}")
    photographer_id: str = "march7th"     # 摄影 NPC（三月七）
    timestamp: datetime
    location: str                          # 拍照地点
    scene_type: PhotoSceneType
    event_name: str = ""                   # 关联事件名（如有）
    event_ref: str = ""                    # 关联事件 ID
    participant_ids: list[str] = Field(default_factory=list)  # 在场 NPC
    description: str = ""                  # 一句话画面描述（LLM 生成，模板兜底）
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_jsonl(self) -> str:
        """序列化为 JSONL 一行"""
        return self.model_dump_json()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Photo":
        """从字典（含 type 字段）反序列化"""
        data = dict(data)
        if isinstance(data.get("timestamp"), str):
            data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        return cls(**data)


# ============================================================
# 相册配置
# ============================================================

class AlbumConfig(BaseModel):
    """相册系统配置（可从 YAML 加载）"""
    # 整体开关
    enabled: bool = True

    # 摄影 NPC
    photographer_npc_id: str = "march7th"

    # 持久化文件
    photos_file: str = "data/album/photos.jsonl"

    # 每个周期事件拍照张数范围（含两端）
    event_photo_min: int = 1
    event_photo_max: int = 3

    # 日常随机拍照频率（每次 tick 触发概率，低频）
    daily_photo_probability: float = 0.02

    # NPC 自发聚会拍照概率
    casual_photo_probability: float = 0.5

    # 日常拍照需要的最少在场人数
    daily_min_participants: int = 3

    # 翻看相册触发回忆的概率（NPC 日程"翻看相册"时）
    browse_recall_probability: float = 0.6

    # LLM 生成照片描述失败时是否使用模板兜底
    use_template_fallback: bool = True


# ============================================================
# 拍照计划（内部）
# ============================================================

def roll_event_photo_count(config: AlbumConfig, rng: random.Random | None = None) -> int:
    """
    一次事件拍几张照片（1-3 张，可配置）。
    多张照片分别捕捉不同时刻/分片，形成活动的连续记录。
    """
    r = rng or random
    lo = max(1, config.event_photo_min)
    hi = max(lo, config.event_photo_max)
    return r.randint(lo, hi)
