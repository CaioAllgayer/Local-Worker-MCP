"""Shared LLM types. Local models are an optimization, never a hard dependency."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMError(Exception):
    def __init__(self, reason: str, *, unavailable: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.unavailable = unavailable


class Unavailable(LLMError):
    def __init__(self, reason: str = "Local LLM endpoint unreachable"):
        super().__init__(reason, unavailable=True)


@dataclass
class Completion:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Health:
    reachable: bool
    latency_ms: float | None = None
    models: list[str] = field(default_factory=list)
    model: str = ""
    model_available: bool | None = None
    context_length: int | None = None
    error: str | None = None


class LLMAdapter(Protocol):
    provider: str
    base_url: str

    def close(self) -> None: ...

    def health(self) -> Health: ...

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 4000,
        temperature: float = 0.2,
    ) -> Completion: ...
