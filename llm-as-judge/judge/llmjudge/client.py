"""DeepSeek 客户端：严格 JSON 输出，失败最多重试一次。

与生产 deepseek_gateway 同一纪律：超时 / 限流 / 5xx / 结构错误重试一次，
仍失败则抛 JudgeUnavailable，由上层标记待人工评审——评测绝不静默放行。
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
TIMEOUT_SECONDS = 30.0
MAX_ATTEMPTS = 2


class JudgeUnavailable(RuntimeError):
    """模型不可用或输出无法解析，评测结果需要人工接管。"""


class DeepSeekClient:
    """最小 chat-completions 封装，仅服务评测，不做业务路由。"""

    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL) -> None:
        self._api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self._model = model

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        """调用模型并解析 JSON 对象；两次尝试均失败抛 JudgeUnavailable。"""
        if not self.available:
            raise JudgeUnavailable("DEEPSEEK_API_KEY 未配置")
        last_error = ""
        for _ in range(MAX_ATTEMPTS):
            try:
                payload = self._request(system, user)
                return self._parse(payload)
            except (httpx.HTTPError, ValueError) as exc:
                last_error = str(exc)
        raise JudgeUnavailable(f"LLM Judge 两次尝试失败: {last_error}")

    def _request(self, system: str, user: str) -> str:
        request = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
        }
        response = httpx.post(
            f"{DEFAULT_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json=request,
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
        return str(body["choices"][0]["message"]["content"])

    def _parse(self, content: str) -> dict[str, Any]:
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("模型输出不是 JSON 对象")
        return data
