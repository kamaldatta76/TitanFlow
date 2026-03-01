"""TitanFlow LLM Client — local Ollama inference with cloud escalation.

Hardened: all external API responses are validated before key access.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any
from urllib.parse import urlparse

import httpx

try:
    import ollama  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    ollama = None

from titanflow.config import LLMConfig

logger = logging.getLogger("titanflow.llm")


# ── Validation helpers ────────────────────────────────────────────────────────

def _validate_ollama_url(url: str) -> str:
    """Fail fast on malformed Ollama base URL."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Ollama base_url must use http/https, got: {url!r}")
    if not parsed.hostname:
        raise ValueError(f"Ollama base_url missing hostname: {url!r}")
    return url


def _safe_extract_ollama_generate(response: Any) -> str:
    """Extract text from Ollama /api/generate response with structure validation.

    Handles both raw dicts (from _OllamaHTTPClient) and Pydantic
    GenerateResponse objects (from ollama.AsyncClient).
    """
    # SDK Pydantic object (GenerateResponse) — attribute access
    if hasattr(response, "response") and not isinstance(response, dict):
        text = response.response
        if text is None:
            raise ValueError(
                f"Ollama generate: GenerateResponse.response is None "
                f"(type={type(response).__name__})"
            )
        return str(text)

    # Raw dict from HTTP fallback
    if isinstance(response, dict):
        text = response.get("response")
        if text is None:
            raise ValueError(
                f"Ollama generate: missing 'response' key. Keys present: {sorted(response.keys())}"
            )
        return str(text)

    raise TypeError(
        f"Ollama generate: expected dict or GenerateResponse, got {type(response).__name__}"
    )


def _safe_extract_ollama_chat(response: Any) -> str:
    """Extract text from Ollama /api/chat response with structure validation.

    Handles both raw dicts (from _OllamaHTTPClient) and Pydantic
    ChatResponse objects (from ollama.AsyncClient).
    """
    # SDK Pydantic object (ChatResponse) — attribute access
    if hasattr(response, "message") and not isinstance(response, dict):
        message = response.message
        if message is None:
            raise ValueError(
                f"Ollama chat: ChatResponse.message is None "
                f"(type={type(response).__name__})"
            )
        # Message object has .content attribute (or might be a dict in edge cases)
        if hasattr(message, "content"):
            content = message.content
        elif isinstance(message, dict):
            content = message.get("content")
        else:
            raise ValueError(
                f"Ollama chat: ChatResponse.message has no 'content'. "
                f"Message type={type(message).__name__}"
            )
        if content is None:
            raise ValueError(
                f"Ollama chat: ChatResponse.message.content is None "
                f"(message type={type(message).__name__})"
            )
        return str(content)

    # Raw dict from HTTP fallback
    if isinstance(response, dict):
        message = response.get("message")
        if not isinstance(message, dict):
            raise ValueError(
                f"Ollama chat: 'message' missing or not a dict. "
                f"Keys present: {sorted(response.keys())}"
            )
        content = message.get("content")
        if content is None:
            raise ValueError(
                f"Ollama chat: 'message.content' is None. "
                f"Message keys: {sorted(message.keys())}"
            )
        return str(content)

    raise TypeError(
        f"Ollama chat: expected dict or ChatResponse, got {type(response).__name__}"
    )


def _safe_extract_anthropic(data: Any) -> str:
    """Extract text from Anthropic Messages API response with validation."""
    if not isinstance(data, dict):
        raise TypeError(f"Anthropic: expected dict, got {type(data).__name__}")
    content = data.get("content")
    if not isinstance(content, list) or not content:
        error_msg = data.get("error", {}).get("message", "unknown")
        raise ValueError(
            f"Anthropic: 'content' missing or empty (error: {error_msg}). "
            f"Keys present: {sorted(data.keys())}"
        )
    first = content[0]
    if not isinstance(first, dict) or "text" not in first:
        raise ValueError(
            f"Anthropic: content[0] missing 'text'. Got type={type(first).__name__}"
        )
    return str(first["text"])


def _safe_extract_openrouter(data: Any) -> str:
    """Extract text from OpenRouter response with validation."""
    if not isinstance(data, dict):
        raise TypeError(f"OpenRouter: expected dict, got {type(data).__name__}")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        error = data.get("error", {})
        raise ValueError(
            f"OpenRouter: 'choices' missing or empty. Error: {error}. "
            f"Keys present: {sorted(data.keys())}"
        )
    msg = choices[0].get("message", {})
    if not isinstance(msg, dict) or "content" not in msg:
        raise ValueError(
            f"OpenRouter: choices[0].message missing 'content'. Got: {choices[0]}"
        )
    return str(msg["content"])


def _validate_num_ctx(raw: str | None) -> int | None:
    """Validate TITANFLOW_OLLAMA_NUM_CTX — must be a positive integer in sane range."""
    if not raw or not raw.strip():
        return None
    try:
        val = int(raw)
    except ValueError:
        logger.warning("TITANFLOW_OLLAMA_NUM_CTX=%r is not a valid integer; ignoring", raw)
        return None
    if val < 256:
        logger.warning("TITANFLOW_OLLAMA_NUM_CTX=%d is below minimum (256); ignoring", val)
        return None
    if val > 1_000_000:
        logger.warning("TITANFLOW_OLLAMA_NUM_CTX=%d exceeds 1M; capping at 131072", val)
        return 131072
    return val


# ── HTTP fallback client ──────────────────────────────────────────────────────

class _OllamaHTTPClient:
    """Fallback Ollama client using HTTP API when python-ollama isn't available."""

    def __init__(self, host: str) -> None:
        _validate_ollama_url(host)
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
        if not model:
            raise ValueError("Ollama generate: model name is required")
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
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError(f"Ollama generate: expected JSON object, got {type(data).__name__}")
        return data

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        options: dict | None = None,
        keep_alive: str | None = None,
    ) -> dict:
        if not model:
            raise ValueError("Ollama chat: model name is required")
        if not messages:
            raise ValueError("Ollama chat: at least one message is required")
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
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError(f"Ollama chat: expected JSON object, got {type(data).__name__}")
        return data

    async def aclose(self) -> None:
        await self._client.aclose()


# ── Main LLM client ──────────────────────────────────────────────────────────

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
        self._num_ctx = _validate_num_ctx(os.environ.get("TITANFLOW_OLLAMA_NUM_CTX"))

        # Validate base URL at init time — fail fast
        _validate_ollama_url(config.base_url)

        if not config.default_model:
            logger.warning("LLM default_model is empty — will rely on fallback_model or cloud")
        if not config.fallback_model:
            logger.warning("LLM fallback_model is empty — no local fallback available")

        if ollama is None:
            logger.info("python-ollama not installed; using HTTP fallback client")
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
        if not model and not force_cloud:
            raise ValueError("No LLM model configured and force_cloud is False")

        if force_cloud:
            if not self.config.cloud.api_key:
                raise RuntimeError("force_cloud=True but no cloud API key configured")
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
            if not self.config.fallback_model:
                logger.error("No fallback model configured — cannot retry locally")
            else:
                try:
                    return await self._ollama_generate(
                        prompt, system=system, model=self.config.fallback_model, temperature=temperature
                    )
                except Exception as e2:
                    logger.warning("Fallback model failed (%s); escalating to cloud", e2)
                    e = e2  # use the more recent error

            if self.config.cloud.api_key:
                logger.info(
                    "LLM generate: escalating to cloud provider %s",
                    self.config.cloud.provider,
                )
                return await self._cloud_generate(
                    prompt, system=system, temperature=temperature, max_tokens=max_tokens
                )
            raise RuntimeError(
                f"All LLM backends failed. Last error: {e}"
            ) from e

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
                    # Older Ollama SDK may not support keep_alive/num_ctx
                    logger.debug("Ollama chat: falling back to basic options (TypeError)")
                    response = await self._ollama.chat(
                        model=model,
                        messages=messages,
                        options={"temperature": temperature},
                    )
            content = _safe_extract_ollama_chat(response)
            # lfm2 (and some other models) silently return empty content on
            # code-heavy or tool-heavy prompts instead of raising an error.
            # Detect this and retry with fallback_model before giving up.
            if not content and self.config.fallback_model and model != self.config.fallback_model:
                logger.warning(
                    "LLM chat: %s returned empty content; retrying with fallback %s",
                    model,
                    self.config.fallback_model,
                )
                async with self._sem:
                    try:
                        fb_response = await self._ollama.chat(
                            model=self.config.fallback_model,
                            messages=messages,
                            options={
                                "temperature": temperature,
                                **({"num_ctx": self._num_ctx} if self._num_ctx else {}),
                            },
                            keep_alive=self._keep_alive,
                        )
                    except TypeError:
                        fb_response = await self._ollama.chat(
                            model=self.config.fallback_model,
                            messages=messages,
                            options={"temperature": temperature},
                        )
                content = _safe_extract_ollama_chat(fb_response)
            return content
        except Exception as e:
            logger.warning("Ollama chat failed (%s); escalating to cloud", e)
            if self.config.cloud.api_key:
                logger.info(
                    "LLM chat: escalating to cloud provider %s",
                    self.config.cloud.provider,
                )
                return await self._cloud_chat(messages, temperature=temperature)
            raise

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
                # Older Ollama SDK may not support keep_alive/num_ctx
                logger.debug("Ollama generate: falling back to basic options (TypeError)")
                response = await self._ollama.generate(
                    model=model,
                    prompt=prompt,
                    system=system or "",
                    options={"temperature": temperature},
                )
            return _safe_extract_ollama_generate(response)

    async def _cloud_generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        """Generate via cloud API."""
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

        if not self.config.cloud.api_key:
            raise RuntimeError(f"Cloud provider '{provider}' has no API key configured")

        if provider == "openrouter":
            return await self._openrouter_chat(
                messages, system=system, temperature=temperature, max_tokens=max_tokens
            )

        # Default: Anthropic Messages API
        if not self.config.cloud.model:
            raise RuntimeError("Anthropic cloud_model is empty — cannot call API")

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
        return _safe_extract_anthropic(data)

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
        return _safe_extract_openrouter(data)

    async def health_check(self) -> dict[str, Any]:
        """Check if Ollama is reachable, list available models, and verify configured models exist."""
        try:
            result = await self._ollama.list()
            # Ollama SDK >=0.4 returns Pydantic objects, not dicts
            models_list = result.models if hasattr(result, "models") else result.get("models", [])
            model_names = []
            for m in models_list:
                name = getattr(m, "model", None) or (m.get("name") if isinstance(m, dict) else str(m))
                model_names.append(name)

            # Verify configured models exist on the server
            warnings = []
            for label, configured in [
                ("default_model", self.config.default_model),
                ("fallback_model", self.config.fallback_model),
            ]:
                if configured and configured not in model_names:
                    # Also check without tag (e.g., "flow:24b" may appear as "flow:24b")
                    base_name = configured.split(":")[0] if ":" in configured else configured
                    found = any(base_name in n for n in model_names)
                    if not found:
                        warnings.append(f"{label}={configured!r} not found on Ollama server")
                        logger.warning("LLM health: %s", warnings[-1])

            status = {"status": "ok", "provider": "ollama", "models": model_names}
            if warnings:
                status["warnings"] = warnings
            return status
        except Exception as e:
            return {"status": "error", "provider": "ollama", "error": str(e)}

    async def close(self) -> None:
        """Close all HTTP clients. Safe to call multiple times."""
        await self._http.aclose()
        if hasattr(self._ollama, "aclose"):
            await self._ollama.aclose()
