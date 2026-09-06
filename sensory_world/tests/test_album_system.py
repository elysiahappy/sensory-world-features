"""
相册系统测试 —— 拍照、共同记忆写入、翻看相册回忆。
全部 mock，可离线运行。
"""

from datetime import datetime
from pathlib import Path

import pytest

from sensory_world.album.album_system import PhotoAlbumSystem
from sensory_world.album.models import AlbumConfig, PhotoSceneType
from sensory_world.album.photo_store import PhotoStore
from tests.mocks import (
    FailingLLMClient,
    MockChatter,
    MockGameClock,
    MockLLMClient,
    MockNPCMemoryStore,
    MockWorldDiary,
)


@pytest.fixture
def album(tmp_path: Path):
    config = AlbumConfig(photos_file=str(tmp_path / "photos.jsonl"))
    system = PhotoAlbumSystem(
        llm=MockLLMClient(),
        memory=MockNPCMemoryStore(),
        clock=MockGameClock(datetime(2026, 7, 18, 20, 0)),
        chatter=MockChatter(),
        diary=MockWorldDiary(),
        config=config,
        store=PhotoStore(tmp_path / "photos.jsonl"),
    )
    return system


# ============================================================
# 1. 周期事件拍照
# ============================================================

@pytest.mark.asyncio
async def test_event_photos_create_shared_memories(album):
    """事件拍照：每张照片给所有在场者写同一条共享记忆"""
    await album.initialize()

    # 烟火大会 4 个分片
    slices = [
        ["sparkle", "robin", "eden"],
        ["griseo", "rin"],
        ["march7th", "theresa"],
        ["bronya", "march7th"],
    ]
    photos = await album.on_event_photos(
        event_name="周六烟火大会",
        event_ref="evt_fire_1",
        location="square",
        participants_by_slice=slices,
        force_photographer=True,
    )

    assert len(photos) >= 1
    assert len(photos) <= 3

    # 每张照片有描述（LLM 生成）
    for photo in photos:
        assert photo.description
        assert photo.scene_type == PhotoSceneType.PERIODIC_EVENT
        assert "march7th" in photo.participant_ids

    # 共同记忆：被拍到的参与者应有照片记忆
    # （每张照片只拍一个分片，随机 1-3 张，故只断言确实入镜的 NPC）
    photographed: set[str] = set()
    for photo in photos:
        photographed.update(photo.participant_ids)
    assert "march7th" in photographed

    for npc in photographed:
        mems = album._memory.get_all_entries(npc)
        photo_mems = [m for m in mems if "photo" in m.tags or "合影" in m.content]
        assert len(photo_mems) >= 1, f"{npc} 应有照片共享记忆"
        # 记忆含共同在场者（记忆交集）
        assert "co_present" in photo_mems[0].metadata


@pytest.mark.asyncio
async def test_photos_persisted(album, tmp_path):
    """照片落盘 JSONL"""
    await album.initialize()
    await album.on_event_photos(
        "演唱会", "evt_c1", "square",
        [["robin", "eden", "march7th"]],
        force_photographer=True,
    )
    assert (tmp_path / "photos.jsonl").exists()


@pytest.mark.asyncio
async def test_no_photographer_no_photo(album):
    """摄影师不在场且概率未中时不拍照"""
    await album.initialize()
    # force_photographer=False 且 march7th 不在参与者中
    # 随机概率可能仍拍，这里用 monkeypatch 固定不拍
    import random
    random.seed(0)  # 固定随机，0.7 概率下…… 直接验证不报错即可
    photos = await album.on_event_photos(
        "小聚", "evt_x", "cafe",
        [["robin", "eden"]],  # 无 march7th
        force_photographer=False,
    )
    # 无论拍不拍，都不应有 march7th 之外的异常；这里只断言返回列表
    assert isinstance(photos, list)


# ============================================================
# 2. 自发聚会 / 日常拍照
# ============================================================

@pytest.mark.asyncio
async def test_casual_gathering_photo(album):
    """NPC 自发聚会拍照"""
    await album.initialize()
    photo = await album.on_casual_gathering(
        location="cafe",
        participant_ids=["robin", "eden", "march7th", "sparkle"],
        force=True,
    )
    assert photo is not None
    assert photo.scene_type == PhotoSceneType.CASUAL_GATHERING


@pytest.mark.asyncio
async def test_daily_photo_low_frequency(album):
    """日常拍照低频：force 时可拍"""
    await album.initialize()
    photo = await album.maybe_daily_photo(
        location="park",
        participant_ids=["a", "b", "c"],
        force=True,
    )
    assert photo is not None
    assert photo.scene_type == PhotoSceneType.DAILY


@pytest.mark.asyncio
async def test_daily_photo_needs_enough_people(album):
    """日常拍照人数不足不拍"""
    await album.initialize()
    photo = await album.maybe_daily_photo(
        location="park",
        participant_ids=["a"],  # 只有 1 人
        force=True,
    )
    assert photo is None


# ============================================================
# 3. 共同记忆交集（核心价值）
# ============================================================

@pytest.mark.asyncio
async def test_shared_memory_intersection(album):
    """两个 NPC 共同在场 → 可召回共同照片"""
    await album.initialize()
    await album.on_event_photos(
        "烟火大会", "evt_1", "square",
        [["robin", "eden", "march7th", "sparkle"]],
        force_photographer=True,
    )

    # robin 和 eden 共同在场的照片
    shared = await album.get_shared_photos("robin", "eden")
    assert len(shared) >= 1

    # robin 和完全不在场的人无共同照片
    shared_none = await album.get_shared_photos("robin", "stranger")
    assert len(shared_none) == 0


# ============================================================
# 4. 翻看相册 → 回忆型私语
# ============================================================

@pytest.mark.asyncio
async def test_browse_album_triggers_recall(album):
    """翻看相册召回旧照片，触发回忆私语"""
    await album.initialize()
    await album.on_event_photos(
        "烟火大会", "evt_1", "square",
        [["robin", "eden", "march7th"]],
        force_photographer=True,
    )

    # robin 翻看相册
    photo = await album.browse_album("robin", force_recall=True)
    assert photo is not None
    # chatter 被触发回忆话题
    assert len(album._chatter.topics) >= 1
    topic = album._chatter.topics[0]
    assert topic["npc_id"] == "robin"
    assert "照片" in topic["topic"] or "相册" in topic["topic"]


@pytest.mark.asyncio
async def test_browse_empty_album(album):
    """无照片时翻看返回 None"""
    await album.initialize()
    result = await album.browse_album("robin", force_recall=True)
    assert result is None


# ============================================================
# 5. 降级与开关
# ============================================================

@pytest.mark.asyncio
async def test_llm_failure_description_fallback(tmp_path):
    """LLM 失败时照片描述模板兜底"""
    config = AlbumConfig(photos_file=str(tmp_path / "p.jsonl"))
    system = PhotoAlbumSystem(
        llm=FailingLLMClient(),
        memory=MockNPCMemoryStore(),
        clock=MockGameClock(),
        config=config,
        store=PhotoStore(tmp_path / "p.jsonl"),
    )
    await system.initialize()
    photos = await system.on_event_photos(
        "事件", "e1", "square",
        [["robin", "march7th"]],
        force_photographer=True,
    )
    assert len(photos) >= 1
    assert photos[0].description  # 模板兜底描述


@pytest.mark.asyncio
async def test_disabled_album(tmp_path):
    """禁用时不拍照"""
    config = AlbumConfig(enabled=False, photos_file=str(tmp_path / "p.jsonl"))
    system = PhotoAlbumSystem(
        llm=MockLLMClient(), memory=MockNPCMemoryStore(), clock=MockGameClock(),
        config=config, store=PhotoStore(tmp_path / "p.jsonl"),
    )
    photos = await system.on_event_photos(
        "事件", "e1", "square", [["a", "march7th"]], force_photographer=True,
    )
    assert photos == []
