import asyncio
import json
import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

import ollama

from app import llm_logger

logger = logging.getLogger(__name__)

OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "mistral-small3.1")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_STREAM_IDLE_SECONDS = float(os.environ.get("OLLAMA_STREAM_IDLE_SECONDS", "20"))
OLLAMA_STRUCTURED_TIMEOUT_SECONDS = float(
    os.environ.get("OLLAMA_STRUCTURED_TIMEOUT_SECONDS", "30")
)


class OllamaTimeoutError(Exception):
    """Raised when an Ollama call exceeds its timeout."""


class OllamaUnreachableError(Exception):
    """Raised when Ollama cannot be contacted."""


def _get_client() -> ollama.AsyncClient:
    return ollama.AsyncClient(host=OLLAMA_BASE_URL)


async def stream_chat(
    model: str,
    messages: list[dict[str, str]],
    options: dict[str, Any] | None = None,
    num_predict: int | None = None,
) -> AsyncGenerator[str, None]:
    """
    Async generator yielding token strings from an Ollama streaming chat call.

    Enforces a per-token idle timeout (OLLAMA_STREAM_IDLE_SECONDS). If no token
    arrives within that window, raises OllamaTimeoutError.

    Raises:
        OllamaTimeoutError: if a token idle timeout or total timeout fires.
        OllamaUnreachableError: if Ollama cannot be contacted.
    """
    llm_logger.log_input("stream_chat", model, messages)
    _log_buf: list[str] = []
    client = _get_client()
    effective_options: dict[str, Any] = dict(options or {})
    if num_predict is not None:
        effective_options["num_predict"] = num_predict
    try:
        stream = await client.chat(
            model=model,
            messages=messages,
            stream=True,
            options=effective_options,
        )
        aiter = stream.__aiter__()
        while True:
            try:
                chunk = await asyncio.wait_for(
                    aiter.__anext__(),
                    timeout=OLLAMA_STREAM_IDLE_SECONDS,
                )
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError as exc:
                raise OllamaTimeoutError(
                    f"No token received for {OLLAMA_STREAM_IDLE_SECONDS}s"
                ) from exc
            token: str = chunk.message.content or ""
            if token:
                _log_buf.append(token)
                yield token
    except (ConnectionError, OSError) as exc:
        raise OllamaUnreachableError(f"Ollama unreachable: {exc}") from exc
    except ollama.ResponseError as exc:
        raise OllamaUnreachableError(f"Ollama response error: {exc}") from exc
    finally:
        llm_logger.log_output("stream_chat", model, "".join(_log_buf))


async def structured_chat(
    model: str,
    messages: list[dict[str, str]],
    schema: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Non-streaming Ollama call that returns a parsed JSON dict.

    STAGE 1 STUB: The timeout and exception handling skeleton is complete.
    The `format` parameter (Ollama structured output) is wired up in Stage 2.

    Raises:
        OllamaTimeoutError: if the call exceeds OLLAMA_STRUCTURED_TIMEOUT_SECONDS.
        OllamaUnreachableError: if Ollama cannot be contacted.
    """
    llm_logger.log_input("structured_chat", model, messages)
    client = _get_client()
    try:
        response = await asyncio.wait_for(
            client.chat(
                model=model,
                messages=messages,
                format=schema,
                options=options or {},
            ),
            timeout=OLLAMA_STRUCTURED_TIMEOUT_SECONDS,
        )
        result: dict[str, Any] = json.loads(response.message.content)  # type: ignore[arg-type]
        llm_logger.log_output("structured_chat", model, result)
        return result
    except asyncio.TimeoutError as exc:
        raise OllamaTimeoutError(
            f"Structured call timed out after {OLLAMA_STRUCTURED_TIMEOUT_SECONDS}s"
        ) from exc
    except (ConnectionError, OSError) as exc:
        raise OllamaUnreachableError(f"Ollama unreachable: {exc}") from exc
    except ollama.ResponseError as exc:
        raise OllamaUnreachableError(f"Ollama response error: {exc}") from exc
