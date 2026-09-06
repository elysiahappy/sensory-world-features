"""
RealLLMAdapter —— 主项目本地大模型（OpenAI /v1/chat/completions，8089）适配。

勘测事实（事实 9）：主项目直配 OpenAI 兼容接口
    POST http://127.0.0.1:8089/v1/chat/completions
    模型 Qwen3-8B，-np 8（8 推理槽）；创意后端类名 LLMBackend5080。

本适配器只负责"按 OpenAI 协议发请求、拿回复文本"，并自带：
  - 超时、有限重试（网络抖动/槽位忙）；
  - 失败时**返回空串**而非抛异常（配合三阶段的 SafeLLMClient/FallbackLLMClient
    做模板兜底）；推荐用法见模块末尾文档：把本适配器包进 SafeLLMClient。

零额外依赖：HTTP 调用用标准库 urllib（在线程池里跑，不阻塞事件循环）。
生产环境若已有 openai/httpx，也可替换 _http_post 实现。
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from typing import Any

from ..protocols import LLMClientProtocol

logger = logging.getLogger(__name__)


class RealLLMAdapter:
    """OpenAI 兼容 /v1/chat/completions → LLMClientProtocol。"""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8089/v1/chat/completions",
        model: str = "Qwen3-8B",
        api_key: str = "not-needed",
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        temperature: float = 0.8,
    ) -> None:
        self._url = base_url
        self._model = model
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._temperature = temperature

    async def chat(self, messages: list[dict[str, str]]) -> str:
        """
        发送对话，返回回复文本。

        :return: 成功返回模型文本；**失败返回空串 ""**（由上层 SafeLLMClient
                 判定为空后走模板兜底，绝不抛异常打断城市模拟）。
        """
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "stream": False,
        }
        try:
            # 同步 HTTP 放线程池，避免阻塞 asyncio 事件循环
            return await asyncio.to_thread(self._chat_sync, payload)
        except Exception as err:  # noqa: BLE001
            logger.warning("LLM 调用最终失败（将走兜底）：%s", err)
            return ""

    # ---------- 同步实现（线程池内执行） ----------

    def _chat_sync(self, payload: dict[str, Any]) -> str:
        last_err: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                raw = self._http_post(payload)
                text = self._extract_content(raw)
                if text:
                    return text
                # 空回复也算失败，触发重试
                last_err = ValueError("模型返回空内容")
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as err:
                # 网络类错误：槽位忙/服务未起，退避后重试
                last_err = err
                if attempt < self._max_retries:
                    asyncio_sleep = 0.2 * (attempt + 1)
                    # 线程池里用 time.sleep 做退避
                    import time

                    time.sleep(asyncio_sleep)
                    continue
            except Exception as err:  # noqa: BLE001 - 协议/解析错误不重试
                logger.warning("LLM 响应解析失败：%s", err)
                return ""
        logger.warning("LLM 重试耗尽：%s", last_err)
        return ""

    def _http_post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """用标准库发 POST，返回解析后的 JSON dict。"""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self._url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
            data = resp.read().decode("utf-8")
        return json.loads(data)

    @staticmethod
    def _extract_content(data: dict[str, Any]) -> str:
        """从 OpenAI 响应中抽取首个 choice 的文本。"""
        try:
            choices = data.get("choices") or []
            if not choices:
                return ""
            msg = choices[0].get("message") or {}
            content = msg.get("content")
            if isinstance(content, str):
                return content.strip()
            # 部分后端把 reasoning/内容分块，做兜底拼接
            if isinstance(content, list):
                parts = [c.get("text", "") for c in content if isinstance(c, dict)]
                return "".join(parts).strip()
        except (AttributeError, IndexError, TypeError) as err:
            logger.warning("LLM 响应结构异常：%s", err)
        return ""


def build_safe_llm(
    base_url: str = "http://127.0.0.1:8089/v1/chat/completions",
    model: str = "Qwen3-8B",
    **kwargs: Any,
) -> object:
    """
    便捷工厂：把 RealLLMAdapter 包进三阶段已有的 SafeLLMClient，
    得到"重试 + 超时 + 模板兜底"一体的 LLM 客户端，直接注入各功能模块。

    导入放在函数内，避免仅用裸适配器时产生循环依赖。
    """
    from ..llm_client import SafeLLMClient

    adapter = RealLLMAdapter(base_url=base_url, model=model, **kwargs)
    # SafeLLMClient(primary)：primary 失败/返回空串时，自动降级到默认 FallbackLLMClient 模板
    return SafeLLMClient(adapter)
