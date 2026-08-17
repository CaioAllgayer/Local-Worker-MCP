"""OpenAI-compatible backends: LM Studio, llama.cpp server, vLLM, etc."""

from __future__ import annotations

import time

from .base import Completion, Health, LLMError
from .httputil import make_client, request_json


class OpenAICompatibleAdapter:
    provider = "openai_compatible"

    def __init__(
        self,
        base_url: str,
        model: str = "",
        *,
        connect_timeout: float = 2.0,
        request_timeout: float = 45.0,
        max_retries: int = 0,
        api_key: str = "",
    ):
        self.base_url = _normalize_base(base_url)
        self.configured_model = model
        self.max_retries = max_retries
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = make_client(connect_timeout, request_timeout, headers)
        self._resolved_model = model

    def close(self) -> None:
        self._client.close()

    def health(self) -> Health:
        started = time.perf_counter()
        try:
            models = self.list_models()
            latency = round((time.perf_counter() - started) * 1000, 1)
        except Exception as exc:
            return Health(reachable=False, error=str(exc) or "unreachable")
        model = self.resolve_model(models)
        available = bool(model) and (not models or model in models or any(model in m for m in models))
        return Health(
            reachable=True,
            latency_ms=latency,
            models=models,
            model=model,
            model_available=available if models else (True if model else None),
        )

    def list_models(self) -> list[str]:
        data = request_json(self._client, "GET", f"{self.base_url}/models", max_retries=0)
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        names: list[str] = []
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                names.append(str(item["id"]))
        return names

    def resolve_model(self, models: list[str] | None = None) -> str:
        if self.configured_model:
            return self.configured_model
        if self._resolved_model:
            return self._resolved_model
        names = models if models is not None else self.list_models()
        for name in names:
            if "gemma" in name.lower():
                self._resolved_model = name
                return name
        if names:
            self._resolved_model = names[0]
            return names[0]
        return ""

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 4000,
        temperature: float = 0.2,
    ) -> Completion:
        model = self.resolve_model()
        if not model:
            raise LLMError("No local model configured or detected")
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        data = request_json(
            self._client,
            "POST",
            f"{self.base_url}/chat/completions",
            json_body={
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            max_retries=self.max_retries,
        )
        if not isinstance(data, dict):
            raise LLMError("Local LLM returned an unexpected payload")
        choices = data.get("choices") or []
        text = ""
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
            if isinstance(message, dict):
                text = str(message.get("content") or "")
            if not text:
                text = str(choices[0].get("text") or "")
        if not text.strip():
            raise LLMError("Local LLM returned an empty response")
        usage = data.get("usage") or {}
        return Completion(
            text=text,
            model=str(data.get("model") or model),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            raw=data,
        )


def _normalize_base(url: str) -> str:
    url = url.rstrip("/")
    if url.endswith("/v1"):
        return url
    return f"{url}/v1"
