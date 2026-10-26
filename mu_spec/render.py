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
import hashlib
import json
import re

from mu_spec import body as bodies
from mu_spec.identifiers import LEGACY_SEPARATOR, SEPARATOR, parse as parse_id
from mu_spec.units import TEST_SUFFIX, anchor_of, is_test_key

# GitHub allows 256. A title is a line in a list, not a manifest, and a unit
# writing nine files does not earn nine paths in it.
MAX_TITLE = 120

# GitHub's issue body ceiling. Not enforced here on purpose: rendering is a
# pure function of the graph and has no business refusing. The largest unit
# measured is 44,880, which is comfortable and not so comfortable that nobody
# should ever check.
BODY_LIMIT = 65536
# What a follow-up puts above the body (emit's `_follow_up`), with room to
# spare. A ticket inline within this of the ceiling goes as titles instead:
# DARK's S-264-T was 14 characters under it and its follow-up took a 422.
FOLLOW_UP_ROOM = 1024

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
    # What the ticket SAYS, hashed without the cut and the press it came from.
    # A new cut that says the same thing about a unit must leave its issue
    # alone; hashing `cut_seq` would rewrite every open issue on every cut.
    fingerprint: str = ""
    # (cited id, the entry whose rendered text cites it), for every entry the
    # body shows: its heading or line, and its body. Unit keys -- the title,
    # blockers, overlap, the machine block -- are names, never citations.
    cites: tuple[tuple[str, str], ...] = ()
    # (retired id, live successor, the entry whose text named it): every
    # citation rewritten `S-317 (was S-128)` in this body.
    rewritten: tuple[tuple[str, str, str], ...] = ()

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
_LEGACY_REF = re.compile(rf"([A-Z]){re.escape(LEGACY_SEPARATOR)}([0-9])")


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
        piece if index % 2 else _LEGACY_REF.sub(lambda m: m[1] + SEPARATOR + m[2], piece)
        for index, piece in enumerate(parts)
    )


# A cited id to rewrite: not one already written `(was S-128)`, and not the
# front of a unit key (`S-128-T`) -- keys are names, never citations.
_CITED = re.compile(rf"(?<!was ){bodies.ID_RE.pattern}(?!{re.escape(TEST_SUFFIX)}\b)")


def _text(body: str) -> str:
    """An authored body as a ticket shows it: provenance lines out, then
    references re-spelled."""
    return _respell(bodies.strip_provenance(body))


def _machine(
    payload: dict, unit: dict, write_set: list[str],
    repo: str | None, cut_seq: int | None,
    batch: int | None = None, context_ids: "list[str] | None" = None,
) -> dict:
    """What a consumer reads instead of the markdown.

    `write_set` is the authoritative list of paths this unit may modify, and
    it is the ONLY name for it here. The payload also calls it
    `audit.editable_paths`; the two are the same list and always have been, so
    carrying both would invite a consumer to wonder which wins.

    `file_scope` carries paths and entry identifiers only, never the other
    contracts' bodies -- those are in the markdown, in full, because an agent
    has to read them. A machine deciding what a unit may touch does not.

    `batch` is the Ship press that created or last edited the issue: what a
    wave review waits on, and never renumbered, unlike a wave. `context_ids`
    names the other contracts left as titles because inlining them would
    overflow the ticket -- empty on every ticket that carries them in full.
    """
    scope = {
        path: [other.get("id") for other in others]
        for path, others in (payload.get("file_scope") or {}).items()
        if others
    }
    merge = _merge_only(payload)
    block = {
        "key": unit["key"],
        "anchor": unit.get("anchor"),
        "project": payload.get("project"),
        "kind": TEST if is_test_key(unit["key"]) else IMPLEMENTATION,
        "repo": repo,
        "cut_seq": cut_seq,
        "batch": batch,
        "slices": list(unit.get("slices") or ()),
        "write_set": write_set,
        "blocked_by": _blocked_by(payload),
        # Blocker key -> the keys it had before its anchor was superseded. A
        # renamed unit keeps its issue (docs/TICKETS.md section 1), but a
        # touched issue is never edited, so its own block may still carry an
        # old key: a blocker counts as done under any of these names.
        "blocked_by_was": {k: list(v) for k, v in
                           (payload.get("follows_was") or {}).items()},
        "mutex": list(payload.get("overlap") or ()),
        "file_scope": scope,
        "context_ids": list(context_ids or ()),
    }
    # Merge order (M31, D11): blockers Ship derived this press, never entry
    # edges. In `blocked_by` so a consumer waits on them as it waits on any
    # blocker; named here so it can tell them apart. Absent when there are
    # none, so a ticket without one is byte for byte what it was.
    if merge:
        block["order"] = {key: "merge" for key in merge}
    return block


def _merge_only(payload: dict) -> list[str]:
    """The merge-order blockers that are not also dependencies."""
    follows = set(payload.get("follows") or ())
    return [k for k in payload.get("merge_after") or () if k not in follows]


def _blocked_by(payload: dict) -> list[str]:
    return list(payload.get("follows") or ()) + _merge_only(payload)


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
    batch: int | None = None,
    existing: "frozenset[str] | set[str]" = frozenset(),
    retired: "dict[str, str | None] | None" = None,
) -> Ticket:
    """One work unit as a ticket.

    `existing` is the unit keys closed `completed`, read from the emission
    record. A shared file one of them writes is on main, so its other
    contracts go as id + title: the file's shape is decided and is the code.

    `retired` maps a superseded id to the live end of its chain (None for a
    dead end). Every entry text the ticket shows cites the live id as
    `S-317 (was S-128)`; the stored entry is untouched, and so are unit
    keys, the title, the blockers and the machine block.

    The other contracts a file serves go in full when the ticket still fits,
    and as titles plus `context_ids` when it would not. Measured on `dark`:
    140 of 144 fit, median 17 KB -> 25 KB. The four that do not keep the
    titles, because a ticket GitHub refuses helps nobody; past that it
    degrades further, as `_DROP_ORDER` says.

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
    level = 0
    while True:
        ticket = _render(payload, repo, cut_seq, extra_labels, batch, existing, retired,
                         level=level)
        if len(ticket.body) <= BODY_LIMIT - FOLLOW_UP_ROOM or level == _LAST_LEVEL:
            return ticket
        level += 1


# How a body too large for the tracker degrades, one level per pass, stopping
# at the first that fits (DARK S-351-T: 85,756 characters, eleven shared test
# files). Level 0 inlines the other contracts a file serves; 1 lists them as
# titles under each file; 2 lists each once, id + title + its file; from 3 the
# sections below go, in this order. The contract, the write set, blockers,
# overlap, lineage and the machine block never do -- so `contract_of` cannot
# move -- and a contract too large alone is still refused by whoever emits.
_DROP_ORDER = (
    ("tests_pending", "Specified but not yet implemented"),
    ("read_set", "Read-only context"),
    ("justification", "Why this exists"),
    ("tests", "Scenarios that judge this"),
    ("file_scope", "Other contracts these files must also serve"),
)
_LAST_LEVEL = 2 + len(_DROP_ORDER)


def _render(payload, repo, cut_seq, extra_labels, batch, existing=frozenset(), retired=None, *,
            level: int) -> Ticket:
    inline = level == 0
    dropped = {field for field, _ in _DROP_ORDER[:max(0, level - 2)]}
    omitted = [heading for field, heading in _DROP_ORDER
               if field in dropped and payload.get(field)]
    payload = {k: v for k, v in payload.items() if k not in dropped or k == "file_scope"}
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
    cites: dict[tuple[str, str], None] = {}

    # A unit keyed by its lineage root (M13) whose anchor has since been
    # superseded is named by the live entry first, so searching the id finds
    # its issue, then by the key it revises. Leading, not trailing: a long
    # write set truncates the end of a title. A key that IS its anchor -- every
    # unit of a project keyed by live anchor, DARK's -- renders as before.
    key = unit["key"]
    anchor = anchor_of(str(unit.get("anchor") or ""))
    name = key
    if anchor and anchor_of(key) != anchor:
        name = f"{anchor}{key[len(anchor_of(key)):]} (rev of {key})"
        chain = list(payload.get("lineage") or ()) or [anchor_of(key), anchor]
        out.append(f"> Unit `{key}`, live entry `{anchor}`. Lineage: "
                   + " → ".join(f"`{i}`" for i in chain) + ".")

    rewritten: dict[tuple[str, str, str], None] = {}

    def cite(entry: dict, text: str) -> str:
        """`text` from `entry` as the ticket shows it. What it cites is
        recorded, itself included; a retired id whose chain ends in a live
        entry is rewritten `S-317 (was S-128)`. A dead end is left as written
        for the press to refuse."""
        own = str(entry.get("id"))
        for found in bodies.parse(f"{own} {text}", "").id_mentions:
            cites[(found, own)] = None

        def swap(match: "re.Match") -> str:
            old = str(parse_id(match.group(0)))
            new = retired.get(old)
            if not new:
                return match.group(0)
            rewritten[(old, new, own)] = None
            return f"{new} (was {old})"

        return _CITED.sub(swap, text) if retired else text

    # What to build. First, because it is the only section that cannot be
    # skipped.
    out.append("## Contract")
    for entry in payload.get("entries") or ():
        out.append(f"### {entry.get('id')} — {cite(entry, entry.get('title', ''))}")
        if entry.get("purpose"):
            # Scenarios only. Says why the scenario is worth having, which is
            # not recoverable from what it checks.
            out.append(f"*Why this case:* {cite(entry, entry['purpose'])}")
        if entry.get("body"):
            out.append(cite(entry, _text(entry["body"])))

    def line(entry: dict) -> str:
        return f"- `{entry.get('id')}` — {cite(entry, _respell(entry.get('title', '')))}"

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

    if _blocked_by(payload):
        out.append("## Blocked by")
        out.append(_bullets([f"- `{key}`" for key in payload.get("follows") or ()]
                            + [f"- `{key}` — merge order: it merges first so main stays "
                               "green; not a dependency" for key in _merge_only(payload)]))

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
    context_ids: list[str] = []
    if scope and level >= 2:
        # One line per entry, not per file: a scenario a dozen shared test
        # files serve is named once, at the first of them.
        where: dict[str, list[str]] = {}
        entry: dict[str, dict] = {}
        on_main: dict[str, bool] = {}
        for path, others in scope.items():
            exists = any(o.get("unit") in existing for o in others)
            for other in others:
                where.setdefault(other.get("id"), []).append(path)
                entry.setdefault(other.get("id"), other)
                on_main[other.get("id")] = on_main.get(other.get("id"), True) and exists
        context_ids = [i for i in where if not on_main[i]]
        if "file_scope" not in dropped:
            out.append("## Other contracts these files must also serve")
            out.append(
                "A unit is one contract; a file is not. Whichever unit reaches a "
                "file while it is still empty decides its shape for all of them, "
                "so design for these too. Listed by title only to fit the "
                "tracker: read each in full with mu-spec `get_entry`."
            )
            out.append(_bullets(
                line(entry[i]) + f" — `{paths[0]}`"
                + (f" (+{len(paths) - 1} more)" if len(paths) > 1 else "")
                for i, paths in where.items()))
    elif scope:
        out.append("## Other contracts these files must also serve")
        out.append(
            "A unit is one contract; a file is not. Whichever unit reaches a "
            "file while it is still empty decides its shape for all of them, "
            "so design for these too."
        )
        written: set = set()
        for path, others in scope.items():
            out.append(f"**`{path}`**")
            # The file is on main once a unit writing it closed `completed`:
            # its shape is the code now, so the others go as titles.
            exists = any(o.get("unit") in existing for o in others)
            for other in others:
                if exists or not inline or not other.get("body") or other.get("id") in written:
                    # Listed, not repeated: a contract serving two files is
                    # written out under the first and named under the second.
                    out.append(line(other))
                    if not inline and not exists and other.get("id") not in context_ids:
                        context_ids.append(other.get("id"))
                    continue
                written.add(other.get("id"))
                out.append(f"#### {other.get('id')} — {cite(other, _respell(other.get('title', '')))}")
                out.append(cite(other, _text(other["body"])))

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
            run.append(line(ancestor))
            if ancestor.get("body"):
                out.append(_bullets(run))
                run = []
                out.append(cite(ancestor, _text(ancestor["body"])))
        if run:
            out.append(_bullets(run))

    context = list(payload.get("read_set") or ())
    if context:
        out.append("## Read-only context")
        out.append(
            "Contracts this one depends on, owned by other units. Read them; "
            "do not change them."
        )
        out.append(_bullets(line(entry) for entry in context))

    if payload.get("tests"):
        out.append("## Scenarios that judge this")
        out.append(
            "These exist as files and are expected to pass when you are done."
        )
        out.append(_bullets(line(test) for test in payload["tests"]))

    if payload.get("tests_pending"):
        out.append("## Specified but not yet implemented")
        out.append(
            "Written as scenarios, not yet built, and **not** expected to "
            "pass. They order nothing."
        )
        out.append(
            _bullets(f"- `{identifier}`" for identifier in payload["tests_pending"])
        )
        for identifier in payload["tests_pending"]:
            cite({"id": identifier}, "")

    if omitted:
        out.append("*Omitted to fit the tracker:* " + ", ".join(omitted)
                   + ". Read them with mu-spec `get_work_unit`.")

    # Last, deliberately. The first screen belongs to the contract and the
    # write set -- an agent that reads only that far must still know its
    # scope -- and a machine finds a fenced block wherever it sits.
    out.append(MACHINE_HEADING)
    out.append(
        "The same facts as above, for a consumer that should not parse prose. "
        "A heading can be reworded; these field names are a contract."
    )
    machine = _machine(payload, unit, write_set, repo, cut_seq, batch, context_ids)
    title = _title(name, write_set)
    labels = _labels(unit) + tuple(extra_labels)
    stable = {k: v for k, v in machine.items() if k not in ("cut_seq", "batch")}
    fingerprint = hashlib.sha256(json.dumps(
        [title, out, stable, sorted(labels)], ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")).hexdigest()

    out.append(MACHINE_FENCE + "\n" + json.dumps(
        machine, indent=2, ensure_ascii=False,
    ) + "\n```")

    return Ticket(
        key=unit["key"],
        title=title,
        body="\n\n".join(part for part in out if str(part).strip()),
        labels=labels,
        blocked_by=tuple(_blocked_by(payload)),
        fingerprint=fingerprint,
        cites=tuple(cites),
        rewritten=tuple(rewritten),
    )
