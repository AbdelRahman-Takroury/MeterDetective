"""Configurable structured-output LLM adapter with one repair attempt."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TypeVar
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field, ValidationError

JsonPostTransport = Callable[[str, dict[str, Any], dict[str, str], float], dict[str, Any]]
OutputT = TypeVar("OutputT", bound=BaseModel)


def _post_json(
    url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float
) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "MeterDetective/0.1",
            **headers,
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - configured provider
        return json.loads(response.read().decode("utf-8"))


class InvestigationNarrative(BaseModel):
    """Narrative-only output; deterministic tools own every numerical conclusion."""

    summary: str = Field(min_length=1)
    hypothesis_interpretation: str = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)
    requested_tools: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class LLMProviderError(RuntimeError):
    pass


class StructuredLLMAdapter:
    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout_seconds: float,
        transport: JsonPostTransport = _post_json,
    ) -> None:
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def generate(self, prompt: str, output_type: type[OutputT]) -> OutputT:
        if self.provider == "disabled":
            raise LLMProviderError("LLM provider is disabled")
        if self.provider != "openai_compatible":
            raise LLMProviderError(f"Unsupported LLM provider: {self.provider}")
        if not self.api_key or not self.model:
            raise LLMProviderError("LLM provider credentials/model are not configured")
        instruction = (
            "Return only JSON matching this schema. Do not calculate or introduce numerical "
            f"claims; deterministic tools own them. Schema: {output_type.model_json_schema()}"
        )
        first_error: Exception | None = None
        for attempt in range(2):
            messages = [
                {"role": "system", "content": instruction},
                {"role": "user", "content": prompt},
            ]
            if attempt and first_error is not None:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Repair the prior invalid JSON. Validation error: {first_error}"
                        ),
                    }
                )
            try:
                response = self.transport(
                    f"{self.base_url}/chat/completions",
                    {"model": self.model, "messages": messages, "temperature": 0},
                    {"Authorization": f"Bearer {self.api_key}"},
                    self.timeout_seconds,
                )
                content = response["choices"][0]["message"]["content"]
                return output_type.model_validate_json(content)
            except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValidationError) as exc:
                first_error = exc
            except (OSError, TimeoutError) as exc:
                raise LLMProviderError(
                    f"LLM provider request failed safely: {type(exc).__name__}"
                ) from exc
        raise LLMProviderError("LLM returned invalid structured output after one repair retry")
