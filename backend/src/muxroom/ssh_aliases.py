"""Discover concrete Host aliases from an OpenSSH client configuration."""

import glob
import os
import shlex
from pathlib import Path

MAX_INCLUDE_DEPTH = 8
WILDCARD_CHARS = set("*?!")


def default_ssh_config() -> Path:
    return Path("~/.ssh/config").expanduser()


def _resolve_include(pattern: str, base: Path) -> list[Path]:
    expanded = os.path.expanduser(pattern)
    if not os.path.isabs(expanded):
        expanded = str(base / expanded)  # relative includes are relative to ~/.ssh
    return [Path(p) for p in sorted(glob.glob(expanded))]


def _tokens(line: str) -> tuple[str, list[str]] | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    keyword, _, rest = line.replace("=", " ", 1).partition(" ")
    try:
        args = shlex.split(rest, comments=True)
    except ValueError:
        args = rest.split()
    return keyword.lower(), args


def list_ssh_aliases(path: Path | None = None) -> list[str]:
    """Host patterns without wildcards or negation, in file order, de-duplicated."""
    path = path or default_ssh_config()
    base = Path("~/.ssh").expanduser()
    aliases: list[str] = []
    seen_files: set[Path] = set()

    def walk(file: Path, depth: int) -> None:
        if depth > MAX_INCLUDE_DEPTH or file in seen_files or not file.is_file():
            return
        seen_files.add(file)
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for raw in text.splitlines():
            parsed = _tokens(raw)
            if parsed is None:
                continue
            keyword, args = parsed
            if keyword == "include":
                for pattern in args:
                    for included in _resolve_include(pattern, base):
                        walk(included, depth + 1)
            elif keyword == "host":
                for pattern in args:
                    if not WILDCARD_CHARS & set(pattern) and pattern not in aliases:
                        aliases.append(pattern)

    walk(path, 0)
    return aliases
