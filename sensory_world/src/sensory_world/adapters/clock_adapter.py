"""
RealClockAdapter —— 主项目 WorldClock → GameClock 协议的适配。

职责：
  1. 把主项目唯一真相源 ``total_game_minutes``（整数游戏分钟，5x 速度，
     1 游戏天 ≈ 4.8 真实分钟）换算成三阶段功能模块需要的：
       - async now() -> datetime
       - async weekday() -> int   （主项目**无周历**，由本适配器自维护周计数）
       - async total_days() -> int
  2. 桥接主项目 on_new_hour/day/season/year 同步回调（可选，注册失败静默）。
  3. 提供 tick 内的"按游戏分钟节流"判断（事件系统不需要 10Hz）。

⚠️ 主项目有"季=28 天"的概念，但功能模块 CityCalendar 自带 90 天季节注释，
   两者互不冲突：本适配器只透传边界回调，季节注释以 CityCalendar 为准。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..protocols import GameClockProtocol

logger = logging.getLogger(__name__)


MINUTES_PER_DAY = 1440
# 城市纪元锚点：total_game_minutes=0 对应这个真实日历时刻（取周一）。
# 主项目 WorldClock 若直接暴露当前 datetime 则优先用其值；否则用此锚点反推。
EPOCH_REFERENCE = datetime(2024, 1, 1, 0, 0, 0)  # 2024-01-01 是周一


# ============================================================
# 纯换算函数（不依赖时钟对象，便于离线单测）
# ============================================================

def total_minutes_to_days(total_game_minutes: int) -> int:
    """累计游戏分钟 → 累计经过的完整游戏天数（第 1 天为 0）。"""
    return max(0, total_game_minutes) // MINUTES_PER_DAY


def total_minutes_to_datetime(
    total_game_minutes: int,
    epoch: datetime = EPOCH_REFERENCE,
) -> datetime:
    """
    累计游戏分钟 → datetime（用锚点 + 分钟偏移反推）。

    游戏内一天 = 真实一天的时钟面（24h），只是流逝更快；所以直接加
    timedelta(minutes=...) 即可得到正确的"游戏内挂钟时间"与星期。
    """
    return epoch + timedelta(minutes=int(total_game_minutes))


def total_minutes_to_weekday(total_game_minutes: int) -> int:
    """
    累计游戏分钟 → 星期几（0=周一 … 6=周日）。

    主项目无周历，"每周六"由功能模块自维护周计数：
    第 N 天（从 0 起）= 星期 (N % 7)，锚点取周一，故 day%7==5 即周六。
    """
    day = total_minutes_to_days(total_game_minutes)
    return day % 7


@dataclass
class MinuteThrottle:
    """
    按游戏时间分钟变化的节流器（纯逻辑，同步）。

    主循环 10Hz 调 T7，事件/日历系统无需该频率；仅当游戏时间相对上次
    真正推进了 >= interval_minutes 个游戏分钟时，``should_fire`` 才返回 True。
    """

    interval_minutes: int
    _last_fired_minutes: int = -1

    def should_fire(self, current_game_minutes: int) -> bool:
        """是否到达本次触发点。首次调用（_last 为 -1）总是触发一次以初始化。"""
        if self._last_fired_minutes < 0:
            self._last_fired_minutes = current_game_minutes
            return True
        if current_game_minutes - self._last_fired_minutes >= self.interval_minutes:
            self._last_fired_minutes = current_game_minutes
            return True
        return False

    def reset(self) -> None:
        self._last_fired_minutes = -1


class RealClockAdapter:
    """
    WorldClock → GameClockProtocol 适配器。

    :param world_clock: 主项目 WorldClock 实例（鸭子类型，需有
        ``total_game_minutes`` 属性）。
    :param epoch: 可选，游戏分钟=0 对应的真实锚点 datetime。
    :param register_boundary_callbacks: 是否尝试注册 on_new_* 边界回调。
    """

    def __init__(
        self,
        world_clock: object,
        epoch: datetime = EPOCH_REFERENCE,
        register_boundary_callbacks: bool = True,
    ) -> None:
        self._clock = world_clock
        self._epoch = epoch
        # 边界回调监听器列表（外部可订阅）：回调签名 fn(boundary: str, game_minutes: int)
        self._boundary_listeners: list = []
        self._last_minutes = self._read_minutes()
        if register_boundary_callbacks:
            self._try_register_boundary_callbacks()

    # ---------- GameClockProtocol ----------

    async def now(self) -> datetime:
        """返回当前游戏内挂钟时间。优先取主项目暴露的 now/属性，否则锚点反推。"""
        minutes = self._read_minutes()
        # 主项目可能直接暴露 datetime（属性或方法），优先采用以便与其历法对齐
        for attr in ("now_datetime", "current_datetime"):
            val = getattr(self._clock, attr, None)
            dt = val() if callable(val) else val
            if isinstance(dt, datetime):
                return dt
        return total_minutes_to_datetime(minutes, self._epoch)

    async def weekday(self) -> int:
        """游戏内星期几（0=周一…6=周日）。主项目无周历，按游戏天数自维护。"""
        return total_minutes_to_weekday(self._read_minutes())

    async def total_days(self) -> int:
        """自城市启动经过的完整游戏天数（第 1 天为 0）。"""
        return total_minutes_to_days(self._read_minutes())

    # ---------- 同步便捷访问（供波浪调度等同步逻辑使用） ----------

    @property
    def total_game_minutes(self) -> int:
        return self._read_minutes()

    def current_minutes(self) -> int:
        """同步读取当前游戏分钟（状态机调度用，避免反复 await）。"""
        return self._read_minutes()

    # ---------- 边界回调桥接 ----------

    def add_boundary_listener(self, listener) -> None:
        """
        订阅时间边界（hour/day/season/year）。listener 签名：
            fn(boundary: str, game_minutes: int) -> None  （同步；内部勿做重活）
        """
        self._boundary_listeners.append(listener)

    def poll_boundaries(self) -> list[str]:
        """
        在 tick 中轮询分钟变化、检测跨边界并通知监听者。

        主项目回调为同步注册式；为避免集成方注册入口差异，本方法提供
        "轮询兜底"：即使没注册成功，也能靠 total_game_minutes 变化识别
        跨小时/跨天。返回本次跨越的边界名列表。
        """
        minutes = self._read_minutes()
        if minutes == self._last_minutes:
            return []
        old_day, new_day = self._last_minutes // MINUTES_PER_DAY, minutes // MINUTES_PER_DAY
        old_hour, new_hour = self._last_minutes // 60, minutes // 60
        crossed: list[str] = []
        if new_hour != old_hour:
            crossed.append("hour")
        if new_day != old_day:
            crossed.append("day")
        self._last_minutes = minutes
        for boundary in crossed:
            for listener in self._boundary_listeners:
                try:
                    listener(boundary, minutes)
                except Exception:  # noqa: BLE001 - 监听器异常不影响时钟
                    logger.warning("时钟边界监听器异常 boundary=%s", boundary, exc_info=True)
        return crossed

    # ---------- 内部 ----------

    def _read_minutes(self) -> int:
        """安全读取主项目唯一真相源 total_game_minutes。"""
        val = getattr(self._clock, "total_game_minutes", 0)
        try:
            return max(0, int(val))
        except (TypeError, ValueError):
            return 0

    def _try_register_boundary_callbacks(self) -> None:
        """
        尝试把适配器的边界处理注册到主项目 on_new_* 回调。

        ⚠️ 主项目注册器名称未在勘测中最终确认，这里容错探测常见命名，
           全部失败则静默依赖 poll_boundaries() 轮询兜底。
        """
        target = self._clock
        event_map = {
            "hour": ("on_new_hour",),
            "day": ("on_new_day",),
            "season": ("on_new_season",),
            "year": ("on_new_year",),
        }
        for boundary, aliases in event_map.items():
            registered = False
            # 注册器可能叫 register_callback / on / add_listener ...
            for reg_name in ("register_callback", "on", "add_listener", "subscribe"):
                reg = getattr(target, reg_name, None)
                if not callable(reg):
                    continue
                for alias in aliases:
                    try:
                        reg(alias, self._make_host_callback(boundary))
                        registered = True
                        break
                    except Exception:  # noqa: BLE001
                        continue
                if registered:
                    break
            if not registered:
                logger.debug("未能注册主项目 %s 回调，将使用轮询兜底", boundary)

    def _make_host_callback(self, boundary: str):
        """构造桥接主项目同步回调的闭包（签名容错：忽略主项目传入的任意参数）。"""

        def _cb(*_args, **_kwargs) -> None:
            minutes = self._read_minutes()
            for listener in self._boundary_listeners:
                try:
                    listener(boundary, minutes)
                except Exception:  # noqa: BLE001
                    logger.warning("时钟边界监听器异常 boundary=%s", boundary, exc_info=True)

        return _cb
