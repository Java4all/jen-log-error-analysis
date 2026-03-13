"""
ai_service.py -- Unified AI provider abstraction.
Supports: Anthropic Claude, Ollama (local GPU), private OpenAI-compatible endpoints.
"""
from __future__ import annotations
import json
import logging
from typing import AsyncIterator, Optional

import httpx

from config import AIConfig, get_config

logger = logging.getLogger(__name__)


class AIServiceError(Exception):
    pass


# -- Base -----------------------------------------------------------------------

class BaseAIProvider:
    async def complete(self, system: str, user: str, timeout: Optional[int] = None) -> str:
        raise NotImplementedError

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        raise NotImplementedError
        yield ""  # make it a generator


# -- Anthropic -----------------------------------------------------------------

class AnthropicProvider(BaseAIProvider):
    def __init__(self, cfg: AIConfig):
        self.cfg = cfg.anthropic
        if not self.cfg.api_key:
            raise AIServiceError("Anthropic API key is not configured.")

    async def complete(self, system: str, user: str, timeout: Optional[int] = None) -> str:
        async with httpx.AsyncClient(timeout=timeout or 120) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.cfg.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.cfg.model,
                    "max_tokens": self.cfg.max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                raise AIServiceError(data["error"]["message"])
            return data["content"][0]["text"]

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        async with httpx.AsyncClient(timeout=180) as client:
            async with client.stream(
                "POST",
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.cfg.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.cfg.model,
                    "max_tokens": self.cfg.max_tokens,
                    "stream": True,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                },
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            evt = json.loads(payload)
                            if evt.get("type") == "content_block_delta":
                                yield evt["delta"].get("text", "")
                        except json.JSONDecodeError:
                            pass


# -- Ollama --------------------------------------------------------------------

class OllamaProvider(BaseAIProvider):
    """Calls local Ollama instance. GPU is managed by Ollama itself."""

    def __init__(self, cfg: AIConfig):
        self.cfg = cfg.ollama
        self.gpu_enabled = cfg.gpu_enabled
        self.gpu_layers = cfg.gpu_layers

    def _options(self) -> dict:
        opts: dict = {}
        if self.gpu_enabled:
            opts["num_gpu"] = self.gpu_layers
        else:
            opts["num_gpu"] = 0
        return opts

    async def complete(self, system: str, user: str, timeout: Optional[int] = None) -> str:
        try:
            async with httpx.AsyncClient(
                base_url=self.cfg.base_url, timeout=timeout or self.cfg.timeout
            ) as client:
                resp = await client.post(
                    "/api/chat",
                    json={
                        "model": self.cfg.model,
                        "stream": False,
                        "options": self._options(),
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return data["message"]["content"]
        except httpx.ConnectError:
            raise AIServiceError(
                f"Cannot connect to Ollama at {self.cfg.base_url}. "
                f"Is the Ollama container running? "
                f"Run: docker exec jenkins-analyzer-ollama ollama pull {self.cfg.model}"
            )
        except httpx.TimeoutException:
            raise AIServiceError(
                f"Ollama request timed out after {self.cfg.timeout}s. "
                f"The model may still be loading -- try again in a moment."
            )
        except httpx.HTTPStatusError as e:
            _body = e.response.text[:400]
            _status = e.response.status_code
            # Try to extract Ollama's own error field
            try:
                _detail = e.response.json().get("error", _body)
            except Exception:
                _detail = _body
            if _status == 404:
                raise AIServiceError(
                    f"Ollama model '{self.cfg.model}' not found. "
                    f"Pull it: docker exec jenkins-analyzer-ollama ollama pull {self.cfg.model}"
                )
            if _status == 500:
                if "model" in _detail.lower() and ("not found" in _detail.lower() or "unknown" in _detail.lower()):
                    raise AIServiceError(
                        f"Ollama model '{self.cfg.model}' not loaded. "
                        f"Pull it: docker exec jenkins-analyzer-ollama ollama pull {self.cfg.model}"
                    )
                raise AIServiceError(
                    f"Ollama internal error (500): {_detail}. "
                    f"Check logs: docker logs jenkins-analyzer-ollama"
                )
            raise AIServiceError(f"Ollama HTTP {_status}: {_detail}")

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        try:
            async with httpx.AsyncClient(
                base_url=self.cfg.base_url, timeout=self.cfg.timeout
            ) as client:
                async with client.stream(
                    "POST",
                    "/api/chat",
                    json={
                        "model": self.cfg.model,
                        "stream": True,
                        "options": self._options(),
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                    },
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if line:
                            try:
                                evt = json.loads(line)
                                chunk = evt.get("message", {}).get("content", "")
                                if chunk:
                                    yield chunk
                                if evt.get("done"):
                                    break
                            except json.JSONDecodeError:
                                pass
        except httpx.ConnectError:
            raise AIServiceError(
                f"Cannot connect to Ollama at {self.cfg.base_url}. "
                f"Is the Ollama container running? "
                f"Run: docker exec jenkins-analyzer-ollama ollama pull {self.cfg.model}"
            )
        except httpx.TimeoutException:
            raise AIServiceError(
                f"Ollama request timed out after {self.cfg.timeout}s. "
                f"The model may still be loading -- try again in a moment."
            )
        except httpx.HTTPStatusError as e:
            _body = e.response.text[:400]
            _status = e.response.status_code
            try:
                _detail = e.response.json().get("error", _body)
            except Exception:
                _detail = _body
            if _status == 404:
                raise AIServiceError(
                    f"Ollama model '{self.cfg.model}' not found. "
                    f"Pull it: docker exec jenkins-analyzer-ollama ollama pull {self.cfg.model}"
                )
            if _status == 500:
                if "model" in _detail.lower() and ("not found" in _detail.lower() or "unknown" in _detail.lower()):
                    raise AIServiceError(
                        f"Ollama model '{self.cfg.model}' not loaded. "
                        f"Pull it: docker exec jenkins-analyzer-ollama ollama pull {self.cfg.model}"
                    )
                raise AIServiceError(
                    f"Ollama internal error (500): {_detail}. "
                    f"Check logs: docker logs jenkins-analyzer-ollama"
                )
            raise AIServiceError(f"Ollama HTTP {_status}: {_detail}")


# -- Private / OpenAI-compatible -----------------------------------------------

class PrivateProvider(BaseAIProvider):
    """
    Works with any OpenAI-compatible endpoint:
    llama.cpp server, vLLM, LM Studio, LocalAI, text-generation-webui, etc.
    GPU usage is configured on the server side; gpu_layers sent as model param
    when supported (llama.cpp).
    """

    def __init__(self, cfg: AIConfig):
        self.cfg = cfg.private
        self.gpu_enabled = cfg.gpu_enabled
        self.gpu_layers = cfg.gpu_layers

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            h["Authorization"] = f"Bearer {self.cfg.api_key}"
        return h

    def _body(self, system: str, user: str, stream: bool = False) -> dict:
        body: dict = {
            "model": self.cfg.model,
            "stream": stream,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        # llama.cpp server supports n_gpu_layers via model params
        if self.gpu_enabled:
            body["n_gpu_layers"] = self.gpu_layers
        return body

    async def complete(self, system: str, user: str, timeout: Optional[int] = None) -> str:
        async with httpx.AsyncClient(
            timeout=self.cfg.timeout,
            verify=self.cfg.verify_ssl,
        ) as client:
            url = f"{self.cfg.base_url.rstrip('/')}/chat/completions"
            resp = await client.post(url, headers=self._headers(), json=self._body(system, user))
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        async with httpx.AsyncClient(
            timeout=self.cfg.timeout,
            verify=self.cfg.verify_ssl,
        ) as client:
            url = f"{self.cfg.base_url.rstrip('/')}/chat/completions"
            async with client.stream(
                "POST", url, headers=self._headers(), json=self._body(system, user, stream=True)
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            evt = json.loads(payload)
                            delta = evt["choices"][0].get("delta", {})
                            chunk = delta.get("content", "")
                            if chunk:
                                yield chunk
                        except (json.JSONDecodeError, KeyError):
                            pass


# -- Factory -------------------------------------------------------------------

def get_ai_provider(override_provider: str | None = None) -> BaseAIProvider:
    cfg = get_config().ai
    provider = override_provider or cfg.provider
    logger.info(f"AI provider: {provider} | GPU: {cfg.gpu_enabled}")

    if provider == "anthropic":
        return AnthropicProvider(cfg)
    elif provider == "ollama":
        return OllamaProvider(cfg)
    elif provider == "private":
        return PrivateProvider(cfg)
    else:
        raise AIServiceError(f"Unknown AI provider: '{provider}'. Valid: anthropic, ollama, private")


async def ai_complete(system: str, user: str, provider: str | None = None, timeout: Optional[int] = None) -> str:
    return await get_ai_provider(provider).complete(system, user, timeout=timeout)
