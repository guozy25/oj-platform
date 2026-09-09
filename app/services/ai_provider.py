from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from app.models.ai import ModelConfigUpdate

# A generated problem contains several programs and many test cases, but it
# should still fit comfortably in one response.  More importantly, this
# prevents reasoning models from consuming an unbounded amount of output.
MAX_RESPONSE_BYTES = 1_000_000
MAX_GENERATED_INPUT_BYTES = 16_384
ProgressCallback = Callable[[str, int], Awaitable[None]]


class AIProviderError(RuntimeError):
    """Safe model-provider failure suitable for returning to a task owner."""

    def __init__(
        self,
        message: str,
        *,
        partial_content: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        estimated: bool = True,
    ) -> None:
        super().__init__(message)
        self.partial_content = partial_content
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.estimated = estimated


@dataclass(frozen=True, slots=True)
class ProviderResult:
    content: str
    input_tokens: int
    output_tokens: int
    estimated: bool


def _estimate_tokens(text: str) -> int:
    # This deliberately conservative approximation is disclosed through `estimated`.
    return max(1, (len(text) + 3) // 4)


def _message_text(content: object) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        if parts:
            return "".join(parts)
    return None


def _contains_oversized_input_string(content: str) -> bool:
    """Detect a too-large JSON input string while the response is incomplete."""
    search_from = 0
    marker = '"input"'
    while True:
        marker_start = content.find(marker, search_from)
        if marker_start == -1:
            return False
        cursor = marker_start + len(marker)
        while cursor < len(content) and content[cursor].isspace():
            cursor += 1
        if cursor >= len(content) or content[cursor] != ":":
            search_from = marker_start + 1
            continue
        cursor += 1
        while cursor < len(content) and content[cursor].isspace():
            cursor += 1
        if cursor >= len(content) or content[cursor] != '"':
            search_from = marker_start + 1
            continue
        cursor += 1
        size = 0
        escaped = False
        while cursor < len(content):
            character = content[cursor]
            if escaped:
                size += len(character.encode("utf-8"))
                escaped = False
            elif character == "\\":
                size += 1
                escaped = True
            elif character == '"':
                break
            else:
                size += len(character.encode("utf-8"))
            if size > MAX_GENERATED_INPUT_BYTES:
                return True
            cursor += 1
        search_from = marker_start + 1


class AIProviderClient:
    """Minimal OpenAI-compatible chat-completions client."""

    def __init__(self, config: ModelConfigUpdate) -> None:
        self.config = config

    def _endpoint(self) -> str:
        if self.config.provider_url.endswith("/chat/completions"):
            return self.config.provider_url
        return f"{self.config.provider_url}/chat/completions"

    async def complete(self, system_prompt: str, user_prompt: str) -> ProviderResult:
        return await self.complete_stream(system_prompt, user_prompt)

    async def complete_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        on_progress: ProgressCallback | None = None,
    ) -> ProviderResult:
        request_payload = {
            "model": self.config.model,
            "temperature": 0.2,
            "max_tokens": self.config.max_output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        provider_hint = f"{self.config.provider_url} {self.config.model}".lower()
        if "deepseek" in provider_hint:
            # DeepSeek's current models enable thinking by default.  For this
            # structured artifact task that can consume the whole budget before
            # any JSON is emitted, so explicitly request non-thinking JSON.
            request_payload["thinking"] = {"type": "disabled"}
            request_payload["response_format"] = {"type": "json_object"}
        headers = {
            "Authorization": f"Bearer {self.config.api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(90.0, connect=10.0)
        content_parts: list[str] = []
        display_parts: list[str] = []
        actual_input: int | None = None
        actual_output: int | None = None
        finish_reason: str | None = None
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                async with client.stream(
                    "POST", self._endpoint(), headers=headers, json=request_payload
                ) as response:
                    if response.status_code >= 400:
                        raise AIProviderError(
                            f"model provider returned HTTP {response.status_code}"
                        )
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            continue
                        try:
                            chunk = json.loads(data)
                        except (ValueError, TypeError) as exc:
                            raise AIProviderError(
                                "model provider returned an invalid stream chunk"
                            ) from exc
                        usage = chunk.get("usage")
                        if isinstance(usage, dict):
                            prompt_tokens = usage.get("prompt_tokens")
                            completion_tokens = usage.get("completion_tokens")
                            if isinstance(prompt_tokens, int):
                                actual_input = prompt_tokens
                            if isinstance(completion_tokens, int):
                                actual_output = completion_tokens
                        choices = chunk.get("choices")
                        if not isinstance(choices, list) or not choices:
                            continue
                        choice = choices[0]
                        if not isinstance(choice, dict):
                            continue
                        finish_reason = choice.get("finish_reason") or finish_reason
                        delta = choice.get("delta")
                        reasoning = (
                            _message_text(delta.get("reasoning_content"))
                            if isinstance(delta, dict)
                            else None
                        )
                        if reasoning:
                            display_parts.append(reasoning)
                            if on_progress is not None:
                                await on_progress(
                                    "".join(display_parts),
                                    _estimate_tokens("".join(display_parts)),
                                )
                        piece = (
                            _message_text(delta.get("content"))
                            if isinstance(delta, dict)
                            else None
                        )
                        if piece:
                            content_parts.append(piece)
                            display_parts.append(piece)
                            partial = "".join(display_parts)
                            if len(partial.encode("utf-8")) > MAX_RESPONSE_BYTES:
                                raise AIProviderError("model provider response is too large")
                            if _contains_oversized_input_string(partial):
                                raise AIProviderError(
                                    "generated input exceeds the 16384-byte limit; "
                                    "use a compact, varied testcase",
                                    partial_content=partial,
                                    output_tokens=_estimate_tokens(partial),
                                )
                            if on_progress is not None:
                                await on_progress(partial, _estimate_tokens(partial))
        except httpx.TimeoutException as exc:
            raise AIProviderError("model provider request timed out") from exc
        except httpx.RequestError as exc:
            raise AIProviderError("could not connect to model provider") from exc
        if finish_reason == "length":
            raise AIProviderError(
                f"model provider output reached the {self.config.max_output_tokens} token limit",
                partial_content="".join(display_parts),
                input_tokens=actual_input or _estimate_tokens(system_prompt + user_prompt),
                output_tokens=actual_output or _estimate_tokens("".join(display_parts)),
                estimated=actual_output is None,
            )
        content = "".join(content_parts)
        if not content:
            raise AIProviderError("model provider returned empty content")
        if len(content.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise AIProviderError("model provider response is too large")

        has_actual_usage = (
            isinstance(actual_input, int)
            and actual_input >= 0
            and isinstance(actual_output, int)
            and actual_output >= 0
        )
        return ProviderResult(
            content=content,
            input_tokens=(
                actual_input
                if has_actual_usage
                else _estimate_tokens(system_prompt + user_prompt)
            ),
            output_tokens=actual_output if has_actual_usage else _estimate_tokens(content),
            estimated=not has_actual_usage,
        )
