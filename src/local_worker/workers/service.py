"""Delegation service: fail fast, compress, cache, never block the frontier."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ..cache.store import CacheStore, cache_key, hash_file, hash_text
from ..config import Settings
from ..documents.jsonutil import InvalidModelJSON, parse_json_object
from ..documents.pipeline import DocumentPipeline
from ..documents.reader import UnsupportedFormat, load_text_file
from ..llm.base import LLMError, Unavailable
from ..llm.factory import LLMGateway
from ..logging_setup import get_logger, setup_logging
from ..pdf.extract import PdfExtractError, extract_pdf
from ..results import compression_stats, error, estimate_tokens, partial, success, unavailable
from ..security import SecurityError, SecurityPolicy
from ..telemetry.recorder import Telemetry
from .fs_tools import WorkerFS

TASK_SYSTEM = """You are a local worker. Do mechanical, verifiable work.
Return ONLY compact JSON:
{
  "result": "short synthesis",
  "findings": [{"text": "...", "evidence": {}}],
  "evidence": [{"file": "...", "page": 1, "line": 1, "excerpt": "..."}],
  "uncertainties": ["..."],
  "confidence": 0.0
}
Do not invent evidence. Preserve numbers, rules, dates, exceptions, and limitations.
Keep the answer small enough for a frontier model to review quickly.
"""


class WorkerService:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        gateway: LLMGateway | None = None,
        cache: CacheStore | None = None,
        telemetry: Telemetry | None = None,
        security: SecurityPolicy | None = None,
    ):
        self.settings = settings or Settings.from_env()
        setup_logging(self.settings)
        self.gateway = gateway or LLMGateway(self.settings)
        self.cache = cache or CacheStore(self.settings)
        self.telemetry = telemetry or Telemetry(self.settings)
        self.security = security or SecurityPolicy(self.settings)
        self.fs = WorkerFS(self.settings, self.security)
        self.pipeline = DocumentPipeline(
            self.gateway,
            chunk_tokens=self.settings.chunk_tokens,
            overlap_tokens=self.settings.chunk_overlap_tokens,
        )
        self.cache.maybe_startup_cleanup()
        self.log = get_logger()

    def close(self) -> None:
        self.gateway.close()

    def local_status(self) -> dict[str, Any]:
        started = time.perf_counter()
        health = self.gateway.health()
        duration = _ms(started)
        cache_stats = self.cache.stats()
        return {
            "provider": self.settings.provider,
            "endpoint": self.settings.base_url,
            "endpoint_kind": self.settings.endpoint_kind,
            "reachable": bool(health.reachable),
            "latency_ms": health.latency_ms,
            "model": health.model or self.settings.model,
            "model_available": health.model_available,
            "context_length": health.context_length,
            "capabilities": [
                "delegate_task",
                "delegate_batch",
                "delegate_file",
                "delegate_pdf",
                "hierarchical_synthesis",
                "cache",
                "telemetry",
            ],
            "tools": self.settings.mcp_tools,
            "worker_tools": self.settings.worker_tools,
            "security_mode": self.settings.security_mode,
            "workers": self.settings.max_parallel_workers,
            "circuit_breaker": self.gateway.snapshot(),
            "cache": cache_stats,
            "telemetry": self.telemetry.summary(),
            "error": health.error,
            "execution": {"duration_ms": duration},
        }

    def delegate_task(
        self,
        objective: str,
        context: str = "",
        expected_output: str = "",
        max_output_tokens: int | None = None,
        persistent: bool = False,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        original = estimate_tokens(context) + estimate_tokens(objective)
        key = cache_key(
            content_hash=hash_text(context),
            provider=self.settings.provider,
            model=self.settings.model or "auto",
            objective=objective + "\n" + expected_output,
            pipeline_version=self.settings.pipeline_version,
            extra="task",
        )
        cached = None if force_refresh else self.cache.get(key)
        if cached:
            return self._finish("delegate_task", cached, started, original, cache_hit=True)

        prompt = f"Objective:\n{objective}\n"
        if expected_output:
            prompt += f"\nExpected output:\n{expected_output}\n"
        if context:
            prompt += f"\nContext:\n{context}\n"
        try:
            completion = self.gateway.complete(
                prompt,
                system=TASK_SYSTEM,
                max_tokens=max_output_tokens or self.settings.max_output_tokens,
            )
            parsed = parse_json_object(completion.text)
        except Unavailable as exc:
            return self._unavailable("delegate_task", exc.reason, started, original)
        except (LLMError, InvalidModelJSON) as exc:
            return self._error("delegate_task", str(exc), started, original)

        payload = self._task_payload(parsed, completion, original, started)
        self.cache.put(key, payload, persistent=persistent)
        return self._finish("delegate_task", payload, started, original, cache_hit=False, persist=False)

    def delegate_batch(self, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        started = time.perf_counter()
        if not tasks:
            return error("batch is empty", duration_ms=_ms(started), fallback_recommended=False)
        workers = min(self.settings.max_parallel_workers, len(tasks))
        results: list[dict[str, Any] | None] = [None] * len(tasks)

        def run(index: int, task: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            task_id = str(task.get("id") or f"task_{index + 1}")
            kind = (task.get("tool") or task.get("type") or "task").lower()
            try:
                if kind in {"file", "delegate_file"}:
                    result = self.delegate_file(
                        path=str(task.get("path") or ""),
                        objective=str(task.get("objective") or ""),
                        output_mode=str(task.get("output_mode") or "structured"),
                        force_refresh=bool(task.get("force_refresh") or False),
                        persistent=bool(task.get("persistent") or False),
                    )
                elif kind in {"pdf", "delegate_pdf"}:
                    result = self.delegate_pdf(
                        path=str(task.get("path") or ""),
                        objective=str(task.get("objective") or ""),
                        output_mode=str(task.get("output_mode") or "analysis"),
                        force_refresh=bool(task.get("force_refresh") or False),
                        persistent=bool(task.get("persistent") or False),
                    )
                else:
                    result = self.delegate_task(
                        objective=str(task.get("objective") or ""),
                        context=str(task.get("context") or ""),
                        expected_output=str(task.get("expected_output") or ""),
                        max_output_tokens=task.get("max_output_tokens"),
                        persistent=bool(task.get("persistent") or False),
                        force_refresh=bool(task.get("force_refresh") or False),
                    )
            except Exception as exc:
                result = error(str(exc))
            result = dict(result)
            result["id"] = task_id
            return index, result

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(run, index, task) for index, task in enumerate(tasks)]
            for future in as_completed(futures):
                index, result = future.result()
                results[index] = result

        items = [item or error("missing result") for item in results]
        statuses = [item.get("status") for item in items]
        duration = _ms(started)
        summary = {
            "total": len(items),
            "success": statuses.count("success"),
            "partial": statuses.count("partial"),
            "error": statuses.count("error"),
            "unavailable": statuses.count("unavailable"),
        }
        if summary["success"] == len(items):
            status = "success"
        elif summary["unavailable"] == len(items):
            return unavailable(
                "Local LLM unavailable for the entire batch",
                duration_ms=duration,
                results=items,
                summary=summary,
            )
        else:
            status = "partial"
        payload = {
            "status": status,
            "fallback_recommended": status != "success",
            "results": items,
            "summary": summary,
            "execution": {"duration_ms": duration},
        }
        self.telemetry.record(
            {
                "tool": "delegate_batch",
                "status": status,
                "duration_ms": duration,
                "provider": self.settings.provider,
                "endpoint_kind": self.settings.endpoint_kind,
                "error": None if status == "success" else "batch incomplete",
            }
        )
        return payload

    def delegate_file(
        self,
        path: str,
        objective: str,
        output_mode: str = "structured",
        force_refresh: bool = False,
        persistent: bool = False,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            resolved = self.security.resolve(path)
        except SecurityError as exc:
            return self._error("delegate_file", exc.reason, started, 0, fallback_recommended=False)
        if not resolved.exists():
            return self._error(
                "delegate_file", f"file not found: {resolved}", started, 0, fallback_recommended=False
            )
        if resolved.suffix.lower() == ".pdf":
            return self.delegate_pdf(
                str(resolved),
                objective,
                output_mode=output_mode or "analysis",
                force_refresh=force_refresh,
                persistent=persistent,
            )
        try:
            raw, pages, kind = load_text_file(resolved)
        except UnsupportedFormat as exc:
            return self._error("delegate_file", str(exc), started, 0, fallback_recommended=False)
        except OSError as exc:
            return self._error("delegate_file", str(exc), started, 0, fallback_recommended=False)

        original = estimate_tokens(raw)
        key = cache_key(
            content_hash=hash_file(resolved),
            provider=self.settings.provider,
            model=self.settings.model or "auto",
            objective=objective,
            pipeline_version=self.settings.pipeline_version,
            extra=f"file|{output_mode}|{kind}",
        )
        cached = None if force_refresh else self.cache.get(key)
        if cached:
            return self._finish("delegate_file", cached, started, original, cache_hit=True)

        return self._analyze_pages(
            tool="delegate_file",
            pages=pages,
            objective=objective,
            original=original,
            started=started,
            key=key,
            persistent=persistent,
            extra={"file": str(resolved), "kind": kind},
        )

    def delegate_pdf(
        self,
        path: str,
        objective: str,
        output_mode: str = "analysis",
        force_refresh: bool = False,
        persistent: bool = False,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            resolved = self.security.resolve(path)
        except SecurityError as exc:
            return self._error("delegate_pdf", exc.reason, started, 0, fallback_recommended=False)
        if not resolved.exists():
            return self._error(
                "delegate_pdf", f"file not found: {resolved}", started, 0, fallback_recommended=False
            )
        try:
            pages, meta = extract_pdf(resolved)
            incomplete = False
            extract_reason = None
        except PdfExtractError as exc:
            if not exc.pages:
                return self._error("delegate_pdf", exc.reason, started, 0, fallback_recommended=False)
            pages = exc.pages
            meta = {"failed": True}
            incomplete = exc.incomplete
            extract_reason = exc.reason

        original = estimate_tokens("\n".join(page.text for page in pages))
        key = cache_key(
            content_hash=hash_file(resolved),
            provider=self.settings.provider,
            model=self.settings.model or "auto",
            objective=objective,
            pipeline_version=self.settings.pipeline_version,
            extra=f"pdf|{output_mode}",
        )
        cached = None if force_refresh else self.cache.get(key)
        if cached:
            return self._finish("delegate_pdf", cached, started, original, cache_hit=True)

        extra = {"file": str(resolved), "pdf": meta, "output_mode": output_mode}
        result = self._analyze_pages(
            tool="delegate_pdf",
            pages=pages,
            objective=objective,
            original=original,
            started=started,
            key=key,
            persistent=persistent,
            extra=extra,
        )
        if incomplete and result.get("status") == "success":
            result["status"] = "partial"
            result["fallback_recommended"] = True
            result["reason"] = extract_reason
        return result

    def cache_stats(self) -> dict[str, Any]:
        self.cache.maybe_periodic_cleanup()
        return self.cache.stats()

    def cache_cleanup(self) -> dict[str, Any]:
        return self.cache.cleanup(force=True)

    def cache_clear(self, include_persistent: bool = False) -> dict[str, Any]:
        return self.cache.clear(include_persistent=include_persistent)

    def _analyze_pages(
        self,
        *,
        tool: str,
        pages,
        objective: str,
        original: int,
        started: float,
        key: str,
        persistent: bool,
        extra: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            merged = self.pipeline.run(
                pages,
                objective,
                max_output_tokens=self.settings.max_output_tokens,
            )
        except Unavailable as exc:
            return self._unavailable(tool, exc.reason, started, original)
        except (LLMError, InvalidModelJSON) as exc:
            return self._error(tool, str(exc), started, original)

        usage = merged.pop("_usage", {"local_input_tokens": 0, "local_output_tokens": 0})
        incomplete = bool(merged.pop("_incomplete", False))
        merged.pop("_chunks", None)
        merged.pop("_extracts", None)
        compact = {
            "summary": merged.get("summary"),
            "key_findings": merged.get("key_findings") or [],
            "numbers": merged.get("numbers") or [],
            "evidence": merged.get("evidence") or [],
            "uncertainties": merged.get("uncertainties") or [],
            "confidence": merged.get("confidence"),
        }
        duration = _ms(started)
        payload = success(
            result=merged.get("summary") or "",
            findings=merged.get("findings") or [],
            evidence=merged.get("evidence") or [],
            uncertainties=merged.get("uncertainties") or [],
            confidence=merged.get("confidence"),
            usage=usage,
            compression=compression_stats(original, compact),
            cache={"hit": False},
            duration_ms=duration,
            summary=merged.get("summary") or "",
            key_findings=merged.get("key_findings") or [],
            numbers=merged.get("numbers") or [],
            rules=merged.get("rules") or [],
            parameters=merged.get("parameters") or [],
            dates=merged.get("dates") or [],
            methodology=merged.get("methodology") or [],
            limitations=merged.get("limitations") or [],
            **extra,
        )
        if incomplete:
            payload = partial(
                reason="analysis incomplete; some chunks failed",
                result=payload["result"],
                findings=payload["findings"],
                evidence=payload["evidence"],
                uncertainties=payload["uncertainties"],
                confidence=payload["confidence"],
                usage=usage,
                compression=payload["compression"],
                cache={"hit": False},
                duration_ms=duration,
                summary=payload.get("summary"),
                key_findings=payload.get("key_findings"),
                numbers=payload.get("numbers"),
                **extra,
            )
        self.cache.put(key, payload, persistent=persistent)
        return self._finish(tool, payload, started, original, cache_hit=False, persist=False)

    def _task_payload(
        self, parsed: dict[str, Any], completion, original: int, started: float
    ) -> dict[str, Any]:
        result = parsed.get("result") or parsed.get("summary") or ""
        findings = parsed.get("findings") or []
        evidence = parsed.get("evidence") or []
        uncertainties = parsed.get("uncertainties") or []
        compact = {
            "result": result,
            "findings": findings,
            "evidence": evidence,
            "uncertainties": uncertainties,
            "confidence": parsed.get("confidence"),
        }
        return success(
            result=result,
            findings=findings,
            evidence=evidence,
            uncertainties=uncertainties,
            confidence=parsed.get("confidence"),
            usage={
                "local_input_tokens": completion.input_tokens,
                "local_output_tokens": completion.output_tokens,
            },
            compression=compression_stats(original, compact),
            cache={"hit": False},
            duration_ms=_ms(started),
        )

    def _finish(
        self,
        tool: str,
        payload: dict[str, Any],
        started: float,
        original: int,
        *,
        cache_hit: bool,
        persist: bool = True,
    ) -> dict[str, Any]:
        result = dict(payload)
        result.setdefault("cache", {})["hit"] = cache_hit
        result["execution"] = {"duration_ms": _ms(started)}
        compression = result.get("compression") or {}
        usage = result.get("usage") or {}
        self.telemetry.record(
            {
                "tool": tool,
                "status": result.get("status"),
                "cache_hit": cache_hit,
                "original_tokens": compression.get("estimated_original_tokens") or original,
                "frontier_tokens": compression.get("frontier_tokens") or 0,
                "reduction_percent": compression.get("reduction_percent") or 0,
                "local_input_tokens": usage.get("local_input_tokens") or 0,
                "local_output_tokens": usage.get("local_output_tokens") or 0,
                "duration_ms": result["execution"]["duration_ms"],
                "model": self.settings.model,
                "provider": self.settings.provider,
                "endpoint_kind": self.settings.endpoint_kind,
                "error": result.get("reason"),
            }
        )
        self.cache.maybe_periodic_cleanup()
        return result

    def _unavailable(self, tool: str, reason: str, started: float, original: int) -> dict[str, Any]:
        payload = unavailable(reason, duration_ms=_ms(started))
        self.telemetry.record(
            {
                "tool": tool,
                "status": "unavailable",
                "error": reason,
                "original_tokens": original,
                "duration_ms": payload["execution"]["duration_ms"],
                "provider": self.settings.provider,
                "endpoint_kind": self.settings.endpoint_kind,
                "model": self.settings.model,
            }
        )
        return payload

    def _error(
        self,
        tool: str,
        reason: str,
        started: float,
        original: int,
        fallback_recommended: bool = True,
    ) -> dict[str, Any]:
        payload = error(reason, duration_ms=_ms(started), fallback_recommended=fallback_recommended)
        self.telemetry.record(
            {
                "tool": tool,
                "status": "error",
                "error": reason,
                "original_tokens": original,
                "duration_ms": payload["execution"]["duration_ms"],
                "provider": self.settings.provider,
                "endpoint_kind": self.settings.endpoint_kind,
                "model": self.settings.model,
            }
        )
        return payload


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
