"""Documentation discovery: the Markdown this repo already ships, made
reachable at runtime.

Nothing here writes documentation. The index is **derived from the files
on disk**, never declared -- adding a document is saving the file, so no
second list exists to drift from the first. See the node's
`docs/UNIT_STANDARDS.md`, "Documentation: two endpoints, two tools".

A docs module is duplicated per unit on purpose: units are separate
repos with no shared runtime, and this one has no unit-specific
behaviour to share.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

DOCS_SUBDIR = "docs"

# A document's name is its filename lowercased without the extension:
# UNIT_CONTRACT.md is `unit_contract`, docs/OPERATIONS.md is `operations`.
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

MAX_SUMMARY_CHARS = 240

# The repo root, two levels up from this file: <root>/<package>/docs.py.
REPO_ROOT = Path(__file__).resolve().parent.parent


def _name_for(path: Path) -> str:
    return path.stem.lower()


def _title_and_summary(text: str) -> tuple[str | None, str | None]:
    """Best effort, from the document's own shape: the first `# Heading`
    is the title, the first ordinary paragraph after it is the summary.
    No front-matter required -- these files were written before this
    endpoint existed."""
    title: str | None = None
    summary_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if title is None:
            if stripped.startswith("#"):
                title = stripped.lstrip("#").strip() or None
            continue
        if not stripped:
            if summary_lines:
                break
            continue
        # Structure rather than prose makes a poor one-line summary.
        if stripped.startswith(("#", "|", "```", "---", ">")):
            if summary_lines:
                break
            continue
        summary_lines.append(stripped)

    summary = " ".join(summary_lines) or None
    if summary and len(summary) > MAX_SUMMARY_CHARS:
        summary = summary[: MAX_SUMMARY_CHARS - 1].rstrip() + "…"
    return title, summary


def _sources(root: Path) -> dict[str, Path]:
    """Every document this repo ships, by name. `docs/` wins a name
    collision with a top-level file -- stated rather than left to
    iteration order."""
    found: dict[str, Path] = {}
    for path in sorted(root.glob("*.md")):
        if path.is_file():
            found[_name_for(path)] = path
    docs_dir = root / DOCS_SUBDIR
    if docs_dir.is_dir():
        for path in sorted(docs_dir.glob("*.md")):
            if path.is_file():
                found[_name_for(path)] = path
    return {name: path for name, path in found.items() if NAME_PATTERN.match(name)}


def build_index(root: Path | None = None) -> list[dict[str, Any]]:
    """The list an agent reads before deciding what to fetch. Summaries
    are carried so the bodies need not be. Sorted by name, because an
    index that reshuffles is one nothing can diff."""
    index: list[dict[str, Any]] = []
    for name, path in sorted(_sources(root or REPO_ROOT).items()):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        title, summary = _title_and_summary(text)
        index.append(
            {
                "name": name,
                "title": title or name,
                "summary": summary,
                "bytes": len(text.encode("utf-8")),
            }
        )
    return index


def read_doc(name: str, root: Path | None = None) -> str | None:
    """One document, or None when there is no such name.

    The name is looked up in the derived index rather than joined onto a
    path. That is the whole traversal defence: `../../units.yaml` is not
    a key in that mapping, so there is no path to sanitise and no
    sanitiser to get wrong.
    """
    if not isinstance(name, str) or not NAME_PATTERN.match(name.lower()):
        return None
    path = _sources(root or REPO_ROOT).get(name.lower())
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None
