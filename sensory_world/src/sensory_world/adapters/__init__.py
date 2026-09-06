"""
sensory_world.adapters —— 主项目（小爱世界）集成适配层。

把三阶段功能模块（周期事件 / 邮差相册 / 日历）所依赖的抽象协议，
桥接到主项目真实生产接口。所有适配器**不 import 主项目代码**，
通过依赖注入接收主项目对象（鸭子类型），便于离线单测（全 mock）。

适配器清单：
  - RealClockAdapter     主项目 WorldClock（total_game_minutes）→ GameClock 协议；
                         自维护周历（主项目无周历）、桥接 on_new_* 回调、分钟节流。
  - RealMemoryAdapter    NPCMemoryStore → 记忆协议；高信度写走私有 _append_memory，
                         共同记忆正文强制含事件名+全部在场人名（tags 不参与 RAG）。
  - RealEmotionAdapter   npc_emotion 全同步方法的 async 薄 wrapper。
  - RealGroupSceneAdapter  用"移动调度成群"替代定向开群；波浪状态机限峰
                         （每片≤5人、同时≤6片、每2~3游戏分钟放一批）。
  - RealChatterAdapter   话题注入走记忆正文（零侵入），无强制私语 API。
  - RealDiaryAdapter     WorldDiary 无公开写方法 → 优先调用主项目补丁方法，
                         否则直接追加 data/diary/day-XXXX.md。
  - RealLLMAdapter       OpenAI /v1/chat/completions（8089，Qwen3-8B），
                         超时+重试+失败返空串（配合 SafeLLMClient 模板兜底）。
  - RealScheduleAdapter  自由活动字符串写入日程 + 显式移动聚集（schedule_book=None
                         的新居民只能靠显式移动）。

⚠️ 标注"需主项目确认"的接口见 PATCHES.md / DEPLOY.md。
"""

from __future__ import annotations

from .clock_adapter import (
    EPOCH_REFERENCE,
    MinuteThrottle,
    RealClockAdapter,
    total_minutes_to_datetime,
    total_minutes_to_days,
    total_minutes_to_weekday,
)
from .constants import AdapterDefaults, DEFAULT_DEFAULTS
from .diary_adapter import RealDiaryAdapter
from .emotion_adapter import RealEmotionAdapter
from .group_scene_adapter import (
    RealChatterAdapter,
    RealGroupSceneAdapter,
    WavePlan,
    WaveSlice,
)
from .llm_adapter import RealLLMAdapter, build_safe_llm
from .memory_adapter import RealMemoryAdapter, build_shared_memory_text
from .schedule_adapter import RealScheduleAdapter

__all__ = [
    # 时钟
    "RealClockAdapter",
    "MinuteThrottle",
    "EPOCH_REFERENCE",
    "total_minutes_to_datetime",
    "total_minutes_to_days",
    "total_minutes_to_weekday",
    # 记忆
    "RealMemoryAdapter",
    "build_shared_memory_text",
    # 情绪
    "RealEmotionAdapter",
    # 群聊 / 闲聊
    "RealGroupSceneAdapter",
    "RealChatterAdapter",
    "WavePlan",
    "WaveSlice",
    # 日记
    "RealDiaryAdapter",
    # LLM
    "RealLLMAdapter",
    "build_safe_llm",
    # 日程
    "RealScheduleAdapter",
    # 默认参数
    "AdapterDefaults",
    "DEFAULT_DEFAULTS",
]
