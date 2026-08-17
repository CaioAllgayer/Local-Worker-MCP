"""Adapter factory plus a fail-fast gateway with circuit breaker."""

from __future__ import annotations

from typing import Any

from ..config import Settings
from .base import Completion, Health, LLMError, Unavailable
from .circuit import CircuitBreaker
from .ollama import OllamaAdapter
from .openai_compatible import OpenAICompatibleAdapter


def create_adapter(settings: Settings):
    kwargs = dict(
        base_url=settings.base_url,
        model=settings.model,
        connect_timeout=settings.connect_timeout_seconds,
        request_timeout=settings.request_timeout_seconds,
        max_retries=settings.max_retries,
        api_key=settings.api_key,
    )
    if settings.provider == "openai_compatible":
        return OpenAICompatibleAdapter(**kwargs)
    return OllamaAdapter(**kwargs)


class LLMGateway:
    def __init__(self, settings: Settings, adapter=None, breaker: CircuitBreaker | None = None):
        self.settings = settings
        self.adapter = adapter or create_adapter(settings)
        self.breaker = breaker or CircuitBreaker(
            failure_threshold=settings.circuit_breaker_failures,
            cooldown_seconds=settings.circuit_breaker_cooldown_seconds,
        )

    def close(self) -> None:
        close = getattr(self.adapter, "close", None)
        if close:
            close()

    def check_circuit(self) -> None:
        if not self.breaker.allow():
            raise Unavailable("Local LLM circuit open; fallback recommended")

    def health(self, *, probe: bool = True) -> Health:
        if not probe or not self.breaker.allow():
            return Health(
                reachable=False,
                model=self.settings.model,
                error="circuit open" if self.breaker.state != "closed" else "probe skipped",
            )
        try:
            health = self.adapter.health()
        except Unavailable as exc:
            self.breaker.record_failure()
            return Health(reachable=False, error=exc.reason)
        except LLMError as exc:
            if exc.unavailable:
                self.breaker.record_failure()
            return Health(reachable=False, error=exc.reason)
        except Exception as exc:
            self.breaker.record_failure()
            return Health(reachable=False, error=str(exc) or "unreachable")
        if health.reachable:
            self.breaker.record_success()
        else:
            self.breaker.record_failure()
        return health

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> Completion:
        self.check_circuit()
        try:
            result = self.adapter.complete(
                prompt,
                system=system,
                max_tokens=max_tokens or self.settings.max_output_tokens,
                temperature=temperature,
            )
        except Unavailable:
            self.breaker.record_failure()
            raise
        except LLMError as exc:
            if exc.unavailable:
                self.breaker.record_failure()
            raise
        except Exception as exc:
            self.breaker.record_failure()
            raise Unavailable(str(exc) or "Local LLM endpoint unreachable") from exc
        self.breaker.record_success()
        return result

    def snapshot(self) -> dict[str, Any]:
        return self.breaker.snapshot()
