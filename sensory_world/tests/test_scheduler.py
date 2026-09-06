"""
调度器测试 —— 验证 PeriodicEventSystem 的 tick 逻辑
"""

import pytest
from datetime import datetime, timedelta

from sensory_world.periodic_event.scheduler import PeriodicEventSystem
from sensory_world.periodic_event.models import EventPhase

from tests.mocks import (
    MockLLMClient,
    MockWorldDiary,
    MockNPCMemoryStore,
    MockNPCEmotion,
    MockGroupScene,
    MockChatter,
    MockScheduleBook,
    MockGameClock,
)


def _make_system(
    mock_llm, mock_diary, mock_memory, mock_emotion,
    mock_group_scene, mock_chatter, mock_schedule, mock_clock,
    config_dicts: list[dict] | None = None,
    max_concurrent=4,
) -> PeriodicEventSystem:
    """辅助函数：创建 PeriodicEventSystem 实例"""
    system = PeriodicEventSystem(
        llm=mock_llm,
        diary=mock_diary,
        memory=mock_memory,
        emotion=mock_emotion,
        group_scene=mock_group_scene,
        chatter=mock_chatter,
        schedule=mock_schedule,
        clock=mock_clock,
        max_concurrent_scenes=max_concurrent,
    )
    return system


@pytest.mark.asyncio
class TestPeriodicEventSystemInit:
    """初始化测试"""

    async def test_initialize_from_dicts(
        self, mock_llm, mock_diary, mock_memory, mock_emotion,
        mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        sample_event_config_dict,
    ):
        """从字典初始化"""
        system = _make_system(
            mock_llm, mock_diary, mock_memory, mock_emotion,
            mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        )
        await system.initialize_from_dicts([sample_event_config_dict])
        assert "test_fireworks" in system.event_configs

    async def test_disabled_system(
        self, mock_llm, mock_diary, mock_memory, mock_emotion,
        mock_group_scene, mock_chatter, mock_schedule, mock_clock,
    ):
        """禁用系统时不触发任何事件"""
        system = _make_system(
            mock_llm, mock_diary, mock_memory, mock_emotion,
            mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        )
        system.disable()
        assert system.enabled is False

        # tick 不应报错
        await system.tick(datetime(2024, 1, 6, 20, 0))
        assert len(system.active_events) == 0


@pytest.mark.asyncio
class TestPeriodicEventSystemTick:
    """Tick 逻辑测试"""

    async def test_pre_notice_triggers(
        self, mock_llm, mock_diary, mock_memory, mock_emotion,
        mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        sample_event_config_dict,
    ):
        """预告时间到达时应触发 PRE 阶段"""
        system = _make_system(
            mock_llm, mock_diary, mock_memory, mock_emotion,
            mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        )
        await system.initialize_from_dicts([sample_event_config_dict])

        # 周六 19:50（预告时间 = 20:00 - 15min = 19:45）
        game_time = datetime(2024, 1, 6, 19, 50)  # 周六
        await system.tick(game_time)

        assert "test_fireworks" in system.active_events
        assert system.active_events["test_fireworks"].phase == EventPhase.PRE

    async def test_event_starts_at_trigger_time(
        self, mock_llm, mock_diary, mock_memory, mock_emotion,
        mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        sample_event_config_dict,
    ):
        """到达触发时间时应进入 ACTIVE 阶段"""
        system = _make_system(
            mock_llm, mock_diary, mock_memory, mock_emotion,
            mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        )
        await system.initialize_from_dicts([sample_event_config_dict])

        # 先触发预告
        await system.tick(datetime(2024, 1, 6, 19, 50))  # 周六 19:50
        assert "test_fireworks" in system.active_events

        # 到达触发时间
        await system.tick(datetime(2024, 1, 6, 20, 0))  # 周六 20:00
        assert system.active_events["test_fireworks"].phase == EventPhase.ACTIVE

    async def test_event_ends_after_duration(
        self, mock_llm, mock_diary, mock_memory, mock_emotion,
        mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        sample_event_config_dict,
    ):
        """持续时间结束后应进入 POST 阶段并清理"""
        system = _make_system(
            mock_llm, mock_diary, mock_memory, mock_emotion,
            mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        )
        await system.initialize_from_dicts([sample_event_config_dict])

        # 预告 → 开始 → 结束
        await system.tick(datetime(2024, 1, 6, 19, 50))  # PRE
        await system.tick(datetime(2024, 1, 6, 20, 0))   # ACTIVE
        await system.tick(datetime(2024, 1, 6, 21, 1))   # POST (duration=60min)

        # 事件应已从活跃列表移除
        assert "test_fireworks" not in system.active_events

        # 日记应有预告和摘要
        categories = [e["category"] for e in mock_diary.entries]
        assert "event_preview" in categories
        assert "event_summary" in categories

    async def test_no_duplicate_trigger(
        self, mock_llm, mock_diary, mock_memory, mock_emotion,
        mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        sample_event_config_dict,
    ):
        """同一事件不应重复触发"""
        system = _make_system(
            mock_llm, mock_diary, mock_memory, mock_emotion,
            mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        )
        await system.initialize_from_dicts([sample_event_config_dict])

        # 连续两个 tick 在预告窗口内
        await system.tick(datetime(2024, 1, 6, 19, 50))
        await system.tick(datetime(2024, 1, 6, 19, 55))

        # 应只有一个活跃事件
        assert len(system.active_events) == 1

    async def test_wrong_day_no_trigger(
        self, mock_llm, mock_diary, mock_memory, mock_emotion,
        mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        sample_event_config_dict,
    ):
        """非触发日不应触发事件"""
        system = _make_system(
            mock_llm, mock_diary, mock_memory, mock_emotion,
            mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        )
        await system.initialize_from_dicts([sample_event_config_dict])

        # 周一 19:50（烟火大会配置为周六）
        game_time = datetime(2024, 1, 1, 19, 50)  # 周一
        assert game_time.weekday() == 0  # 确认是周一
        await system.tick(game_time)

        assert len(system.active_events) == 0

    async def test_daily_event_triggers(
        self, mock_llm, mock_diary, mock_memory, mock_emotion,
        mock_group_scene, mock_chatter, mock_schedule, mock_clock,
    ):
        """每日事件在任意日期都能触发"""
        daily_config = {
            "event_id": "daily_test",
            "name": "每日测试",
            "event_type": "scene",
            "trigger": {"trigger_type": "daily", "hour": 12, "minute": 0},
            "main_venue": "cafe",
            "slices": [
                {"slice_id": "main", "location": "cafe", "capacity": 5},
            ],
            "participation": {
                "required_npcs": ["robin"],
                "optional_npcs": [],
                "resident_probability": 0.0,
            },
            "duration_minutes": 30,
            "pre_notice_minutes": 10,
        }

        system = _make_system(
            mock_llm, mock_diary, mock_memory, mock_emotion,
            mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        )
        await system.initialize_from_dicts([daily_config])

        # 任意日期 11:55（预告时间）
        game_time = datetime(2024, 1, 3, 11, 55)  # 周三
        await system.tick(game_time)

        assert "daily_test" in system.active_events


@pytest.mark.asyncio
class TestPeriodicEventSystemStatus:
    """状态查询测试"""

    async def test_get_event_status(
        self, mock_llm, mock_diary, mock_memory, mock_emotion,
        mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        sample_event_config_dict,
    ):
        """获取事件状态"""
        system = _make_system(
            mock_llm, mock_diary, mock_memory, mock_emotion,
            mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        )
        await system.initialize_from_dicts([sample_event_config_dict])

        # 无活跃事件
        assert system.get_event_status("test_fireworks") is None

        # 触发后
        await system.tick(datetime(2024, 1, 6, 19, 50))
        status = system.get_event_status("test_fireworks")
        assert status is not None
        assert status["phase"] == "pre"
        assert status["event_id"] == "test_fireworks"

    async def test_concurrency_stats(
        self, mock_llm, mock_diary, mock_memory, mock_emotion,
        mock_group_scene, mock_chatter, mock_schedule, mock_clock,
    ):
        """获取并发统计"""
        system = _make_system(
            mock_llm, mock_diary, mock_memory, mock_emotion,
            mock_group_scene, mock_chatter, mock_schedule, mock_clock,
            max_concurrent=6,
        )
        stats = system.concurrency_stats
        assert stats["max_concurrent"] == 6
        assert stats["active_count"] == 0

    async def test_enable_disable(
        self, mock_llm, mock_diary, mock_memory, mock_emotion,
        mock_group_scene, mock_chatter, mock_schedule, mock_clock,
    ):
        """启用/禁用切换"""
        system = _make_system(
            mock_llm, mock_diary, mock_memory, mock_emotion,
            mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        )
        assert system.enabled is True
        system.disable()
        assert system.enabled is False
        system.enable()
        assert system.enabled is True
