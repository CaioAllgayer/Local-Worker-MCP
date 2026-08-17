from pathlib import Path

from local_worker.config import Settings


def test_defaults_are_safe(monkeypatch):
    monkeypatch.delenv("LOCAL_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("SECURITY_MODE", raising=False)
    monkeypatch.delenv("ENABLE_SHELL", raising=False)
    monkeypatch.delenv("LOCAL_LLM_MAX_RETRIES", raising=False)
    monkeypatch.setenv("LOCAL_WORKER_HOME", str(Path.cwd() / "unused-home"))
    settings = Settings.from_env(load_files=False)
    assert settings.provider == "ollama"
    assert settings.base_url == "http://127.0.0.1:11434"
    assert settings.connect_timeout_seconds == 2.0
    assert settings.request_timeout_seconds == 45.0
    assert settings.max_retries == 0
    assert settings.security_mode == "READ_ONLY"
    assert settings.enable_shell is False
    assert settings.endpoint_kind == "local"


def test_lan_endpoint(monkeypatch):
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://192.168.1.100:11434")
    settings = Settings.from_env(load_files=False)
    assert settings.endpoint_kind == "lan"
    assert settings.base_url == "http://192.168.1.100:11434"


def test_localhost_aliases(monkeypatch):
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434")
    assert Settings.from_env(load_files=False).endpoint_kind == "local"


def test_invalid_provider_and_mode_fall_back(monkeypatch):
    monkeypatch.setenv("LOCAL_LLM_PROVIDER", "mystery")
    monkeypatch.setenv("SECURITY_MODE", "YOLO")
    settings = Settings.from_env(load_files=False)
    assert settings.provider == "ollama"
    assert settings.security_mode == "READ_ONLY"


def test_allowed_paths_keep_windows_drives(monkeypatch):
    monkeypatch.setenv("ALLOWED_PATHS", r"C:\Projects,D:\Research")
    settings = Settings.from_env(load_files=False)
    assert settings.allowed_paths == [r"C:\Projects", r"D:\Research"]
