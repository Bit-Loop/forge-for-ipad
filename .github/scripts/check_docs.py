#!/usr/bin/env python3
"""Check local Markdown links without network access."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
REMOTE_SCHEMES = ("http://", "https://", "mailto:", "app://")


def destination(raw: str) -> str:
    value = raw.strip()
    if value.startswith("<") and ">" in value:
        return value[1 : value.index(">")]
    return value.split(maxsplit=1)[0]


def main(paths: list[str]) -> int:
    failures: list[str] = []
    for name in paths:
        source = Path(name)
        text = source.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), 1):
            for match in LINK.finditer(line):
                target = destination(match.group(1))
                if not target or target.startswith("#") or target.startswith(REMOTE_SCHEMES):
                    continue
                relative = unquote(target.split("#", 1)[0])
                candidate = (source.parent / relative).resolve()
                if not candidate.exists():
                    failures.append(f"{source}:{line_number}: missing link target {relative!r}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"validated local links in {len(paths)} Markdown files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
