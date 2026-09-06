"""日历系统数据模型 —— 城市历法、季节、生日、开张配置。

城市纪元（CityCalendar）在游戏时间之上叠加一层"城市历法"：
- 记录城市纪元第 N 天 / 周 / 月 / 季 / 年；
- 季节流转（春夏秋冬），季节影响天气倾向与 NPC 行为注释；
- 居民生日（配置驱动）；
- 新店开张协议（城市生长）。

时间推进以 GameClock 的累计天数（total_days，城市纪元第 1 天为第 0 天）
为基准推导，不改动现有游戏时钟。
"""

from __future__ import annotations

import enum
from datetime import date
from typing import Any

from pydantic import BaseModel, Field


# ============================================================
# 季节枚举
# ============================================================

class Season(str, enum.Enum):
    """一年四季。"""

    SPRING = "spring"   # 春
    SUMMER = "summer"   # 夏
    AUTUMN = "autumn"   # 秋
    WINTER = "winter"   # 冬

    @property
    def cn(self) -> str:
        return {
            Season.SPRING: "春",
            Season.SUMMER: "夏",
            Season.AUTUMN: "秋",
            Season.WINTER: "冬",
        }[self]


# ============================================================
# 城市历法日期
# ============================================================

class CityDate:
    """
    城市纪元中的一个日期（不可变快照）。

    day 从 1 开始计数（城市纪元第 1 天）。
    周以 7 天为一周；月以 30 天为一月；季以 90 天为一季；年以 360 天为一年
    （游戏历法，规则简单且自洽，方便季节与年度回声计算）。
    """

    DAYS_PER_WEEK = 7
    DAYS_PER_MONTH = 30
    DAYS_PER_SEASON = 90
    DAYS_PER_YEAR = 360

    def __init__(self, day: int):
        # day 从 1 开始；内部用从 0 开始的偏移量做整除
        self.day_index = max(day - 1, 0)

    @classmethod
    def from_total_days(cls, total_days: int) -> "CityDate":
        """从游戏累计天数（第 1 天 total_days=0）构造城市日期。"""
        return cls(day=total_days + 1)

    @property
    def day(self) -> int:
        """城市纪元第 N 天（从 1 开始）。"""
        return self.day_index + 1

    @property
    def year(self) -> int:
        """城市纪元第 N 年（从 1 开始）。"""
        return self.day_index // self.DAYS_PER_YEAR + 1

    @property
    def season(self) -> Season:
        """当前季节（一年 360 天，每季 90 天）。"""
        day_in_year = self.day_index % self.DAYS_PER_YEAR
        season_idx = day_in_year // self.DAYS_PER_SEASON
        return [Season.SPRING, Season.SUMMER, Season.AUTUMN, Season.WINTER][season_idx]

    @property
    def day_of_year(self) -> int:
        """一年中的第几天（0~359）。"""
        return self.day_index % self.DAYS_PER_YEAR

    @property
    def is_year_start(self) -> bool:
        """是否为一年的第一天（城市新年）。"""
        return self.day_of_year == 0

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<CityDate 纪元第{self.day}天 第{self.year}年 {self.season.cn}季>"
        )


# ============================================================
# 季节注释（供事件系统与 chatter 读取）
# ============================================================

class SeasonNote:
    """
    季节对天气倾向与 NPC 行为的注释。

    通过 CityCalendar.season_note 暴露给事件系统与 chatter，
    作为私语话题/行为的"气候旁白"，不直接驱动硬逻辑。
    """

    def __init__(self, season: Season):
        self.season = season
        # 天气倾向（供天气系统参考，标注为"建议"）
        self.weather_tendency: dict[str, float] = {}
        # NPC 行为注释（人类可读，供 chatter / 事件引用）
        self.behavior_note: str = ""
        # 供 LLM 话题使用的季节关键词
        self.topic_hints: list[str] = []
        self._fill()

    def _fill(self) -> None:
        s = self.season
        if s == Season.SPRING:
            self.weather_tendency = {"clear": 0.45, "cloudy": 0.3, "rain": 0.2, "snow": 0.0}
            self.behavior_note = "春天到了，居民们更愿意在白天出门，公园里散步、赏花的人多了起来。"
            self.topic_hints = ["开春", "花开", "暖和起来", "春游"]
        elif s == Season.SUMMER:
            self.weather_tendency = {"clear": 0.5, "cloudy": 0.2, "rain": 0.25, "snow": 0.0}
            self.behavior_note = "夏天炎热，居民们倾向傍晚和夜间出门，白天多待在室内或阴凉处。"
            self.topic_hints = ["夏天", "傍晚", "乘凉", "夜市", "蝉鸣"]
        elif s == Season.AUTUMN:
            self.weather_tendency = {"clear": 0.4, "cloudy": 0.35, "rain": 0.2, "snow": 0.0}
            self.behavior_note = "秋高气爽，是出门聚会的好时节，居民们谈起收成与换季。"
            self.topic_hints = ["入秋", "落叶", "秋风", "换季", "收获"]
        else:  # WINTER
            self.weather_tendency = {"clear": 0.3, "cloudy": 0.3, "rain": 0.1, "snow": 0.3}
            self.behavior_note = "冬天寒冷，居民们倾向待在室内场所（餐馆、书店、游戏厅），围炉取暖。"
            self.topic_hints = ["冬天", "下雪", "取暖", "热饮", "年末"]

    def as_text(self) -> str:
        """供 chatter 话题注入的一段话。"""
        return f"（{self.season.cn}季）{self.behavior_note}"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SeasonNote {self.season.cn}>"


# ============================================================
# 生日配置
# ============================================================

class Birthday(BaseModel):
    """
    单个 NPC 的城市内生日。

    以"城市历法一年中的第几天"（day_of_year，1~360）定义，
    与原作生日无关；未配置的 NPC 不触发生日链路。
    """

    npc_id: str
    day_of_year: int = Field(ge=1, le=360, description="城市历法一年中的第几天（1~360）")
    # 可选：备注（如"喜欢热闹"），不参与逻辑
    note: str = ""

    @property
    def season(self) -> Season:
        """该生日落在哪个季节。"""
        idx = (self.day_of_year - 1) // CityDate.DAYS_PER_SEASON
        return [Season.SPRING, Season.SUMMER, Season.AUTUMN, Season.WINTER][idx]


# ============================================================
# 日历系统配置
# ============================================================

class CalendarConfig(BaseModel):
    """日历系统配置（可从 YAML 加载）。"""

    enabled: bool = True
    # 城市纪元起始：游戏累计天数 total_days=0 对应城市纪元第几天（默认第 1 天）
    epoch_start_total_day: int = 0

    # ---- 生日链路 ----
    birthday_enabled: bool = True
    # 生日时邮差贺卡概率（"邮差最先得知"——默认可调，贺卡由城里的朋友们联名）
    birthday_card_probability: float = 0.9
    # 生日合照概率
    birthday_photo_probability: float = 0.85
    # 亲近 NPC 祝福私语概率
    birthday_greeting_chatter_probability: float = 0.7
    # 每场生日祝福私语最多触发的亲近 NPC 数
    birthday_greeting_max_npcs: int = 4

    # ---- 年度回声 ----
    echo_enabled: bool = True
    # 年度回声召回每个参与者的最大记忆条数（防上下文膨胀）
    echo_memory_per_npc: int = 2
    # 年度回声触发私语话题的最大配对数
    echo_max_topics: int = 3

    # ---- 新店开张 ----
    new_shop_enabled: bool = True
    # 开张首日造访 group_scene 的周边 NPC 数（不含店主）
    new_shop_visitor_count: int = 5
    # 开张拍照概率（摄影 NPC 首张店铺照片）
    new_shop_photo_probability: float = 0.95
    # 发送开业邀请函概率
    new_shop_invitation_probability: float = 0.8
    # 邀请函最多发送给多少个 NPC
    new_shop_invitation_max: int = 6

    # 店铺类型中文名映射（开张日记/邀请函用）
    shop_type_names: dict[str, str] = Field(
        default_factory=lambda: {
            "bookstore": "书店",
            "bakery": "点心铺",
            "restaurant": "餐馆",
            "post_office": "邮差小屋",
            "cafe": "咖啡馆",
            "flower_shop": "花店",
        }
    )


# ============================================================
# 生日触发记录（幂等去重）
# ============================================================

class BirthdayRecord:
    """一次生日庆祝记录，用于同年同 NPC 不重复触发。"""

    def __init__(self, npc_id: str, year: int, day: int):
        self.npc_id = npc_id
        self.year = year
        self.day = day

    def key(self) -> str:
        return f"{self.npc_id}:{self.year}"


# ============================================================
# 开张记录
# ============================================================

class ShopOpening(BaseModel):
    """一次新店开张记录（可持久化/供主项目地点系统同步）。"""

    npc_id: str
    shop_location: str
    shop_type: str
    day: int
    city_date_desc: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "npc_id": self.npc_id,
            "shop_location": self.shop_location,
            "shop_type": self.shop_type,
            "day": self.day,
            "city_date_desc": self.city_date_desc,
        }


def parse_day_of_year(month: int, day: int) -> int:
    """
    便捷工具：把"城市历法月/日"换算为 day_of_year（1~360）。
    城市历法每月固定 30 天。

    :param month: 月（1~12）
    :param day: 日（1~30）
    """
    return (month - 1) * CityDate.DAYS_PER_MONTH + day
