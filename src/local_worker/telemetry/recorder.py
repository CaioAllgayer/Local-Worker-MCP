"""Per-task savings telemetry plus accumulated totals."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any

from ..config import Settings
from ..logging_setup import get_logger


class Telemetry:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = settings.log_dir / "telemetry.jsonl"
        self.db_path = settings.log_dir / "telemetry.sqlite3"
        self._lock = threading.Lock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=5)

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS totals (
                    k TEXT PRIMARY KEY,
                    v REAL NOT NULL
                )
                """
            )
            for key in (
                "tasks",
                "successes",
                "failures",
                "unavailable",
                "cache_hits",
                "original_tokens",
                "frontier_tokens",
                "local_input_tokens",
                "local_output_tokens",
                "duration_ms",
            ):
                conn.execute("INSERT OR IGNORE INTO totals(k, v) VALUES(?, 0)", (key,))

    def record(self, event: dict[str, Any]) -> None:
        payload = dict(event)
        payload.setdefault("ts", time.time())
        line = json.dumps(payload, ensure_ascii=False, default=str)
        with self._lock:
            with self.jsonl_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            with self._connect() as conn:
                conn.execute("UPDATE totals SET v = v + 1 WHERE k = 'tasks'")
                status = payload.get("status")
                if status == "success":
                    conn.execute("UPDATE totals SET v = v + 1 WHERE k = 'successes'")
                elif status == "unavailable":
                    conn.execute("UPDATE totals SET v = v + 1 WHERE k = 'unavailable'")
                else:
                    conn.execute("UPDATE totals SET v = v + 1 WHERE k = 'failures'")
                if payload.get("cache_hit"):
                    conn.execute("UPDATE totals SET v = v + 1 WHERE k = 'cache_hits'")
                for key in (
                    "original_tokens",
                    "frontier_tokens",
                    "local_input_tokens",
                    "local_output_tokens",
                    "duration_ms",
                ):
                    conn.execute(
                        "UPDATE totals SET v = v + ? WHERE k = ?", (float(payload.get(key) or 0), key)
                    )
        logger = get_logger()
        logger.info(
            "tool=%s status=%s model=%s provider=%s endpoint=%s cache=%s orig=%s frontier=%s reduction=%s duration_ms=%s error=%s",
            payload.get("tool"),
            payload.get("status"),
            payload.get("model"),
            payload.get("provider"),
            payload.get("endpoint_kind"),
            "HIT" if payload.get("cache_hit") else "MISS",
            payload.get("original_tokens"),
            payload.get("frontier_tokens"),
            payload.get("reduction_percent"),
            payload.get("duration_ms"),
            payload.get("error") or "",
        )

    def summary(self) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            rows = {row[0]: row[1] for row in conn.execute("SELECT k, v FROM totals")}
        original = rows.get("original_tokens") or 0
        frontier = rows.get("frontier_tokens") or 0
        reduction = round((1 - (frontier / original)) * 100, 1) if original else 0.0
        tasks = int(rows.get("tasks") or 0)
        return {
            "tasks": tasks,
            "successes": int(rows.get("successes") or 0),
            "failures": int(rows.get("failures") or 0),
            "unavailable": int(rows.get("unavailable") or 0),
            "cache_hits": int(rows.get("cache_hits") or 0),
            "original_tokens": int(original),
            "frontier_tokens": int(frontier),
            "local_input_tokens": int(rows.get("local_input_tokens") or 0),
            "local_output_tokens": int(rows.get("local_output_tokens") or 0),
            "reduction_percent": reduction,
            "duration_ms": int(rows.get("duration_ms") or 0),
        }
