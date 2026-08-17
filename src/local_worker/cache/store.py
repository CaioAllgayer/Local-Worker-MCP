"""Persistent, self-cleaning cache. Default entries are disposable."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from ..config import Settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    key TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    last_accessed_at REAL NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 0,
    size_bytes INTEGER NOT NULL,
    persistent INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS counters (
    k TEXT PRIMARY KEY,
    v INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
"""


def cache_key(
    *,
    content_hash: str,
    provider: str,
    model: str,
    objective: str,
    pipeline_version: str,
    extra: str = "",
) -> str:
    normalized = " ".join((objective or "").lower().split())
    raw = f"{content_hash}|{provider}|{model}|{normalized}|{pipeline_version}|{extra}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CacheStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = settings.cache_dir / "cache.sqlite3"
        self._lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        if settings.cache_enabled:
            settings.cache_dir.mkdir(parents=True, exist_ok=True)
            self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            conn.execute("INSERT OR IGNORE INTO counters(k, v) VALUES('hits', 0)")
            conn.execute("INSERT OR IGNORE INTO counters(k, v) VALUES('misses', 0)")
            conn.execute("INSERT OR IGNORE INTO meta(k, v) VALUES('last_cleanup', '0')")

    def get(self, key: str) -> dict[str, Any] | None:
        if not self.settings.cache_enabled:
            return None
        now = time.time()
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM entries WHERE key = ?", (key,)).fetchone()
            if row is None:
                conn.execute("UPDATE counters SET v = v + 1 WHERE k = 'misses'")
                return None
            expired = (not row["persistent"]) and (
                now - row["last_accessed_at"] > self.settings.cache_ttl_days * 86400
            )
            if expired:
                conn.execute("DELETE FROM entries WHERE key = ?", (key,))
                conn.execute("UPDATE counters SET v = v + 1 WHERE k = 'misses'")
                return None
            conn.execute(
                "UPDATE entries SET last_accessed_at = ?, access_count = access_count + 1 WHERE key = ?",
                (now, key),
            )
            conn.execute("UPDATE counters SET v = v + 1 WHERE k = 'hits'")
            try:
                return json.loads(row["payload"])
            except json.JSONDecodeError:
                conn.execute("DELETE FROM entries WHERE key = ?", (key,))
                conn.execute("UPDATE counters SET v = v + 1 WHERE k = 'misses'")
                return None

    def put(self, key: str, payload: dict[str, Any], *, persistent: bool = False) -> None:
        if not self.settings.cache_enabled:
            return
        encoded = json.dumps(payload, ensure_ascii=False, default=str)
        now = time.time()
        size = len(encoded.encode("utf-8"))
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO entries(key, created_at, last_accessed_at, access_count, size_bytes, persistent, payload)
                VALUES(?, ?, ?, 0, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    created_at = excluded.created_at,
                    last_accessed_at = excluded.last_accessed_at,
                    access_count = 0,
                    size_bytes = excluded.size_bytes,
                    persistent = excluded.persistent,
                    payload = excluded.payload
                """,
                (key, now, now, size, 1 if persistent else 0, encoded),
            )
        if self._size_bytes() >= self._threshold_bytes():
            self.cleanup()

    def stats(self) -> dict[str, Any]:
        if not self.settings.cache_enabled:
            return {
                "enabled": False,
                "size_bytes": 0,
                "limit_bytes": self.settings.cache_max_bytes,
                "entries": 0,
                "hits": 0,
                "misses": 0,
                "hit_rate": 0.0,
                "expired": 0,
            }
        now = time.time()
        ttl = self.settings.cache_ttl_days * 86400
        with self._lock, self._connect() as conn:
            size = conn.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM entries").fetchone()[0]
            entries = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            expired = conn.execute(
                "SELECT COUNT(*) FROM entries WHERE persistent = 0 AND last_accessed_at < ?",
                (now - ttl,),
            ).fetchone()[0]
            hits = conn.execute("SELECT v FROM counters WHERE k = 'hits'").fetchone()[0]
            misses = conn.execute("SELECT v FROM counters WHERE k = 'misses'").fetchone()[0]
        total = hits + misses
        return {
            "enabled": True,
            "size_bytes": int(size),
            "limit_bytes": self.settings.cache_max_bytes,
            "entries": int(entries),
            "hits": int(hits),
            "misses": int(misses),
            "hit_rate": round((hits / total) * 100, 1) if total else 0.0,
            "expired": int(expired),
        }

    def cleanup(self, *, force: bool = False) -> dict[str, Any]:
        if not self.settings.cache_enabled:
            return {"removed": 0, "size_bytes": 0}
        if not self._cleanup_lock.acquire(blocking=False):
            return {"removed": 0, "skipped": True}
        try:
            return self._cleanup_locked(force=force)
        finally:
            self._cleanup_lock.release()

    def maybe_startup_cleanup(self) -> None:
        if not self.settings.cache_enabled:
            return
        last = self._last_cleanup()
        interval = self.settings.cache_cleanup_interval_hours * 3600
        if last == 0 or time.time() - last >= interval:
            self.cleanup(force=True)

    def maybe_periodic_cleanup(self) -> None:
        if not self.settings.cache_enabled:
            return
        last = self._last_cleanup()
        interval = self.settings.cache_cleanup_interval_hours * 3600
        if time.time() - last >= interval:
            thread = threading.Thread(target=self.cleanup, kwargs={"force": False}, daemon=True)
            thread.start()

    def clear(self, *, include_persistent: bool = False) -> dict[str, Any]:
        if not self.settings.cache_enabled:
            return {"removed": 0}
        with self._lock, self._connect() as conn:
            if include_persistent:
                removed = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
                conn.execute("DELETE FROM entries")
            else:
                removed = conn.execute("SELECT COUNT(*) FROM entries WHERE persistent = 0").fetchone()[0]
                conn.execute("DELETE FROM entries WHERE persistent = 0")
        return {"removed": int(removed)}

    def _cleanup_locked(self, *, force: bool) -> dict[str, Any]:
        size = self._size_bytes()
        threshold = self._threshold_bytes()
        target = int(self.settings.cache_max_bytes * (self.settings.cache_target_usage_percent / 100))
        if not force and size < threshold:
            self._set_last_cleanup()
            return {"removed": 0, "size_bytes": size}
        now = time.time()
        ttl = self.settings.cache_ttl_days * 86400
        removed = 0
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM entries WHERE persistent = 0 AND last_accessed_at < ?",
                (now - ttl,),
            )
            removed += cur.rowcount

            def over_target() -> bool:
                current = conn.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM entries").fetchone()[0]
                return int(current) > target

            if over_target():
                unused = conn.execute(
                    """
                    SELECT key FROM entries
                    WHERE persistent = 0 AND access_count <= 1
                    ORDER BY last_accessed_at ASC
                    """
                ).fetchall()
                for row in unused:
                    if not over_target():
                        break
                    conn.execute("DELETE FROM entries WHERE key = ?", (row["key"],))
                    removed += 1

            if over_target():
                lru = conn.execute(
                    "SELECT key FROM entries WHERE persistent = 0 ORDER BY last_accessed_at ASC"
                ).fetchall()
                for row in lru:
                    if not over_target():
                        break
                    conn.execute("DELETE FROM entries WHERE key = ?", (row["key"],))
                    removed += 1
            size = int(conn.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM entries").fetchone()[0])
        self._set_last_cleanup()
        return {"removed": removed, "size_bytes": size}

    def _size_bytes(self) -> int:
        with self._lock, self._connect() as conn:
            return int(conn.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM entries").fetchone()[0])

    def _threshold_bytes(self) -> int:
        return int(self.settings.cache_max_bytes * (self.settings.cache_cleanup_threshold_percent / 100))

    def _last_cleanup(self) -> float:
        try:
            with self._lock, self._connect() as conn:
                row = conn.execute("SELECT v FROM meta WHERE k = 'last_cleanup'").fetchone()
            return float(row[0]) if row else 0.0
        except (TypeError, ValueError, sqlite3.Error):
            return 0.0

    def _set_last_cleanup(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO meta(k, v) VALUES('last_cleanup', ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                (str(time.time()),),
            )
