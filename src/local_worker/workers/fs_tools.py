"""Internal worker filesystem tools. Not meant to dump raw files into frontier context."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ..config import Settings
from ..security import SecurityError, SecurityPolicy


class WorkerFS:
    def __init__(self, settings: Settings, security: SecurityPolicy):
        self.settings = settings
        self.security = security

    def read_file(self, path: str, *, max_bytes: int = 200_000) -> dict[str, Any]:
        resolved = self.security.resolve(path)
        if not resolved.is_file():
            raise FileNotFoundError(str(resolved))
        size = resolved.stat().st_size
        data = resolved.read_bytes()[:max_bytes]
        text = data.decode("utf-8", errors="replace")
        truncated = size > max_bytes
        return {
            "path": str(resolved),
            "size_bytes": size,
            "truncated": truncated,
            "text": text,
        }

    def list_directory(self, path: str) -> dict[str, Any]:
        resolved = self.security.resolve(path)
        if not resolved.is_dir():
            raise NotADirectoryError(str(resolved))
        entries = []
        for child in sorted(resolved.iterdir(), key=lambda p: p.name.lower()):
            entries.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "type": "dir" if child.is_dir() else "file",
                    "size_bytes": child.stat().st_size if child.is_file() else None,
                }
            )
        return {"path": str(resolved), "entries": entries, "count": len(entries)}

    def search_files(self, root: str, query: str, *, glob: str = "*", limit: int = 50) -> dict[str, Any]:
        resolved = self.security.resolve(root)
        if not resolved.exists():
            raise FileNotFoundError(str(resolved))
        haystack_root = resolved if resolved.is_dir() else resolved.parent
        matches: list[dict[str, Any]] = []
        needle = query.lower()
        for path in haystack_root.rglob(glob):
            if not path.is_file():
                continue
            try:
                self.security.resolve(path)
            except SecurityError:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if needle in line.lower():
                    matches.append(
                        {
                            "file": str(path),
                            "line": lineno,
                            "excerpt": line.strip()[:240],
                        }
                    )
                    if len(matches) >= limit:
                        return {"query": query, "matches": matches, "truncated": True}
        return {"query": query, "matches": matches, "truncated": False}

    def run_command(self, command: str, *, cwd: str | None = None) -> dict[str, Any]:
        parts = self.security.assert_command(command)
        workdir = self.security.resolve(cwd) if cwd else Path.cwd()
        if not workdir.is_dir():
            raise NotADirectoryError(str(workdir))
        try:
            completed = subprocess.run(
                parts,
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=self.settings.command_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"command timed out after {self.settings.command_timeout_seconds}s") from exc
        limit = self.settings.command_output_limit
        stdout = (completed.stdout or "")[:limit]
        stderr = (completed.stderr or "")[:limit]
        return {
            "command": parts,
            "cwd": str(workdir),
            "returncode": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": len(completed.stdout or "") > limit or len(completed.stderr or "") > limit,
        }
