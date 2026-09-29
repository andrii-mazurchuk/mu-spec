"""A work unit as a ticket: title, labels, body, and what blocks it.

One pure function over the payload `get_work_unit` already returns. It opens
nothing, reaches nothing, and decides nothing -- which is what lets a rendered
ticket be read and argued about before a single issue exists anywhere.

**The audience is an agent.** Not a person: the tickets happen to be legible
and that is incidental, so nothing here is shortened for a human's comfort.
The body carries every contract the builder needs in full, because the
alternative is an agent inferring a contract it was not given.

**`blocked_by` is unit keys, never issue numbers.** Numbers do not exist until
the issues do. Resolving keys to numbers belongs to whoever creates them, and
keeping it out of here is what makes a ticket a function of the graph alone.

**Tracker-neutral by omission, not by abstraction.** There is no interface
here and no second implementation to satisfy: the ticket is a title, some
labels, a markdown body and a list of keys, which is roughly what every
tracker accepts. A second tracker becomes a second caller of this function,
not a strategy object.

**Sizes, measured on `dark`'s 144 units** rather than guessed: smallest body
5.2 KB, median 16.4 KB, largest 44.9 KB, 2.5 MB in total. GitHub's ceiling is
65,536 characters, so the largest real ticket uses 68% of it. Nothing here
truncates -- a silently shortened contract is worse than a refused emission --
so whoever creates the issues is the one that must notice `BODY_LIMIT`.
"""

from __future__ import annotations

import dataclasses
import json
import re

from mu_spec.units import is_test_key

# GitHub allows 256. A title is a line in a list, not a manifest, and a unit
# writing nine files does not earn nine paths in it.
MAX_TITLE = 120

# GitHub's issue body ceiling. Not enforced here on purpose: rendering is a
# pure function of the graph and has no business refusing. The largest unit
# measured is 44,880, which is comfortable and not so comfortable that nobody
# should ever check.
BODY_LIMIT = 65536

IMPLEMENTATION = "implementation"
TEST = "test"

# The prefix on the label naming a unit's anchor. Scoped so it cannot collide
# with a label somebody already uses on the repository -- these are created on
# a repository this unit does not own, and a bare `S-01` is the sort of thing
# another tool invents too.
LABEL_NAMESPACE = "mu-spec"

# The heading and fence a machine reads. Named constants because a consumer's
# path-guard matches on them: renaming a heading is then a change to a value
# with a comment saying what it breaks, rather than an edit to a string
# literal in the middle of prose.
MACHINE_HEADING = "## Machine-readable"
MACHINE_FENCE = "```json"


@dataclasses.dataclass(frozen=True)
class Ticket:
    """What a tracker needs, and nothing about any particular tracker."""

    key: str
    title: str
    body: str
    labels: tuple[str, ...]
    blocked_by: tuple[str, ...]

    @property
    def oversized(self) -> bool:
        """Whether this body exceeds what a tracker will accept.

        Reported rather than refused, and reported here rather than recomputed
        by every caller, because the caller that creates issues needs to skip
        this one and say why instead of taking a 422 with no explanation.
        """
        return len(self.body) > BODY_LIMIT

    def to_json(self) -> dict:
        return {
            "key": self.key,
            "title": self.title,
            "body": self.body,
            "labels": list(self.labels),
            "blocked_by": list(self.blocked_by),
        }


def _title(key: str, write_set: list[str]) -> str:
    """`S-01 — dark/config/declare.py`.

    Computable, which it was not under the old grain: a unit held several
    entries then, and `a12837b4` ruled that naming a unit was judgement. A unit
    is now one entry and its modules, and the paths name the work better than
    the entry's own title does -- `S-01`'s title is a 150-character sentence
    and makes a useless line in a list.
    """
    if not write_set:
        # A module map mid-edit leaves a unit with no paths. An unhelpful title
        # beats an empty one.
        return key
    head = f"{key} — {write_set[0]}"
    if len(write_set) == 1:
        return head if len(head) <= MAX_TITLE else head[: MAX_TITLE - 1] + "…"
    joined = f"{key} — {', '.join(write_set)}"
    if len(joined) <= MAX_TITLE:
        return joined
    return f"{head} (+{len(write_set) - 1} more)"


def _labels(unit: dict) -> tuple[str, ...]:
    """The anchor, the kind, and every slice the unit touches.

    One label per slice rather than a comma-joined one: a file can straddle,
    and filtering by either half should work. The anchor is namespaced because
    these land on a repository this unit does not own.
    """
    kind = TEST if is_test_key(unit["key"]) else IMPLEMENTATION
    labels = [f"{LABEL_NAMESPACE}:{unit['anchor']}", f"kind:{kind}"]
    labels += [f"slice:{name}" for name in unit.get("slices", [])]
    return tuple(labels)


# An identifier reference inside authored prose, in the old spelling. Bodies
# were written by sessions that predate the change and say things like
# "Imports `Subspace` (S·41)" -- the entry is stored exactly as written, and
# rewriting the store would be editing an author's text. This re-spells the
# REFERENCES as the ticket is rendered, so a reader is never shown two
# spellings of one identifier and left wondering whether they are the same
# thing. The graph keeps the bytes it was given.
_LEGACY_REF = re.compile("([A-Z])\u00b7([0-9])")


def _respell(text: str) -> str:
    """Old-spelling identifier references, in the current spelling.

    Fence-aware: a fenced block is code or data, and rewriting a character
    inside one would change a program rather than a citation. Splitting on
    ``` and rewriting only the odd-numbered pieces is the whole of it --
    both sample bodies from `dark` carry a ```python block, which is how this
    turned out to matter.
    """
    parts = text.split("```")
    return "```".join(
        piece if index % 2 else _LEGACY_REF.sub(r"\1-\2", piece)
        for index, piece in enumerate(parts)
    )


def _machine(
    payload: dict, unit: dict, write_set: list[str],
    repo: str | None, cut_seq: int | None,
) -> dict:
    """What a consumer reads instead of the markdown.

    `write_set` is the authoritative list of paths this unit may modify, and
    it is the ONLY name for it here. The payload also calls it
    `audit.editable_paths`; the two are the same list and always have been, so
    carrying both would invite a consumer to wonder which wins.

    `file_scope` carries paths and entry identifiers only, never the other
    contracts' bodies -- those are in the markdown, in full, because an agent
    has to read them. A machine deciding what a unit may touch does not.
    """
    scope = {
        path: [other.get("id") for other in others]
        for path, others in (payload.get("file_scope") or {}).items()
        if others
    }
    return {
        "key": unit["key"],
        "anchor": unit.get("anchor"),
        "kind": TEST if is_test_key(unit["key"]) else IMPLEMENTATION,
        "repo": repo,
        "cut_seq": cut_seq,
        "slices": list(unit.get("slices") or ()),
        "write_set": write_set,
        "blocked_by": list(payload.get("follows") or ()),
        "mutex": list(payload.get("overlap") or ()),
        "file_scope": scope,
    }


def _entry_line(entry: dict) -> str:
    return f"- `{entry.get('id')}` — {_respell(entry.get('title', ''))}"


def _bullets(lines) -> str:
    """One list, single-spaced.

    Parts are joined with a blank line, which is right between blocks and wrong
    between list items -- markdown renders those as a loose list, and it costs
    real bytes across 144 tickets whose median body is 16 KB.
    """
    return "\n".join(lines)


def render(
    payload: dict,
    *,
    repo: str | None = None,
    cut_seq: int | None = None,
    extra_labels: "tuple[str, ...] | list[str]" = (),
) -> Ticket:
    """One work unit as a ticket.

    `payload` is what `get_work_unit` returns. An unissued payload is refused
    rather than rendered: it carries `reason` where a contract should be, and a
    ticket whose contract section is an error message is the kind of thing
    nobody notices until an agent tries to build from it.

    `repo`, `cut_seq` and `extra_labels` are the three things a ticket cannot
    derive from its own payload -- they belong to the project and the emission,
    not to the unit -- so they are passed in rather than looked up. That keeps
    this a pure function of its arguments, which is what lets a ticket be read
    and argued about before any issue exists.
    """
    if not payload.get("issued"):
        raise ValueError(
            "this work unit was not issued, so there is nothing to render: "
            + str(payload.get("reason", "no reason given"))
        )
    unit = payload.get("unit")
    if not unit or not unit.get("key"):
        raise ValueError("a work unit payload must carry its `unit`")

    write_set = list(payload.get("write_set") or ())
    out: list[str] = []

    # What to build. First, because it is the only section that cannot be
    # skipped.
    out.append("## Contract")
    for entry in payload.get("entries") or ():
        out.append(f"### {entry.get('id')} — {entry.get('title', '')}")
        if entry.get("purpose"):
            # Scenarios only. Says why the scenario is worth having, which is
            # not recoverable from what it checks.
            out.append(f"*Why this case:* {entry['purpose']}")
        if entry.get("body"):
            out.append(_respell(entry["body"]))

    # Scope, before anything explanatory. An agent that reads only the first
    # screen must still know what it may touch.
    out.append("## Files you may write")
    if write_set:
        out.append(_bullets(f"- `{path}`" for path in write_set))
    else:
        out.append(
            "*Nothing is declared yet.* No module claims this contract, so "
            "there is no write set -- declare one before building."
        )
    rule = (payload.get("audit") or {}).get("rule")
    if rule:
        out.append(f"> {rule}")

    if payload.get("follows"):
        out.append("## Blocked by")
        out.append(_bullets(f"- `{key}`" for key in payload["follows"]))

    if payload.get("overlap"):
        out.append("## Must not run at the same time as")
        out.append(
            "These units write files this one writes. This is **not** an "
            "order: neither waits for the other, and nothing here says which "
            "goes first. They must simply not be in progress together."
        )
        out.append(_bullets(f"- `{key}`" for key in payload["overlap"]))

    scope = {
        path: others
        for path, others in (payload.get("file_scope") or {}).items()
        if others
    }
    if scope:
        out.append("## Other contracts these files must also serve")
        out.append(
            "A unit is one contract; a file is not. Whichever unit reaches a "
            "file while it is still empty decides its shape for all of them, "
            "so design for these too."
        )
        for path, others in scope.items():
            out.append(f"**`{path}`**")
            out.append(_bullets(_entry_line(other) for other in others))

    if payload.get("justification"):
        out.append("## Why this exists")
        out.append(
            "The derivation chain above this contract, nearest last. One "
            "chain: every entry in a unit shares an anchor and so an ancestry."
        )
        # Consecutive title-only ancestors make one list. A body interrupts it,
        # because a paragraph between two items ends the list anyway.
        run: list[str] = []
        for ancestor in payload["justification"]:
            run.append(_entry_line(ancestor))
            if ancestor.get("body"):
                out.append(_bullets(run))
                run = []
                out.append(_respell(ancestor["body"]))
        if run:
            out.append(_bullets(run))

    context = list(payload.get("read_set") or ()) + list(
        payload.get("cross_cutting") or ()
    )
    if context:
        out.append("## Read-only context")
        out.append(
            "Contracts this one depends on, owned by other units. Read them; "
            "do not change them."
        )
        out.append(_bullets(_entry_line(entry) for entry in context))

    if payload.get("tests"):
        out.append("## Scenarios that judge this")
        out.append(
            "These exist as files and are expected to pass when you are done."
        )
        out.append(_bullets(_entry_line(test) for test in payload["tests"]))

    if payload.get("tests_pending"):
        out.append("## Specified but not yet implemented")
        out.append(
            "Written as scenarios, not yet built, and **not** expected to "
            "pass. They order nothing."
        )
        out.append(
            _bullets(f"- `{identifier}`" for identifier in payload["tests_pending"])
        )

    # Last, deliberately. The first screen belongs to the contract and the
    # write set -- an agent that reads only that far must still know its
    # scope -- and a machine finds a fenced block wherever it sits.
    out.append(MACHINE_HEADING)
    out.append(
        "The same facts as above, for a consumer that should not parse prose. "
        "A heading can be reworded; these field names are a contract."
    )
    out.append(MACHINE_FENCE + "\n" + json.dumps(
        _machine(payload, unit, write_set, repo, cut_seq), indent=2,
        ensure_ascii=False,
    ) + "\n```")

    return Ticket(
        key=unit["key"],
        title=_title(unit["key"], write_set),
        body="\n\n".join(part for part in out if str(part).strip()),
        labels=_labels(unit) + tuple(extra_labels),
        blocked_by=tuple(payload.get("follows") or ()),
    )
