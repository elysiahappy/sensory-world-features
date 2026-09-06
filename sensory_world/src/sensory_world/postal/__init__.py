"""
邮差系统（PostalSystem）—— 阶段二模块一

承载 NPC：薇尔莉特（violet），代人写信、正在学习理解爱的代笔邮差。

核心模块：
  - postal_system.PostalSystem: 邮差系统主入口
  - content_checker.LetterContentChecker: 信件内容审查（不指令铁律）
  - letter_store.LetterStore: 信件 JSONL 持久化
  - models: 信件/城外信箱数据模型
"""

from sensory_world.postal.content_checker import LetterContentChecker
from sensory_world.postal.letter_store import LetterStore
from sensory_world.postal.models import (
    ContentCheckResult,
    Letter,
    LetterDirection,
    LetterStatus,
    OutsideMailbox,
    PostalConfig,
)
from sensory_world.postal.postal_system import PostalSystem

__all__ = [
    "PostalSystem",
    "PostalConfig",
    "Letter",
    "LetterDirection",
    "LetterStatus",
    "OutsideMailbox",
    "ContentCheckResult",
    "LetterContentChecker",
    "LetterStore",
]
