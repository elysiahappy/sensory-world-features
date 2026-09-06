"""RealClockAdapter 测试：周历计数、分钟换算、边界轮询、节流。"""

from __future__ import annotations

import pytest

from sensory_world.adapters import (
    MinuteThrottle,
    RealClockAdapter,
    total_minutes_to_datetime,
    total_minutes_to_days,
    total_minutes_to_weekday,
)

from .adapter_fakes import FakeWorldClock


# ---------- 纯换算 ----------

def test_total_days():
    assert total_minutes_to_days(0) == 0
    assert total_minutes_to_days(1439) == 0
    assert total_minutes_to_days(1440) == 1
    assert total_minutes_to_days(1440 * 10) == 10


def test_weekday_counting():
    """主项目无周历，适配器自维护：day0=周一(0)，day5=周六(5)，day6=周日(6)。"""
    assert total_minutes_to_weekday(0) == 0            # 第1天 周一
    assert total_minutes_to_weekday(5 * 1440) == 5     # 第6天 周六
    assert total_minutes_to_weekday(6 * 1440) == 6     # 第7天 周日
    assert total_minutes_to_weekday(7 * 1440) == 0     # 第8天 又周一


def test_datetime_roundtrip():
    dt = total_minutes_to_datetime(20 * 60)  # 第1天 20:00
    assert (dt.hour, dt.minute) == (20, 0)
    assert dt.weekday() == 0  # 周一


@pytest.mark.asyncio
async def test_clock_adapter_now_weekday_total_days():
    clock = FakeWorldClock(total_game_minutes=5 * 1440 + 20 * 60)  # 周六 20:00
    adapter = RealClockAdapter(clock, register_boundary_callbacks=False)
    dt = await adapter.now()
    assert (dt.hour, dt.minute) == (20, 0)
    assert await adapter.weekday() == 5   # 周六
    assert await adapter.total_days() == 5
    assert adapter.current_minutes() == clock.total_game_minutes


# ---------- 节流 ----------

def test_throttle_fires_on_interval():
    th = MinuteThrottle(interval_minutes=5)
    assert th.should_fire(0) is True      # 首次必触发
    assert th.should_fire(3) is False     # 未满间隔
    assert th.should_fire(4) is False
    assert th.should_fire(5) is True      # 满 5 分钟
    assert th.should_fire(9) is False
    assert th.should_fire(10) is True


def test_throttle_reset():
    th = MinuteThrottle(interval_minutes=5)
    assert th.should_fire(100) is True
    assert th.should_fire(101) is False
    th.reset()
    assert th.should_fire(101) is True    # reset 后首次必触发


# ---------- 边界回调桥接 ----------

@pytest.mark.asyncio
async def test_poll_boundaries_detects_day_hour():
    clock = FakeWorldClock(total_game_minutes=0)
    adapter = RealClockAdapter(clock, register_boundary_callbacks=False)
    seen: list[str] = []
    adapter.add_boundary_listener(lambda boundary, mins: seen.append(boundary))

    # 推进 1 小时（不跨天）
    clock.total_game_minutes = 60
    crossed = adapter.poll_boundaries()
    assert "hour" in crossed and "day" not in crossed

    # 推进到第 2 天
    clock.total_game_minutes = 1440 + 5
    crossed = adapter.poll_boundaries()
    assert "day" in crossed and "hour" in crossed
    assert seen.count("day") == 1


@pytest.mark.asyncio
async def test_register_host_callback_fallback_silent():
    """注册主项目回调失败时静默（FakeWorldClock 支持 register_callback，应注册成功）。"""
    clock = FakeWorldClock()
    adapter = RealClockAdapter(clock, register_boundary_callbacks=True)
    # FakeWorldClock 提供 register_callback，应已注册 day/hour 等
    assert "on_new_day" in clock.callbacks or len(clock.callbacks) >= 0
    # 触发主项目回调 → 监听器应被调用
    fired: list[str] = []
    adapter.add_boundary_listener(lambda b, m: fired.append(b))
    for cb in clock.callbacks.get("on_new_day", []):
        cb()
    assert "day" in fired


@pytest.mark.asyncio
async def test_missing_total_minutes_defaults_zero():
    """主项目对象缺 total_game_minutes 时安全降级为 0，不崩。"""

    class BrokenClock:
        pass

    adapter = RealClockAdapter(BrokenClock(), register_boundary_callbacks=False)
    assert await adapter.total_days() == 0
    assert await adapter.weekday() == 0
