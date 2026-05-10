from __future__ import annotations

from dataclasses import dataclass
import json
from typing import AsyncIterator

import httpx


class VLLMError(RuntimeError):
    """Raised when vLLM cannot satisfy a request."""


@dataclass(frozen=True)
class TranslationParams:
    model: str
    prompt: str
    temperature: float
    top_p: float
    top_k: int
    repetition_penalty: float

    def as_payload(self, *, stream: bool = False) -> dict:
        return {
            "model": self.model,
            "messages": [{"role": "user", "content": self.prompt}],
            "temperature": self.temperature,
            "top_p": self.top_p,
            "stream": stream,
            "extra_body": {
                "top_k": self.top_k,
                "repetition_penalty": self.repetition_penalty,
            },
        }


class VLLMClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, trust_env=False)

    async def list_models(self) -> list[str]:
        payload = await self._request("GET", "/v1/models")
        models = payload.get("data", [])
        return [item.get("id", "") for item in models if item.get("id")]

    async def is_reachable(self, expected_model: str | None = None) -> bool:
        try:
            models = await self.list_models()
        except VLLMError:
            return False
        if expected_model is None:
            return True
        return expected_model in models

    async def translate(self, params: TranslationParams) -> str:
        payload = await self._request(
            "POST",
            "/v1/chat/completions",
            json=params.as_payload(),
        )

        try:
            return payload["choices"][0]["message"]["content"].strip()
        except (IndexError, KeyError, TypeError, AttributeError) as exc:
            raise VLLMError("vLLM returned an invalid completion payload") from exc

    async def stream_translate(self, params: TranslationParams) -> AsyncIterator[str]:
        url = f"{self._base_url}/v1/chat/completions"
        try:
            async with self._build_client() as client:
                async with client.stream(
                    "POST",
                    url,
                    json=params.as_payload(stream=True),
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            payload = json.loads(data)
                        except json.JSONDecodeError as exc:
                            raise VLLMError("vLLM returned invalid streaming JSON") from exc
                        delta = self._extract_delta_text(payload)
                        if delta:
                            yield delta
        except httpx.HTTPStatusError as exc:
            detail = await exc.response.aread()
            detail_text = detail.decode("utf-8", errors="replace")[:500]
            raise VLLMError(
                f"vLLM streaming request failed with status {exc.response.status_code}: {detail_text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise VLLMError(f"vLLM streaming request failed: {exc}") from exc

    def _extract_delta_text(self, payload: dict) -> str:
        try:
            choices = payload.get("choices", [])
            if not choices:
                return ""
            delta = choices[0].get("delta", {})
            content = delta.get("content", "")
            if isinstance(content, list):
                return "".join(
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict)
                )
            if isinstance(content, str):
                return content
            return ""
        except AttributeError as exc:
            raise VLLMError("vLLM returned an invalid streaming payload") from exc

    async def _request(self, method: str, path: str, json: dict | None = None) -> dict:
        url = f"{self._base_url}{path}"
        try:
            async with self._build_client() as client:
                response = await client.request(method, url, json=json)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise VLLMError(
                f"vLLM request failed with status {exc.response.status_code}: {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            raise VLLMError(f"vLLM request failed: {exc}") from exc
