"""Rotating, self-cleaning logs. Never persist full prompts or file contents."""

from __future__ import annotations

import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import Settings

LOGGER_NAME = "local_worker"
_LOG_PATH: str | None = None


def setup_logging(settings: Settings) -> logging.Logger:
    global _LOG_PATH
    logger = logging.getLogger(LOGGER_NAME)
    target = str(settings.log_dir / "worker.log")
    if _LOG_PATH == target and logger.handlers:
        return logger

    settings.log_dir.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    max_bytes = max(1_000_000, int((settings.log_max_size_mb * 1024 * 1024) / 4))
    handler = RotatingFileHandler(
        settings.log_dir / "worker.log",
        maxBytes=max_bytes,
        backupCount=4,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)

    cleanup_old_logs(settings.log_dir, settings.log_retention_days, settings.log_max_size_mb)
    _LOG_PATH = target
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def cleanup_old_logs(log_dir: Path, retention_days: int, max_size_mb: float) -> dict[str, int]:
    log_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    cutoff = now - (retention_days * 86400)
    removed = 0
    files = sorted(log_dir.glob("worker.log*"), key=lambda p: p.stat().st_mtime)
    for path in files:
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue

    max_bytes = int(max_size_mb * 1024 * 1024)
    while _dir_size(log_dir) > max_bytes:
        rotated = sorted(
            [p for p in log_dir.glob("worker.log*") if p.name != "worker.log"],
            key=lambda p: p.stat().st_mtime,
        )
        if not rotated:
            break
        try:
            rotated[0].unlink()
            removed += 1
        except OSError:
            break
    return {"removed": removed}


def _dir_size(path: Path) -> int:
    total = 0
    for child in path.glob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                continue
    return total
