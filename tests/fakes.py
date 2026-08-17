from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from local_worker.config import Settings
from local_worker.llm.base import Completion, Health, Unavailable
from local_worker.llm.circuit import CircuitBreaker
from local_worker.llm.factory import LLMGateway
from local_worker.workers.service import WorkerService


@dataclass
class FakeAdapter:
    provider: str = "ollama"
    base_url: str = "http://127.0.0.1:11434"
    model: str = "gemma-test"
    responses: list[Any] = field(default_factory=list)
    fail_with: Exception | None = None
    fail_times: int = 0
    calls: int = 0
    context_length: int | None = 8192

    def close(self) -> None:
        return None

    def health(self) -> Health:
        if self.fail_with and self.calls < self.fail_times:
            self.calls += 1
            if isinstance(self.fail_with, Unavailable):
                raise self.fail_with
            return Health(reachable=False, error=str(self.fail_with))
        return Health(
            reachable=True,
            latency_ms=7.0,
            models=[self.model],
            model=self.model,
            model_available=True,
            context_length=self.context_length,
        )

    def complete(
        self, prompt: str, *, system: str | None = None, max_tokens: int = 4000, temperature: float = 0.2
    ) -> Completion:
        self.calls += 1
        if self.fail_with and (self.fail_times == 0 or self.calls <= self.fail_times):
            raise self.fail_with
        payload = self.responses.pop(0) if self.responses else _default_payload(prompt)
        if isinstance(payload, Exception):
            raise payload
        if isinstance(payload, Completion):
            return payload
        if isinstance(payload, dict):
            text = json.dumps(payload, ensure_ascii=False)
        else:
            text = str(payload)
        return Completion(text=text, model=self.model, input_tokens=12, output_tokens=8)


def _default_payload(prompt: str) -> dict[str, Any]:
    import re

    match = re.search(r"\[page (\d+)\]", prompt)
    page = int(match.group(1)) if match else 1
    excerpt = "stop de 2 ATR" if "2 ATR" in prompt or "ATR" in prompt else "ok"
    return {
        "summary": "compact result",
        "result": "compact result",
        "findings": [{"text": "A estratégia utiliza stop de 2 ATR.", "page": page, "excerpt": excerpt}],
        "key_findings": ["A estratégia utiliza stop de 2 ATR."],
        "numbers": [{"label": "stop", "value": "2 ATR", "page": page, "excerpt": excerpt}],
        "rules": [{"text": "stop de 2 ATR", "page": page}],
        "parameters": [{"name": "stop", "value": "2 ATR", "page": page}],
        "dates": [],
        "methodology": ["local extraction"],
        "limitations": [],
        "evidence": [{"page": page, "text": excerpt}],
        "uncertainties": [],
        "confidence": 0.91,
    }


def make_settings(tmp_path, **overrides) -> Settings:
    values = dict(
        provider="ollama",
        base_url="http://127.0.0.1:11434",
        model="gemma-test",
        connect_timeout_seconds=0.2,
        request_timeout_seconds=1.0,
        max_retries=0,
        circuit_breaker_failures=2,
        circuit_breaker_cooldown_seconds=60.0,
        max_parallel_workers=4,
        security_mode="READ_ONLY",
        allowed_paths=[str(tmp_path)],
        enable_shell=False,
        cache_enabled=True,
        cache_ttl_days=30,
        cache_max_size_gb=0.00001,
        cache_cleanup_threshold_percent=90.0,
        cache_target_usage_percent=80.0,
        cache_cleanup_interval_hours=6.0,
        home_dir=tmp_path / "home",
        pipeline_version="1.0",
    )
    values.update(overrides)
    return Settings(**values)


def make_service(tmp_path, adapter: FakeAdapter | None = None, **overrides) -> WorkerService:
    settings = make_settings(tmp_path, **overrides)
    adapter = adapter or FakeAdapter()
    gateway = LLMGateway(
        settings,
        adapter=adapter,
        breaker=CircuitBreaker(
            failure_threshold=settings.circuit_breaker_failures,
            cooldown_seconds=settings.circuit_breaker_cooldown_seconds,
        ),
    )
    return WorkerService(settings, gateway=gateway)
