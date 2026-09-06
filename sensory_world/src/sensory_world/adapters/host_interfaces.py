"""
主项目（小爱世界）真实接口形状描述。

本模块用 Protocol 描述阶段四"只读接口勘测"得到的主项目生产代码接口。
这些 Protocol **只用于类型标注与文档对齐**，适配层从不 import 主项目代码，
运行时通过鸭子类型（duck typing）调用注入进来的真实对象。

⚠️ 权威事实来源：阶段四勘测结论。所有方法名/同步异步/私有方法均照此实现，
   不得臆造。标注"⚠ 私有方法依赖"处，表示主项目没有等价公开方法，
   适配层对其做了封装并在 PATCHES.md 中登记待主项目确认。

勘测到的主项目接口：
  - core/world_clock.py       WorldClock
  - core/npc_memory_store.py  NPCMemoryStore（_append_memory / retrieve_texts）
  - core/npc_emotion.py       全同步：get_emotion / update_emotion / record_proximity
  - core/group_scene          GroupSceneSystem（按位置自动聚类，每群≤5人，无定向开群 API）
  - core/world_diary.py       WorldDiary（无公开写条目方法）
  - schedule_book             ScheduleEntry.activity 自由字符串；新居民可能为 None
  - LLM                       OpenAI /v1/chat/completions，8089 端口，Qwen3-8B
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


# ============================================================
# 时钟（core/world_clock.py，异步）
# ============================================================

@runtime_checkable
class WorldClockLike(Protocol):
    """主项目 WorldClock 的接口形状（异步 advance）。"""

    # 唯一真相源：自城市启动累计的游戏分钟数（整数）
    total_game_minutes: int

    async def advance(self, minutes: int = 1) -> None:
        """推进游戏时间（异步）。适配层一般不调用，由主循环驱动。"""
        ...

    # ---- 同步回调注册（勘测：on_new_hour/on_new_day/on_new_season/on_new_year）----
    # 主项目以"注册回调"方式广播时间边界；签名为 fn(...) 同步。
    # 适配层可选注册以做边界感知（非必须，也可靠轮询 total_game_minutes）。
    def register_callback(self, event: str, callback: Any) -> None:
        """
        注册时间边界回调。
        ⚠ 需主项目确认注册入口的真实方法名（勘测给出事件名
           on_new_hour/on_new_day/on_new_season/on_new_year，注册器名称待定）。
        适配层用 register_callback(event, fn) 容错调用，不存在则静默跳过。
        """
        ...


# ============================================================
# 记忆（core/npc_memory_store.py）
# ============================================================

@runtime_checkable
class NPCMemoryStoreLike(Protocol):
    """主项目 NPCMemoryStore 的接口形状。"""

    # ⚠ 私有方法依赖（L461）：高信度写入的唯一可靠入口。
    # 公开的 maybe_store_thought 会被 LLM 判为"不值得记"而改写/丢弃，
    # 功能模块写入的是"事件事实"（必须落库），故直调私有方法。
    def _append_memory(
        self,
        npc_id: str,
        text: str,
        *,
        importance: float = 0.8,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """
        直接追加一条记忆（同步）。
        ⚠ 依赖主项目私有方法 _append_memory；关键字参数为容错适配，
           真实签名以主项目为准（见 RealMemoryAdapter._safe_append）。
        """
        ...

    # 召回（L270）：同步 RAG。哈希 embedder 只看 text + importance + 近因，
    # tags 不参与检索 —— 故共同记忆必须把事件名与人名写进正文文本。
    def retrieve_texts(
        self,
        npc_id: str,
        query: str,
        top_k: int = 5,
    ) -> list[Any]:
        """检索记忆文本（同步）。返回元素可能是 str 或带 text/内容属性的对象。"""
        ...


# ============================================================
# 情绪（core/npc_emotion.py，全同步）
# ============================================================

@runtime_checkable
class NPCEmotionLike(Protocol):
    """主项目 npc_emotion 的接口形状（全同步）。"""

    def get_emotion(self, npc_id: str) -> dict[str, float]:
        """获取 NPC 当前情绪 {emotion_type: intensity}（L373，同步）。"""
        ...

    def update_emotion(
        self,
        npc_id: str,
        emotion: str,
        intensity: float = 0.5,
        reason: str = "",
    ) -> Any:
        """更新/注入情绪（L455，同步）。duration 参数主项目可能不支持，容错忽略。"""
        ...

    def record_proximity(
        self,
        npc_a: str,
        npc_b: str,
        amount: float = 1.0,
    ) -> Any:
        """记录两个 NPC 相处（L525，同步）。"""
        ...


# ============================================================
# 群聊（core/group_scene，异步；按位置自动聚类）
# ============================================================

@runtime_checkable
class GroupSceneSystemLike(Protocol):
    """
    主项目群聊系统形状。

    勘测结论：**没有"指定参与者+话题"的定向开群 API**。群是 GroupSceneSystem
    在 tick（L241）时按 NPC 实时位置自动聚类形成的。因此适配层不调用任何
    "开群"方法，而是通过"把参与者移动到分片地点"来触发自动聚类。
    本 Protocol 主要用于类型标注与确认"无需调用"。
    """

    async def tick(self) -> None:
        """主循环驱动的群聊 tick（适配层不调用）。"""
        ...


# ============================================================
# 日记（core/world_diary.py，无公开写条目方法）
# ============================================================

@runtime_checkable
class WorldDiaryLike(Protocol):
    """
    主项目 WorldDiary 形状。

    勘测结论：只订阅 npc.brain.thought 事件写 data/diary/day-XXXX.md，
    **没有公开写条目方法**。适配层 RealDiaryAdapter 提供两条路径：
      1) 若主项目已按 PATCHES.md 补了 add_event_entry（或同名）公开方法，优先调用；
      2) 否则适配器直接以一致格式追加 data/diary/day-XXXX.md。
    """

    diary_dir: str  # ⚠ 需主项目确认：日记目录属性名；缺省用 data/diary


# ============================================================
# 日程（schedule_book，条件键 register_festival/weekday/season/weather）
# ============================================================

@runtime_checkable
class ScheduleBookLike(Protocol):
    """主项目日程本形状（同步 tick；activity 为自由字符串）。"""

    def add_entry(self, entry: Any) -> Any:
        """
        添加日程条目（自由活动字符串如"翻看相册""去邮差小屋寄信"）。
        ⚠ 需主项目确认：添加条目的真实方法名与 ScheduleEntry 构造字段。
        """
        ...

    def get_location(self, npc_id: str) -> str | None:
        """查询 NPC 当前所在地点 id（可能为空）。"""
        ...


# ============================================================
# 移动命令（显式聚集用）
# ============================================================

# 勘测事实 8：新注册居民 schedule_book=None 不参与日程移动；
# 事件聚集必须**显式下移动命令**。主项目移动 API 的确切函数名未在勘测中给出，
# 故适配层通过"可调用对象注入"来移动 NPC（见 RealScheduleAdapter.move_callback），
# 由集成方在启动时绑定真实的 NPC 移动函数。⚠ 需主项目确认移动 API 名称。
MoveCommand = Any  # 形如 async def move(npc_id: str, location_id: str, reason: str = "") -> None
