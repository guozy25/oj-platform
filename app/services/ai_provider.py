from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.models.ai import ModelConfigUpdate


class AIProviderError(RuntimeError):
    """Safe model-provider failure suitable for returning to a task owner."""


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


class AIProviderClient:
    """Minimal OpenAI-compatible chat-completions client."""

    def __init__(self, config: ModelConfigUpdate) -> None:
        self.config = config

    def _endpoint(self) -> str:
        if self.config.provider_url.endswith("/chat/completions"):
            return self.config.provider_url
        return f"{self.config.provider_url}/chat/completions"

    async def complete(self, system_prompt: str, user_prompt: str) -> ProviderResult:
        request_payload = {
            "model": self.config.model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(90.0, connect=10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                response = await client.post(
                    self._endpoint(), headers=headers, json=request_payload
                )
        except httpx.TimeoutException as exc:
            raise AIProviderError("model provider request timed out") from exc
        except httpx.RequestError as exc:
            raise AIProviderError("could not connect to model provider") from exc

        if response.status_code >= 400:
            # Provider bodies can echo prompts or credentials, so never expose them.
            raise AIProviderError(f"model provider returned HTTP {response.status_code}")
        try:
            payload = response.json()
            content = _message_text(payload["choices"][0]["message"]["content"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AIProviderError("model provider returned an invalid response") from exc
        if not content:
            raise AIProviderError("model provider returned empty content")
        if len(content.encode("utf-8")) > 2_000_000:
            raise AIProviderError("model provider response is too large")

        usage = payload.get("usage")
        actual_input = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        actual_output = usage.get("completion_tokens") if isinstance(usage, dict) else None
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
