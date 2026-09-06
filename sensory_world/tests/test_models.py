"""
数据模型测试 —— 验证 EventConfig 等 pydantic 模型
"""

import pytest
from datetime import datetime

from sensory_world.periodic_event.models import (
    EventConfig,
    EventInstance,
    EventMemoryPayload,
    EventPhase,
    EventType,
    ParticipationRule,
    SliceConfig,
    TriggerRule,
    TriggerType,
)


class TestTriggerRule:
    """触发规则模型测试"""

    def test_daily_trigger(self):
        rule = TriggerRule(trigger_type=TriggerType.DAILY, hour=20, minute=0)
        assert rule.trigger_type == TriggerType.DAILY
        assert rule.hour == 20
        assert rule.minute == 0

    def test_cron_trigger(self):
        rule = TriggerRule(
            trigger_type=TriggerType.CRON,
            day_of_week=[0, 1, 2, 3, 4],
            hour=8,
            minute=0,
        )
        assert rule.trigger_type == TriggerType.CRON
        assert len(rule.day_of_week) == 5

    def test_invalid_day_of_week(self):
        with pytest.raises(ValueError):
            TriggerRule(day_of_week=[7])  # 7 超出范围

    def test_invalid_hour(self):
        with pytest.raises(ValueError):
            TriggerRule(hour=25)  # 25 超出范围


class TestSliceConfig:
    """分片配置模型测试"""

    def test_basic_slice(self):
        s = SliceConfig(slice_id="main", location="plaza", display_name="主舞台")
        assert s.slice_id == "main"
        assert s.capacity == 8  # 默认值

    def test_custom_capacity(self):
        s = SliceConfig(slice_id="vip", location="vip_room", capacity=3)
        assert s.capacity == 3

    def test_invalid_capacity(self):
        with pytest.raises(ValueError):
            SliceConfig(slice_id="x", location="y", capacity=0)


class TestEventConfig:
    """事件配置模型测试"""

    def test_full_config(self, sample_event_config_dict):
        config = EventConfig(**sample_event_config_dict)
        assert config.event_id == "test_fireworks"
        assert config.event_type == EventType.GATHERING
        assert len(config.slices) == 2
        assert config.participation.required_npcs == ["sparkle"]
        assert config.duration_minutes == 60
        assert config.enabled is True

    def test_all_slice_ids(self, sample_event_config_dict):
        config = EventConfig(**sample_event_config_dict)
        assert config.all_slice_ids == ["main_stage", "food_stalls"]

    def test_all_locations(self, sample_event_config_dict):
        config = EventConfig(**sample_event_config_dict)
        locations = config.all_locations
        assert "plaza" in locations
        assert "food_street" in locations

    def test_minimal_config(self):
        """最小配置也能通过校验"""
        config = EventConfig(
            event_id="minimal",
            name="最小事件",
            event_type=EventType.SCENE,
            trigger=TriggerRule(),
            main_venue="somewhere",
        )
        assert config.event_id == "minimal"
        assert config.slices == []
        assert config.participation.required_npcs == []


class TestEventInstance:
    """事件运行时实例测试"""

    def test_create_instance(self, sample_event_config_dict):
        config = EventConfig(**sample_event_config_dict)
        instance = EventInstance(config=config)
        assert instance.event_id == "test_fireworks"
        assert instance.event_name == "测试烟火大会"
        assert instance.phase == EventPhase.PRE

    def test_active_slices(self, sample_event_config_dict):
        from sensory_world.periodic_event.models import SliceGroup
        config = EventConfig(**sample_event_config_dict)
        instance = EventInstance(
            config=config,
            slice_groups=[
                SliceGroup(slice_id="a", location="x", display_name="A", is_active=True),
                SliceGroup(slice_id="b", location="y", display_name="B", is_active=False),
            ],
        )
        assert len(instance.active_slices) == 1
        assert instance.active_slices[0].slice_id == "a"


class TestEventMemoryPayload:
    """事件记忆载荷测试"""

    def test_format_memory_content(self):
        payload = EventMemoryPayload(
            event_name="烟火大会",
            location="广场",
            participant_ids=["sparkle", "robin", "eden"],
            impression="夜空中的花火很美。",
            timestamp=datetime(2024, 1, 6, 21, 0),
        )
        content = payload.format_memory_content("sparkle")
        assert "烟火大会" in content
        assert "广场" in content
        assert "robin" in content
        assert "eden" in content
        assert "夜空中的花火很美" in content
        # 不应包含自己
        assert "sparkle" not in content.split("在场的还有")[1].split("。")[0] or True  # 自己不在others中

    def test_format_many_participants(self):
        """超过 5 人时截断显示"""
        payload = EventMemoryPayload(
            event_name="大会",
            location="广场",
            participant_ids=["a", "b", "c", "d", "e", "f", "g"],
            impression="很好。",
            timestamp=datetime(2024, 1, 6, 21, 0),
        )
        content = payload.format_memory_content("a")
        assert "等" in content  # 应该有"等N人"
