"""
RealScheduleAdapter —— 主项目 schedule_book → ScheduleBookProtocol 的适配。

勘测事实（事实 8）：
  - ScheduleEntry.activity 是**自由字符串**，"翻看相册""去邮差小屋寄信"可直接写入；
  - 条件键支持 register_festival / weekday / season / weather；
  - ⚠️ **新注册居民 schedule_book=None**，不参与日程移动；事件聚集必须显式下移动命令。

本适配器：
  - override_schedule / add_activity：往日程本写自由活动条目（容错探测主项目写入方法）；
  - get_location：读 NPC 当前位置；
  - gather_to / move_to：**显式移动命令**（对 schedule_book=None 的居民尤其重要），
    通过注入的移动回调执行（与 RealGroupSceneAdapter 共用同一移动回调）。
"""

from __future__ import annotations

import inspect
import logging
from datetime import datetime
from typing import Any, Awaitable, Callable

from ..protocols import ScheduleBookProtocol

logger = logging.getLogger(__name__)

MoveFn = Callable[[str, str, str], Awaitable[None]]


class RealScheduleAdapter:
    """schedule_book → ScheduleBookProtocol + 显式移动聚集。"""

    def __init__(
        self,
        schedule_book: object | None = None,
        move_fn: MoveFn | None = None,
        entry_factory: Callable[..., Any] | None = None,
    ) -> None:
        """
        :param schedule_book: 主项目日程本（可能为 None，尤其在新居民视角）。
        :param move_fn: 显式移动命令 async (npc_id, location_id, reason)。
            ⚠️ 主项目移动 API 名称需集成方绑定（勘测未给出）。
        :param entry_factory: ScheduleEntry 构造器（可选）。若提供，add_activity
            会用它构造条目再交给日程本；否则直接传 dict 让主项目自行解析。
        """
        self._book = schedule_book
        self._move = move_fn
        self._entry_factory = entry_factory

    # ---------- ScheduleBookProtocol ----------

    async def get_schedule(self, npc_id: str, game_time: datetime) -> dict[str, Any]:
        """查询 NPC 在指定时间的日程。主项目无统一查询接口时尽力探测。"""
        if self._book is None:
            return {}
        for name in ("get_schedule", "get_entry", "schedule_at", "get_activity"):
            fn = getattr(self._book, name, None)
            if callable(fn):
                try:
                    if inspect.iscoroutinefunction(fn):
                        res = await fn(npc_id, game_time)
                    else:
                        res = fn(npc_id, game_time)
                    return res if isinstance(res, dict) else {"raw": res}
                except Exception:  # noqa: BLE001
                    continue
        return {}

    async def override_schedule(
        self,
        npc_id: str,
        start: datetime,
        end: datetime,
        location: str,
        activity: str,
        reason: str = "",
    ) -> None:
        """临时覆盖/新增一段日程（自由活动字符串）。"""
        self.add_activity(
            npc_id, activity=activity, location=location,
            start=start, end=end, reason=reason,
        )

    async def get_location(self, npc_id: str) -> str:
        """获取 NPC 当前位置（同步方法包 async）。"""
        if self._book is None:
            return ""
        fn = getattr(self._book, "get_location", None)
        if callable(fn):
            try:
                res = fn(npc_id)
                if inspect.iscoroutine(res):
                    res = await res
                return res or ""
            except Exception:  # noqa: BLE001
                logger.debug("get_location 失败 npc=%s", npc_id, exc_info=True)
        return ""

    # ---------- 活动写入（自由字符串） ----------

    def add_activity(
        self,
        npc_id: str,
        activity: str,
        location: str = "",
        start: datetime | None = None,
        end: datetime | None = None,
        reason: str = "",
        weekday: int | None = None,
        season: str | None = None,
        weather: str | None = None,
    ) -> bool:
        """
        写入一条自由活动日程（如"翻看相册""去邮差小屋寄信"）。

        利用主项目支持的条件键（weekday/season/weather/register_festival）。
        :return: 是否成功写入。
        """
        if self._book is None:
            logger.info("日程本为空（可能为新居民），活动 '%s' 改为直接移动 npc=%s", activity, npc_id)
            return False
        entry = self._build_entry(
            npc_id=npc_id, activity=activity, location=location,
            start=start, end=end, reason=reason,
            weekday=weekday, season=season, weather=weather,
        )
        for name in ("add_entry", "add", "register_entry", "insert"):
            fn = getattr(self._book, name, None)
            if callable(fn):
                try:
                    fn(entry)
                    logger.debug("日程活动已写入 npc=%s activity=%s", npc_id, activity)
                    return True
                except Exception:  # noqa: BLE001
                    continue
        logger.warning("日程本未找到可用写入方法，活动 '%s' 未写入", activity)
        return False

    # ---------- 显式移动 / 事件聚集 ----------

    async def move_to(self, npc_id: str, location_id: str, reason: str = "") -> bool:
        """
        显式移动 NPC 到指定地点。

        对 schedule_book=None 的新注册居民这是**唯一**能让其参与事件聚集的方式
        （他们不参与日程移动）。
        """
        if self._move is None:
            logger.warning("未绑定移动回调，无法移动 npc=%s -> %s", npc_id, location_id)
            return False
        try:
            res = self._move(npc_id, location_id, reason)
            if inspect.isawaitable(res):
                await res
            return True
        except Exception:  # noqa: BLE001
            logger.warning("移动失败 npc=%s -> %s", npc_id, location_id, exc_info=True)
            return False

    async def gather_to(self, npc_ids: list[str], location_id: str, reason: str = "") -> int:
        """把一批 NPC 显式聚集到某地点（事件分片用）。返回成功移动人数。"""
        ok = 0
        for npc_id in npc_ids:
            if await self.move_to(npc_id, location_id, reason):
                ok += 1
        return ok

    # ---------- 内部 ----------

    def _build_entry(self, **fields: Any) -> Any:
        """构造日程条目：有工厂用工厂，否则返回 dict（主项目鸭子类型解析）。"""
        fields = {k: v for k, v in fields.items() if v is not None}
        if self._entry_factory is not None:
            try:
                return self._entry_factory(**fields)
            except Exception:  # noqa: BLE001
                logger.debug("entry_factory 构造失败，退回 dict", exc_info=True)
        return fields
