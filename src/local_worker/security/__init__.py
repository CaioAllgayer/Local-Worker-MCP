"""Path and shell policy. Default is read-only with no shell."""

from __future__ import annotations

import os
import re
from pathlib import Path

from ..config import (
    SECURITY_FULL_LOCAL,
    SECURITY_READ_ONLY,
    SECURITY_WORKSPACE_WRITE,
    Settings,
)

DESTRUCTIVE_COMMANDS = {
    "rm",
    "rmdir",
    "rd",
    "del",
    "erase",
    "format",
    "mkfs",
    "diskpart",
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
    "cipher",
    "remove-item",
    "rm-item",
    "clear-disk",
    "format-volume",
    "stop-computer",
    "restart-computer",
    "reg",
}


class SecurityError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class SecurityPolicy:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.roots = [Path(p).expanduser().resolve() for p in settings.allowed_paths]

    @property
    def mode(self) -> str:
        return self.settings.security_mode

    def can_write(self) -> bool:
        return self.mode in {SECURITY_WORKSPACE_WRITE, SECURITY_FULL_LOCAL}

    def can_shell(self) -> bool:
        return bool(self.settings.enable_shell) and self.mode != SECURITY_READ_ONLY

    def resolve(self, path: str | os.PathLike[str], *, write: bool = False) -> Path:
        raw = Path(path).expanduser()
        try:
            resolved = raw.resolve()
        except OSError as exc:
            raise SecurityError(f"cannot resolve path: {path}") from exc

        if write and not self.can_write():
            raise SecurityError("write disabled in current security mode")

        if self.mode == SECURITY_FULL_LOCAL and not self.roots:
            return resolved

        if not self.roots:
            raise SecurityError("ALLOWED_PATHS is empty; configure authorized directories")

        for root in self.roots:
            if _is_relative_to(resolved, root):
                return resolved
        raise SecurityError("path is outside ALLOWED_PATHS")

    def assert_command(self, command: str) -> list[str]:
        if not self.can_shell():
            raise SecurityError("shell is disabled")
        parts = _split_command(command)
        if not parts:
            raise SecurityError("empty command")
        binary = Path(parts[0]).name.lower()
        if binary.endswith(".exe"):
            binary = binary[:-4]
        if binary in DESTRUCTIVE_COMMANDS or (
            binary == "reg" and any(p.lower() == "delete" for p in parts[1:3])
        ):
            raise SecurityError(f"destructive command blocked: {binary}")
        allowed = [c.lower() for c in self.settings.allowed_commands]
        if allowed and binary not in allowed and parts[0].lower() not in allowed:
            raise SecurityError(f"command not in ALLOWED_COMMANDS: {binary}")
        return parts


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _split_command(command: str) -> list[str]:
    if os.name == "nt":
        return [part for part in re.findall(r'"([^"]+)"|(\S+)', command) for part in part if part]
    import shlex

    return shlex.split(command)
