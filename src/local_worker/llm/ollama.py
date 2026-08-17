"""First-class Ollama backend. Model name is detected or configured, never hardcoded."""

from __future__ import annotations

import re
import time

from .base import Completion, Health, LLMError
from .httputil import make_client, request_json


class OllamaAdapter:
    provider = "ollama"

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
        self.base_url = _strip_v1(base_url.rstrip("/"))
        self.configured_model = model
        self.max_retries = max_retries
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = make_client(connect_timeout, request_timeout, headers)
        self._resolved_model = model
        self._context_length: int | None = None

    def close(self) -> None:
        self._client.close()

    def health(self) -> Health:
        started = time.perf_counter()
        try:
            response = self._client.get(self.base_url)
            latency = round((time.perf_counter() - started) * 1000, 1)
        except Exception as exc:
            return Health(reachable=False, error=str(exc) or "unreachable")

        models = self.list_models()
        model = self.resolve_model(models)
        available = bool(model) and any(_same_model(m, model) for m in models)
        context = self.context_length(model) if model and available else None
        return Health(
            reachable=response.is_success,
            latency_ms=latency,
            models=models,
            model=model,
            model_available=available if models else None,
            context_length=context,
            error=None if response.is_success else f"HTTP {response.status_code}",
        )

    def list_models(self) -> list[str]:
        data = request_json(self._client, "GET", f"{self.base_url}/api/tags", max_retries=0)
        models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(models, list):
            return []
        names: list[str] = []
        for item in models:
            if isinstance(item, dict) and item.get("name"):
                names.append(str(item["name"]))
            elif isinstance(item, str):
                names.append(item)
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

    def context_length(self, model: str) -> int | None:
        if self._context_length:
            return self._context_length
        try:
            data = request_json(
                self._client,
                "POST",
                f"{self.base_url}/api/show",
                json_body={"name": model},
                max_retries=0,
            )
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        info = data.get("model_info") or data.get("details") or {}
        if isinstance(info, dict):
            for key, value in info.items():
                if "context" in str(key).lower() and isinstance(value, (int, float)):
                    self._context_length = int(value)
                    return self._context_length
        params = data.get("parameters")
        if isinstance(params, str):
            match = re.search(r"num_ctx\s+(\d+)", params)
            if match:
                self._context_length = int(match.group(1))
                return self._context_length
        return None

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
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        data = request_json(
            self._client,
            "POST",
            f"{self.base_url}/api/chat",
            json_body={
                "model": model,
                "messages": messages,
                "stream": False,
                "options": {"num_predict": max_tokens, "temperature": temperature},
            },
            max_retries=self.max_retries,
        )
        if not isinstance(data, dict):
            raise LLMError("Local LLM returned an unexpected payload")
        message = data.get("message") or {}
        text = ""
        if isinstance(message, dict):
            text = str(message.get("content") or "")
        if not text:
            text = str(data.get("response") or data.get("text") or "")
        if not text.strip():
            raise LLMError("Local LLM returned an empty response")
        return Completion(
            text=text,
            model=str(data.get("model") or model),
            input_tokens=int(data.get("prompt_eval_count") or 0),
            output_tokens=int(data.get("eval_count") or 0),
            raw=data,
        )


def _strip_v1(url: str) -> str:
    return url[:-3] if url.endswith("/v1") else url


def _same_model(left: str, right: str) -> bool:
    return left == right or left.split(":")[0] == right.split(":")[0]
