"""
并发护栏测试 —— 验证 ConcurrencyGuard 的并发控制
"""

import asyncio
import pytest

from sensory_world.periodic_event.concurrency_guard import (
    ConcurrencyGuard,
    ConcurrencyRejectError,
    run_with_guard,
)


@pytest.mark.asyncio
class TestConcurrencyGuard:
    """并发护栏核心测试"""

    async def test_basic_acquire_release(self):
        """基本获取和释放"""
        guard = ConcurrencyGuard(max_concurrent=2)
        slot = await guard.acquire("test_slot")
        assert guard.active_count == 1
        async with slot:
            assert guard.active_count == 1
        assert guard.active_count == 0

    async def test_max_concurrent_limit(self):
        """超过最大并发时阻塞"""
        guard = ConcurrencyGuard(max_concurrent=1, acquire_timeout=0.1)

        # 获取第一个槽
        slot1 = await guard.acquire("slot_1")

        # 第二个应该超时
        with pytest.raises(ConcurrencyRejectError):
            await guard.acquire("slot_2")

        # 释放后可以再获取
        await guard._release("slot_1")
        slot3 = await guard.acquire("slot_3")
        assert guard.active_count == 1
        await guard._release("slot_3")

    async def test_concurrent_execution(self):
        """并发执行多个任务"""
        guard = ConcurrencyGuard(max_concurrent=3)
        results = []

        async def task(name: str):
            slot = await guard.acquire(name)
            async with slot:
                results.append(f"start_{name}")
                await asyncio.sleep(0.01)
                results.append(f"end_{name}")

        await asyncio.gather(
            task("a"), task("b"), task("c")
        )

        assert len(results) == 6
        assert guard.stats.total_acquired == 3
        assert guard.stats.total_released == 3
        assert guard.active_count == 0

    async def test_stats_tracking(self):
        """统计信息跟踪"""
        guard = ConcurrencyGuard(max_concurrent=1, acquire_timeout=0.05)

        slot = await guard.acquire("test")
        assert guard.stats.total_acquired == 1
        await guard._release("test")
        assert guard.stats.total_released == 1

        # 超时导致拒绝（max_concurrent=1，占满后第二个应超时）
        s1 = await guard.acquire("s1")
        with pytest.raises(ConcurrencyRejectError):
            await guard.acquire("s2")
        assert guard.stats.total_rejected == 1
        assert guard.stats.total_timed_out == 1
        await guard._release("s1")

    async def test_get_active_slots(self):
        """获取活跃槽位列表"""
        guard = ConcurrencyGuard(max_concurrent=5)
        s1 = await guard.acquire("alpha")
        s2 = await guard.acquire("beta")

        active = guard.get_active_slots()
        assert "alpha" in active
        assert "beta" in active

        await guard._release("alpha")
        active = guard.get_active_slots()
        assert "alpha" not in active
        assert "beta" in active

        await guard._release("beta")

    async def test_update_max_concurrent(self):
        """动态更新最大并发数"""
        guard = ConcurrencyGuard(max_concurrent=2)
        assert guard.max_concurrent == 2
        guard.update_max_concurrent(5)
        assert guard.max_concurrent == 5

    async def test_invalid_max_concurrent(self):
        """无效的最大并发数"""
        guard = ConcurrencyGuard(max_concurrent=2)
        with pytest.raises(ValueError):
            guard.update_max_concurrent(0)


@pytest.mark.asyncio
class TestRunWithGuard:
    """run_with_guard 辅助函数测试"""

    async def test_successful_execution(self):
        """正常执行"""
        guard = ConcurrencyGuard(max_concurrent=2)

        async def my_task():
            return 42

        result = await run_with_guard(guard, "test", my_task)
        assert result == 42

    async def test_fallback_on_reject(self):
        """被拒绝时返回降级值"""
        guard = ConcurrencyGuard(max_concurrent=1, acquire_timeout=0.05)

        # 占满并发
        slot = await guard.acquire("blocker")

        async def my_task():
            return 42

        result = await run_with_guard(guard, "test", my_task, fallback=-1)
        assert result == -1

        await guard._release("blocker")

    async def test_fallback_on_exception(self):
        """任务异常时正常传播"""
        guard = ConcurrencyGuard(max_concurrent=2)

        async def failing_task():
            raise ValueError("任务失败")

        with pytest.raises(ValueError, match="任务失败"):
            await run_with_guard(guard, "test", failing_task)
