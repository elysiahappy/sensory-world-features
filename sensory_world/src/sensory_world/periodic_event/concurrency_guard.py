"""
周期事件系统 —— 并发护栏

控制同一时刻 LLM 群聊的并发数量，防止推理槽位被打爆。
背景：50 个 NPC 共享 8 个推理槽，峰值并发必须可控。

核心机制：
  - 基于 asyncio.Semaphore 的令牌桶
  - 支持全局并发上限和每事件并发上限
  - 超额请求进入等待队列，超时则降级跳过
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass
class ConcurrencyStats:
    """并发统计信息（用于监控和调试）"""
    active_count: int = 0
    total_acquired: int = 0
    total_released: int = 0
    total_rejected: int = 0
    total_timed_out: int = 0


class ConcurrencyGuard:
    """
    并发护栏 —— 控制 LLM 群聊的并发数量。

    用法：
        guard = ConcurrencyGuard(max_concurrent=4)
        async with guard.acquire("fireworks_night:main_stage"):
            # 在此并发槽内执行 LLM 群聊
            await group_scene.start_scene(...)

    配置项：
        max_concurrent: 全局最大并发 LLM 群聊数（默认 4）
        acquire_timeout: 获取并发槽的超时秒数（默认 30）
        on_reject: 被拒绝时的回调（可选）
    """

    def __init__(
        self,
        max_concurrent: int = 4,
        acquire_timeout: float = 30.0,
        on_reject: Callable[[str], Any] | None = None,
    ):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._acquire_timeout = acquire_timeout
        self._on_reject = on_reject
        self._stats = ConcurrencyStats()
        self._active_slots: set[str] = set()
        self._lock = asyncio.Lock()

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    @property
    def active_count(self) -> int:
        return self._stats.active_count

    @property
    def stats(self) -> ConcurrencyStats:
        return self._stats

    async def acquire(self, slot_name: str) -> ConcurrencySlot:
        """
        获取一个并发槽。
        :param slot_name: 槽位名称（用于日志和追踪，如 'fireworks_night:main_stage'）
        :return: ConcurrencySlot 上下文管理器
        :raises ConcurrencyRejectError: 超时无法获取槽位
        """
        try:
            acquired = await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=self._acquire_timeout,
            )
        except asyncio.TimeoutError:
            self._stats.total_rejected += 1
            self._stats.total_timed_out += 1
            logger.warning(
                "并发槽获取超时 (%.1fs): %s（当前活跃: %d/%d）",
                self._acquire_timeout, slot_name,
                self._stats.active_count, self._max_concurrent,
            )
            if self._on_reject:
                self._on_reject(slot_name)
            raise ConcurrencyRejectError(
                f"并发槽获取超时: {slot_name}（{self._stats.active_count}/{self._max_concurrent}）"
            )

        async with self._lock:
            self._stats.active_count += 1
            self._stats.total_acquired += 1
            self._active_slots.add(slot_name)

        logger.debug(
            "并发槽已获取: %s（活跃: %d/%d）",
            slot_name, self._stats.active_count, self._max_concurrent,
        )
        return ConcurrencySlot(self, slot_name)

    async def _release(self, slot_name: str) -> None:
        """释放一个并发槽"""
        async with self._lock:
            self._stats.active_count -= 1
            self._stats.total_released += 1
            self._active_slots.discard(slot_name)
        self._semaphore.release()
        logger.debug(
            "并发槽已释放: %s（活跃: %d/%d）",
            slot_name, self._stats.active_count, self._max_concurrent,
        )

    def get_active_slots(self) -> list[str]:
        """获取当前活跃的槽位名称列表"""
        return list(self._active_slots)

    def update_max_concurrent(self, new_max: int) -> None:
        """
        动态更新最大并发数（运行时调整）。
        注意：只影响后续获取，不影响已持有的槽位。
        """
        if new_max < 1:
            raise ValueError("最大并发数至少为 1")
        old_max = self._max_concurrent
        self._max_concurrent = new_max
        # 重建 semaphore（简单实现：创建新的）
        diff = new_max - old_max
        if diff > 0:
            # 增加容量：释放额外的令牌
            for _ in range(diff):
                self._semaphore.release()
        logger.info("并发上限已更新: %d → %d", old_max, new_max)


class ConcurrencySlot:
    """
    并发槽上下文管理器 —— 使用 async with 自动获取/释放。
    """

    def __init__(self, guard: ConcurrencyGuard, slot_name: str):
        self._guard = guard
        self._slot_name = slot_name

    async def __aenter__(self) -> ConcurrencySlot:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self._guard._release(self._slot_name)


class ConcurrencyRejectError(Exception):
    """并发槽获取被拒绝时抛出"""
    pass


async def run_with_guard(
    guard: ConcurrencyGuard,
    slot_name: str,
    coro_func: Callable[[], Awaitable[Any]],
    fallback: Any = None,
) -> Any:
    """
    在并发护栏保护下执行协程。
    获取槽位失败时返回 fallback 值而非抛出异常。

    :param guard: 并发护栏
    :param slot_name: 槽位名称
    :param coro_func: 要执行的协程函数
    :param fallback: 获取失败时的降级返回值
    :return: 协程结果或 fallback
    """
    try:
        slot = await guard.acquire(slot_name)
    except ConcurrencyRejectError:
        logger.warning("并发护栏拒绝，使用降级值: %s", slot_name)
        return fallback

    async with slot:
        return await coro_func()
