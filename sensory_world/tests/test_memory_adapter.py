"""RealMemoryAdapter 测试：私有高信度写入、共同记忆正文含人名/事件名、召回包装。"""

from __future__ import annotations

from datetime import datetime

import pytest

from sensory_world.adapters import RealMemoryAdapter, build_shared_memory_text
from sensory_world.protocols import MemoryEntry

from .adapter_fakes import FakeMemoryStore


NAME_MAP = {"sparkle": "花火", "robin": "知更鸟", "eden": "伊甸", "march7th": "三月七"}


def make_adapter() -> RealMemoryAdapter:
    store = FakeMemoryStore()
    return RealMemoryAdapter(store, name_resolver=lambda nid: NAME_MAP.get(nid, nid))


def test_shared_memory_text_contains_event_and_all_names():
    """tags 不参与 RAG，正文必须含事件名 + 全部在场人名。"""
    text = build_shared_memory_text(
        "周六烟火大会", "广场", ["花火", "知更鸟", "伊甸"], kind="photo"
    )
    assert "周六烟火大会" in text
    for name in ("花火", "知更鸟", "伊甸"):
        assert name in text


@pytest.mark.asyncio
async def test_add_entry_uses_private_append_high_importance():
    adapter = make_adapter()
    store: FakeMemoryStore = adapter._store  # type: ignore[attr-defined]
    entry = MemoryEntry(
        entry_id="e1", npc_id="robin", content="参加了烟火大会",
        timestamp=datetime.now(), confidence=0.9, tags=["event"], metadata={"x": 1},
    )
    await adapter.add_entry("robin", entry)
    assert len(store.append_calls) == 1
    npc_id, text, importance, _tags, _meta = store.append_calls[0]
    assert npc_id == "robin"
    assert "烟火大会" in text
    assert importance == 0.9  # 高信度


@pytest.mark.asyncio
async def test_write_shared_memory_resolves_names_and_co_present():
    adapter = make_adapter()
    store: FakeMemoryStore = adapter._store  # type: ignore[attr-defined]
    present = ["sparkle", "robin", "eden"]
    adapter.write_shared_memory(
        "robin", event_name="周六烟火大会", location="广场",
        present_npc_ids=present, kind="photo",
    )
    rec = store.stored["robin"][0]
    # 正文含全部人名（中文）与事件名
    for name in ("花火", "知更鸟", "伊甸", "周六烟火大会"):
        assert name in rec["text"]
    # metadata 带 co_present id 列表
    assert set(rec["metadata"]["co_present"]) == set(present)


@pytest.mark.asyncio
async def test_recall_async_wrapper_hits_by_text_not_tags():
    """召回是同步 retrieve_texts 的 async 包装；靠正文文本命中（tags 不参与）。"""
    adapter = make_adapter()
    adapter.append_high_confidence(
        "robin",
        "我参加了周六烟火大会，和花火、伊甸一起合影",
        tags=["photo"],
    )
    # 用事件名查询能召回
    results = await adapter.recall("robin", "周六烟火大会 合影", top_k=3)
    assert len(results) == 1
    assert "烟火大会" in results[0].content
    assert results[0].npc_id == "robin"


@pytest.mark.asyncio
async def test_recall_failure_returns_empty():
    """记忆库异常时召回静默返回空列表，不抛异常。"""

    class BoomStore(FakeMemoryStore):
        def retrieve_texts(self, npc_id, query, top_k=5):  # type: ignore[override]
            raise RuntimeError("RAG 挂了")

    adapter = RealMemoryAdapter(BoomStore())
    assert await adapter.recall("x", "q") == []


def test_signature_fallback_when_kwargs_unsupported():
    """主项目 _append_memory 只接受 (npc_id, text) 时，逐级降级仍能写入。"""

    class PositionalOnlyStore(FakeMemoryStore):
        def _append_memory(self, npc_id, text):  # type: ignore[override]
            # 不接受任何关键字参数
            self.stored.setdefault(npc_id, []).append({"text": text})  # type: ignore[attr-defined]

    store = PositionalOnlyStore()
    adapter = RealMemoryAdapter(store)
    adapter.append_high_confidence("robin", "一条记忆", importance=0.95, tags=["a"], metadata={"k": 1})
    assert store.stored["robin"][0]["text"] == "一条记忆"
