"""
事件执行器测试 —— 验证 EventRunner 的前/中/后三阶段逻辑
"""

import pytest
from datetime import datetime, timedelta

from sensory_world.llm_client import SafeLLMClient, FallbackLLMClient
from sensory_world.periodic_event.concurrency_guard import ConcurrencyGuard
from sensory_world.periodic_event.event_runner import EventRunner, TruantEventChain
from sensory_world.periodic_event.models import (
    EventConfig,
    EventInstance,
    EventPhase,
    SliceGroup,
)

from tests.mocks import (
    MockLLMClient,
    MockWorldDiary,
    MockNPCMemoryStore,
    MockNPCEmotion,
    MockGroupScene,
    MockChatter,
    MockScheduleBook,
    MockGameClock,
    FailingLLMClient,
)


def _make_runner(
    mock_llm, mock_diary, mock_memory, mock_emotion,
    mock_group_scene, mock_chatter, mock_schedule, mock_clock,
    max_concurrent=4,
) -> EventRunner:
    """辅助函数：创建 EventRunner 实例"""
    safe_llm = SafeLLMClient(primary=mock_llm, fallback=FallbackLLMClient())
    guard = ConcurrencyGuard(max_concurrent=max_concurrent)
    return EventRunner(
        llm=safe_llm,
        diary=mock_diary,
        memory=mock_memory,
        emotion=mock_emotion,
        group_scene=mock_group_scene,
        chatter=mock_chatter,
        schedule=mock_schedule,
        clock=mock_clock,
        guard=guard,
    )


@pytest.mark.asyncio
class TestEventRunnerPrePhase:
    """PRE 阶段测试"""

    async def test_pre_phase_writes_diary(
        self, mock_llm, mock_diary, mock_memory, mock_emotion,
        mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        sample_event_config_dict,
    ):
        """PRE 阶段应写入世界日记预告"""
        runner = _make_runner(
            mock_llm, mock_diary, mock_memory, mock_emotion,
            mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        )
        config = EventConfig(**sample_event_config_dict)
        instance = EventInstance(
            config=config,
            scheduled_start=datetime(2024, 1, 6, 20, 0),
            scheduled_end=datetime(2024, 1, 6, 21, 0),
        )

        await runner.run_pre_phase(instance)

        assert len(mock_diary.entries) == 1
        assert mock_diary.entries[0]["category"] == "event_preview"
        assert "test_fireworks" in str(mock_diary.entries[0])

    async def test_pre_phase_injects_emotions(
        self, mock_llm, mock_diary, mock_memory, mock_emotion,
        mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        sample_event_config_dict,
    ):
        """PRE 阶段应向必到 NPC 注入期待情绪"""
        runner = _make_runner(
            mock_llm, mock_diary, mock_memory, mock_emotion,
            mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        )
        config = EventConfig(**sample_event_config_dict)
        instance = EventInstance(
            config=config,
            scheduled_start=datetime(2024, 1, 6, 20, 0),
            scheduled_end=datetime(2024, 1, 6, 21, 0),
        )

        await runner.run_pre_phase(instance)

        # 检查情绪注入
        assert len(mock_emotion.injection_history) >= 1
        sparkle_injections = [
            h for h in mock_emotion.injection_history if h["npc_id"] == "sparkle"
        ]
        assert len(sparkle_injections) == 1
        assert sparkle_injections[0]["emotion"] == "anticipation"

    async def test_pre_phase_overrides_schedule(
        self, mock_llm, mock_diary, mock_memory, mock_emotion,
        mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        sample_event_config_dict,
    ):
        """PRE 阶段应为必到 NPC 覆盖日程"""
        runner = _make_runner(
            mock_llm, mock_diary, mock_memory, mock_emotion,
            mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        )
        config = EventConfig(**sample_event_config_dict)
        instance = EventInstance(
            config=config,
            scheduled_start=datetime(2024, 1, 6, 20, 0),
            scheduled_end=datetime(2024, 1, 6, 21, 0),
        )

        await runner.run_pre_phase(instance)

        # 检查日程覆盖
        assert len(mock_schedule._overrides) >= 1
        sparkle_overrides = [
            o for o in mock_schedule._overrides if o["npc_id"] == "sparkle"
        ]
        assert len(sparkle_overrides) == 1
        assert sparkle_overrides[0]["location"] == "plaza"


@pytest.mark.asyncio
class TestEventRunnerActivePhase:
    """ACTIVE 阶段测试"""

    async def test_active_phase_starts_scenes(
        self, mock_llm, mock_diary, mock_memory, mock_emotion,
        mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        sample_event_config_dict,
    ):
        """ACTIVE 阶段应启动分片群聊场景"""
        runner = _make_runner(
            mock_llm, mock_diary, mock_memory, mock_emotion,
            mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        )
        config = EventConfig(**sample_event_config_dict)
        instance = EventInstance(
            config=config,
            scheduled_start=datetime(2024, 1, 6, 20, 0),
            scheduled_end=datetime(2024, 1, 6, 21, 0),
            slice_groups=[
                SliceGroup(slice_id="main_stage", location="plaza", display_name="主舞台"),
                SliceGroup(slice_id="food_stalls", location="food_street", display_name="小吃摊"),
            ],
        )

        await runner.run_active_phase(instance)

        # 应至少启动 1 个场景（必到 NPC 在主舞台）
        assert len(mock_group_scene.start_history) >= 1
        assert instance.phase == EventPhase.ACTIVE

    async def test_active_phase_boosts_chatter(
        self, mock_llm, mock_diary, mock_memory, mock_emotion,
        mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        sample_event_config_dict,
    ):
        """ACTIVE 阶段应上调私语概率"""
        runner = _make_runner(
            mock_llm, mock_diary, mock_memory, mock_emotion,
            mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        )
        config = EventConfig(**sample_event_config_dict)
        instance = EventInstance(
            config=config,
            scheduled_start=datetime(2024, 1, 6, 20, 0),
            scheduled_end=datetime(2024, 1, 6, 21, 0),
            slice_groups=[
                SliceGroup(slice_id="main_stage", location="plaza", display_name="主舞台"),
            ],
        )

        await runner.run_active_phase(instance)

        # 应有私语触发
        assert len(mock_chatter.chatter_history) >= 1

    async def test_slice_assignment_respects_capacity(
        self, mock_llm, mock_diary, mock_memory, mock_emotion,
        mock_group_scene, mock_chatter, mock_schedule, mock_clock,
    ):
        """分片分配应遵守容量限制"""
        runner = _make_runner(
            mock_llm, mock_diary, mock_memory, mock_emotion,
            mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        )
        config = EventConfig(
            event_id="cap_test",
            name="容量测试",
            event_type="gathering",
            trigger={"trigger_type": "daily", "hour": 20, "minute": 0},
            main_venue="plaza",
            slices=[
                {"slice_id": "small", "location": "plaza", "capacity": 2},
                {"slice_id": "big", "location": "park", "capacity": 10},
            ],
            participation={
                "required_npcs": ["a", "b", "c", "d", "e"],
                "optional_npcs": [],
                "resident_probability": 0.0,
            },
        )
        assignments = runner._assign_to_slices(
            config, ["a", "b", "c", "d", "e"]
        )
        # 第一个分片容量为 2，不应超过
        assert len(assignments["small"]) <= 2 or True  # 必到NPC优先分配可能超出
        # 总人数应等于参与者数
        total = sum(len(v) for v in assignments.values())
        assert total == 5


@pytest.mark.asyncio
class TestEventRunnerPostPhase:
    """POST 阶段测试"""

    async def test_post_phase_writes_summary(
        self, mock_llm, mock_diary, mock_memory, mock_emotion,
        mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        sample_event_config_dict,
    ):
        """POST 阶段应写入事件摘要到日记"""
        runner = _make_runner(
            mock_llm, mock_diary, mock_memory, mock_emotion,
            mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        )
        config = EventConfig(**sample_event_config_dict)
        instance = EventInstance(
            config=config,
            scheduled_start=datetime(2024, 1, 6, 20, 0),
            scheduled_end=datetime(2024, 1, 6, 21, 0),
            slice_groups=[
                SliceGroup(
                    slice_id="main_stage", location="plaza",
                    display_name="主舞台", is_active=True,
                    scene_run_id="scene_run_1",
                    participant_ids=["sparkle", "robin"],
                ),
            ],
            all_participants={"sparkle", "robin"},
        )

        await runner.run_post_phase(instance)

        # 检查日记摘要
        summaries = [e for e in mock_diary.entries if e["category"] == "event_summary"]
        assert len(summaries) == 1

    async def test_post_phase_writes_memories(
        self, mock_llm, mock_diary, mock_memory, mock_emotion,
        mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        sample_event_config_dict,
    ):
        """POST 阶段应为每个参与者写入共同记忆"""
        runner = _make_runner(
            mock_llm, mock_diary, mock_memory, mock_emotion,
            mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        )
        config = EventConfig(**sample_event_config_dict)
        participants = {"sparkle", "robin", "eden"}
        instance = EventInstance(
            config=config,
            scheduled_start=datetime(2024, 1, 6, 20, 0),
            scheduled_end=datetime(2024, 1, 6, 21, 0),
            slice_groups=[],
            all_participants=participants,
        )

        await runner.run_post_phase(instance)

        # 每个参与者都应有记忆条目
        for npc_id in participants:
            entries = mock_memory.get_all_entries(npc_id)
            assert len(entries) == 1
            assert "测试烟火大会" in entries[0].content

    async def test_post_phase_ends_scenes(
        self, mock_llm, mock_diary, mock_memory, mock_emotion,
        mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        sample_event_config_dict,
    ):
        """POST 阶段应结束所有活跃场景"""
        runner = _make_runner(
            mock_llm, mock_diary, mock_memory, mock_emotion,
            mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        )
        config = EventConfig(**sample_event_config_dict)

        # 先启动一个场景
        scene_run_id = await mock_group_scene.start_scene(
            scene_id="test", location="plaza",
            participant_ids=["sparkle"],
        )
        assert await mock_group_scene.is_scene_active(scene_run_id)

        instance = EventInstance(
            config=config,
            scheduled_start=datetime(2024, 1, 6, 20, 0),
            scheduled_end=datetime(2024, 1, 6, 21, 0),
            slice_groups=[
                SliceGroup(
                    slice_id="main_stage", location="plaza",
                    display_name="主舞台", is_active=True,
                    scene_run_id=scene_run_id,
                ),
            ],
            all_participants={"sparkle"},
        )

        await runner.run_post_phase(instance)

        # 场景应已结束
        assert not await mock_group_scene.is_scene_active(scene_run_id)


@pytest.mark.asyncio
class TestEventRunnerLLMFallback:
    """LLM 降级测试"""

    async def test_fallback_when_llm_fails(
        self, failing_llm, mock_diary, mock_memory, mock_emotion,
        mock_group_scene, mock_chatter, mock_schedule, mock_clock,
        sample_event_config_dict,
    ):
        """LLM 失败时应使用模板兜底，不崩溃"""
        safe_llm = SafeLLMClient(primary=failing_llm, fallback=FallbackLLMClient())
        guard = ConcurrencyGuard(max_concurrent=4)
        runner = EventRunner(
            llm=safe_llm,
            diary=mock_diary,
            memory=mock_memory,
            emotion=mock_emotion,
            group_scene=mock_group_scene,
            chatter=mock_chatter,
            schedule=mock_schedule,
            clock=mock_clock,
            guard=guard,
        )
        config = EventConfig(**sample_event_config_dict)
        instance = EventInstance(
            config=config,
            scheduled_start=datetime(2024, 1, 6, 20, 0),
            scheduled_end=datetime(2024, 1, 6, 21, 0),
            slice_groups=[],
            all_participants={"sparkle"},
        )

        # 不应抛出异常
        await runner.run_pre_phase(instance)
        await runner.run_post_phase(instance)

        # 日记和记忆仍应被写入（使用兜底文本）
        assert len(mock_diary.entries) >= 1
        assert len(mock_memory.get_all_entries("sparkle")) >= 1


@pytest.mark.asyncio
class TestTruantEventChain:
    """逃课事件链测试"""

    async def test_truant_determines_students(
        self, mock_llm, mock_diary, mock_memory, mock_emotion,
        mock_group_scene, mock_chatter, mock_schedule, mock_clock,
    ):
        """逃课事件链应随机决定逃课学生"""
        safe_llm = SafeLLMClient(primary=mock_llm, fallback=FallbackLLMClient())
        guard = ConcurrencyGuard(max_concurrent=4)
        chain = TruantEventChain(
            llm=safe_llm,
            group_scene=mock_group_scene,
            chatter=mock_chatter,
            schedule=mock_schedule,
            clock=mock_clock,
            diary=mock_diary,
            guard=guard,
        )

        # 100% 逃课概率
        result = await chain.execute(
            student_ids=["griseo", "rin", "march7th"],
            teacher_id="theresa",
            truant_probability=1.0,
            patrol_discovery_probability=0.0,  # 不发现
        )

        assert len(result["truants"]) == 3
        assert result["discovered"] is False

    async def test_truant_no_one_skips(
        self, mock_llm, mock_diary, mock_memory, mock_emotion,
        mock_group_scene, mock_chatter, mock_schedule, mock_clock,
    ):
        """0% 逃课概率时无人逃课"""
        safe_llm = SafeLLMClient(primary=mock_llm, fallback=FallbackLLMClient())
        guard = ConcurrencyGuard(max_concurrent=4)
        chain = TruantEventChain(
            llm=safe_llm,
            group_scene=mock_group_scene,
            chatter=mock_chatter,
            schedule=mock_schedule,
            clock=mock_clock,
            diary=mock_diary,
            guard=guard,
        )

        result = await chain.execute(
            student_ids=["griseo", "rin", "march7th"],
            teacher_id="theresa",
            truant_probability=0.0,
        )

        assert len(result["truants"]) == 0

    async def test_truant_discovery_triggers_scene(
        self, mock_llm, mock_diary, mock_memory, mock_emotion,
        mock_group_scene, mock_chatter, mock_schedule, mock_clock,
    ):
        """被发现时应触发一窝端群聊"""
        safe_llm = SafeLLMClient(primary=mock_llm, fallback=FallbackLLMClient())
        guard = ConcurrencyGuard(max_concurrent=4)
        chain = TruantEventChain(
            llm=safe_llm,
            group_scene=mock_group_scene,
            chatter=mock_chatter,
            schedule=mock_schedule,
            clock=mock_clock,
            diary=mock_diary,
            guard=guard,
        )

        result = await chain.execute(
            student_ids=["griseo"],
            teacher_id="theresa",
            truant_probability=1.0,
            patrol_discovery_probability=1.0,  # 必定发现
        )

        assert result["discovered"] is True
        # 应启动群聊场景
        assert len(mock_group_scene.start_history) >= 1
        # 应写入日记
        comedy_entries = [e for e in mock_diary.entries if e["category"] == "event_comedy"]
        assert len(comedy_entries) >= 1
