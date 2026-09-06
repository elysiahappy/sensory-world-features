"""
RealDiaryAdapter —— 主项目 WorldDiary → WorldDiaryProtocol 的适配。

勘测事实（core/world_diary.py）：WorldDiary **没有公开写条目方法**，它只订阅
npc.brain.thought 事件，把内容写进 data/diary/day-XXXX.md（XXXX 为城市日序号）。

本适配器提供两条路径（见 PATCHES.md 补丁二）：
  方案 A（推荐，零侵入）：适配器直接以与现有日记一致的格式追加 day-XXXX.md；
  方案 B（需主项目补丁）：若主项目按补丁补了 add_event_entry 公开方法，则优先调用。

初始化时会探测主项目对象是否已有公开写条目方法，有则走 B，否则走 A。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

from ..protocols import WorldDiaryProtocol

logger = logging.getLogger(__name__)

MINUTES_PER_DAY = 1440


class RealDiaryAdapter:
    """WorldDiary → WorldDiaryProtocol。"""

    def __init__(
        self,
        world_diary: object | None = None,
        diary_dir: str = "data/diary",
        clock_adapter: object | None = None,
        entry_prefix: str = "【城市记事】",
    ) -> None:
        """
        :param world_diary: 主项目 WorldDiary 实例（可选；若其带公开写方法则优先用）。
        :param diary_dir: 日记目录（方案 A 直接追加文件用）。
        :param clock_adapter: RealClockAdapter，用于推导 day-XXXX 序号与时间戳。
        :param entry_prefix: 追加条目前缀，便于在日记中辨识功能模块写入的内容。
        """
        self._diary = world_diary
        self._clock = clock_adapter
        self._entry_prefix = entry_prefix
        # 日记目录：默认入参；若主项目对象显式暴露了目录属性则采用之
        self._diary_dir = diary_dir
        if world_diary is not None:
            for attr in ("diary_dir", "diary_path"):
                val = getattr(world_diary, attr, None)
                if isinstance(val, str) and val:
                    # 属性可能直接指向 .../data/diary，也可能指向 data
                    self._diary_dir = val if val.rstrip("/").endswith("diary") else os.path.join(val, "diary")
                    break
        self._public_writer = self._detect_public_writer(world_diary)

    async def write_entry(self, category: str, content: str, **metadata: Any) -> None:
        """WorldDiaryProtocol 入口：写一条城市日记。"""
        text = f"[{category}] {content}" if category else content
        if self._public_writer is not None:
            await self._call_public_writer(text, category, metadata)
        else:
            self._append_to_file(text)

    async def add_event_entry(self, text: str, category: str = "event") -> None:
        """
        部署文档/补丁中约定的公开方法名（供主项目与集成方调用）。
        内部等价于 write_entry。
        """
        await self.write_entry(category, text)

    # ---------- 方案 B：主项目公开方法 ----------

    @staticmethod
    def _detect_public_writer(world_diary: object | None):
        """探测主项目是否已补公开写条目方法（补丁二）。"""
        if world_diary is None:
            return None
        for name in ("add_event_entry", "write_event_entry", "append_entry", "add_entry"):
            fn = getattr(world_diary, name, None)
            if callable(fn):
                return (name, fn)
        return None

    async def _call_public_writer(self, text: str, category: str, metadata: dict) -> None:
        name, fn = self._public_writer  # type: ignore[misc]
        try:
            # 容忍不同签名：(text) / (category, text) / (text, category, metadata)
            try:
                res = fn(text)
            except TypeError:
                res = fn(category, text)
            if hasattr(res, "__await__"):
                await res
            logger.debug("日记条目经主项目 %s 写入", name)
        except Exception as err:  # noqa: BLE001
            logger.warning("主项目 %s 写日记失败，回退直接追加文件：%s", name, err)
            self._append_to_file(text)

    # ---------- 方案 A：直接追加 day-XXXX.md ----------

    def _day_index(self) -> int:
        """推导当前城市日序号（day-XXXX）。优先时钟 total_days。"""
        if self._clock is not None:
            try:
                minutes = self._clock.current_minutes()  # type: ignore[attr-defined]
                return max(1, minutes // MINUTES_PER_DAY + 1)
            except Exception:  # noqa: BLE001
                pass
        # 兜底：用真实日期的年积日，保证文件名稳定可写
        return max(1, datetime.now().timetuple().tm_yday)

    def _append_to_file(self, text: str) -> None:
        """
        以与现有日记一致的格式追加条目。

        现有 day-XXXX.md 为 Markdown，按时间戳分节。这里采用保守格式：
            - HH:MM 【城市记事】正文
        若文件不存在则创建并加一个一级标题；已存在则追加一行。
        """
        try:
            os.makedirs(self._diary_dir, exist_ok=True)
            day = self._day_index()
            path = os.path.join(self._diary_dir, f"day-{day:04d}.md")
            timestamp = self._now_hhmm()
            line = f"- {timestamp} {self._entry_prefix}{text}\n"
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(f"# 城市日记 第 {day} 天\n\n")
                    f.write(line)
            else:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line)
            logger.debug("日记条目已追加 %s", path)
        except Exception as err:  # noqa: BLE001
            # 日记写入失败不能影响主流程
            logger.error("日记追加失败：%s", err)

    def _now_hhmm(self) -> str:
        """当前游戏内 HH:MM（由时钟反推）；失败用真实时间。"""
        if self._clock is not None:
            try:
                minutes = self._clock.current_minutes()  # type: ignore[attr-defined]
                minute_of_day = minutes % MINUTES_PER_DAY
                return f"{minute_of_day // 60:02d}:{minute_of_day % 60:02d}"
            except Exception:  # noqa: BLE001
                pass
        return datetime.now().strftime("%H:%M")
