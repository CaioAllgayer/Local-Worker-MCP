import time

from local_worker.logging_setup import cleanup_old_logs, setup_logging
from tests.fakes import make_service, make_settings


def test_telemetry_records_compression(tmp_path):
    service = make_service(tmp_path)
    service.delegate_task("summarize", context="word " * 200)
    summary = service.telemetry.summary()
    assert summary["tasks"] >= 1
    assert summary["original_tokens"] > 0
    assert (tmp_path / "home" / "logs" / "telemetry.jsonl").exists()


def test_log_rotation_and_retention(tmp_path):
    settings = make_settings(tmp_path, log_retention_days=1, log_max_size_mb=0.001)
    log_dir = settings.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    old = log_dir / "worker.log.1"
    old.write_text("old\n", encoding="utf-8")
    old_time = time.time() - 10 * 86400
    # st_mtime update
    import os

    os.utime(old, (old_time, old_time))
    (log_dir / "worker.log.2").write_text("x" * 5000, encoding="utf-8")
    result = cleanup_old_logs(log_dir, retention_days=1, max_size_mb=0.001)
    assert result["removed"] >= 1
    setup_logging(settings)
    assert (log_dir / "worker.log").exists()


def test_cache_tools_and_status(tmp_path):
    service = make_service(tmp_path, cache_max_size_gb=1)
    service.delegate_task("hi", context="abc")
    stats = service.cache_stats()
    assert "entries" in stats
    cleanup = service.cache_cleanup()
    assert "removed" in cleanup
    cleared = service.cache_clear()
    assert cleared["removed"] >= 0
    status = service.local_status()
    assert status["provider"] == "ollama"
    assert status["reachable"] is True
    assert status["circuit_breaker"]["state"] == "closed"
    assert "cache" in status
