"""CLI: serve the MCP, probe status, or benchmark compression on a file."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Settings
from .workers.service import WorkerService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="local-worker",
        description="Local Worker MCP — frontier plans, local model executes.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("serve", help="Start the MCP server on stdio (default)")
    sub.add_parser("status", help="Probe the configured local LLM")

    bench = sub.add_parser("benchmark", help="Measure local compression on a file")
    bench.add_argument("file", help="Path to a PDF or text file")
    bench.add_argument("--objective", default="Extract the important facts, numbers, rules, and limitations.")
    bench.add_argument("--json", action="store_true", help="Print raw JSON instead of a report")

    args = parser.parse_args(argv)
    command = args.command or "serve"

    if command == "serve":
        from .server.mcp import main as serve

        serve()
        return 0

    settings = Settings.from_env()
    if command == "benchmark":
        target = Path(args.file).expanduser().resolve()
        settings.allowed_paths = list(settings.allowed_paths) + [str(target.parent)]

    service = WorkerService(settings)
    try:
        if command == "status":
            print(json.dumps(service.local_status(), indent=2, ensure_ascii=False))
            return 0
        if command == "benchmark":
            return _benchmark(service, Path(args.file), args.objective, raw_json=args.json)
    finally:
        service.close()
    return 1


def _benchmark(service: WorkerService, path: Path, objective: str, *, raw_json: bool) -> int:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        result = service.delegate_pdf(str(path), objective)
    else:
        result = service.delegate_file(str(path), objective)
    if raw_json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0 if result.get("status") in {"success", "partial"} else 1

    status = service.local_status()
    compression = result.get("compression") or {}
    usage = result.get("usage") or {}
    cache = result.get("cache") or {}
    execution = result.get("execution") or {}
    size = path.stat().st_size if path.exists() else 0
    lines = [
        f"Arquivo: {path}",
        f"Worker: {status.get('model') or service.settings.model or '(auto)'}",
        f"Backend: {service.settings.provider}",
        f"Endpoint: {service.settings.endpoint_kind} ({service.settings.base_url})",
        f"Tamanho: {size} bytes",
        f"Tokens originais estimados: {compression.get('estimated_original_tokens', 0)}",
        f"Tokens processados localmente: {int(usage.get('local_input_tokens') or 0) + int(usage.get('local_output_tokens') or 0)}",
        f"Resultado para frontier: {compression.get('frontier_tokens', 0)} tokens",
        f"Compressão: {compression.get('reduction_percent', 0)}%",
        f"Tempo: {(execution.get('duration_ms') or 0) / 1000:.1f} s",
        f"Cache: {'HIT' if cache.get('hit') else 'MISS'}",
        f"Status: {result.get('status')}",
    ]
    if result.get("reason"):
        lines.append(f"Reason: {result.get('reason')}")
    print("\n".join(lines))
    return 0 if result.get("status") in {"success", "partial"} else 1


if __name__ == "__main__":
    sys.exit(main())
