"""Enforce the engineering standards that lint and types cannot express.

Most code here is agent-written and will not be reviewed line by line, so quality
rests on a gate bad code cannot pass rather than on review. This script checks the
rules from CONTEXT.md sections 4.7 and 4.8:

* no suppressions -- ``# type: ignore``, ``# noqa``, ``# pragma: no cover``, ``# nosec``
* an authored-line budget, because the right response to hitting it is to install a
  dependency rather than to keep writing
* the never-commit files are actually covered by .gitignore

Run with ``uv run python scripts/guard.py``. Exits non-zero with a specific reason.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve()

LINE_BUDGET = 4_000
"""CONTEXT.md 4.8: past roughly this, something in the dependency list has been
reimplemented. Alembic versions are excluded -- they are generated, not authored."""

SUPPRESSION_TOKENS = ("type: ignore", "noqa", "pragma: no cover", "nosec")
"""This file is excluded from its own scan, so the tokens can be written plainly."""

NEVER_COMMIT = (".env", "ebay_rest.json", "arb.db")

AUTHORED_ANY = re.compile(r"(?::\s*Any\b|->\s*Any\b|\[\s*Any\b|\bAny\s*\])")
"""Hand-written `Any` in authored source. mypy's disallow_any_explicit cannot express
this without also flagging pydantic's synthesised __init__; see pyproject.toml."""


def _scanned_files() -> list[Path]:
    roots = (ROOT / "src", ROOT / "tests", ROOT / "scripts")
    return [
        path
        for root in roots
        if root.is_dir()
        for path in sorted(root.rglob("*.py"))
        if "migrations" not in path.parts and path.resolve() != SELF
    ]


def _authored_files() -> list[Path]:
    src = ROOT / "src"
    return [p for p in sorted(src.rglob("*.py")) if "migrations" not in p.parts]


def check_no_suppressions() -> list[str]:
    """Suppressions are the mechanism by which a gate quietly stops being one."""
    pattern = re.compile(r"#\s*(?:" + "|".join(re.escape(t) for t in SUPPRESSION_TOKENS) + ")")
    failures: list[str] = []
    for path in _scanned_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                rel = path.relative_to(ROOT)
                failures.append(f"{rel}:{lineno}: suppression not permitted -- {line.strip()}")
    return failures


def check_no_authored_any() -> list[str]:
    """`Any` defeats the type checker at exactly the boundaries where it matters --
    parsing marketplace payloads. Narrow with `object` and isinstance instead."""
    failures: list[str] = []
    for path in _authored_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#") or '"""' in stripped:
                continue
            if AUTHORED_ANY.search(line):
                rel = path.relative_to(ROOT)
                failures.append(f"{rel}:{lineno}: authored Any -- narrow the type instead")
    return failures


def check_line_budget() -> list[str]:
    total = 0
    for path in _authored_files():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                total += 1
    if total > LINE_BUDGET:
        return [
            (
                f"authored source is {total} lines, over the {LINE_BUDGET} budget: "
                "something in the dependency list has probably been reimplemented"
            )
        ]
    sys.stdout.write(f"guard: authored source {total} / {LINE_BUDGET} lines\n")
    return []


def check_gitignore_covers_secrets() -> list[str]:
    """Static check, deliberately. The repo is public, so this is one of three
    layers alongside pre-commit's detect-private-key and GitHub secret scanning."""
    gitignore = ROOT / ".gitignore"
    if not gitignore.is_file():
        return [".gitignore is missing"]
    entries = {
        line.strip()
        for line in gitignore.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    return [f".gitignore does not cover {name}" for name in NEVER_COMMIT if name not in entries]


def main() -> int:
    failures = [
        *check_no_suppressions(),
        *check_no_authored_any(),
        *check_line_budget(),
        *check_gitignore_covers_secrets(),
    ]
    for failure in failures:
        sys.stderr.write(f"GUARD: {failure}\n")
    if failures:
        sys.stderr.write("\nDo not weaken the gate to make something pass. Escalate instead.\n")
        return 1
    sys.stdout.write("guard: ok\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
