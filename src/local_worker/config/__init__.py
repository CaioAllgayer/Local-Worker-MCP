"""Environment-driven settings. Defaults are fail-fast and read-only."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

PIPELINE_VERSION = "1.0"

SECURITY_READ_ONLY = "READ_ONLY"
SECURITY_WORKSPACE_WRITE = "WORKSPACE_WRITE"
SECURITY_FULL_LOCAL = "FULL_LOCAL"
VALID_SECURITY_MODES = {SECURITY_READ_ONLY, SECURITY_WORKSPACE_WRITE, SECURITY_FULL_LOCAL}

VALID_PROVIDERS = {"ollama", "openai_compatible"}


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    return int(value) if value is not None else default


def _env_float(name: str, default: float) -> float:
    value = _env(name)
    return float(value) if value is not None else default


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _split_csv(name: str) -> list[str]:
    value = _env(name, "") or ""
    return [part.strip() for part in value.split(",") if part.strip()]


def load_env_files() -> None:
    load_dotenv()
    home = Path(_env("LOCAL_WORKER_HOME", str(Path.home() / ".local-worker-mcp")) or "")
    load_dotenv(home / ".env")


@dataclass
class Settings:
    provider: str = "ollama"
    base_url: str = "http://127.0.0.1:11434"
    model: str = ""
    api_key: str = ""
    connect_timeout_seconds: float = 2.0
    request_timeout_seconds: float = 45.0
    max_retries: int = 0
    circuit_breaker_failures: int = 2
    circuit_breaker_cooldown_seconds: float = 60.0
    max_parallel_workers: int = 4
    security_mode: str = SECURITY_READ_ONLY
    allowed_paths: list[str] = field(default_factory=list)
    enable_shell: bool = False
    allowed_commands: list[str] = field(default_factory=list)
    command_timeout_seconds: float = 30.0
    command_output_limit: int = 8000
    cache_enabled: bool = True
    cache_ttl_days: int = 30
    cache_max_size_gb: float = 10.0
    cache_cleanup_threshold_percent: float = 90.0
    cache_target_usage_percent: float = 80.0
    cache_cleanup_interval_hours: float = 6.0
    log_retention_days: int = 14
    log_max_size_mb: float = 250.0
    max_output_tokens: int = 4000
    chunk_tokens: int = 3500
    chunk_overlap_tokens: int = 200
    home_dir: Path = field(default_factory=lambda: Path.home() / ".local-worker-mcp")
    pipeline_version: str = PIPELINE_VERSION

    @classmethod
    def from_env(cls, load_files: bool = True) -> Settings:
        if load_files:
            load_env_files()
        mode = (_env("SECURITY_MODE", SECURITY_READ_ONLY) or SECURITY_READ_ONLY).upper()
        if mode not in VALID_SECURITY_MODES:
            mode = SECURITY_READ_ONLY
        provider = (_env("LOCAL_LLM_PROVIDER", "ollama") or "ollama").lower()
        if provider not in VALID_PROVIDERS:
            provider = "ollama"
        home = Path(_env("LOCAL_WORKER_HOME", str(Path.home() / ".local-worker-mcp")) or "")
        return cls(
            provider=provider,
            base_url=_env("LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434") or "http://127.0.0.1:11434",
            model=_env("LOCAL_LLM_MODEL", "") or "",
            api_key=_env("LOCAL_LLM_API_KEY", "") or "",
            connect_timeout_seconds=_env_float("LOCAL_LLM_CONNECT_TIMEOUT_SECONDS", 2.0),
            request_timeout_seconds=_env_float("LOCAL_LLM_REQUEST_TIMEOUT_SECONDS", 45.0),
            max_retries=max(0, _env_int("LOCAL_LLM_MAX_RETRIES", 0)),
            circuit_breaker_failures=max(1, _env_int("LOCAL_LLM_CIRCUIT_BREAKER_FAILURES", 2)),
            circuit_breaker_cooldown_seconds=_env_float("LOCAL_LLM_CIRCUIT_BREAKER_COOLDOWN_SECONDS", 60.0),
            max_parallel_workers=max(1, _env_int("MAX_PARALLEL_WORKERS", 4)),
            security_mode=mode,
            allowed_paths=_split_csv("ALLOWED_PATHS"),
            enable_shell=_env_bool("ENABLE_SHELL", False),
            allowed_commands=_split_csv("ALLOWED_COMMANDS"),
            command_timeout_seconds=_env_float("LOCAL_WORKER_COMMAND_TIMEOUT_SECONDS", 30.0),
            command_output_limit=_env_int("LOCAL_WORKER_COMMAND_OUTPUT_LIMIT", 8000),
            cache_enabled=_env_bool("CACHE_ENABLED", True),
            cache_ttl_days=_env_int("CACHE_TTL_DAYS", 30),
            cache_max_size_gb=_env_float("CACHE_MAX_SIZE_GB", 10.0),
            cache_cleanup_threshold_percent=_env_float("CACHE_CLEANUP_THRESHOLD_PERCENT", 90.0),
            cache_target_usage_percent=_env_float("CACHE_TARGET_USAGE_PERCENT", 80.0),
            cache_cleanup_interval_hours=_env_float("CACHE_CLEANUP_INTERVAL_HOURS", 6.0),
            log_retention_days=_env_int("LOG_RETENTION_DAYS", 14),
            log_max_size_mb=_env_float("LOG_MAX_SIZE_MB", 250.0),
            max_output_tokens=_env_int("LOCAL_LLM_MAX_OUTPUT_TOKENS", 4000),
            chunk_tokens=_env_int("LOCAL_WORKER_CHUNK_TOKENS", 3500),
            chunk_overlap_tokens=_env_int("LOCAL_WORKER_CHUNK_OVERLAP_TOKENS", 200),
            home_dir=home,
        )

    @property
    def cache_dir(self) -> Path:
        return self.home_dir / "cache"

    @property
    def log_dir(self) -> Path:
        return self.home_dir / "logs"

    @property
    def cache_max_bytes(self) -> int:
        return int(self.cache_max_size_gb * 1024**3)

    @property
    def endpoint_kind(self) -> str:
        host = (urlparse(self.base_url).hostname or "").lower()
        if host in {"127.0.0.1", "localhost", "::1"}:
            return "local"
        return "lan"

    @property
    def mcp_tools(self) -> list[str]:
        return [
            "local_status",
            "delegate_task",
            "delegate_batch",
            "delegate_file",
            "delegate_pdf",
            "cache_stats",
            "cache_cleanup",
            "cache_clear",
        ]

    @property
    def worker_tools(self) -> list[str]:
        tools = ["read_file", "list_directory", "search_files"]
        if self.enable_shell and self.security_mode != SECURITY_READ_ONLY:
            tools.append("run_command")
        return tools
