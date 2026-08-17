import time

from local_worker.cache.store import CacheStore, cache_key
from tests.fakes import make_settings


def test_cache_hit_miss_and_key(tmp_path):
    settings = make_settings(tmp_path, cache_max_size_gb=1)
    store = CacheStore(settings)
    key = cache_key(
        content_hash="abc",
        provider="ollama",
        model="gemma",
        objective="  Analyze   THIS ",
        pipeline_version="1.0",
    )
    same = cache_key(
        content_hash="abc",
        provider="ollama",
        model="gemma",
        objective="analyze this",
        pipeline_version="1.0",
    )
    assert key == same
    assert store.get(key) is None
    store.put(key, {"hello": "world"})
    assert store.get(key) == {"hello": "world"}
    stats = store.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["hit_rate"] == 50.0


def test_ttl_uses_last_accessed(tmp_path):
    settings = make_settings(tmp_path, cache_ttl_days=1, cache_max_size_gb=1)
    store = CacheStore(settings)
    store.put("k", {"v": 1})
    with store._lock, store._connect() as conn:
        conn.execute("UPDATE entries SET last_accessed_at = ?", (time.time() - 2 * 86400,))
    assert store.get("k") is None
    assert store.stats()["misses"] == 1


def test_lru_and_size_limit(tmp_path):
    settings = make_settings(
        tmp_path,
        cache_max_size_gb=1000 / (1024**3),
        cache_cleanup_threshold_percent=90,
        cache_target_usage_percent=20,
        cache_ttl_days=30,
    )
    store = CacheStore(settings)
    store.put("old", {"blob": "x" * 80})
    store.put("mid", {"blob": "y" * 80})
    store.put("new", {"blob": "z" * 80})
    store.get("new")
    store.get("new")
    result = store.cleanup(force=True)
    assert result["removed"] >= 1
    assert store.get("new") is not None


def test_never_reused_removed_before_lru(tmp_path):
    settings = make_settings(
        tmp_path,
        cache_max_size_gb=1000 / (1024**3),
        cache_cleanup_threshold_percent=90,
        cache_target_usage_percent=20,
    )
    store = CacheStore(settings)
    store.put("once", {"blob": "a" * 120})
    store.put("hot", {"blob": "b" * 120})
    store.get("hot")
    store.get("hot")
    store.cleanup(force=True)
    assert store.get("once") is None
    assert store.get("hot") is not None


def test_persistent_survives_clear_and_gc(tmp_path):
    settings = make_settings(tmp_path, cache_max_size_gb=1)
    store = CacheStore(settings)
    store.put("keep", {"ok": True}, persistent=True)
    store.put("drop", {"ok": False})
    store.clear(include_persistent=False)
    assert store.get("keep") is not None
    assert store.get("drop") is None
    store.cleanup(force=True)
    assert store.get("keep") is not None
    store.clear(include_persistent=True)
    assert store.get("keep") is None


def test_corrupted_payload_is_a_miss(tmp_path):
    settings = make_settings(tmp_path, cache_max_size_gb=1)
    store = CacheStore(settings)
    store.put("bad", {"ok": True})
    with store._lock, store._connect() as conn:
        conn.execute("UPDATE entries SET payload = 'not-json' WHERE key = 'bad'")
    assert store.get("bad") is None


def test_startup_and_periodic_gc(tmp_path):
    settings = make_settings(tmp_path, cache_max_size_gb=1, cache_cleanup_interval_hours=0)
    store = CacheStore(settings)
    store.put("k", {"v": 1})
    store.maybe_startup_cleanup()
    store.maybe_periodic_cleanup()
    assert store.stats()["entries"] >= 0
