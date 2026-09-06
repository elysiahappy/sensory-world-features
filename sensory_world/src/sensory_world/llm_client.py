"""
LLM 客户端 —— 统一的大语言模型调用抽象

提供 LLMClient 基类与降级包装器，确保 LLM 不可用时不会崩溃。
所有需要 LLM 的模块统一通过此模块获取客户端实例。
"""

from __future__ import annotations

import logging
from typing import Any

from sensory_world.protocols import LLMClientProtocol

logger = logging.getLogger(__name__)


class FallbackLLMClient:
    """
    降级 LLM 客户端 —— 当真实 LLM 不可用时使用。
    根据消息内容返回模板化回复，保证系统不崩溃。
    """

    # 关键词 → 模板回复映射
    _TEMPLATES: dict[str, str] = {
        "印象": "那是一段值得铭记的时光。",
        "画面": "眼前的景象令人难忘。",
        "信件": "见字如面，愿一切安好。",
        "回忆": "那些日子仿佛就在昨天。",
        "总结": "今天发生了许多故事。",
    }

    _DEFAULT = "这座城市又度过了平凡的一天。"

    async def chat(self, messages: list[dict[str, str]]) -> str:
        """根据消息内容匹配模板返回兜底文本"""
        # 取最后一条用户消息做关键词匹配
        user_msg = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_msg = msg.get("content", "")
                break

        for keyword, template in self._TEMPLATES.items():
            if keyword in user_msg:
                logger.debug("FallbackLLMClient: 命中关键词 '%s'，使用模板回复", keyword)
                return template

        logger.debug("FallbackLLMClient: 未命中关键词，使用默认回复")
        return self._DEFAULT


class SafeLLMClient:
    """
    安全包装器 —— 包装真实 LLM 客户端，调用失败时自动降级。

    用法：
        real_client = MyRealLLMClient(...)
        safe_client = SafeLLMClient(real_client, fallback=FallbackLLMClient())
        result = await safe_client.chat(messages)  # 失败时自动降级
    """

    def __init__(
        self,
        primary: LLMClientProtocol,
        fallback: LLMClientProtocol | None = None,
        max_retries: int = 1,
    ):
        """
        :param primary: 主 LLM 客户端
        :param fallback: 降级客户端，默认使用 FallbackLLMClient
        :param max_retries: 主客户端最大重试次数
        """
        self._primary = primary
        self._fallback = fallback or FallbackLLMClient()
        self._max_retries = max_retries

    async def chat(self, messages: list[dict[str, str]]) -> str:
        """
        调用 LLM，失败时降级。
        降级时记录 warning 日志但不抛出异常。
        """
        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                result = await self._primary.chat(messages)
                if result and result.strip():
                    return result
                logger.warning("LLM 返回空结果（第 %d 次尝试）", attempt)
            except Exception as e:
                last_error = e
                logger.warning(
                    "LLM 调用失败（第 %d/%d 次尝试）: %s",
                    attempt, self._max_retries, e,
                )

        # 所有重试均失败，降级
        logger.warning(
            "LLM 主客户端不可用，启用降级回复。最后错误: %s",
            last_error,
        )
        try:
            return await self._fallback.chat(messages)
        except Exception as fallback_err:
            logger.error("降级客户端也失败: %s", fallback_err)
            return FallbackLLMClient._DEFAULT
