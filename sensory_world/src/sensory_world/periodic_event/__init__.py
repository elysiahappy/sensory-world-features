"""
周期事件系统（PeriodicEventSystem）—— 阶段一

给时间装上刻度：为城市添加周期性大事件的调度能力。

核心模块：
  - scheduler.PeriodicEventSystem: 主调度器（入口）
  - event_runner.EventRunner: 事件执行器（前/中/后三阶段）
  - event_runner.TruantEventChain: 逃课事件链（学园专用）
  - concurrency_guard.ConcurrencyGuard: 并发护栏
  - models: 数据模型
  - event_config: 配置加载
"""

from sensory_world.periodic_event.concurrency_guard import (
    ConcurrencyGuard,
    ConcurrencyRejectError,
    ConcurrencySlot,
    ConcurrencyStats,
    run_with_guard,
)
from sensory_world.periodic_event.event_config import (
    load_all_event_configs,
    load_event_config,
    load_event_configs_from_dicts,
)
from sensory_world.periodic_event.event_runner import EventRunner, TruantEventChain
from sensory_world.periodic_event.models import (
    EventConfig,
    EventInstance,
    EventMemoryPayload,
    EventPhase,
    EventType,
    ParticipationRule,
    SliceConfig,
    SliceGroup,
    TriggerRule,
    TriggerType,
)
from sensory_world.periodic_event.scheduler import PeriodicEventSystem

__all__ = [
    # 主入口
    "PeriodicEventSystem",
    # 执行器
    "EventRunner",
    "TruantEventChain",
    # 并发控制
    "ConcurrencyGuard",
    "ConcurrencyRejectError",
    "ConcurrencySlot",
    "ConcurrencyStats",
    "run_with_guard",
    # 配置加载
    "load_event_config",
    "load_all_event_configs",
    "load_event_configs_from_dicts",
    # 数据模型
    "EventConfig",
    "EventInstance",
    "EventMemoryPayload",
    "EventPhase",
    "EventType",
    "ParticipationRule",
    "SliceConfig",
    "SliceGroup",
    "TriggerRule",
    "TriggerType",
]
