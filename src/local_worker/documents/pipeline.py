"""Hierarchical extract → merge → synthesize. Preserve numbers, rules, evidence."""

from __future__ import annotations

from typing import Any

from ..llm.base import Completion, LLMError, Unavailable
from ..llm.factory import LLMGateway
from .chunking import Chunk, Page, chunk_pages
from .jsonutil import InvalidModelJSON, parse_json_object

EXTRACT_SYSTEM = """You extract compact structured facts from a document chunk.
Return ONLY JSON with this shape:
{
  "summary": "2-6 sentences, no fluff",
  "findings": [{"text": "...", "page": 1, "excerpt": "short quote"}],
  "numbers": [{"label": "...", "value": "...", "page": 1, "excerpt": "..."}],
  "rules": [{"text": "...", "page": 1}],
  "parameters": [{"name": "...", "value": "...", "page": 1}],
  "dates": [{"text": "...", "page": 1}],
  "methodology": ["..."],
  "limitations": ["..."],
  "uncertainties": ["..."],
  "confidence": 0.0
}
Rules:
- Never invent page numbers or quotes.
- Prefer evidence that supports the objective.
- Keep excerpts short.
- Preserve numbers, conditions, exceptions, dates, stats, and caveats.
- If the chunk is irrelevant, return empty arrays and a one-line summary.
"""

MERGE_SYSTEM = """You merge structured extractions from the same document.
Return ONLY JSON with the same schema as the inputs.
Deduplicate repeated findings. Keep every distinct number, rule, parameter, date, limitation, and evidence quote.
Never invent pages. Drop items that conflict without evidence and list them under uncertainties.
The objective decides what to keep. Stay compact.
"""


class DocumentPipeline:
    def __init__(self, llm: LLMGateway, *, chunk_tokens: int = 3500, overlap_tokens: int = 200):
        self.llm = llm
        self.chunk_tokens = chunk_tokens
        self.overlap_tokens = overlap_tokens

    def run(
        self,
        pages: list[Page],
        objective: str,
        *,
        max_output_tokens: int = 4000,
        require_json: bool = True,
    ) -> dict[str, Any]:
        valid_pages = {page.number for page in pages}
        page_text = {page.number: page.text for page in pages}
        chunks = chunk_pages(pages, max_tokens=self.chunk_tokens, overlap_tokens=self.overlap_tokens)
        extracts: list[dict[str, Any]] = []
        usage = {"local_input_tokens": 0, "local_output_tokens": 0}
        errors: list[str] = []
        unavailable = False

        for chunk in chunks:
            try:
                parsed, completion = self._extract_chunk(chunk, objective, max_output_tokens)
            except Unavailable:
                unavailable = True
                errors.append(f"chunk {chunk.index}: local LLM unavailable")
                break
            except (LLMError, InvalidModelJSON) as exc:
                errors.append(f"chunk {chunk.index}: {exc}")
                continue
            _accumulate(usage, completion)
            extracts.append(self._sanitize(parsed, valid_pages, page_text, chunk))

        if not extracts:
            if unavailable:
                raise Unavailable(errors[0] if errors else "Local LLM endpoint unreachable")
            raise LLMError(errors[0] if errors else "document analysis failed")

        merged, merge_usage = self._reduce(extracts, objective, max_output_tokens, valid_pages, page_text)
        _accumulate(usage, merge_usage)
        merged = self._sanitize(merged, valid_pages, page_text)
        if errors:
            merged.setdefault("uncertainties", [])
            merged["uncertainties"] = list(merged.get("uncertainties") or []) + errors
            merged["_incomplete"] = True
        merged["_usage"] = usage
        merged["_chunks"] = len(chunks)
        merged["_extracts"] = len(extracts)
        if require_json and not isinstance(merged, dict):
            raise InvalidModelJSON("synthesis produced invalid JSON")
        return merged

    def _extract_chunk(
        self, chunk: Chunk, objective: str, max_output_tokens: int
    ) -> tuple[dict[str, Any], Completion]:
        prompt = (
            f"Objective:\n{objective}\n\n"
            f"Chunk {chunk.index} pages {chunk.pages} lines {chunk.line_start}-{chunk.line_end}\n\n"
            f"{chunk.text}"
        )
        completion = self.llm.complete(prompt, system=EXTRACT_SYSTEM, max_tokens=max_output_tokens)
        return parse_json_object(completion.text), completion

    def _reduce(
        self,
        extracts: list[dict[str, Any]],
        objective: str,
        max_output_tokens: int,
        valid_pages: set[int],
        page_text: dict[int, str],
    ) -> tuple[dict[str, Any], dict[str, int]]:
        usage = {"local_input_tokens": 0, "local_output_tokens": 0}
        current = extracts
        if len(current) == 1:
            return current[0], usage
        while len(current) > 1:
            nxt: list[dict[str, Any]] = []
            for group in _batched(current, 5):
                if len(group) == 1:
                    nxt.append(group[0])
                    continue
                parsed, completion = self._merge_group(group, objective, max_output_tokens)
                _accumulate(usage, completion)
                nxt.append(self._sanitize(parsed, valid_pages, page_text))
            current = nxt
        return current[0], usage

    def _merge_group(
        self, group: list[dict[str, Any]], objective: str, max_output_tokens: int
    ) -> tuple[dict[str, Any], Completion]:
        import json

        compact = [_strip_private(item) for item in group]
        prompt = f"Objective:\n{objective}\n\nExtractions:\n{json.dumps(compact, ensure_ascii=False)}"
        completion = self.llm.complete(prompt, system=MERGE_SYSTEM, max_tokens=max_output_tokens)
        return parse_json_object(completion.text), completion

    def _sanitize(
        self,
        payload: dict[str, Any],
        valid_pages: set[int],
        page_text: dict[int, str],
        chunk: Chunk | None = None,
    ) -> dict[str, Any]:
        uncertainties = [str(item) for item in (payload.get("uncertainties") or []) if item]
        findings = _normalize_items(payload.get("findings") or payload.get("key_findings") or [])
        evidence = _normalize_items(payload.get("evidence") or [])
        numbers = _normalize_items(payload.get("numbers") or [])
        for collection_name, collection in (
            ("findings", findings),
            ("evidence", evidence),
            ("numbers", numbers),
        ):
            kept = []
            for item in collection:
                cleaned, note = _check_evidence(item, valid_pages, page_text, chunk)
                if note:
                    uncertainties.append(note)
                if cleaned is not None:
                    kept.append(cleaned)
            if collection_name == "findings":
                findings = kept
            elif collection_name == "evidence":
                evidence = kept
            else:
                numbers = kept

        if not evidence:
            evidence = [
                {
                    "page": item.get("page"),
                    "text": item.get("excerpt") or item.get("text"),
                    "file": item.get("file"),
                    "line": item.get("line") or item.get("line_start"),
                    "line_start": item.get("line_start"),
                    "line_end": item.get("line_end"),
                }
                for item in findings
                if item.get("excerpt") or item.get("page") is not None
            ]

        confidence = payload.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None
        if confidence is not None:
            confidence = max(0.0, min(1.0, confidence))

        summary = str(payload.get("summary") or payload.get("result") or "").strip()
        return {
            "summary": summary,
            "findings": findings,
            "key_findings": [item.get("text") for item in findings if item.get("text")],
            "numbers": numbers,
            "rules": _normalize_items(payload.get("rules") or []),
            "parameters": _normalize_items(payload.get("parameters") or []),
            "dates": _normalize_items(payload.get("dates") or []),
            "methodology": payload.get("methodology") or [],
            "limitations": payload.get("limitations") or [],
            "evidence": evidence,
            "uncertainties": uncertainties,
            "confidence": confidence,
        }


def _normalize_items(items: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, str):
            normalized.append({"text": item})
        elif isinstance(item, dict):
            text = item.get("text") or item.get("finding") or item.get("value") or item.get("label")
            entry = dict(item)
            if text and "text" not in entry:
                entry["text"] = text
            normalized.append(entry)
    return normalized


def _check_evidence(
    item: dict[str, Any],
    valid_pages: set[int],
    page_text: dict[int, str],
    chunk: Chunk | None,
) -> tuple[dict[str, Any] | None, str | None]:
    page = item.get("page")
    if page is not None:
        try:
            page = int(page)
            item["page"] = page
        except (TypeError, ValueError):
            return item, "dropped non-numeric page citation"
    if page is not None and valid_pages and page not in valid_pages:
        return None, f"dropped invented page {page}"
    excerpt = str(item.get("excerpt") or item.get("text") or "").strip()
    if page is not None and excerpt and page in page_text:
        haystack = page_text[page]
        if excerpt[:80].lower() not in haystack.lower() and excerpt.lower() not in haystack.lower():
            return None, f"dropped unverifiable excerpt on page {page}"
    if chunk is not None and item.get("page") is None and chunk.pages:
        item.setdefault("pages", chunk.pages)
    return item, None


def _strip_private(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if not str(key).startswith("_")}


def _batched(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _accumulate(usage: dict[str, int], source: Completion | dict[str, int]) -> None:
    if isinstance(source, Completion):
        usage["local_input_tokens"] += source.input_tokens
        usage["local_output_tokens"] += source.output_tokens
    else:
        usage["local_input_tokens"] += int(source.get("local_input_tokens") or 0)
        usage["local_output_tokens"] += int(source.get("local_output_tokens") or 0)
