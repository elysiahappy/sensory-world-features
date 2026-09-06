"""城市历法数据模型测试（CityDate / SeasonNote / Birthday / 配置加载）。"""

from pathlib import Path

import pytest

from sensory_world.calendar.models import (
    Birthday,
    CalendarConfig,
    CityDate,
    Season,
    SeasonNote,
    parse_day_of_year,
)
from sensory_world.calendar.config_loader import load_birthdays, load_calendar_config

CONFIG_DIR = Path(__file__).resolve().parent.parent / "src" / "sensory_world" / "configs"


# ============================================================
# CityDate 历法推进
# ============================================================

class TestCityDate:
    def test_first_day(self):
        d = CityDate.from_total_days(0)
        assert d.day == 1
        assert d.year == 1
        assert d.season == Season.SPRING
        assert d.is_year_start is True

    def test_summer_start(self):
        # 第 91 天（day_index 90）进入夏季
        d = CityDate(day=91)
        assert d.season == Season.SUMMER

    def test_autumn_start(self):
        d = CityDate(day=181)
        assert d.season == Season.AUTUMN

    def test_winter_start(self):
        d = CityDate(day=271)
        assert d.season == Season.WINTER

    def test_year_rollover(self):
        # 第 361 天 = 第 2 年第 1 天
        d = CityDate(day=361)
        assert d.year == 2
        assert d.day_of_year == 0
        assert d.is_year_start is True
        assert d.season == Season.SPRING

    def test_second_year_summer(self):
        # 第 2 年夏天：day = 360 + 91 = 451
        d = CityDate(day=451)
        assert d.year == 2
        assert d.season == Season.SUMMER

    def test_season_cn_name(self):
        assert Season.SPRING.cn == "春"
        assert Season.WINTER.cn == "冬"


# ============================================================
# SeasonNote 季节注释
# ============================================================

class TestSeasonNote:
    def test_summer_behavior_note(self):
        note = SeasonNote(Season.SUMMER)
        assert "傍晚" in note.behavior_note
        assert note.weather_tendency.get("clear", 0) > 0

    def test_winter_behavior_note(self):
        note = SeasonNote(Season.WINTER)
        assert "室内" in note.behavior_note
        # 冬季有下雪倾向
        assert note.weather_tendency.get("snow", 0) > 0

    def test_as_text_contains_season(self):
        note = SeasonNote(Season.AUTUMN)
        text = note.as_text()
        assert "秋" in text
        assert len(note.topic_hints) > 0


# ============================================================
# Birthday / parse_day_of_year
# ============================================================

class TestBirthday:
    def test_parse_day_of_year(self):
        assert parse_day_of_year(1, 1) == 1
        assert parse_day_of_year(1, 30) == 30
        assert parse_day_of_year(2, 1) == 31
        assert parse_day_of_year(12, 30) == 360

    def test_birthday_season(self):
        # 3 月 7 日 -> day_of_year 67，春季
        b = Birthday(npc_id="march7th", day_of_year=67)
        assert b.season == Season.SPRING
        # day_of_year 150（夏：91~180）
        b2 = Birthday(npc_id="sparkle", day_of_year=150)
        assert b2.season == Season.SUMMER


# ============================================================
# 配置加载
# ============================================================

class TestConfigLoader:
    def test_load_calendar_yaml(self):
        cfg = load_calendar_config(CONFIG_DIR / "calendar.yaml")
        assert cfg.enabled is True
        assert 0 < cfg.birthday_card_probability <= 1
        assert cfg.shop_type_names.get("bookstore") == "书店"

    def test_load_missing_calendar_uses_default(self, tmp_path):
        cfg = load_calendar_config(tmp_path / "nope.yaml")
        assert isinstance(cfg, CalendarConfig)
        assert cfg.echo_enabled is True

    def test_load_birthdays_yaml(self):
        bdays = load_birthdays(CONFIG_DIR / "birthdays.yaml")
        assert "march7th" in bdays
        assert "sparkle" in bdays
        # 3 月 7 日 -> 67
        assert bdays["march7th"].day_of_year == 67
        # violet 2 月 14 -> 44
        assert bdays["violet"].day_of_year == 44

    def test_load_missing_birthdays_empty(self, tmp_path):
        bdays = load_birthdays(tmp_path / "nope.yaml")
        assert bdays == {}
