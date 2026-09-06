"""
RealGroupSceneAdapter + RealScheduleAdapter 测试。

重点：波浪分片调度顺序（每片≤5人、同时≤6片、每节拍放一批）、
显式移动聚集、日程活动写入。
"""

from __future__ import annotations

import pytest

from sensory_world.adapters import (
    RealGroupSceneAdapter,
    RealScheduleAdapter,
)

from .adapter_fakes import FakeScheduleBook


class MoveRecorder:
    """记录显式移动命令的 async 回调。"""

    def __init__(self) -> None:
        self.moves: list[tuple[str, str, str]] = []

    async def __call__(self, npc_id: str, location_id: str, reason: str = "") -> None:
        self.moves.append((npc_id, location_id, reason))


@pytest.mark.asyncio
async def test_wave_chunks_oversized_group_into_slices():
    """12 人 → 按每片≤5人拆成 3 片，首片主场地、其余片额外场地。"""
    move = MoveRecorder()
    adapter = RealGroupSceneAdapter(move_fn=move, group_max_size=5, max_active_groups=6, wave_interval_minutes=3)
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
    move = MoveRecorder()
    # 构造 10 片（每片 5 人），窗口 6
    adapter = RealGroupSceneAdapter(move_fn=move, group_max_size=5, max_active_groups=6, wave_interval_minutes=3)
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
    assert len(move.moves) == 30

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
    move = MoveRecorder()
    adapter = RealGroupSceneAdapter(move_fn=move, group_max_size=5, max_active_groups=6)
    await adapter.start_scene(
        scene_id="s", location="square",
        participant_ids=["a", "b", "c"],
        context={"event_name": "演唱会", "extra_locations": ["stall"], "current_game_minutes": 0},
    )
    moved_locs = {npc: loc for npc, loc, _ in move.moves}
    assert moved_locs == {"a": "square", "b": "square", "c": "square"}
    # reason 含事件名
    assert all("演唱会" in reason for _, _, reason in move.moves)


@pytest.mark.asyncio
async def test_wave_topic_injected_via_shared_memory():
    """分片放出时通过记忆适配器写共同记忆（含事件名+人名）。"""
    from sensory_world.adapters import RealMemoryAdapter
    from .adapter_fakes import FakeMemoryStore

    store = FakeMemoryStore()
    mem = RealMemoryAdapter(store)
    move = MoveRecorder()
    adapter = RealGroupSceneAdapter(move_fn=move, memory_adapter=mem)
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
    move = MoveRecorder()
    adapter = RealGroupSceneAdapter(move_fn=move)
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
async def test_schedule_book_none_uses_explicit_move():
    """新居民 schedule_book=None：活动写不进，但显式移动生效（事件聚集）。"""
    move = MoveRecorder()
    adapter = RealScheduleAdapter(schedule_book=None, move_fn=move)
    ok = adapter.add_activity("newbie", activity="去广场", location="square")
    assert ok is False  # 日程写不进
    moved = await adapter.gather_to(["newbie", "newbie2"], "square", reason="烟火大会")
    assert moved == 2
    assert {npc for npc, _, _ in move.moves} == {"newbie", "newbie2"}
    assert all(loc == "square" for _, loc, _ in move.moves)


@pytest.mark.asyncio
async def test_schedule_get_location():
    book = FakeScheduleBook()
    book.locations["robin"] = "square"
    adapter = RealScheduleAdapter(schedule_book=book)
    assert await adapter.get_location("robin") == "square"
    assert await adapter.get_location("nobody") == ""


@pytest.mark.asyncio
async def test_schedule_move_without_callback_safe():
    adapter = RealScheduleAdapter(schedule_book=FakeScheduleBook(), move_fn=None)
    ok = await adapter.move_to("robin", "square", reason="x")
    assert ok is False  # 未绑定移动回调，安全返回 False 不崩
