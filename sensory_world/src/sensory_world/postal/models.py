"""
邮差系统（PostalSystem）—— 数据模型

定义信件、城外信箱、回信链路等核心数据结构。

世界观设定：
  - 城外地址包装为"城市边缘一个没有寄件人姓名的旧邮筒"
  - 外部来信的寄件人统一标识为"城外的朋友"
  - 代码与信件文本中严禁出现"用户/管理员/造物主/玩家"等破墙词汇
  - NPC 间逐渐流传"有个守护这座城的人"的城市传说

三条铁律（代码层强制）：
  1. 稀疏：外部寄信有周配额限制；NPC 寄往城外需事件由头 + 概率触发
  2. 平权：城外信进入记忆库时与普通记忆同权重，不打特殊标记
  3. 不指令：含命令式内容的信件拒收并记录
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ============================================================
# 枚举类型
# ============================================================

class LetterDirection(str, enum.Enum):
    """信件方向"""
    NPC_TO_NPC = "npc_to_npc"       # NPC 之间通信
    OUTSIDE_IN = "outside_in"       # 城外 → 城内（外部来信）
    NPC_TO_OUTSIDE = "npc_to_outside"  # 城内 → 城外（NPC 回信给城外的朋友）


class LetterStatus(str, enum.Enum):
    """信件状态"""
    DRAFT = "draft"             # 草稿（邮差正在代写）
    DELIVERED = "delivered"     # 已送达
    REJECTED = "rejected"       # 被拒收（内容审查不通过）
    PENDING = "pending"         # 待投递（城外发件箱，等待外部取走）


# ============================================================
# 配置模型
# ============================================================

class PostalConfig(BaseModel):
    """
    邮差系统配置。
    通过 YAML 或构造函数注入。
    """
    # 邮差 NPC 标识
    postman_npc_id: str = Field(default="violet", description="邮差 NPC 标识")

    # 城外信箱配置
    outside_weekly_quota: int = Field(
        default=3, ge=1,
        description="外部寄信周配额（每游戏周最多收到多少封城外信）"
    )
    npc_outside_reply_probability: float = Field(
        default=0.15, ge=0.0, le=1.0,
        description="NPC 在有事件由头时主动寄信往城外的概率"
    )

    # 回信链路配置
    reply_window_days: int = Field(
        default=3, ge=1,
        description="收信后多少天内可能触发回信"
    )
    reply_probability: float = Field(
        default=0.4, ge=0.0, le=1.0,
        description="收到信后在窗口期内回信的概率"
    )

    # 内容审查
    content_check_enabled: bool = Field(
        default=True,
        description="是否启用命令式内容检查"
    )

    # 数据文件路径（相对于工作目录）
    letters_file: str = Field(
        default="data/postal/letters.jsonl",
        description="信件持久化文件路径"
    )

    # 系统开关
    enabled: bool = Field(default=True)


# ============================================================
# 运行时数据模型
# ============================================================

@dataclass
class Letter:
    """
    信件实体 —— 一封信的完整记录。

    字段说明：
      letter_id: 唯一标识
      direction: 信件方向（NPC间/城外入/出城）
      sender_id: 寄信方 NPC 标识（城外信固定为 "outside_friend"）
      recipient_id: 收信方 NPC 标识
      postman_id: 邮差 NPC 标识（通常是 violet）
      subject: 信件主题/缘由
      body: 信件正文（LLM 生成或模板兜底）
      reason: 写信缘由（触发原因）
      status: 信件状态
      event_ref: 关联事件 ID（如有事件由头）
      created_at: 创建时间（游戏时间）
      delivered_at: 送达时间
      metadata: 附加元数据
    """
    letter_id: str = field(default_factory=lambda: f"letter_{uuid.uuid4().hex[:10]}")
    direction: LetterDirection = LetterDirection.NPC_TO_NPC
    sender_id: str = ""
    recipient_id: str = ""
    postman_id: str = "violet"
    subject: str = ""
    body: str = ""
    reason: str = ""
    status: LetterStatus = LetterStatus.DRAFT
    event_ref: str = ""
    created_at: datetime | None = None
    delivered_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_jsonl_dict(self) -> dict[str, Any]:
        """序列化为 JSONL 可存储的字典"""
        return {
            "letter_id": self.letter_id,
            "direction": self.direction.value,
            "sender_id": self.sender_id,
            "recipient_id": self.recipient_id,
            "postman_id": self.postman_id,
            "subject": self.subject,
            "body": self.body,
            "reason": self.reason,
            "status": self.status.value,
            "event_ref": self.event_ref,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "delivered_at": self.delivered_at.isoformat() if self.delivered_at else None,
            "metadata": self.metadata,
        }

    @classmethod
    def from_jsonl_dict(cls, data: dict[str, Any]) -> Letter:
        """从 JSONL 字典反序列化"""
        return cls(
            letter_id=data.get("letter_id", f"letter_{uuid.uuid4().hex[:10]}"),
            direction=LetterDirection(data.get("direction", "npc_to_npc")),
            sender_id=data.get("sender_id", ""),
            recipient_id=data.get("recipient_id", ""),
            postman_id=data.get("postman_id", "violet"),
            subject=data.get("subject", ""),
            body=data.get("body", ""),
            reason=data.get("reason", ""),
            status=LetterStatus(data.get("status", "delivered")),
            event_ref=data.get("event_ref", ""),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None,
            delivered_at=datetime.fromisoformat(data["delivered_at"]) if data.get("delivered_at") else None,
            metadata=data.get("metadata", {}),
        )


@dataclass
class OutsideMailbox:
    """
    城外信箱 —— 世界观包装为"城市边缘的旧邮筒"。

    管理：
      - 外部来信收件箱（等待邮差发现并投递）
      - NPC 发往城外的发件箱（等待外部取走）
      - 周配额计数
    """
    # 世界观中城外寄信人的统一标识（禁止出现"用户/管理员"等破墙词）
    OUTSIDE_SENDER_ID: str = "outside_friend"
    OUTSIDE_SENDER_DISPLAY: str = "城外的朋友"

    # 收件箱：等待邮差取走投递的城外信
    inbox: list[Letter] = field(default_factory=list)
    # 发件箱：NPC 寄往城外、等待外部取走的信
    outbox: list[Letter] = field(default_factory=list)
    # 本周已收到的城外信数量（配额计数）
    weekly_received_count: int = 0
    # 当前配额周期的起始时间
    quota_period_start: datetime | None = None

    def has_quota(self, quota: int) -> bool:
        """检查本周是否还有收件配额"""
        return self.weekly_received_count < quota

    def reset_quota(self, period_start: datetime) -> None:
        """重置周配额（新的一周开始时调用）"""
        self.weekly_received_count = 0
        self.quota_period_start = period_start


@dataclass
class ContentCheckResult:
    """内容审查结果"""
    passed: bool
    reason: str = ""
    matched_patterns: list[str] = field(default_factory=list)
