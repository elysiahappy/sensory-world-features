"""
适配层测试用的主项目 mock 对象。

严格按阶段四勘测到的主项目真实接口形状构造（方法名/同步异步/私有方法），
用于离线验证适配器：
  - FakeWorldClock     ：total_game_minutes 真相源 + advance
  - FakeMemoryStore    ：_append_memory（私有，高信度）+ retrieve_texts（同步）
  - FakeEmotionSystem  ：全同步 get/update/record_proximity
  - FakeDiary          ：无公开写方法（方案A）/ 带 add_event_entry（方案B）
  - FakeScheduleBook   ：add_entry + get_location
"""

from __future__ import annotations

from typing import Any


class FakeWorldClock:
    """主项目 WorldClock 形状：唯一真相源 total_game_minutes。"""

    def __init__(self, total_game_minutes: int = 0) -> None:
        self.total_game_minutes = total_game_minutes
        self.callbacks: dict[str, list] = {}

    async def advance(self, minutes: int = 1) -> None:
        self.total_game_minutes += minutes

    def register_callback(self, event: str, callback: Any) -> None:
        self.callbacks.setdefault(event, []).append(callback)


class FakeMemoryStore:
    """
    主项目 NPCMemoryStore 形状。
    高信度写走私有 _append_memory；tags 不参与检索（这里模拟：只按文本匹配）。
    """

    def __init__(self) -> None:
        # npc_id -> list[dict(text, importance, tags, metadata)]
        self.stored: dict[str, list[dict[str, Any]]] = {}
        self.append_calls: list[tuple] = []

    def _append_memory(
        self,
        npc_id: str,
        text: str,
        *,
        importance: float = 0.8,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """模拟主项目私有高信度写入方法（L461）。"""
        self.append_calls.append((npc_id, text, importance, tags, metadata))
        self.stored.setdefault(npc_id, []).append(
            {"text": text, "importance": importance, "tags": tags or [], "metadata": metadata or {}}
        )

    def retrieve_texts(self, npc_id: str, query: str, top_k: int = 5) -> list[Any]:
        """同步 RAG：模拟哈希 embedder 只看文本（关键词命中），忽略 tags。"""
        hits = []
        for rec in self.stored.get(npc_id, []):
            # 任意查询关键词出现在正文即命中（模拟语义近似）
            if any(tok and tok in rec["text"] for tok in query.split()):
                hits.append(rec)
        return hits[:top_k]


class FakeEmotionSystem:
    """主项目 npc_emotion 形状（全同步）。"""

    def __init__(self) -> None:
        self.emotions: dict[str, dict[str, float]] = {}
        self.proximity: list[tuple] = []

    def get_emotion(self, npc_id: str) -> dict[str, float]:
        return dict(self.emotions.get(npc_id, {}))

    def update_emotion(self, npc_id: str, emotion: str, intensity: float = 0.5, reason: str = "") -> None:
        self.emotions.setdefault(npc_id, {})[emotion] = intensity

    def record_proximity(self, npc_a: str, npc_b: str, amount: float = 1.0) -> None:
        self.proximity.append((npc_a, npc_b, amount))


class FakeDiaryNoWriter:
    """主项目 WorldDiary（无公开写条目方法）——用于验证方案 A 直接追加文件。"""

    def __init__(self, diary_dir: str = "") -> None:
        self.diary_dir = diary_dir  # 故意不暴露可用写方法


class FakeDiaryWithWriter:
    """主项目打过补丁后的 WorldDiary（带公开 add_event_entry）——方案 B。"""

    def __init__(self) -> None:
        self.entries: list[str] = []

    def add_event_entry(self, text: str) -> None:
        self.entries.append(text)


class FakeScheduleBook:
    """主项目日程本形状：add_entry + get_location。"""

    def __init__(self) -> None:
        self.entries: list[Any] = []
        self.locations: dict[str, str] = {}

    def add_entry(self, entry: Any) -> None:
        self.entries.append(entry)

    def get_location(self, npc_id: str) -> str | None:
        return self.locations.get(npc_id)


class FakeLLMHttp:
    """打补丁的 urllib 请求拦截器（monkeypatch urlopen 用）。"""

    def __init__(self, content: str = "这是模型生成的内容。", fail_times: int = 0) -> None:
        self.content = content
        self.fail_times = fail_times
        self.calls = 0

    def __call__(self, req, timeout=None):  # noqa: ANN001
        import io
        import json as _json

        self.calls += 1
        if self.calls <= self.fail_times:
            raise ConnectionError("槽位忙/服务未起")
        payload = {
            "choices": [{"message": {"role": "assistant", "content": self.content}}]
        }
        data = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return _FakeResp(io.BytesIO(data))


class _FakeResp:
    def __init__(self, stream) -> None:
        self._stream = stream

    def read(self) -> bytes:
        return self._stream.read()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False
