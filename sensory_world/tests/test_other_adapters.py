"""RealEmotion / RealChatter / RealDiary 适配器测试。"""

from __future__ import annotations

import os

import pytest

from sensory_world.adapters import (
    RealChatterAdapter,
    RealDiaryAdapter,
    RealEmotionAdapter,
)

from .adapter_fakes import (
    FakeDiaryNoWriter,
    FakeDiaryWithWriter,
    FakeEmotionSystem,
    FakeMemoryStore,
)
from sensory_world.adapters import RealMemoryAdapter


# ---------- 情绪：同步方法 async wrapper ----------

@pytest.mark.asyncio
async def test_emotion_sync_methods_wrapped():
    emo = FakeEmotionSystem()
    adapter = RealEmotionAdapter(emo)
    # inject 映射到 update_emotion（duration 被忽略）
    await adapter.inject_emotion("robin", "anticipation", intensity=0.8, reason="烟火大会")
    assert emo.emotions["robin"]["anticipation"] == 0.8
    # get_emotion
    state = await adapter.get_emotion("robin")
    assert state == {"anticipation": 0.8}
    # record_proximity
    await adapter.record_proximity("robin", "eden", 1.0)
    assert ("robin", "eden", 1.0) in emo.proximity


@pytest.mark.asyncio
async def test_emotion_failure_silent():
    class BoomEmotion(FakeEmotionSystem):
        def update_emotion(self, *a, **k):  # type: ignore[override]
            raise RuntimeError("情绪系统崩了")

    adapter = RealEmotionAdapter(BoomEmotion())
    # 不抛异常
    await adapter.inject_emotion("x", "joy")
    assert await adapter.get_emotion("nobody") == {}


# ---------- 闲聊：话题走记忆正文（零侵入） ----------

@pytest.mark.asyncio
async def test_chatter_topic_injected_via_memory():
    mem_store = FakeMemoryStore()
    mem = RealMemoryAdapter(mem_store)
    chatter = RealChatterAdapter(memory_adapter=mem)
    await chatter.trigger_topic("robin", "去年烟火大会的烟花真美", {"photo": "p1"})
    # 记忆正文含话题
    rec = mem_store.stored["robin"][0]
    assert "去年烟火大会" in rec["text"]
    assert chatter.triggered_topics[0]["npc_id"] == "robin"


@pytest.mark.asyncio
async def test_chatter_pair_records_proximity():
    emo = FakeEmotionSystem()
    chatter = RealChatterAdapter(memory_adapter=None, emotion_adapter=RealEmotionAdapter(emo))
    await chatter.trigger_chatter("robin", "eden", topic="演唱会", boost_probability=0.9)
    # boost_probability 被忽略（主项目无入口），但相处被记录
    assert ("robin", "eden", 1.0) in emo.proximity


# ---------- 日记：双方案 ----------

@pytest.mark.asyncio
async def test_diary_writer_prefers_host_public_method(tmp_path):
    host = FakeDiaryWithWriter()
    adapter = RealDiaryAdapter(world_diary=host, diary_dir=str(tmp_path / "diary"))
    await adapter.write_entry("birthday", "今天是三月七的生日")
    # 走主项目公开方法
    assert host.entries and "birthday" in host.entries[0]
    # 没有产生本地文件
    assert not (tmp_path / "diary").exists() or not any((tmp_path / "diary").iterdir())


@pytest.mark.asyncio
async def test_diary_fallback_appends_day_file(tmp_path):
    from sensory_world.adapters import RealClockAdapter

    class Clock:
        total_game_minutes = 3 * 1440 + 9 * 60  # 第4天 09:00

    clock = RealClockAdapter(Clock(), register_boundary_callbacks=False)
    diary_dir = tmp_path / "diary"
    adapter = RealDiaryAdapter(
        world_diary=FakeDiaryNoWriter(),  # 无公开写方法
        diary_dir=str(diary_dir),
        clock_adapter=clock,
    )
    await adapter.write_entry("event_summary", "烟火大会圆满结束")
    await adapter.add_event_entry("新店开张", category="new_shop")

    # day-0004.md（第4天）
    files = list(diary_dir.glob("day-*.md"))
    assert len(files) == 1
    assert os.path.basename(files[0]) == "day-0004.md"
    content = files[0].read_text(encoding="utf-8")
    assert "烟火大会圆满结束" in content
    assert "新店开张" in content
    assert "09:0" in content  # 时间戳
    # 追加而非覆盖
    assert content.count("【城市记事】") == 2
