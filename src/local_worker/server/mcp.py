"""FastMCP server. Frontier-agnostic stdio transport."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..workers.service import WorkerService

_service: WorkerService | None = None


def get_service() -> WorkerService:
    global _service
    if _service is None:
        _service = WorkerService()
    return _service


def create_mcp(service: WorkerService | None = None) -> FastMCP:
    mcp = FastMCP("local-worker")
    worker = service or get_service()

    @mcp.tool()
    def local_status() -> dict:
        """Check local worker health: provider, endpoint, model, circuit breaker, cache."""
        return worker.local_status()

    @mcp.tool()
    def delegate_task(
        objective: str,
        context: str = "",
        expected_output: str = "",
        max_output_tokens: int = 4000,
        persistent: bool = False,
        force_refresh: bool = False,
    ) -> dict:
        """Delegate a mechanical task to the local worker. Returns compact structured output."""
        return worker.delegate_task(
            objective=objective,
            context=context,
            expected_output=expected_output,
            max_output_tokens=max_output_tokens,
            persistent=persistent,
            force_refresh=force_refresh,
        )

    @mcp.tool()
    def delegate_batch(tasks: list[dict[str, Any]]) -> dict:
        """Run independent local tasks in parallel. One failure does not cancel the batch."""
        return worker.delegate_batch(tasks)

    @mcp.tool()
    def delegate_file(
        path: str,
        objective: str,
        output_mode: str = "structured",
        force_refresh: bool = False,
        persistent: bool = False,
    ) -> dict:
        """Have the local worker read a file (txt/md/csv/json/code/log) without loading it into frontier context."""
        return worker.delegate_file(
            path=path,
            objective=objective,
            output_mode=output_mode,
            force_refresh=force_refresh,
            persistent=persistent,
        )

    @mcp.tool()
    def delegate_pdf(
        path: str,
        objective: str,
        output_mode: str = "analysis",
        force_refresh: bool = False,
        persistent: bool = False,
    ) -> dict:
        """Extract, chunk, and analyze a PDF locally. Returns compact findings with page evidence."""
        return worker.delegate_pdf(
            path=path,
            objective=objective,
            output_mode=output_mode,
            force_refresh=force_refresh,
            persistent=persistent,
        )

    @mcp.tool()
    def cache_stats() -> dict:
        """Cache size, entry count, hits, misses, hit rate, and expired entries."""
        return worker.cache_stats()

    @mcp.tool()
    def cache_cleanup() -> dict:
        """Run cache garbage collection now (TTL, unused, then LRU)."""
        return worker.cache_cleanup()

    @mcp.tool()
    def cache_clear(include_persistent: bool = False) -> dict:
        """Delete disposable cache entries. Persistent artifacts stay unless include_persistent=true."""
        return worker.cache_clear(include_persistent=include_persistent)

    return mcp


def main() -> None:
    create_mcp().run()


if __name__ == "__main__":
    main()
