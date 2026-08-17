import httpx
import pytest

from local_worker.llm.base import LLMError, Unavailable
from local_worker.llm.circuit import CircuitBreaker
from local_worker.llm.factory import LLMGateway
from local_worker.llm.httputil import make_client, request_json
from local_worker.llm.ollama import OllamaAdapter
from local_worker.llm.openai_compatible import OpenAICompatibleAdapter
from tests.fakes import FakeAdapter, make_settings


def test_ollama_complete_and_model_detection():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3"}, {"name": "gemma4:12b-qat"}]})
        if request.url.path == "/api/chat":
            body = request.read()
            assert b"gemma4:12b-qat" in body
            return httpx.Response(
                200,
                json={
                    "model": "gemma4:12b-qat",
                    "message": {"role": "assistant", "content": '{"ok": true}'},
                    "prompt_eval_count": 11,
                    "eval_count": 4,
                },
            )
        if request.url.path == "/":
            return httpx.Response(200, text="Ollama is running")
        return httpx.Response(404)

    adapter = OllamaAdapter("http://127.0.0.1:11434", model="")
    adapter._client = httpx.Client(transport=httpx.MockTransport(handler))
    health = adapter.health()
    assert health.reachable
    assert health.model == "gemma4:12b-qat"
    completion = adapter.complete("hi")
    assert completion.text == '{"ok": true}'
    assert completion.input_tokens == 11
    adapter.close()


def test_openai_compatible_lan_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "192.168.1.100"
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "gemma-local"}]})
        return httpx.Response(
            200,
            json={
                "model": "gemma-local",
                "choices": [{"message": {"content": "done"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
        )

    adapter = OpenAICompatibleAdapter("http://192.168.1.100:1234", model="gemma-local")
    adapter._client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://192.168.1.100:1234/v1"
    )
    # recreate with mock that still hits absolute URLs
    adapter._client = httpx.Client(transport=httpx.MockTransport(handler))
    health = adapter.health()
    assert health.reachable
    assert health.model == "gemma-local"
    completion = adapter.complete("summarize")
    assert completion.text == "done"
    assert completion.output_tokens == 2
    adapter.close()


def test_backend_offline_is_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = make_client(0.2, 1.0)
    client._transport = httpx.MockTransport(handler)
    with pytest.raises(Unavailable, match="unreachable"):
        request_json(
            httpx.Client(transport=httpx.MockTransport(handler)), "GET", "http://127.0.0.1:11434/api/tags"
        )


def test_timeout_is_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(Unavailable, match="timed out"):
        request_json(
            httpx.Client(transport=httpx.MockTransport(handler)), "POST", "http://127.0.0.1:11434/api/chat"
        )


def test_empty_and_invalid_output():
    def empty_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    with pytest.raises(LLMError, match="empty"):
        request_json(httpx.Client(transport=httpx.MockTransport(empty_handler)), "GET", "http://x/y")

    adapter = OllamaAdapter("http://127.0.0.1:11434", model="gemma")

    def blank_chat(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "   "}})

    adapter._client = httpx.Client(transport=httpx.MockTransport(blank_chat))
    with pytest.raises(LLMError, match="empty"):
        adapter.complete("hi")


def test_model_missing_is_error_not_success():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model not found"})

    adapter = OllamaAdapter("http://127.0.0.1:11434", model="missing")
    adapter._client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError, match="model not found"):
        adapter.complete("hi")


def test_fail_fast_no_retry_on_connect(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(Unavailable):
        request_json(
            httpx.Client(transport=httpx.MockTransport(handler)),
            "GET",
            "http://127.0.0.1:9/api/tags",
            max_retries=3,
        )
    assert calls["n"] == 1


def test_circuit_breaker_opens_and_cools_down(tmp_path):
    adapter = FakeAdapter(fail_with=Unavailable("down"), fail_times=2)
    settings = make_settings(tmp_path, circuit_breaker_failures=2, circuit_breaker_cooldown_seconds=60)
    gateway = LLMGateway(settings, adapter=adapter, breaker=CircuitBreaker(2, 60))
    with pytest.raises(Unavailable):
        gateway.complete("one")
    with pytest.raises(Unavailable):
        gateway.complete("two")
    assert gateway.breaker.state == "open"
    with pytest.raises(Unavailable, match="circuit open"):
        gateway.complete("three")
    assert adapter.calls == 2

    gateway.breaker.opened_at -= 61
    adapter.fail_with = None
    result = gateway.complete("probe")
    assert result.text
    assert gateway.breaker.state == "closed"


def test_circuit_reopens_after_failed_half_open(tmp_path):
    adapter = FakeAdapter(fail_with=Unavailable("down"))
    gateway = LLMGateway(make_settings(tmp_path), adapter=adapter, breaker=CircuitBreaker(1, 10))
    with pytest.raises(Unavailable):
        gateway.complete("a")
    assert gateway.breaker.state == "open"
    gateway.breaker.opened_at -= 11
    with pytest.raises(Unavailable):
        gateway.complete("b")
    assert gateway.breaker.state == "open"
