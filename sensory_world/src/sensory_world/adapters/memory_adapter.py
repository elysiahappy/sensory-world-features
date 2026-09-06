"""
RealMemoryAdapter —— 主项目 NPCMemoryStore → NPCMemoryStoreProtocol 的适配。

关键勘测事实（务必遵守）：
  1. **高信度写入必须直调私有方法** ``_append_memory(npc_id, text, ...)``（L461）。
     公开的 ``maybe_store_thought`` 会被 LLM 判为"不值得记"而改写/丢弃，
     而功能模块写入的是"事件事实/共同记忆"，必须落库。
  2. 召回是**同步** ``retrieve_texts(npc_id, query, top_k)``（L270），本适配器
     包一层 async wrapper。
  3. **tags 字段不参与 RAG 检索**（哈希 embedder 只看 text+importance+近因）。
     ⇒ 共同记忆/co_present 信息必须把**事件名 + 全部在场人名写进记忆正文文本**，
     否则年度回声、相册回忆等基于语义召回的功能将检索不到。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from ..protocols import MemoryEntry, NPCMemoryStoreProtocol

logger = logging.getLogger(__name__)


def build_shared_memory_text(
    event_name: str,
    location: str,
    present_names: list[str],
    self_name: str | None = None,
    kind: str = "event",
) -> str:
    """
    构造"共同记忆"正文。

    ⚠️ 因 tags 不参与 RAG，正文必须显式包含：事件名 + 地点 + **全部在场人名**。
    这样任一在场者日后以"去年{事件名}""和某人合影"等查询都能命中。

    :param event_name: 事件/活动名（如"周六烟火大会"）
    :param location: 地点 id 或中文名
    :param present_names: 在场 NPC 名字列表（含本人）；名字用于语义召回
    :param self_name: 记忆归属者名字（用于语序，可选）
    :param kind: event=事件共同记忆 / photo=合影 / birthday=生日 / shop=开张
    """
    # 去重并保序，去掉空名
    seen: dict[str, None] = {}
    for n in present_names:
        if n:
            seen.setdefault(str(n), None)
    names = list(seen.keys())
    name_blob = "、".join(names)
    if kind == "photo":
        return (
            f"在「{event_name}」上（{location}），我和 {name_blob} 一起合了影，"
            f"照片在三月七的相册里。在场的有：{name_blob}。"
        )
    if kind == "birthday":
        return (
            f"「{event_name}」那天（{location}），大家为 {self_name or '寿星'} 庆祝生日，"
            f"在场的有：{name_blob}。我们一起唱了生日歌、合了照。"
        )
    if kind == "shop":
        return (
            f"「{event_name}」开张那天（{location}），我去凑热闹，"
            f"在场的有：{name_blob}。新店真热闹。"
        )
    # 默认事件共同记忆
    return (
        f"我参加了「{event_name}」（{location}），和 {name_blob} 在一起。"
        f"当时在场的有：{name_blob}。"
    )


class RealMemoryAdapter:
    """NPCMemoryStore → NPCMemoryStoreProtocol 适配器（同步方法包 async）。"""

    def __init__(self, memory_store: object, name_resolver=None) -> None:
        """
        :param memory_store: 主项目 NPCMemoryStore 实例（鸭子类型）。
        :param name_resolver: 可选，npc_id -> 中文名 的解析函数，用于把
            id 转成人名写进记忆正文（RAG 靠人名命中）。签名 (npc_id)->str。
        """
        self._store = memory_store
        self._name_resolver = name_resolver

    # ---------- NPCMemoryStoreProtocol ----------

    async def add_entry(self, npc_id: str, entry: MemoryEntry) -> None:
        """写入一条记忆。映射到主项目私有方法 _append_memory（高信度落库）。"""
        tags = list(entry.tags or [])
        metadata = dict(entry.metadata or {})
        self._safe_append(
            npc_id,
            entry.content,
            importance=float(entry.confidence or 0.8),
            tags=tags,
            metadata=metadata,
        )

    async def recall(self, npc_id: str, query: str, top_k: int = 5) -> list[MemoryEntry]:
        """语义召回。retrieve_texts 是同步，这里包 async 并归一化为 MemoryEntry。"""
        return self._recall_sync(npc_id, query, top_k)

    async def get_entries_by_tag(
        self, npc_id: str, tag: str, limit: int = 10
    ) -> list[MemoryEntry]:
        """
        按标签检索。主项目 tags 不参与检索，也无按 tag 查询接口，
        退化方案：用 tag 文本做一次语义召回（正文里已含关键信息）。
        """
        return self._recall_sync(npc_id, tag, limit)

    # ---------- 适配层专用的高信度写入（公开封装私有方法） ----------

    def append_high_confidence(
        self,
        npc_id: str,
        text: str,
        importance: float = 0.9,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        高信度记忆写入（同步）。

        ⚠️ 主项目私有方法依赖：内部调用 ``NPCMemoryStore._append_memory``（L461）。
        功能模块的事件事实必须走高信度路径，不能走会被 LLM 改写的
        maybe_store_thought。若主项目未来提供公开等价方法，请替换此处。
        """
        self._safe_append(npc_id, text, importance=importance, tags=tags, metadata=metadata)

    def write_shared_memory(
        self,
        npc_id: str,
        event_name: str,
        location: str,
        present_npc_ids: list[str],
        kind: str = "event",
        importance: float = 0.9,
        extra_tags: list[str] | None = None,
    ) -> None:
        """
        给单个在场者写一条"共同记忆"（正文含事件名+全部在场人名）。

        :param present_npc_ids: 全部在场者 npc_id（含 npc_id 本人）
        """
        names = [self._resolve_name(i) for i in present_npc_ids]
        self_name = self._resolve_name(npc_id)
        text = build_shared_memory_text(
            event_name, location, names, self_name=self_name, kind=kind
        )
        tags = list(extra_tags or []) + [kind, "shared", event_name]
        self.append_high_confidence(
            npc_id,
            text,
            importance=importance,
            tags=tags,
            metadata={"co_present": list(present_npc_ids), "event": event_name},
        )

    # ---------- 内部 ----------

    def _resolve_name(self, npc_id: str) -> str:
        """npc_id -> 人名；解析器缺失时退回 id 本身。"""
        if self._name_resolver is not None:
            try:
                name = self._name_resolver(npc_id)
                if name:
                    return str(name)
            except Exception:  # noqa: BLE001
                logger.debug("人名解析失败 npc=%s", npc_id, exc_info=True)
        return str(npc_id)

    def _safe_append(
        self,
        npc_id: str,
        text: str,
        importance: float = 0.8,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        容错调用主项目 _append_memory。

        主项目真实签名以 L461 为准（位置参 npc_id, text）；tags/metadata/
        importance 为关键字，若主项目不接受某些 kwarg，会逐级降级重试
        （先去掉 metadata，再只保留位置参），保证写入不因签名差异失败。
        """
        attempts = (
            lambda: self._store._append_memory(  # type: ignore[attr-defined]  # noqa: SLF001
                npc_id, text, importance=importance, tags=tags or [], metadata=metadata or {}
            ),
            lambda: self._store._append_memory(  # type: ignore[attr-defined]  # noqa: SLF001
                npc_id, text, importance=importance, tags=tags or []
            ),
            lambda: self._store._append_memory(npc_id, text, importance),  # type: ignore[attr-defined]  # noqa: SLF001
            lambda: self._store._append_memory(npc_id, text),  # type: ignore[attr-defined]  # noqa: SLF001
        )
        last_err: Exception | None = None
        for call in attempts:
            try:
                call()
                return
            except TypeError as err:
                # 关键字不被接受 → 尝试下一个更简签名
                last_err = err
                continue
            except Exception as err:  # noqa: BLE001
                logger.error("记忆写入失败 npc=%s: %s", npc_id, err)
                return
        logger.error("记忆写入签名全部不匹配 npc=%s: %s", npc_id, last_err)

    def _recall_sync(self, npc_id: str, query: str, top_k: int) -> list[MemoryEntry]:
        """同步召回并归一化为 MemoryEntry。"""
        try:
            raw = self._store.retrieve_texts(npc_id, query, top_k)  # type: ignore[attr-defined]
        except Exception as err:  # noqa: BLE001
            logger.warning("记忆召回失败 npc=%s: %s", npc_id, err)
            return []
        entries: list[MemoryEntry] = []
        for idx, item in enumerate(raw or []):
            content = self._extract_text(item)
            if not content:
                continue
            entries.append(
                MemoryEntry(
                    entry_id=f"recall_{npc_id}_{idx}",
                    npc_id=npc_id,
                    content=content,
                    timestamp=datetime.now(),
                    confidence=0.8,
                    tags=[],
                    metadata=self._extract_meta(item),
                )
            )
        return entries

    @staticmethod
    def _extract_text(item: Any) -> str:
        """从召回结果元素中抽取文本（可能是 str 或带 text/content/记忆对象）。"""
        if isinstance(item, str):
            return item
        for attr in ("text", "content", "memory", "thought"):
            val = getattr(item, attr, None)
            if isinstance(val, str) and val:
                return val
        # 字典形式
        if isinstance(item, dict):
            for key in ("text", "content", "memory"):
                if isinstance(item.get(key), str):
                    return item[key]
        return str(item) if item is not None else ""

    @staticmethod
    def _extract_meta(item: Any) -> dict[str, Any]:
        """尽力抽取召回元素上的 metadata（含 co_present 等）。"""
        if isinstance(item, dict):
            meta = item.get("metadata")
            return dict(meta) if isinstance(meta, dict) else {}
        meta = getattr(item, "metadata", None)
        return dict(meta) if isinstance(meta, dict) else {}
