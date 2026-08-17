"""Load text-like files without sending raw bytes to the frontier."""

from __future__ import annotations

import json
from pathlib import Path

from .chunking import Page, with_line_numbers

TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".log",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".xml",
    ".html",
    ".htm",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".go",
    ".rs",
    ".c",
    ".h",
    ".cpp",
    ".cc",
    ".hpp",
    ".cs",
    ".rb",
    ".php",
    ".sh",
    ".ps1",
    ".sql",
    ".mq5",
    ".mq4",
    ".mqh",
}

CODE_SUFFIXES = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".go",
    ".rs",
    ".c",
    ".h",
    ".cpp",
    ".cc",
    ".hpp",
    ".cs",
    ".rb",
    ".php",
    ".sh",
    ".ps1",
    ".sql",
    ".mq5",
    ".mq4",
    ".mqh",
}


class UnsupportedFormat(Exception):
    def __init__(self, suffix: str):
        super().__init__(f"unsupported file format: {suffix or '(none)'}")
        self.suffix = suffix


def load_text_file(path: Path, *, max_bytes: int = 8_000_000) -> tuple[str, list[Page], str]:
    suffix = path.suffix.lower()
    if suffix not in TEXT_SUFFIXES:
        raise UnsupportedFormat(suffix)
    size = path.stat().st_size
    if size > max_bytes:
        raise OSError(f"file too large ({size} bytes); max {max_bytes}")
    raw = path.read_text(encoding="utf-8", errors="replace")
    kind = _kind(suffix)
    if kind == "json":
        try:
            parsed = json.loads(raw)
            raw = json.dumps(parsed, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            kind = "text"
    numbered = kind in {"code", "log"}
    text = with_line_numbers(raw) if numbered else raw
    line_count = raw.count("\n") + (1 if raw else 0)
    page = Page(
        number=1,
        text=text,
        source=str(path),
        line_start=1 if line_count else None,
        line_end=line_count or None,
    )
    return text, [page], kind


def _kind(suffix: str) -> str:
    if suffix in CODE_SUFFIXES:
        return "code"
    if suffix == ".log":
        return "log"
    if suffix in {".csv", ".tsv"}:
        return "csv"
    if suffix in {".json", ".jsonl"}:
        return "json"
    return "text"
