import threading
import time

from local_worker.llm.base import Unavailable
from tests.fakes import FakeAdapter, make_service


def test_batch_partial_failure_does_not_cancel(tmp_path):
    adapter = FakeAdapter(
        responses=[
            {"result": "one", "findings": [], "evidence": [], "uncertainties": [], "confidence": 0.8},
            Unavailable("down"),
        ]
    )
    service = make_service(tmp_path, adapter, max_parallel_workers=2)
    result = service.delegate_batch(
        [
            {"id": "task_1", "objective": "ok"},
            {"id": "task_2", "objective": "fail"},
        ]
    )
    assert result["status"] == "partial"
    assert result["results"][0]["status"] == "success"
    assert result["results"][1]["status"] == "unavailable"
    assert result["summary"]["success"] == 1
    assert result["summary"]["unavailable"] == 1


def test_batch_parallelism(tmp_path):
    lock = threading.Lock()
    current = {"n": 0, "max": 0}

    class SlowAdapter(FakeAdapter):
        def complete(self, prompt, **kwargs):
            with lock:
                current["n"] += 1
                current["max"] = max(current["max"], current["n"])
            time.sleep(0.15)
            try:
                return super().complete(prompt, **kwargs)
            finally:
                with lock:
                    current["n"] -= 1

    service = make_service(tmp_path, SlowAdapter(), max_parallel_workers=3)
    result = service.delegate_batch(
        [
            {"id": "a", "objective": "1"},
            {"id": "b", "objective": "2"},
            {"id": "c", "objective": "3"},
        ]
    )
    assert result["status"] == "success"
    assert current["max"] >= 2


def test_empty_batch(tmp_path):
    result = make_service(tmp_path).delegate_batch([])
    assert result["status"] == "error"
