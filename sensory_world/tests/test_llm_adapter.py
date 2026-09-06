"""RealLLMAdapter 测试：成功解析、失败重试、最终降级返空串。"""

from __future__ import annotations

import urllib.request

import pytest

from sensory_world.adapters import RealLLMAdapter, build_safe_llm

from .adapter_fakes import FakeLLMHttp


@pytest.mark.asyncio
async def test_llm_success_extracts_content(monkeypatch):
    fake = FakeLLMHttp(content="夜色真美。")
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    adapter = RealLLMAdapter(base_url="http://127.0.0.1:8089/v1/chat/completions")
    out = await adapter.chat([{"role": "user", "content": "写句台词"}])
    assert out == "夜色真美。"
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_llm_retries_then_succeeds(monkeypatch):
    # 前 1 次连接失败，第 2 次成功
    fake = FakeLLMHttp(content="好了。", fail_times=1)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    adapter = RealLLMAdapter(max_retries=2)
    out = await adapter.chat([{"role": "user", "content": "hi"}])
    assert out == "好了。"
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_llm_all_fail_returns_empty(monkeypatch):
    # 一直失败 → 重试耗尽 → 返回空串（由 SafeLLMClient 兜底）
    fake = FakeLLMHttp(content="x", fail_times=99)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    adapter = RealLLMAdapter(max_retries=1, timeout_seconds=0.01)
    out = await adapter.chat([{"role": "user", "content": "hi"}])
    assert out == ""


@pytest.mark.asyncio
async def test_build_safe_llm_falls_back_to_template(monkeypatch):
    """裸适配器返空串时，SafeLLMClient 走模板兜底，不抛异常。"""
    fake = FakeLLMHttp(content="x", fail_times=99)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    safe = build_safe_llm(max_retries=0)
    out = await safe.chat([{"role": "user", "content": "请写一封信"}])
    # 应返回非空兜底文本
    assert isinstance(out, str) and out.strip()
