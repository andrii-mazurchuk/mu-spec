"""Entry bodies, read as facts.

Pure and deterministic: a body and its layer in, a frozen value out. No I/O,
the graph untouched, and nothing refused -- a malformed line is recorded in
`problems` for a report to show, never raised. The gates that consume these
facts decide what, if anything, they mean.

The graph needs only an id, the edge lists and a body (CLAUDE.md); these are
the conventions that constrain what goes *inside* a body (D4):

- S: a first line `Imports: S-41 Name, Name; pkg.mod Name; ...` and an
  optional line `Errors: ExcA, ExcB`.
- T: an optional line `Exercises: S-250, S-266`.

No heading is structural. Nothing here splits on `##`, so a heading inside a
body cannot truncate what follows it (e2764c1).
"""

from __future__ import annotations

import dataclasses
import re

from mu_spec import identifiers
from mu_spec.identifiers import SEPARATOR_CLASS, SPEC, TEST

_NAME = re.compile(r"[A-Za-z_]\w*$")
_DOTTED_NAME = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")
_MODULE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$")
_ID = re.compile(
    rf"(?<![A-Za-z0-9])[{''.join(identifiers.ALL_LAYERS)}]{SEPARATOR_CLASS}[0-9]+(?![0-9])"
)
# An entry id as prose writes it, either spelling. Public for the renderer,
# which rewrites retired ones; it must find exactly what `id_mentions` finds.
ID_RE = _ID
# A directory ending in `/`, or a file with an extension, under at least one
# directory. A bare `and/or` is neither.
_PATH = re.compile(
    r"(?<![\w./-])(?:[\w.-]+/)+(?:[\w-]+(?:\.[\w-]+)*\.[A-Za-z0-9]+)?(?![\w/])"
)
_TOP_LEVEL = re.compile(
    r"^(?:(?:async\s+)?def\s+(\w+)|class\s+(\w+)|([A-Z][A-Z0-9_]*)\s*(?::|=(?!=)))"
)
_PYTHON_FENCES = ("", "python", "py", "python3")

IMPORTS = "Imports:"
ERRORS = "Errors:"
EXERCISES = "Exercises:"


@dataclasses.dataclass(frozen=True)
class Import:
    target: str  # a canonical entry id (`S-41`) or a dotted module path
    names: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class BodyFacts:
    imports: tuple[Import, ...] = ()
    imports_line_present: bool = False
    errors: tuple[str, ...] = ()
    exercises: tuple[str, ...] = ()
    defines: tuple[str, ...] = ()
    id_mentions: tuple[str, ...] = ()
    path_mentions: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()


def parse(body: str, layer: str) -> BodyFacts:
    body = body if isinstance(body, str) else ""
    lines = body.splitlines()
    problems: list[str] = []
    imports: list[Import] = []
    errors: tuple[str, ...] = ()
    exercises: tuple[str, ...] = ()
    present = False

    if layer == SPEC:
        found = [line.strip() for line in lines if line.strip().startswith(IMPORTS)]
        present = bool(found)
        if found:
            first = next(line.strip() for line in lines if line.strip())
            if not first.startswith(IMPORTS):
                problems.append("Imports not on first line")
            if len(found) > 1:
                problems.append("more than one Imports line")
            imports = _imports_line(found[0][len(IMPORTS):], problems)
        errors = _list_line(lines, ERRORS, _DOTTED_NAME.match, problems)
    elif layer == TEST:
        exercises = tuple(
            _canonical(item)
            for item in _list_line(lines, EXERCISES, _canonical, problems)
        )

    defines = _defines(lines, problems)
    ids = (_canonical(m.group(0)) for m in _ID.finditer(body))
    paths = [m.group(0) for m in _PATH.finditer(body)]
    paths += [i.target for i in imports if _MODULE.match(i.target)]
    return BodyFacts(
        imports=tuple(imports),
        imports_line_present=present,
        errors=errors,
        exercises=exercises,
        defines=defines,
        id_mentions=_unique(i for i in ids if i),
        path_mentions=_unique(paths),
        problems=tuple(problems),
    )


# A fixed-shape history line: a whole line, parenthesised (optionally in `*`
# or `_`), opening `Supersedes` or `Raised as iss-NNNN`. DARK wrote 192, all
# `*(Supersedes ... .)*`, some with `Raised as iss-NNNN` inside. A ruling
# (`iss-0181 ruled ...`, `*(Updated for ... iss-0181 ...)*`) is NOT one: it
# can carry contract text, so it is never matched.
_PROVENANCE = re.compile(
    r"\n*^[ \t]*([*_]?)\((?:Supersedes\b|Raised as iss-\d+)[^\n]*\)\1[ \t]*$", re.M
)


def strip_provenance(body: str) -> str:
    """`body` without its provenance lines, and nothing else changed but the
    blank line each one stood behind."""
    body = body if isinstance(body, str) else ""
    out = _PROVENANCE.sub("", body)
    return out.lstrip("\n") if out != body else body


def _canonical(text: str) -> str | None:
    try:
        return str(identifiers.parse(text))
    except identifiers.InvalidIdentifier:
        return None


def _unique(items) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))


def _target(text: str) -> str | None:
    return _canonical(text) or (text if _MODULE.match(text) else None)


def _imports_line(rest: str, problems: list[str]) -> list[Import]:
    if not rest.strip():
        return []
    out = []
    for item in rest.split(";"):
        head, _, tail = item.strip().partition(" ")
        target = _target(head)
        names = tuple(n.strip() for n in tail.split(","))
        if target is None or not tail.strip() or not all(_NAME.match(n) for n in names):
            problems.append(f"malformed Imports item: {item.strip()!r}")
            continue
        out.append(Import(target, names))
    return out


def _list_line(lines, label, valid, problems) -> tuple[str, ...]:
    """The comma-separated items of the first line starting with `label`."""
    line = next((l.strip() for l in lines if l.strip().startswith(label)), None)
    if line is None or not line[len(label):].strip():
        return ()
    good = []
    for item in (i.strip() for i in line[len(label):].split(",")):
        if valid(item):
            good.append(item)
        else:
            problems.append(f"malformed {label[:-1]} item: {item!r}")
    return tuple(good)


def _defines(lines, problems) -> tuple[str, ...]:
    """Top-level def / class / CONSTANT names in python code fences. Column 0
    is top level; a regex rather than `ast`, because a spec's code is stubs
    and fragments (`...`, `…`) that need not parse."""
    names, fence = [], None
    for line in lines:
        if line.lstrip().startswith("```"):
            fence = None if fence is not None else line.lstrip()[3:].strip().lower()
            continue
        if fence in _PYTHON_FENCES:
            m = _TOP_LEVEL.match(line)
            if m:
                names.append(next(g for g in m.groups() if g))
    if fence is not None:
        problems.append("unclosed code fence")
    return _unique(names)


# -- LEGACY ------------------------------------------------------------------
#
# Best-effort reading of the pre-D4 prose form DARK was written in:
#   Module: `path`. <libs>. Imports `X`/`Y` (S·NN); `Z` from `pkg.mod`, ...
# It exists so DARK replays (V1, replay/harness.py) can feed the import gates.
# New bodies use the `Imports:` line and `parse`; do not extend this, delete
# it when nothing replays pre-D4 bodies.
#
# Within each sentence opening `Imports`: backticked names accumulate, and a
# parenthesised id list assigns them to each id in it (`(S·45, S·40)` gives
# both ids the same names -- the prose does not say which is whose). A
# backticked dotted module takes the pending names, or, with none pending,
# the names that trail it to the end of the sentence (`from `dark.record`:
# `A`, `B``). Names left with no target are dropped.

_LEGACY_START = re.compile(r"(?:^|(?<=\. )|(?<=\n))Imports\b(?!:)")
_LEGACY_TOKEN = re.compile(r"`([^`\n]*)`|\(([^)\n]*)\)")


def legacy_imports(body: str) -> tuple[Import, ...]:
    body = body if isinstance(body, str) else ""
    out: list[Import] = []
    for start in _LEGACY_START.finditer(body):
        # "... and reaches `X` only through `Y()`": X is not imported (S-80).
        sentence = _sentence(body, start.end()).split(" reaches ", 1)[0]
        pending: list[str] = []
        trailing = None
        for tok in _LEGACY_TOKEN.finditer(sentence):
            code, paren = tok.groups()
            if paren is not None:
                ids = [c for c in (_canonical(m.group(0)) for m in _ID.finditer(paren)) if c]
                if ids and pending:
                    out += [Import(i, tuple(pending)) for i in ids]
                    pending = []
            elif _NAME.match(code):
                pending.append(code)
            elif _MODULE.match(code):
                if pending:
                    out.append(Import(code, tuple(pending)))
                    pending = []
                else:
                    trailing = code
        if pending and trailing:
            out.append(Import(trailing, tuple(pending)))
    return tuple(out)


def _sentence(body: str, i: int) -> str:
    """From `i` to the first full stop outside backticks and parentheses, or a
    blank line."""
    tick, depth, j = False, 0, i
    while j < len(body):
        c = body[j]
        if c == "`":
            tick = not tick
        elif not tick and c == "(":
            depth += 1
        elif not tick and c == ")":
            depth = max(0, depth - 1)
        elif not tick and depth == 0 and c == "." and (j + 1 == len(body) or body[j + 1].isspace()):
            break
        elif body.startswith("\n\n", j):
            break
        j += 1
    return body[i:j]
