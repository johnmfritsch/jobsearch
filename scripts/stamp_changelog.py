#!/usr/bin/env python3
"""Promote CHANGELOG.md's Unreleased entries to a dated semantic version."""

import re
import sys
from datetime import date
from pathlib import Path


def _unreleased_bounds(lines):
    start = next((i for i, line in enumerate(lines) if line == "## [Unreleased]\n"), None)
    if start is None:
        raise ValueError("CHANGELOG.md needs a '## [Unreleased]' section.")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## [")), len(lines))
    return start, end


def _bullets(lines):
    return [line for line in lines if re.match(r"^- \S", line)]


def _next_version(text, change_count):
    found = re.findall(r"^## \[v(\d+)\.(\d+)\.(\d+)", text, re.MULTILINE)
    if not found:
        return "v0.1.0"
    major, minor, patch = max((int(a), int(b), int(c)) for a, b, c in found)
    return f"v{major}.{minor + 1}.0" if change_count >= 10 else f"v{major}.{minor}.{patch + 1}"


def _read(path):
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


def preview(path):
    lines = _read(path)
    start, end = _unreleased_bounds(lines)
    changes = _bullets(lines[start + 1:end])
    if not changes:
        print("  [Unreleased] has no bullet items — nothing will be stamped.")
        return False
    version = _next_version("".join(lines), len(changes))
    print(f"  Changelog will be stamped as [{version} — {date.today():%B %d, %Y}] ({len(changes)} change(s))")
    for change in changes:
        print(f"    {change.rstrip()}")
    return True


def stamp(path):
    lines = _read(path)
    start, end = _unreleased_bounds(lines)
    body = lines[start + 1:end]
    changes = _bullets(body)
    if not changes:
        print("  [Unreleased] has no bullet items — nothing stamped.")
        return False
    version = _next_version("".join(lines), len(changes))
    today = date.today().strftime("%B %d, %Y").replace(" 0", " ")
    fresh = ["## [Unreleased]\n", "### Added\n", "### Fixed\n", "### Changed\n", "\n", "---\n", "\n"]
    path.write_text("".join(lines[:start] + fresh + [f"## [{version} — {today}]\n"] + body + lines[end:]), encoding="utf-8")
    print(f"  Stamped [{version} — {today}]")
    return True


def main():
    if len(sys.argv) not in (2, 3):
        raise SystemExit(f"Usage: {sys.argv[0]} [--preview] CHANGELOG.md")
    preview_only = sys.argv[1] == "--preview"
    target = Path(sys.argv[2] if preview_only else sys.argv[1])
    try:
        changed = preview(target) if preview_only else stamp(target)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(0 if changed else 2)


if __name__ == "__main__":
    main()
