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


# -- CODE FACTS --------------------------------------------------------------
#
# What the report-only kinds (M4, M6, M7, M9) read out of a body's code. All
# regexes over stubs that need not parse, so every one is an approximation
# that leans towards seeing less: a name it cannot place is left out, never
# guessed at. Nothing here refuses anything (D3).


@dataclasses.dataclass(frozen=True)
class CodeFacts:
    code: str = ""  # the python fences, verbatim
    # (class, bases, members): fields, methods and class attributes one
    # indent in.
    classes: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = ()
    # Names the body itself binds: defines, classes, any assignment,
    # parameters, and `as X` aliases. Never a reference to someone else.
    local: frozenset[str] = frozenset()
    # CamelCase names the code uses, outside strings, comments and imports.
    references: frozenset[str] = frozenset()
    # `x.attr` in the code and in inline backticks.
    attributes: tuple[tuple[str, str], ...] = ()
    # (scope dirs, allowed files, banned tokens) per sweep sentence.
    sweeps: tuple[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]], ...] = ()


_FENCE_SPLIT = re.compile(r"^[ \t]*```", re.M)
_STRINGS = re.compile(r'(?s)("""|\'\'\').*?\1|"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'')
_COMMENT = re.compile(r"#[^\n]*")
_CLASS = re.compile(r"^class\s+(\w+)\s*(?:\(([^)]*)\))?\s*:")
_MEMBER = re.compile(r"^(\s+)(?:(?:async\s+)?def\s+(\w+)|(\w+)\s*(?::|=(?!=)))")
_ASSIGNED = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?::[^=\n]*)?=(?!=)", re.M)
_DEF_PARAMS = re.compile(r"\bdef\s+\w+\s*\(([^)]*)\)")
_PARAM = re.compile(r"(?:^|[,(*])\s*\**([A-Za-z_]\w*)")
_ALIAS = re.compile(r"\bas\s+`?([A-Za-z_]\w*)")
_IMPORT_LINE = re.compile(r"^\s*(?:from\s+\S+\s+)?import\s+(.*)$", re.M)
_CAMEL = re.compile(r"(?<![\w.])([A-Z][A-Za-z0-9]*[a-z][A-Za-z0-9]*)\b(?!\s*=(?!=))")
_ATTR = re.compile(r"(?<![\w.])([A-Za-z_]\w*)\.([A-Za-z_]\w*)")
_TICK = re.compile(r"`([^`\n]+)`")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z*`])")
_SWEEP = re.compile(r"\bsweep(?:s|ing)?\b", re.I)
_BANNABLE = re.compile(r"[\w.()]+$")
_KEYWORDS = frozenset(__import__("keyword").kwlist) | {"match", "case", "type"}


def _split(body: str) -> tuple[str, str]:
    """(python fences, prose) -- the odd pieces of a fence split are code."""
    code, prose = [], []
    for n, piece in enumerate(_FENCE_SPLIT.split(body)):
        if n % 2:
            lang, _, rest = piece.partition("\n")
            if lang.strip().lower() in _PYTHON_FENCES:
                code.append(rest)
        else:
            prose.append(piece)
    return "\n".join(code), "\n".join(prose)


def _classes(code: str):
    out: dict[str, tuple[tuple[str, ...], list[str]]] = {}
    current, indent = None, None
    for line in code.splitlines():
        m = _CLASS.match(line)
        if m:
            current, indent = m.group(1), None
            bases = tuple(b.strip() for b in (m.group(2) or "").split(",") if b.strip())
            out.setdefault(current, (bases, []))
            continue
        if current is None or not line.strip():
            continue
        if not line[0].isspace():
            current = None
            continue
        mm = _MEMBER.match(line)
        if mm:
            indent = len(mm.group(1)) if indent is None else indent
            name = mm.group(2) or mm.group(3)
            if len(mm.group(1)) == indent and name not in _KEYWORDS:
                out[current][1].append(name)
    return tuple((c, b, tuple(dict.fromkeys(ms))) for c, (b, ms) in out.items())


def _sweeps(prose: str):
    found = []
    for line in prose.splitlines():
        for sentence in _SENTENCE.split(line):
            if not _SWEEP.search(sentence):
                continue
            ticks = _TICK.findall(sentence)
            scope = tuple(t for t in ticks if t.endswith("/") and not t.startswith("tests"))
            banned = tuple(
                t for t in ticks
                if "/" not in t and len(t) > 2 and _BANNABLE.match(t) and not _ID.match(t)
            )
            if scope and banned:
                files = tuple(t for t in ticks if not t.endswith("/") and t.startswith(scope))
                found.append((scope, files, banned))
    return tuple(found)


def code_facts(body: str) -> CodeFacts:
    body = body if isinstance(body, str) else ""
    code, prose = _split(body)
    bare = _COMMENT.sub("", _STRINGS.sub('""', code))
    classes = _classes(code)
    params = {p for m in _DEF_PARAMS.finditer(bare) for p in _PARAM.findall(m.group(1))}
    imported = {n for m in _IMPORT_LINE.finditer(bare) for n in re.findall(r"\w+", m.group(1))}
    local = (
        set(_defines(body.splitlines(), []))
        | {c for c, _, _ in classes}
        | set(_ASSIGNED.findall(bare))
        | params
        | set(_ALIAS.findall(body))
    )
    ticks = _TICK.findall(prose)
    return CodeFacts(
        code=code,
        classes=classes,
        local=frozenset(local),
        references=frozenset(_CAMEL.findall(_IMPORT_LINE.sub("", bare))) - imported,
        attributes=tuple(dict.fromkeys(
            a for text in (bare, *ticks) for a in _ATTR.findall(text)
        )),
        sweeps=_sweeps(prose),
    )


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
