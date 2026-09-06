"""
LLM 客户端测试 —— 验证 SafeLLMClient 和 FallbackLLMClient
"""

import pytest

from sensory_world.llm_client import SafeLLMClient, FallbackLLMClient

from tests.mocks import MockLLMClient, FailingLLMClient


@pytest.mark.asyncio
class TestFallbackLLMClient:
    """降级 LLM 客户端测试"""

    async def test_keyword_matching(self):
        """关键词匹配返回模板回复"""
        client = FallbackLLMClient()
        result = await client.chat([
            {"role": "user", "content": "请描述一下你对这次活动的印象"},
        ])
        assert "铭记" in result or "时光" in result

    async def test_default_response(self):
        """无匹配关键词时返回默认回复"""
        client = FallbackLLMClient()
        result = await client.chat([
            {"role": "user", "content": "今天天气怎么样"},
        ])
        assert result == FallbackLLMClient._DEFAULT

    async def test_empty_messages(self):
        """空消息列表"""
        client = FallbackLLMClient()
        result = await client.chat([])
        assert result == FallbackLLMClient._DEFAULT


@pytest.mark.asyncio
class TestSafeLLMClient:
    """安全包装器测试"""

    async def test_primary_success(self):
        """主客户端成功时直接返回"""
        mock = MockLLMClient(responses={"测试": "成功回复"})
        safe = SafeLLMClient(primary=mock)
        result = await safe.chat([
            {"role": "user", "content": "这是一个测试"},
        ])
        assert result == "成功回复"
        assert mock.call_count == 1

    async def test_primary_fails_fallback(self):
        """主客户端失败时降级"""
        failing = FailingLLMClient()
        safe = SafeLLMClient(primary=failing)
        result = await safe.chat([
            {"role": "user", "content": "测试"},
        ])
        # 应返回降级回复而非抛出异常
        assert isinstance(result, str)
        assert len(result) > 0

    async def test_retry_mechanism(self):
        """重试机制"""
        call_count = 0

        class FlakyClient:
            async def chat(self, messages):
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    raise ConnectionError("暂时不可用")
                return "第三次成功"

        safe = SafeLLMClient(primary=FlakyClient(), max_retries=3)
        result = await safe.chat([{"role": "user", "content": "测试"}])
        assert result == "第三次成功"
        assert call_count == 3

    async def test_empty_response_triggers_retry(self):
        """空响应触发重试"""
        call_count = 0

        class EmptyClient:
            async def chat(self, messages):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return ""  # 空响应
                return "有内容了"

        safe = SafeLLMClient(primary=EmptyClient(), max_retries=2)
        result = await safe.chat([{"role": "user", "content": "测试"}])
        assert result == "有内容了"

    async def test_both_fail(self):
        """主客户端和降级客户端都失败"""
        class AlwaysFail:
            async def chat(self, messages):
                raise RuntimeError("彻底挂了")

        safe = SafeLLMClient(
            primary=AlwaysFail(),
            fallback=AlwaysFail(),
            max_retries=1,
        )
        result = await safe.chat([{"role": "user", "content": "测试"}])
        # 应返回硬编码默认值
        assert result == FallbackLLMClient._DEFAULT
