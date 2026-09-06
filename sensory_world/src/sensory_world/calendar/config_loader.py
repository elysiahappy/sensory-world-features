"""日历/生日配置加载。

生日数据为独立 YAML 配置文件（configs/birthdays.yaml），
每个 NPC 一个城市内生日（月/日），未配置的 NPC 不触发。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from .models import Birthday, CalendarConfig, parse_day_of_year

logger = logging.getLogger(__name__)


def load_calendar_config(path: str | Path | None = None) -> CalendarConfig:
    """
    加载日历系统配置（configs/calendar.yaml）。

    文件不存在时返回默认配置（零配置兜底）。
    """
    if path and Path(path).exists():
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return CalendarConfig(**raw)
    logger.info("日历配置缺失，使用默认配置")
    return CalendarConfig()


def load_birthdays(path: str | Path | None) -> dict[str, Birthday]:
    """
    加载生日配置（configs/birthdays.yaml）。

    支持两种写法：
      1. day_of_year: 直接给一年中的第几天（1~360）
      2. month + day: 城市历法月/日（每月 30 天），自动换算

    :return: {npc_id: Birthday}
    """
    result: dict[str, Birthday] = {}
    if not path or not Path(path).exists():
        logger.info("生日配置缺失：%s（无 NPC 会触发生日链路）", path)
        return result

    with open(path, "r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    # 允许顶层是 {birthdays: [...]} 或直接是列表
    items = raw.get("birthdays", raw) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        logger.warning("生日配置格式异常，应为列表")
        return result

    for item in items:
        try:
            npc_id = item["npc_id"]
            if "day_of_year" in item:
                doy = int(item["day_of_year"])
            else:
                doy = parse_day_of_year(int(item["month"]), int(item["day"]))
            result[npc_id] = Birthday(
                npc_id=npc_id,
                day_of_year=doy,
                note=item.get("note", ""),
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.warning("跳过无效生日配置项 %s: %s", item, e)

    logger.info("已加载 %d 个 NPC 的生日配置", len(result))
    return result
