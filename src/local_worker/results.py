"""Standardized MCP responses. Never report success for an incomplete analysis."""

from __future__ import annotations

from typing import Any


def unavailable(reason: str, duration_ms: int = 0, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "unavailable",
        "fallback_recommended": True,
        "reason": reason,
        "execution": {"duration_ms": duration_ms},
    }
    payload.update(extra)
    return payload


def error(
    reason: str, duration_ms: int = 0, fallback_recommended: bool = True, **extra: Any
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "error",
        "fallback_recommended": fallback_recommended,
        "reason": reason,
        "execution": {"duration_ms": duration_ms},
    }
    payload.update(extra)
    return payload


def success(
    *,
    result: Any = "",
    findings: list[Any] | None = None,
    evidence: list[Any] | None = None,
    uncertainties: list[Any] | None = None,
    confidence: float | None = None,
    usage: dict[str, Any] | None = None,
    compression: dict[str, Any] | None = None,
    cache: dict[str, Any] | None = None,
    execution: dict[str, Any] | None = None,
    duration_ms: int = 0,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "success",
        "result": result,
        "findings": findings or [],
        "evidence": evidence or [],
        "uncertainties": uncertainties or [],
        "confidence": confidence,
        "usage": usage or {"local_input_tokens": 0, "local_output_tokens": 0},
        "compression": compression
        or {
            "estimated_original_tokens": 0,
            "frontier_tokens": 0,
            "reduction_percent": 0.0,
        },
        "cache": cache or {"hit": False},
        "execution": execution or {"duration_ms": duration_ms},
    }
    payload.update(extra)
    return payload


def partial(
    *,
    reason: str,
    result: Any = "",
    findings: list[Any] | None = None,
    evidence: list[Any] | None = None,
    uncertainties: list[Any] | None = None,
    confidence: float | None = None,
    usage: dict[str, Any] | None = None,
    compression: dict[str, Any] | None = None,
    cache: dict[str, Any] | None = None,
    duration_ms: int = 0,
    fallback_recommended: bool = True,
    **extra: Any,
) -> dict[str, Any]:
    payload = success(
        result=result,
        findings=findings,
        evidence=evidence,
        uncertainties=uncertainties,
        confidence=confidence,
        usage=usage,
        compression=compression,
        cache=cache,
        duration_ms=duration_ms,
        **extra,
    )
    payload["status"] = "partial"
    payload["fallback_recommended"] = fallback_recommended
    payload["reason"] = reason
    return payload


def estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def compression_stats(original_tokens: int, frontier_payload: Any) -> dict[str, Any]:
    import json

    if isinstance(frontier_payload, str):
        rendered = frontier_payload
    else:
        rendered = json.dumps(frontier_payload, ensure_ascii=False, default=str)
    frontier_tokens = estimate_tokens(rendered)
    if original_tokens <= 0:
        reduction = 0.0
    else:
        reduction = max(0.0, round((1 - (frontier_tokens / original_tokens)) * 100, 1))
    return {
        "estimated_original_tokens": original_tokens,
        "frontier_tokens": frontier_tokens,
        "reduction_percent": reduction,
    }
