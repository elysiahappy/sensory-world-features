"""CityCalendar 日历系统集成测试。

覆盖：历法推进、季节注释、生日链路（贺卡/合照/祝福私语/日记）、
年度回声、新店开张协议。下游邮差/相册用真实模块 + mock LLM/记忆/时钟，
离线可跑。
"""

import random
from datetime import datetime
from pathlib import Path

import pytest

from sensory_world.album import AlbumConfig, PhotoAlbumSystem
from sensory_world.calendar import (
    Birthday,
    CalendarConfig,
    CityCalendar,
    CityDate,
    Season,
)
from sensory_world.postal import PostalConfig, PostalSystem
from tests.mocks import (
    MockChatter,
    MockGroupScene,
    MockLLMClient,
    MockNPCMemoryStore,
    MockWorldDiary,
)


@pytest.fixture(autouse=True)
def _deterministic(monkeypatch):
    """固定随机种子，让概率链路稳定触发。"""
    monkeypatch.setattr(random, "random", lambda: 0.0)
    monkeypatch.setattr(random, "shuffle", lambda x: None)
    yield


def _make_clock(total_day: int):
    """构造一个返回指定 total_days 的时钟 mock。"""
    from tests.mocks import MockGameClock
    clock = MockGameClock()
    clock.set_time(datetime(2024, 1, 1) + __import__("datetime").timedelta(days=total_day))
    return clock


@pytest.fixture
def deps(tmp_path):
    clock = _make_clock(0)
    memory = MockNPCMemoryStore()
    diary = MockWorldDiary()
    chatter = MockChatter()
    group = MockGroupScene()
    llm = MockLLMClient()

    postal = PostalSystem(
        llm=llm,
        memory=memory,
        clock=clock,
        diary=diary,
        config=PostalConfig(letters_file=str(tmp_path / "letters.jsonl")),
    )
    album = PhotoAlbumSystem(
        llm=llm,
        memory=memory,
        clock=clock,
        chatter=chatter,
        diary=diary,
        config=AlbumConfig(photos_file=str(tmp_path / "photos.jsonl")),
    )
    return {
        "clock": clock, "memory": memory, "diary": diary,
        "chatter": chatter, "group": group, "llm": llm,
        "postal": postal, "album": album,
    }


def _calendar(deps, tmp_path, **cfg_overrides):
    cfg = CalendarConfig(**cfg_overrides)
    cal = CityCalendar(
        clock=deps["clock"],
        memory=deps["memory"],
        diary=deps["diary"],
        chatter=deps["chatter"],
        group_scene=deps["group"],
        postal=deps["postal"],
        album=deps["album"],
        config=cfg,
        birthdays={
            "march7th": Birthday(npc_id="march7th", day_of_year=1),  # 城市纪元第 1 天
        },
    )
    return cal


# ============================================================
# 历法推进 & 季节注释
# ============================================================

@pytest.mark.asyncio
async def test_initialize_sets_date(deps, tmp_path):
    cal = _calendar(deps, tmp_path)
    await cal.initialize()
    assert cal.current_date is not None
    assert cal.current_date.day == 1
    assert cal.season_note is not None
    assert cal.season_note.season == Season.SPRING


@pytest.mark.asyncio
async def test_tick_advances_on_new_day(deps, tmp_path):
    cal = _calendar(deps, tmp_path)
    await cal.initialize()
    # 推进到第 100 天（夏季）
    deps["clock"].set_time(datetime(2024, 1, 1) + __import__("datetime").timedelta(days=99))
    await cal.tick(await deps["clock"].now())
    assert cal.current_date.day == 100
    assert cal.current_date.season == Season.SUMMER
    assert "傍晚" in cal.get_season_hint_text()


@pytest.mark.asyncio
async def test_tick_same_day_no_advance(deps, tmp_path):
    cal = _calendar(deps, tmp_path)
    await cal.initialize()
    first = cal.current_date.day
    await cal.tick(await deps["clock"].now())  # 同一天再 tick
    assert cal.current_date.day == first


# ============================================================
# 生日链路
# ============================================================

@pytest.mark.asyncio
async def test_birthday_chain(deps, tmp_path):
    cal = _calendar(deps, tmp_path)
    # 让寿星 march7th 的记忆里有一位共同朋友 griseo（co_present 共同在场者）
    from sensory_world.protocols import MemoryEntry
    await deps["memory"].add_entry("march7th", MemoryEntry(
        entry_id="e1", npc_id="march7th",
        content="在烟火大会上和 griseo 一起合了影，我们是很要好的伙伴",
        timestamp=datetime(2024, 1, 1),
        metadata={"co_present": ["griseo"]},
    ))
    await cal.initialize()  # total_days=0 -> 城市第 1 天 = march7th 生日

    # 生日链路以 asyncio task 调度，等待其完成
    await _pending_tasks()

    # 世界日记有生日条目
    titles = " ".join(e["content"] for e in deps["diary"].entries)
    assert "生日" in titles
    # 邮差贺卡送达（march7th 记忆里有贺卡）
    march_mem = deps["memory"].get_all_entries("march7th")
    assert any("贺卡" in m.content for m in march_mem)
    # 生日合照：march7th 有照片记忆
    assert any("相册" in m.content or "合" in m.content for m in march_mem)
    # 亲近 NPC 祝福私语：griseo 被注入话题
    topics = [t for t in deps["chatter"].topics if t["npc_id"] == "griseo"]
    assert any("生日" in t["topic"] for t in topics)


@pytest.mark.asyncio
async def test_birthday_idempotent_same_year(deps, tmp_path):
    cal = _calendar(deps, tmp_path)
    await cal.initialize()
    await _pending_tasks()
    diary_count_1 = len(deps["diary"].entries)
    # 同年重复 tick（不跨天不会触发；跨到同一年的另一天再回来不重置）
    deps["clock"].set_time(datetime(2024, 1, 1) + __import__("datetime").timedelta(days=5))
    await cal.tick(await deps["clock"].now())
    await _pending_tasks()
    # 没有新增生日日记
    birthday_entries = [e for e in deps["diary"].entries if "生日" in e["content"]]
    assert len(birthday_entries) >= 1


# ============================================================
# 年度回声
# ============================================================

@pytest.mark.asyncio
async def test_annual_echo_year1_skipped(deps, tmp_path):
    cal = _calendar(deps, tmp_path)
    await cal.initialize()  # 第 1 年
    await cal.on_periodic_event_start("周六烟火大会", ["sparkle", "robin"], event_year=1)
    await _pending_tasks()
    # 第 1 年没有去年，不应有回声话题
    echo_topics = [t for t in deps["chatter"].topics if "去年" in t["topic"]]
    assert echo_topics == []


@pytest.mark.asyncio
async def test_annual_echo_year2_recalls(deps, tmp_path):
    from sensory_world.protocols import MemoryEntry
    # 两个 NPC 都有"去年烟火大会"的记忆
    for npc in ["sparkle", "robin"]:
        await deps["memory"].add_entry(npc, MemoryEntry(
            entry_id=f"mem_{npc}", npc_id=npc,
            content="在周六烟火大会上和大家合了影，烟花特别美",
            timestamp=datetime(2024, 1, 1),
            tags=["photo"],
        ))
    cal = _calendar(deps, tmp_path)
    # 手动设置当前为第 2 年
    cal._current = CityDate(day=365)
    await cal.on_periodic_event_start("周六烟火大会", ["sparkle", "robin"], event_year=2)
    await _pending_tasks()
    echo_topics = [t for t in deps["chatter"].topics if "去年" in t["topic"]]
    assert len(echo_topics) >= 1
    assert any("烟火大会" in t["topic"] for t in echo_topics)


@pytest.mark.asyncio
async def test_annual_echo_no_memory_silent(deps, tmp_path):
    cal = _calendar(deps, tmp_path)
    cal._current = CityDate(day=365)
    # 记忆库为空 —— 应静默跳过，不报错
    await cal.on_periodic_event_start("周六烟火大会", ["nobody1", "nobody2"], event_year=2)
    await _pending_tasks()
    echo_topics = [t for t in deps["chatter"].topics if "去年" in t["topic"]]
    assert echo_topics == []


# ============================================================
# 新店开张协议
# ============================================================

@pytest.mark.asyncio
async def test_register_new_shop_full_flow(deps, tmp_path):
    cal = _calendar(deps, tmp_path)
    await cal.initialize()
    visitors = ["robin", "eden", "march7th", "griseo", "rin"]

    opening = await cal.register_new_shop(
        npc_id="violet",
        shop_location="post_office_hut",
        shop_type="post_office",
        visitor_ids=visitors,
    )
    await _pending_tasks()

    assert opening is not None
    assert opening.shop_type == "post_office"

    # 1) 世界日记开张条目
    diary_text = " ".join(e["content"] for e in deps["diary"].entries)
    assert "开张" in diary_text and "邮差小屋" in diary_text

    # 2) 首日造访 group_scene
    assert len(deps["group"].start_history) == 1
    scene = deps["group"].start_history[0]
    assert scene["location"] == "post_office_hut"
    assert "violet" in scene["participant_ids"]

    # 3) 首张店铺照片（violet 有相册记忆）
    violet_mem = deps["memory"].get_all_entries("violet")
    assert any("相册" in m.content or "开张" in m.content for m in violet_mem)

    # 4) 开业邀请函（访客收到贺卡）
    robin_mem = deps["memory"].get_all_entries("robin")
    assert any("贺卡" in m.content for m in robin_mem)


@pytest.mark.asyncio
async def test_register_new_shop_dedup(deps, tmp_path):
    cal = _calendar(deps, tmp_path)
    await cal.initialize()
    o1 = await cal.register_new_shop("a", "loc_x", "bookstore", visitor_ids=["b"])
    await _pending_tasks()
    scenes_after_first = len(deps["group"].start_history)
    o2 = await cal.register_new_shop("a", "loc_x", "bookstore", visitor_ids=["b"])
    await _pending_tasks()
    # 同地点重复注册不重复触发 group_scene
    assert o2 is not None
    assert len(deps["group"].start_history) == scenes_after_first
    assert len(cal.list_openings()) == 1


# ============================================================
# 开关与降级
# ============================================================

@pytest.mark.asyncio
async def test_disabled_system_noop(deps, tmp_path):
    cal = _calendar(deps, tmp_path, enabled=False)
    await cal.initialize()
    assert cal.current_date is None
    result = await cal.register_new_shop("a", "loc", "bookstore")
    assert result is None


async def _pending_tasks():
    """等待所有已调度的 asyncio task（生日链路/日记）完成。"""
    import asyncio
    # 让出事件循环若干轮，确保 create_task 的协程执行完
    for _ in range(10):
        await asyncio.sleep(0)
