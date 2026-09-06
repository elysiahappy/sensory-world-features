"""
RealScheduleAdapter —— 主项目 schedule_book → ScheduleBookProtocol 的适配。

勘测事实（事实 8）：
  - ScheduleEntry.activity 是**自由字符串**，"翻看相册""去邮差小屋寄信"可直接写入；
  - 条件键支持 register_festival / weekday / season / weather；
  - ⚠️ **新注册居民 schedule_book=None**，不参与日程移动；事件聚集必须显式下移动命令。

事件聚集移动机制（补充事实）：
  主项目通过事件总线广播移动命令：
    event_bus.publish("schedule.npc_command", payload)
  payload 字段：npc_id、target_location（地点 ID）、anchor_id、activity（自由字符串）、
  lateness_policy、schedule_condition。下游 Body 层/寻路系统监听该事件执行实际移动
  （simulation_loop.py T2 t2_schedule L599-643 即此机制）。

本适配器：
  - override_schedule / add_activity：往日程本写自由活动条目（容错探测主项目写入方法）；
  - get_location：读 NPC 当前位置；
  - move_to / gather_to：通过 event_bus 发布 schedule.npc_command 事件执行显式移动。
"""

from __future__ import annotations

import inspect
import logging
from datetime import datetime
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from ..protocols import ScheduleBookProtocol

logger = logging.getLogger(__name__)


@runtime_checkable
class HostEventBus(Protocol):
    """主项目事件总线形状（鸭子类型）。"""

    def publish(self, event_name: str, payload: dict[str, Any]) -> Any: ...


class RealScheduleAdapter:
    """schedule_book → ScheduleBookProtocol + 事件聚集移动（走 event_bus）。"""

    def __init__(
        self,
        schedule_book: object | None = None,
        event_bus: HostEventBus | None = None,
        entry_factory: Callable[..., Any] | None = None,
    ) -> None:
        """
        :param schedule_book: 主项目日程本（可能为 None，尤其在新居民视角）。
        :param event_bus: 主项目事件总线。提供 publish(event_name, payload) 方法。
            事件聚集移动通过 event_bus.publish("schedule.npc_command", payload) 执行。
            无 event_bus 时移动命令降级为告警不崩。
        :param entry_factory: ScheduleEntry 构造器（可选）。若提供，add_activity
            会用它构造条目再交给日程本；否则直接传 dict 让主项目自行解析。
        """
        self._book = schedule_book
        self._bus = event_bus
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

    # ---------- 显式移动 / 事件聚集（走 event_bus） ----------

    async def move_to(self, npc_id: str, location_id: str, reason: str = "") -> bool:
        """
        显式移动 NPC 到指定地点。

        对 schedule_book=None 的新注册居民这是**唯一**能让其参与事件聚集的方式
        （他们不参与日程移动）。

        通过 event_bus.publish("schedule.npc_command", payload) 执行。
        payload 字段：npc_id、target_location、anchor_id、activity、lateness_policy、
        schedule_condition。
        """
        if self._bus is None:
            logger.warning("未注入 event_bus，无法移动 npc=%s -> %s", npc_id, location_id)
            return False
        payload = self._build_move_payload(npc_id, location_id, reason)
        try:
            res = self._bus.publish("schedule.npc_command", payload)
            if inspect.isawaitable(res):
                await res
            logger.debug("已发布移动命令 npc=%s -> %s", npc_id, location_id)
            return True
        except Exception:  # noqa: BLE001
            logger.warning("发布移动命令失败 npc=%s -> %s", npc_id, location_id, exc_info=True)
            return False

    async def gather_to(self, npc_ids: list[str], location_id: str, reason: str = "") -> int:
        """把一批 NPC 显式聚集到某地点（事件分片用）。返回成功移动人数。"""
        ok = 0
        for npc_id in npc_ids:
            if await self.move_to(npc_id, location_id, reason):
                ok += 1
        return ok

    # ---------- 内部 ----------

    def _build_move_payload(self, npc_id: str, location_id: str, reason: str) -> dict[str, Any]:
        """
        构造 schedule.npc_command 事件 payload。

        字段对齐主项目 T2 t2_schedule 期望：
        - npc_id: 被移动 NPC
        - target_location: 目标地点 ID
        - anchor_id: 锚点（同 target_location，无特殊锚点时一致）
        - activity: 自由活动字符串（reason 传入，如"参加烟火大会""上学"）
        - lateness_policy: 迟到策略（默认 "catch_up"，主项目默认值）
        - schedule_condition: 日程条件（空 dict，无条件限制）
        """
        return {
            "npc_id": npc_id,
            "target_location": location_id,
            "anchor_id": location_id,
            "activity": reason or "参加活动",
            "lateness_policy": "catch_up",
            "schedule_condition": {},
        }

    def _build_entry(self, **fields: Any) -> Any:
        """构造日程条目：有工厂用工厂，否则返回 dict（主项目鸭子类型解析）。"""
        fields = {k: v for k, v in fields.items() if v is not None}
        if self._entry_factory is not None:
            try:
                return self._entry_factory(**fields)
            except Exception:  # noqa: BLE001
                logger.debug("entry_factory 构造失败，退回 dict", exc_info=True)
        return fields
