"""
RealGroupSceneAdapter + RealScheduleAdapter 测试。

重点：波浪分片调度顺序（每片≤5人、同时≤6片、每节拍放一批）、
event_bus 发布 schedule.npc_command 事件、日程活动写入。
"""

from __future__ import annotations

import pytest

from sensory_world.adapters import (
    RealGroupSceneAdapter,
    RealScheduleAdapter,
)

from .adapter_fakes import FakeScheduleBook


class FakeEventBus:
    """Mock 主项目事件总线，捕获 publish 调用。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def publish(self, event_name: str, payload: dict) -> None:
        self.events.append((event_name, payload))

    def npc_commands(self) -> list[dict]:
        """返回所有 schedule.npc_command 事件的 payload。"""
        return [p for e, p in self.events if e == "schedule.npc_command"]


@pytest.mark.asyncio
async def test_wave_chunks_oversized_group_into_slices():
    """12 人 → 按每片≤5人拆成 3 片，首片主场地、其余片额外场地。"""
    bus = FakeEventBus()
    adapter = RealGroupSceneAdapter(event_bus=bus, group_max_size=5, max_active_groups=6, wave_interval_minutes=3)
    participants = [f"npc{i}" for i in range(12)]
    plan_id = await adapter.start_scene(
        scene_id="fireworks",
        location="square",
        participant_ids=participants,
        topic="烟火大会",
        context={
            "event_name": "周六烟火大会",
            "extra_locations": ["snack_stall", "river_bank", "rooftop"],
            "current_game_minutes": 1000,
        },
    )
    plan = adapter._plans[plan_id]  # type: ignore[attr-defined]
    assert len(plan.slices) == 3
    assert [len(s.participant_ids) for s in plan.slices] == [5, 5, 2]
    assert plan.slices[0].location == "square"
    assert plan.slices[1].location == "snack_stall"
    assert plan.slices[2].location == "river_bank"


@pytest.mark.asyncio
async def test_wave_releases_first_batch_then_throttled():
    """首批放满窗口（≤6片）；后续片需 advance_wave 且间隔到节拍才放。"""
    bus = FakeEventBus()
    # 构造 10 片（每片 5 人），窗口 6
    adapter = RealGroupSceneAdapter(event_bus=bus, group_max_size=5, max_active_groups=6, wave_interval_minutes=3)
    participants = [f"npc{i}" for i in range(50)]  # 10 片
    plan_id = await adapter.start_scene(
        scene_id="big",
        location="square",
        participant_ids=participants,
        topic="大活动",
        context={"event_name": "超大活动", "extra_locations": [f"loc{i}" for i in range(10)],
                 "current_game_minutes": 0},
    )
    plan = adapter._plans[plan_id]  # type: ignore[attr-defined]
    # 首批应放出 6 片（窗口宽度），移动 30 人
    released_first = [s for s in plan.slices if s.released]
    assert len(released_first) == 6
    assert len(bus.npc_commands()) == 30

    # 节拍未到（只过 2 分钟 < 3），不放新片
    await adapter.advance_wave(2)
    assert len([s for s in plan.slices if s.released]) == 6

    # 节拍到（过 3 分钟），放出下一批窗口（剩 4 片一次放完）
    await adapter.advance_wave(3)
    released_all = [s for s in plan.slices if s.released]
    assert len(released_all) == 10
    assert plan.finished is True


@pytest.mark.asyncio
async def test_wave_moves_each_participant_to_slice_location():
    bus = FakeEventBus()
    adapter = RealGroupSceneAdapter(event_bus=bus, group_max_size=5, max_active_groups=6)
    await adapter.start_scene(
        scene_id="s", location="square",
        participant_ids=["a", "b", "c"],
        context={"event_name": "演唱会", "extra_locations": ["stall"], "current_game_minutes": 0},
    )
    commands = bus.npc_commands()
    moved_locs = {cmd["npc_id"]: cmd["target_location"] for cmd in commands}
    assert moved_locs == {"a": "square", "b": "square", "c": "square"}
    # activity 含事件名
    assert all("演唱会" in cmd["activity"] for cmd in commands)


@pytest.mark.asyncio
async def test_wave_topic_injected_via_shared_memory():
    """分片放出时通过记忆适配器写共同记忆（含事件名+人名）。"""
    from sensory_world.adapters import RealMemoryAdapter
    from .adapter_fakes import FakeMemoryStore

    store = FakeMemoryStore()
    mem = RealMemoryAdapter(store)
    bus = FakeEventBus()
    adapter = RealGroupSceneAdapter(event_bus=bus, memory_adapter=mem)
    await adapter.start_scene(
        scene_id="s", location="square",
        participant_ids=["robin", "eden"],
        context={"event_name": "演唱会", "current_game_minutes": 0},
    )
    # 每个参与者都有一条含事件名的共同记忆
    assert any("演唱会" in r["text"] for r in store.stored.get("robin", []))
    assert any("演唱会" in r["text"] for r in store.stored.get("eden", []))


@pytest.mark.asyncio
async def test_end_scene_returns_summary():
    bus = FakeEventBus()
    adapter = RealGroupSceneAdapter(event_bus=bus)
    pid = await adapter.start_scene(
        scene_id="s", location="square", participant_ids=["a", "b"],
        context={"event_name": "测试", "current_game_minutes": 0},
    )
    summary = await adapter.end_scene(pid)
    assert summary["slices_total"] == 1
    assert summary["slices_released"] == 1


# ---------- 日程适配器 ----------

@pytest.mark.asyncio
async def test_schedule_add_activity_free_string():
    book = FakeScheduleBook()
    adapter = RealScheduleAdapter(schedule_book=book)
    ok = adapter.add_activity("march7th", activity="翻看相册", location="shop_camera")
    assert ok is True
    assert len(book.entries) == 1
    assert book.entries[0]["activity"] == "翻看相册"


@pytest.mark.asyncio
async def test_schedule_book_none_uses_event_bus_move():
    """新居民 schedule_book=None：活动写不进，但 event_bus 移动生效（事件聚集）。"""
    bus = FakeEventBus()
    adapter = RealScheduleAdapter(schedule_book=None, event_bus=bus)
    ok = adapter.add_activity("newbie", activity="去广场", location="square")
    assert ok is False  # 日程写不进
    moved = await adapter.gather_to(["newbie", "newbie2"], "square", reason="烟火大会")
    assert moved == 2
    commands = bus.npc_commands()
    assert len(commands) == 2
    assert {cmd["npc_id"] for cmd in commands} == {"newbie", "newbie2"}
    assert all(cmd["target_location"] == "square" for cmd in commands)


@pytest.mark.asyncio
async def test_schedule_move_without_event_bus_safe():
    adapter = RealScheduleAdapter(schedule_book=FakeScheduleBook(), event_bus=None)
    ok = await adapter.move_to("robin", "square", reason="x")
    assert ok is False  # 未注入 event_bus，安全返回 False 不崩


@pytest.mark.asyncio
async def test_schedule_move_publishes_npc_command_with_correct_payload():
    """验证 schedule.npc_command 事件 payload 字段完整性。"""
    bus = FakeEventBus()
    adapter = RealScheduleAdapter(event_bus=bus)
    await adapter.move_to("robin", "square", reason="参加烟火大会")
    commands = bus.npc_commands()
    assert len(commands) == 1
    cmd = commands[0]
    assert cmd["npc_id"] == "robin"
    assert cmd["target_location"] == "square"
    assert cmd["anchor_id"] == "square"
    assert cmd["activity"] == "参加烟火大会"
    assert cmd["lateness_policy"] == "catch_up"
    assert cmd["schedule_condition"] == {}


@pytest.mark.asyncio
async def test_schedule_activity_samples_contain_required_strings():
    """验证日程活动样例包含'翻看相册'和'去邮差小屋寄信'。"""
    book = FakeScheduleBook()
    adapter = RealScheduleAdapter(schedule_book=book)
    # 翻看相册
    ok1 = adapter.add_activity("march7th", activity="翻看相册", location="shop_camera")
    # 去邮差小屋寄信
    ok2 = adapter.add_activity("violet", activity="去邮差小屋寄信", location="post_violet_hut")
    assert ok1 is True
    assert ok2 is True
    activities = [e["activity"] for e in book.entries]
    assert "翻看相册" in activities
    assert "去邮差小屋寄信" in activities
