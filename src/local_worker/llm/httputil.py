"""HTTP helpers. Connection refused never retries."""

from __future__ import annotations

import json
from typing import Any

import httpx

from .base import LLMError, Unavailable


def make_client(
    connect_timeout: float, request_timeout: float, headers: dict[str, str] | None = None
) -> httpx.Client:
    timeout = httpx.Timeout(
        connect=connect_timeout,
        read=request_timeout,
        write=request_timeout,
        pool=connect_timeout,
    )
    return httpx.Client(timeout=timeout, headers=headers or {}, follow_redirects=True)


def request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
    max_retries: int = 0,
) -> Any:
    last_error: Exception | None = None
    attempts = 1 if max_retries <= 0 else max_retries + 1
    for attempt in range(attempts):
        try:
            response = client.request(method, url, json=json_body)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise Unavailable("Local LLM endpoint unreachable") from exc
        except httpx.TimeoutException as exc:
            raise Unavailable("Local LLM request timed out") from exc
        except httpx.HTTPError as exc:
            raise Unavailable(f"Local LLM HTTP error: {exc}") from exc

        if response.status_code in {429, 408} and attempt + 1 < attempts:
            last_error = LLMError(f"HTTP {response.status_code}")
            continue
        if response.status_code in {502, 503, 504}:
            raise Unavailable(f"Local LLM endpoint returned HTTP {response.status_code}")
        if response.status_code >= 400:
            reason = _error_message(response)
            if response.status_code == 404:
                raise LLMError(reason)
            raise LLMError(reason)

        if not response.content:
            raise LLMError("Local LLM returned an empty response")
        try:
            return response.json()
        except json.JSONDecodeError:
            text = response.text.strip()
            if text:
                return {"text": text}
            raise LLMError("Local LLM returned invalid JSON")
    raise last_error or Unavailable("Local LLM request failed")


def _error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict) and err.get("message"):
                return str(err["message"])
            if isinstance(err, str):
                return err
    except Exception:
        pass
    text = (response.text or "").strip()
    if text:
        return f"HTTP {response.status_code}: {text[:200]}"
    return f"HTTP {response.status_code}"
