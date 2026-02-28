"""TitanFlow LLM Client — local Ollama inference with cloud escalation."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

try:
    import ollama  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    ollama = None


class _OllamaHTTPClient:
    """Fallback Ollama client using HTTP API when python-ollama isn't available."""

    def __init__(self, host: str) -> None:
        self._client = httpx.AsyncClient(base_url=host, timeout=120.0)

    async def generate(
        self,
        *,
        model: str,
        prompt: str,
        system: str = "",
        options: dict | None = None,
        keep_alive: str | None = None,
    ) -> dict:
        payload = {
            "model": model,
            "prompt": prompt,
            "system": system,
            "options": options or {},
            "stream": False,
        }
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive
        response = await self._client.post("/api/generate", json=payload)
        response.raise_for_status()
        return response.json()

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        options: dict | None = None,
        keep_alive: str | None = None,
    ) -> dict:
        payload = {
            "model": model,
            "messages": messages,
            "options": options or {},
            "stream": False,
        }
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive
        response = await self._client.post("/api/chat", json=payload)
        response.raise_for_status()
        return response.json()

    async def aclose(self) -> None:
        await self._client.aclose()

from titanflow.config import LLMConfig

logger = logging.getLogger("titanflow.llm")


class LLMClient:
    """Unified LLM interface: Ollama first, cloud fallback.

    Uses a semaphore to serialize Ollama requests — since Ollama processes
    them one at a time anyway, this ensures that interactive chat requests
    (which arrive between research items) get served promptly via FIFO
    ordering instead of piling up behind a large batch.
    """

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._keep_alive = os.environ.get("TITANFLOW_OLLAMA_KEEP_ALIVE", "10m")
        num_ctx = os.environ.get("TITANFLOW_OLLAMA_NUM_CTX")
        self._num_ctx = int(num_ctx) if num_ctx and num_ctx.isdigit() else None
        if ollama is None:
            self._ollama = _OllamaHTTPClient(config.base_url)
        else:
            self._ollama = ollama.AsyncClient(host=config.base_url)
        self._http = httpx.AsyncClient(timeout=120.0)
        self._sem = asyncio.Semaphore(1)  # serialize Ollama access

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        force_cloud: bool = False,
    ) -> str:
        """Generate a response. Uses local Ollama by default, cloud if forced or local fails."""
        model = model or self.config.default_model

        if force_cloud:
            logger.info(
                "LLM generate: force_cloud=True; using cloud provider %s",
                self.config.cloud.provider,
            )
            return await self._cloud_generate(
                prompt, system=system, temperature=temperature, max_tokens=max_tokens
            )

        try:
            return await self._ollama_generate(
                prompt, system=system, model=model, temperature=temperature
            )
        except Exception as e:
            logger.warning(
                "Ollama generation failed (%s); trying fallback model %s",
                e,
                self.config.fallback_model,
            )
            try:
                return await self._ollama_generate(
                    prompt, system=system, model=self.config.fallback_model, temperature=temperature
                )
            except Exception as e2:
                logger.warning("Fallback model failed (%s); escalating to cloud", e2)
                if self.config.cloud.api_key:
                    logger.info(
                        "LLM generate: escalating to cloud provider %s",
                        self.config.cloud.provider,
                    )
                    return await self._cloud_generate(
                        prompt, system=system, temperature=temperature, max_tokens=max_tokens
                    )
                raise RuntimeError(
                    f"All LLM backends failed. Local: {e}, Fallback: {e2}"
                ) from e2

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        force_cloud: bool = False,
    ) -> str:
        """Chat-style completion with message history."""
        model = model or self.config.default_model

        if force_cloud and self.config.cloud.api_key:
            logger.info(
                "LLM chat: force_cloud=True; using cloud provider %s",
                self.config.cloud.provider,
            )
            return await self._cloud_chat(messages, temperature=temperature)

        try:
            async with self._sem:
                try:
                    response = await self._ollama.chat(
                        model=model,
                        messages=messages,
                        options={
                            "temperature": temperature,
                            **({"num_ctx": self._num_ctx} if self._num_ctx else {}),
                        },
                        keep_alive=self._keep_alive,
                    )
                except TypeError:
                    response = await self._ollama.chat(
                        model=model,
                        messages=messages,
                        options={"temperature": temperature},
                    )
            return response["message"]["content"]
        except Exception as e:
            logger.warning("Ollama chat failed (%s); escalating to cloud", e)
            if self.config.cloud.api_key:
                logger.info(
                    "LLM chat: escalating to cloud provider %s",
                    self.config.cloud.provider,
                )
                return await self._cloud_chat(messages, temperature=temperature)
            raise

    async def close(self) -> None:
        await self._http.aclose()
        if hasattr(self._ollama, "aclose"):
            await self._ollama.aclose()

    async def _ollama_generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str,
        temperature: float,
    ) -> str:
        """Generate via local Ollama (serialized via semaphore)."""
        async with self._sem:
            try:
                response = await self._ollama.generate(
                    model=model,
                    prompt=prompt,
                    system=system or "",
                    options={
                        "temperature": temperature,
                        **({"num_ctx": self._num_ctx} if self._num_ctx else {}),
                    },
                    keep_alive=self._keep_alive,
                )
            except TypeError:
                response = await self._ollama.generate(
                    model=model,
                    prompt=prompt,
                    system=system or "",
                    options={"temperature": temperature},
                )
            return response["response"]

    async def _cloud_generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        """Generate via Anthropic API."""
        messages = [{"role": "user", "content": prompt}]
        return await self._cloud_chat(messages, system=system, temperature=temperature, max_tokens=max_tokens)

    async def _cloud_chat(
        self,
        messages: list[dict[str, str]],
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        """Chat via cloud API. Dispatches based on provider config."""
        provider = self.config.cloud.provider

        if provider == "openrouter":
            return await self._openrouter_chat(
                messages, system=system, temperature=temperature, max_tokens=max_tokens
            )

        # Default: Anthropic Messages API
        payload: dict[str, Any] = {
            "model": self.config.cloud.model,
            "max_tokens": max_tokens,
            "messages": messages,
            "temperature": temperature,
        }
        if system:
            payload["system"] = system

        response = await self._http.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.config.cloud.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        return data["content"][0]["text"]

    async def _openrouter_chat(
        self,
        messages: list[dict[str, str]],
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        """Chat via OpenRouter (OpenAI-compatible API)."""
        full_messages = list(messages)
        if system:
            full_messages.insert(0, {"role": "system", "content": system})

        payload: dict[str, Any] = {
            "model": self.config.cloud.model,
            "max_tokens": max_tokens,
            "messages": full_messages,
            "temperature": temperature,
        }

        response = await self._http.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.config.cloud.api_key}",
                "Content-Type": "application/json",
                "X-Title": "TitanFlow",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    async def health_check(self) -> dict[str, Any]:
        """Check if Ollama is reachable and list available models."""
        try:
            result = await self._ollama.list()
            # Ollama SDK >=0.4 returns Pydantic objects, not dicts
            models_list = result.models if hasattr(result, "models") else result.get("models", [])
            model_names = []
            for m in models_list:
                name = getattr(m, "model", None) or (m.get("name") if isinstance(m, dict) else str(m))
                model_names.append(name)
            return {"status": "ok", "provider": "ollama", "models": model_names}
        except Exception as e:
            return {"status": "error", "provider": "ollama", "error": str(e)}

    async def close(self) -> None:
        await self._http.aclose()
