"""
测试共享 fixtures —— 从 mocks 模块导入并创建 pytest fixtures
"""

from __future__ import annotations

import pytest

from tests.mocks import (
    MockLLMClient,
    FailingLLMClient,
    MockWorldDiary,
    MockNPCMemoryStore,
    MockNPCEmotion,
    MockGroupScene,
    MockChatter,
    MockScheduleBook,
    MockGameClock,
)


# ============================================================
# Pytest Fixtures
# ============================================================

@pytest.fixture
def mock_llm():
    return MockLLMClient()


@pytest.fixture
def failing_llm():
    return FailingLLMClient()


@pytest.fixture
def mock_diary():
    return MockWorldDiary()


@pytest.fixture
def mock_memory():
    return MockNPCMemoryStore()


@pytest.fixture
def mock_emotion():
    return MockNPCEmotion()


@pytest.fixture
def mock_group_scene():
    return MockGroupScene()


@pytest.fixture
def mock_chatter():
    return MockChatter()


@pytest.fixture
def mock_schedule():
    return MockScheduleBook()


@pytest.fixture
def mock_clock():
    return MockGameClock()


@pytest.fixture
def sample_event_config_dict() -> dict:
    """样例事件配置字典（烟火大会）"""
    return {
        "event_id": "test_fireworks",
        "name": "测试烟火大会",
        "event_type": "gathering",
        "trigger": {
            "trigger_type": "cron",
            "day_of_week": [5],
            "hour": 20,
            "minute": 0,
        },
        "main_venue": "plaza",
        "slices": [
            {"slice_id": "main_stage", "location": "plaza", "display_name": "主舞台", "capacity": 5},
            {"slice_id": "food_stalls", "location": "food_street", "display_name": "小吃摊", "capacity": 4},
        ],
        "participation": {
            "required_npcs": ["sparkle"],
            "optional_npcs": ["robin", "eden"],
            "resident_probability": 0.3,
        },
        "duration_minutes": 60,
        "pre_notice_minutes": 15,
        "chatter_boost": 0.2,
        "chatter_topic_hint": "烟火大会的见闻",
        "enabled": True,
    }


@pytest.fixture
def sample_school_config_dict() -> dict:
    """样例学园事件配置字典"""
    return {
        "event_id": "test_school",
        "name": "测试学园上课铃",
        "event_type": "scene",
        "trigger": {
            "trigger_type": "cron",
            "day_of_week": [0, 1, 2, 3, 4],
            "hour": 8,
            "minute": 0,
        },
        "main_venue": "academy_classroom",
        "slices": [
            {"slice_id": "classroom", "location": "academy_classroom", "display_name": "教室", "capacity": 8},
            {"slice_id": "game_center", "location": "shop_game_center", "display_name": "游戏厅", "capacity": 4},
        ],
        "participation": {
            "required_npcs": ["theresa", "griseo", "rin", "march7th"],
            "optional_npcs": ["bronya"],
            "resident_probability": 0.0,
        },
        "duration_minutes": 120,
        "pre_notice_minutes": 5,
        "chatter_boost": 0.1,
        "chatter_topic_hint": "学园生活",
        "enabled": True,
    }
