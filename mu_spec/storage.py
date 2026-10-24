"""The only module that touches the filesystem.

Two responsibilities, kept together because they are two halves of the same
thing: the on-disk *format* of an entry, and the *layout* of a project.

Layout, per the design doc:

    <root>/<project>/
      manifest.json            slices, membership sets, and the identifier
                               high-water marks. Never dependencies -- those
                               are projected from the entries themselves
      intent.jsonl             intent is not sliced -- short by nature,
                               everyone reads it
      tests.jsonl              tests are not sliced either -- a test's column
                               is its parent spec entry's, and is derived
      behaviour/<slice>.jsonl  one file per slice per layer. Not one file per
      architecture/<slice>.jsonl  entry (per-file overhead kills you at fifty
      spec/<slice>.jsonl          reads); not one file per layer (large
                                  systems drown the context)
      history/               amendment log, never loaded by default

Entries are JSON Lines: one object per line, one file per slice per layer.
Not markdown. An entry is a record with a fixed set of structural fields --
identifier, edges, title, body -- and the edges are the load-bearing part.
A prose format makes every new structural field a new regex and a new way to
be silently misparsed, and the field list is still growing.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import threading
from pathlib import Path

from mu_spec.graph import Entry, Graph
from mu_spec.identifiers import (
    ALL_LAYERS,
    BEHAVIOUR,
    IMPLEMENTED,
    INTENT,
    LAYER_NAMES,
    LAYERS,
    TEST,
    UNSLICED,
    Identifier,
    InvalidIdentifier,
    parse,
    sort_key,
)

# The sliced layers, one directory each, named after the layer.
LAYER_DIRS = {l: LAYER_NAMES[l] for l in ALL_LAYERS if l not in UNSLICED}
INTENT_FILE = "intent.jsonl"
# Tests are stored flat, like intent, and for a related reason: they have no
# slice. A test's column is its parent spec entry's, which is derivable from
# the graph at any moment -- so writing it down would be a second copy of a
# fact already held, and the written copy is the one that goes stale.
#
# One file per layer rather than one per slice is also simply true to what
# the split buys. That split exists to bound how many files a reader opens;
# nothing outside this unit reads the tree, and everything in it is served
# from a graph loaded whole. So the granularity buys nothing here, and the
# flat file is what `intent.jsonl` already is.
TESTS_FILE = "tests.jsonl"
# Where behaviour lives before slicing has run. Slices are cut once
# behaviour is complete, so behaviour necessarily exists before any slice
# does; demanding a slice name for it made the designed order impossible.
# Architecture and spec are derived after slicing and always have an owner.
HOLDING_FILE = "_unassigned.jsonl"
# A slicing proposal waiting on a human. Kept out of the manifest because
# it is not a partition yet -- ratification is what makes it one.
PROPOSAL_FILE = "proposal.json"
# Work-unit cuts, one line per cut. Append-only like the other logs, and for
# the same reason: what the projection said when work was handed out is not
# recoverable from what it says now.
UNITS_FILE = "units.jsonl"
# Which work unit became which issue, one line per emission run. Append-only,
# and the only reason a second run does not create every issue again -- so
# unlike every other absence in this unit, an unreadable one is a hard error.
EMISSIONS_FILE = "emissions.jsonl"
# Directories a store may not be rooted in, os.pathsep-separated. The test
# suite sets it to the real state dir (tests/conftest.py), so a test or a
# seeding script it launches cannot open the live projects for writing.
REFUSE_ROOTS_ENV = "MU_SPEC_REFUSE_ROOTS"
MANIFEST_FILE = "manifest.json"
# What the manifest becomes once an `append` lands, written before the entry
# file and removed after the manifest. Present on a load only when a write
# died between the two, and then the entries decide: all there, roll forward;
# not, discard it. Either way the next reader sees one world, not half of two.
PENDING_FILE = "manifest.pending.json"

# Every slice is a plain slice. The cross-cutting type was removed 10-24 (D6);
# a manifest still saying "cross_cutting" loads, and the value is carried
# through untouched and read by nothing -- stored data is never rewritten.
SLICE = "slice"

# `owner/name`, which is the only form a REST path can use. A person has a
# browser URL or an SSH remote in the clipboard instead, so both are accepted
# and reduced to this -- a URL stored verbatim builds
# `/repos/https://github.com/owner/name/issues`, which 404s with nothing
# pointing at the cause.
_REPO_PREFIXES = (
    "https://github.com/",
    "http://github.com/",
    "git@github.com:",
    "ssh://git@github.com/",
    # Scheme-less, because a browser shows the address that way and that is
    # what gets copied out of the bar.
    "www.github.com/",
    "github.com/",
)
_REPO = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


def normalise_repo(value: str) -> str:
    """`owner/name` from any spelling a person is likely to paste.

    Validated rather than trusted: this string is interpolated into every
    issue URL, and the failure of a wrong one is a 404 several hundred writes
    into an emission.
    """
    text = (value or "").strip()
    for prefix in _REPO_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    text = text.rstrip("/")
    if text.endswith(".git"):
        text = text[: -len(".git")]
    if not _REPO.match(text):
        raise ValueError(
            f"{value!r} is not a repository: expected 'owner/name', or a "
            "github.com URL to take one from"
        )
    return text


class MalformedEntryFile(ValueError):
    """A file could not be parsed. A hard error, never a skipped entry: a
    silently dropped entry means the graph comes back missing an edge, and a
    missing edge is the failure this unit exists to prevent."""


class UnknownProject(KeyError):
    pass


# -- the file format --------------------------------------------------------


def _ids(raw, field: str, where: str) -> tuple[Identifier, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise MalformedEntryFile(f"{where}: {field} must be a list")
    try:
        return tuple(parse(str(x)) for x in raw)
    except InvalidIdentifier as exc:
        raise MalformedEntryFile(f"{where}: {field}: {exc}") from exc


def parse_entries(text: str) -> list[Entry]:
    """Parse every entry in one file: one JSON object per line.

    Blank lines are skipped; anything else that is not a well-formed entry is
    a hard error. Storage never silently drops an entry, because a dropped
    entry means the graph comes back missing an edge.
    """
    entries: list[Entry] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        where = f"line {number}"
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MalformedEntryFile(f"{where}: not valid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise MalformedEntryFile(f"{where}: expected an object")
        if "id" not in raw:
            raise MalformedEntryFile(f"{where}: entry has no 'id'")
        try:
            identifier = parse(str(raw["id"]))
        except InvalidIdentifier as exc:
            raise MalformedEntryFile(f"{where}: {exc}") from exc

        supersedes = raw.get("supersedes")
        entries.append(
            Entry(
                id=identifier,
                derives_from=_ids(raw.get("derives_from"), "derives_from", where),
                depends_on=_ids(raw.get("depends_on"), "depends_on", where),
                emits_into=_ids(raw.get("emits_into"), "emits_into", where),
                title=str(raw.get("title", "")),
                purpose=str(raw.get("purpose", "")),
                body=str(raw.get("body", "")),
                supersedes=(
                    _ids([supersedes], "supersedes", where)[0]
                    if supersedes is not None
                    else None
                ),
                preserves_interface=bool(raw.get("preserves_interface", False)),
            )
        )
    return entries


def render_entries(entries: list[Entry]) -> str:
    """Serialise entries back to the file format. Round-trips with
    parse_entries.

    Defaulted fields are omitted rather than written as empty, so a file
    stays readable and a new field arriving later does not rewrite every
    existing line.
    """
    out: list[str] = []
    for entry in sorted(entries, key=lambda e: sort_key(e.id)):
        record: dict = {"id": str(entry.id)}
        if entry.derives_from:
            record["derives_from"] = [str(d) for d in entry.derives_from]
        if entry.depends_on:
            record["depends_on"] = [str(d) for d in entry.depends_on]
        if entry.emits_into:
            record["emits_into"] = [str(d) for d in entry.emits_into]
        if entry.title:
            record["title"] = entry.title
        if entry.purpose:
            record["purpose"] = entry.purpose
        if entry.body:
            record["body"] = entry.body
        if entry.supersedes is not None:
            record["supersedes"] = str(entry.supersedes)
        if entry.preserves_interface:
            record["preserves_interface"] = True
        out.append(json.dumps(record, ensure_ascii=False))
    return "\n".join(out) + "\n" if out else ""


# -- the manifest -----------------------------------------------------------


@dataclasses.dataclass
class Slice:
    name: str
    # A SET, never a range. Identifiers encode layer and creation order only,
    # never slice, so a slice's members are scattered across the number line
    # and a gap is the ordinary case. This is what lets a slice split without
    # renumbering anything.
    members: set[Identifier] = dataclasses.field(default_factory=set)
    # Round-tripped, never read. See SLICE.
    type: str = SLICE


@dataclasses.dataclass
class Manifest:
    project: str
    slices: dict[str, Slice] = dataclasses.field(default_factory=dict)
    # High-water mark per layer. Only ever moves up -- superseding an entry
    # retires it but never frees its number, because every historical
    # reference must keep meaning what it meant.
    allocation: dict[str, int] = dataclasses.field(default_factory=dict)
    # module path -> the spec entries it implements. The bottom layer's
    # backlink: code is not an entry in the graph, so this is the only thing
    # tying a file to the reasoning that produced it. Without it the graph
    # stops at spec and the whole scheme is decorative.
    modules: dict[str, set[Identifier]] = dataclasses.field(default_factory=dict)
    # `owner/name` -- where this project's work units become tickets.
    #
    # Deployment config rather than graph structure, and it lives here anyway
    # because there is nowhere else: a project IS a directory holding a
    # manifest, so `list_projects` scans for this file and no central project
    # list exists. Inventing one would give "which projects exist" a second
    # answer, which is the drift this unit refuses everywhere else. One public
    # field in an existing file is the smaller cost.
    #
    # The TOKEN is deliberately not here. It is not project-specific and a
    # secret does not belong in a file that is read, diffed and shown on a
    # dashboard -- it comes from `os.environ`, as all config in this system
    # does. `None` is normal: every project predates this field.
    repo: str | None = None
    # Labels added to every issue this project's work units become.
    #
    # Per PROJECT and deliberately not per unit: which team owns a piece of
    # work is an operating decision, and the graph is append-only, so a team
    # recorded against a unit would outlive the arrangement that put it there.
    # One list beside the repo is the whole feature, and a person can apply
    # the same label by hand to an issue this unit never generated -- which is
    # what lets a consumer have ONE admission rule rather than two paths.
    labels: tuple[str, ...] = ()
    # Labels a CONSUMER adds when it picks an issue up -- "mine now". An issue
    # carrying one is touched, and this unit never edits or closes a touched
    # issue. Empty means no pickup signal exists, and then nothing is edited at
    # all: without one, untouched cannot be told from admitted.
    pickup: tuple[str, ...] = ()
    # A person's standing grant letting an agent cut and Ship S/T-only repairs
    # of shipped units (M33, D2): {enabled, note, actor, at}. None = never
    # granted, which reads as off. Beside repo and labels for their reason.
    delegation: dict | None = None

    def slice_of(self, identifier: Identifier) -> str | None:
        for name, sl in self.slices.items():
            if identifier in sl.members:
                return name
        return None

    def implementers(self, identifier: Identifier) -> tuple[str, ...]:
        """Which modules declare they implement this entry. The write set is
        built from exactly this."""
        return tuple(
            sorted(p for p, ids in self.modules.items() if identifier in ids)
        )

    def dependency_graph(self, graph: Graph) -> dict[str, tuple[str, ...]]:
        """Which slice depends on which, projected from the entries.

        Slice A depends on slice B when some entry in A depends on some entry
        in B. Never authored, and there is deliberately no field to author it
        in: two statements of the same fact drift, and the one a human
        maintains is the one that goes stale. Deriving it also means the
        cycle check reads the same edges the executors do.
        """
        edges: dict[str, set[str]] = {name: set() for name in self.slices}
        for entry in graph.entries():
            owner = self.slice_of(entry.id)
            if owner is None:
                continue
            for target in entry.depends_on:
                other = self.slice_of(target)
                if other is not None and other != owner:
                    edges.setdefault(owner, set()).add(other)
        return {name: tuple(sorted(deps)) for name, deps in sorted(edges.items())}

    def to_json(self) -> str:
        return json.dumps(
            {
                "project": self.project,
                "slices": {
                    name: {
                        "members": sorted((str(m) for m in sl.members)),
                        "type": sl.type,
                    }
                    for name, sl in sorted(self.slices.items())
                },
                "allocation": self.allocation,
                "modules": {
                    path: sorted(str(i) for i in ids)
                    for path, ids in sorted(self.modules.items())
                },
                "repo": self.repo,
                "labels": list(self.labels),
                "pickup": list(self.pickup),
                "delegation": self.delegation,
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, text: str) -> "Manifest":
        raw = json.loads(text)
        return cls(
            project=raw["project"],
            slices={
                name: Slice(
                    name=name,
                    members={parse(m) for m in body.get("members", [])},
                    type=body.get("type", SLICE),
                )
                for name, body in raw.get("slices", {}).items()
            },
            allocation=dict(raw.get("allocation", {})),
            modules={
                path: {parse(i) for i in ids}
                for path, ids in raw.get("modules", {}).items()
            },
            repo=raw.get("repo") or None,
            labels=tuple(raw.get("labels") or ()),
            pickup=tuple(raw.get("pickup") or ()),
            delegation=raw.get("delegation") if isinstance(raw.get("delegation"), dict) else None,
        )


# -- the store --------------------------------------------------------------


def _clean_labels(labels) -> tuple[str, ...]:
    """Trimmed, de-duplicated, and refused here if GitHub would refuse it."""
    if labels is None:
        return ()
    if isinstance(labels, str) or not isinstance(labels, (list, tuple)):
        raise ValueError("'labels' must be a list of strings")
    seen: list[str] = []
    for raw in labels:
        if not isinstance(raw, str):
            raise ValueError("'labels' must be a list of strings")
        text = raw.strip()
        if not text:
            raise ValueError("a label cannot be empty")
        if len(text) > 50:
            # GitHub's ceiling. Refused here so it is refused once,
            # not once per issue.
            raise ValueError(f"{text!r} is longer than 50 characters")
        if text not in seen:
            seen.append(text)
    return tuple(seen)


class ProjectStore:
    """Every filesystem access in this unit goes through here. Nothing else
    opens a file, which is what makes the backing store swappable later."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        # Held across a write that spans files (`append`), and by any reader
        # that must see the project whole (a cut). Re-entrant, so a holder
        # may go on calling the store. One process owns the store -- the
        # server binds its port exclusively -- so a thread lock is enough.
        # ponytail: one lock per store, not per project; split it if
        # projects ever contend.
        self.lock = threading.RLock()
        # A hard error, not a degrade: a seeding run aimed at the live store
        # writes entries nobody asked for and burns identifiers for good.
        resolved = self._root.resolve()
        for refused in filter(None, os.environ.get(REFUSE_ROOTS_ENV, "").split(os.pathsep)):
            if resolved.is_relative_to(Path(refused).resolve()):
                raise ValueError(
                    f"the store refuses {self._root}: it is inside {refused}, "
                    f"which {REFUSE_ROOTS_ENV} marks as holding real projects"
                )

    # -- projects -----------------------------------------------------------

    def list_projects(self) -> list[str]:
        if not self._root.exists():
            return []
        return sorted(
            p.name for p in self._root.iterdir() if (p / MANIFEST_FILE).exists()
        )

    def _project_dir(self, project: str) -> Path:
        path = self._root / project
        if not (path / MANIFEST_FILE).exists():
            raise UnknownProject(project)
        return path

    def create_project(self, project: str) -> None:
        path = self._root / project
        if (path / MANIFEST_FILE).exists():
            raise FileExistsError(f"project {project!r} already exists")
        for sub in (*LAYER_DIRS.values(), "history"):
            (path / sub).mkdir(parents=True, exist_ok=True)
        (path / INTENT_FILE).write_text("", encoding="utf-8")
        (path / TESTS_FILE).write_text("", encoding="utf-8")
        self._write_manifest(path, Manifest(project=project))

    # -- manifest -----------------------------------------------------------

    def load_manifest(self, project: str) -> Manifest:
        path = self._project_dir(project)
        if (path / PENDING_FILE).exists():
            self._recover(project, path)
        return Manifest.from_json((path / MANIFEST_FILE).read_text(encoding="utf-8"))

    def _recover(self, project: str, path: Path) -> None:
        """Finish or discard an `append` that died between its entry write
        and its manifest write. Deterministic: the journal names the
        identifiers it wrote, and whether they are on disk is a fact."""
        with self.lock:
            pending = path / PENDING_FILE
            if not pending.exists():  # another thread finished it first
                return
            journal = json.loads(pending.read_text(encoding="utf-8"))
            on_disk = {e.id for e in self.load_all(project)}
            if all(parse(i) in on_disk for i in journal["ids"]):
                (path / MANIFEST_FILE).write_text(journal["manifest"], encoding="utf-8")
            pending.unlink()

    def _write_manifest(self, path: Path, manifest: Manifest) -> None:
        (path / MANIFEST_FILE).write_text(manifest.to_json(), encoding="utf-8")

    def save_manifest(self, project: str, manifest: Manifest) -> None:
        self._write_manifest(self._project_dir(project), manifest)

    def set_repo(self, project: str, repo: str | None) -> str | None:
        """Point this project's tickets at a repository, or clear it.

        `None` clears rather than a second method: set in error and set to
        nothing are the same operation on one field. Returns what was stored,
        which is the normalised form and not necessarily what was passed.
        """
        manifest = self.load_manifest(project)
        manifest.repo = normalise_repo(repo) if repo is not None else None
        self.save_manifest(project, manifest)
        return manifest.repo

    def set_labels(self, project: str, labels, pickup=None) -> tuple[str, ...]:
        """Replace the labels added to every issue from this project.

        Replaces rather than merges, and an empty list clears: set in error
        and set to nothing are the same operation on one field, exactly as
        with `repo`.

        Validated, because a label GitHub rejects fails the issue it was
        attached to and not the call that set it -- which would surface as a
        422 several hundred writes into an emission, naming the unit rather
        than the configuration that actually broke it.

        `pickup` is validated the same way; `None` leaves it as it was.
        """
        manifest = self.load_manifest(project)
        manifest.labels = _clean_labels(labels)
        if pickup is not None:
            manifest.pickup = _clean_labels(pickup)
        self.save_manifest(project, manifest)
        return manifest.labels

    def set_delegation(self, project: str, delegation: dict) -> dict:
        """Replace this project's delegation (M33). A grant and a revoke are
        the same write on one field, as with `repo`."""
        manifest = self.load_manifest(project)
        manifest.delegation = dict(delegation)
        self.save_manifest(project, manifest)
        return manifest.delegation

    # -- identifier allocation ----------------------------------------------

    def allocate(self, project: str, layer: str) -> Identifier:
        """Hand out the next identifier for a layer and record it. The mark
        only ever moves up: this is where "never reused" is actually
        enforced, rather than merely documented."""
        if layer not in ALL_LAYERS:
            raise ValueError(f"unknown layer {layer!r}")
        manifest = self.load_manifest(project)
        nxt = manifest.allocation.get(layer, 0) + 1
        manifest.allocation[layer] = nxt
        self.save_manifest(project, manifest)
        return Identifier(layer=layer, number=nxt)

    def set_module(self, project: str, path: str, implements: list[str]) -> None:
        """Declare which spec entries -- or which test entries -- a module
        implements.

        Replaces rather than merges: a module that has stopped implementing
        something must be able to say so, or the write set keeps handing out
        files nobody needs. Declaring nothing removes it.

        Every identifier is checked against the live graph, and must be at
        spec or at test. The layers above are refused: code implements spec,
        and a module claiming an architecture entry has skipped the layer
        that says how, so the backlink would point at a decision rather than
        an instruction.

        **A module implements spec entries or test entries, never both**, and
        this is the rule the whole test separation rests on. The design's
        claim is that "the agent implementing a spec entry may read the test
        files and may never write them" needs no enforcement, because an
        implementation unit and a test unit are anchored on different kinds
        and so are always different units -- different branch, different
        agent. What that buys is only as good as the separation of their
        write sets. One mixed file lands in both, and the agent building
        `S-13` is handed a path the agent building `S-13-T` owns; a
        structural guarantee quietly degrades into an honour-system rule.
        Overlap between two implementation units is ordinary and expected
        (§4a); overlap across the kinds is the thing this refusal prevents.

        There is no stored `kind`. What a module is follows from the
        identifiers it claims, and a field would be a second statement of
        exactly that.
        """
        if not isinstance(path, str) or not path.strip():
            raise ValueError("a module path is required")
        graph = self.load_graph(project)
        parsed: set[Identifier] = set()
        for raw in implements or []:
            try:
                identifier = parse(str(raw))
            except InvalidIdentifier as exc:
                raise ValueError(str(exc)) from exc
            if identifier.layer not in IMPLEMENTED:
                raise ValueError(
                    f"{identifier} is at {identifier.layer_name}; a module "
                    "implements spec entries or test entries, not the layers "
                    "above them"
                )
            if identifier not in graph:
                raise ValueError(f"{identifier} does not exist")
            parsed.add(identifier)

        kinds = {i.layer for i in parsed}
        if len(kinds) > 1:
            raise ValueError(
                f"{path.strip()} claims both spec and test entries. A module "
                "implements one or the other: a test module and an "
                "implementation module share no entry, which is what puts "
                "them in different work units and so on different branches. "
                "A file claiming both merges those units and the separation "
                "between writing a test and writing the code it judges stops "
                "being structural"
            )

        with self.lock:  # or an append's re-pointing could overwrite this
            manifest = self.load_manifest(project)
            if parsed:
                manifest.modules[path.strip()] = parsed
            else:
                manifest.modules.pop(path.strip(), None)
            self.save_manifest(project, manifest)

    # -- entry files --------------------------------------------------------

    def file_for(self, project: str, layer: str, slice_name: str | None) -> Path:
        """Where an entry of this layer would be written. Public so a caller
        can find out that a write is impossible *before* anything is
        committed -- identifiers are allocated first and cannot be handed
        back."""
        return self._file_for(project, layer, slice_name)

    def _file_for(self, project: str, layer: str, slice_name: str | None) -> Path:
        path = self._project_dir(project)
        if layer == INTENT:
            return path / INTENT_FILE
        if layer == TEST:
            return path / TESTS_FILE
        if not slice_name:
            if layer == BEHAVIOUR:
                return path / LAYER_DIRS[layer] / HOLDING_FILE
            raise ValueError(f"layer {layer!r} is sliced -- a slice name is required")
        return path / LAYER_DIRS[layer] / f"{slice_name}.jsonl"

    def _read_file(self, path: Path) -> list[Entry]:
        if not path.exists():
            return []
        return parse_entries(path.read_text(encoding="utf-8"))

    def append(
        self, project: str, entries: list[Entry], slice_name: str | None = None
    ) -> list[dict]:
        """Append-only: existing entries in the target file are read, the new
        ones added, and the whole file rewritten. Nothing is ever removed.

        A module implementing an entry one of these supersedes is re-pointed
        to the successor in the same write (M14). The manifest is this
        unit's own, so that is mechanical -- unlike a test or a dependent
        pointing at the retired entry, which stay as written. Returns each
        re-pointed module as `{module, was, now}`.

        Two files change, so the write is journalled: the manifest-to-be is
        written first, then the entries, then the manifest, then the journal
        is removed. A death anywhere in between is finished or discarded by
        the next `load_manifest` (`_recover`), and the lock keeps a reader in
        this process from seeing the middle.
        """
        if not entries:
            return []
        with self.lock:
            existing_ids = {e.id for e in self.load_all(project)}
            for entry in entries:
                if entry.id in existing_ids:
                    raise ValueError(
                        f"identifier {entry.id} already exists in {project}"
                    )

            by_file: dict[Path, list[Entry]] = {}
            for entry in entries:
                path = self._file_for(project, entry.id.layer, slice_name)
                by_file.setdefault(path, []).append(entry)
            merged = {path: self._read_file(path) + new for path, new in by_file.items()}

            manifest = self.load_manifest(project)
            if slice_name:
                sl = manifest.slices.setdefault(slice_name, Slice(name=slice_name))
                # A test entry never joins a slice. Its column is its parent
                # spec entry's, derived on demand; a stored membership would
                # be the second copy that goes stale.
                sl.members.update(e.id for e in entries if e.id.layer != TEST)
            moved = {e.supersedes: e.id for e in entries if e.supersedes is not None}
            repointed = []
            for module, ids in sorted(manifest.modules.items()):
                if ids & moved.keys():
                    now = {moved.get(i, i) for i in ids}
                    repointed.append({
                        "module": module,
                        "was": [str(i) for i in sorted(ids, key=sort_key)],
                        "now": [str(i) for i in sorted(now, key=sort_key)],
                    })
                    manifest.modules[module] = now

            project_dir = self._project_dir(project)
            pending = project_dir / PENDING_FILE
            pending.write_text(json.dumps({
                "ids": [str(e.id) for e in entries],
                "manifest": manifest.to_json(),
            }), encoding="utf-8")
            for path, rows in merged.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(render_entries(rows), encoding="utf-8")
            self._write_manifest(project_dir, manifest)
            pending.unlink()
            return repointed

    def settle_holding(self, project: str) -> list[Identifier]:
        """Move behaviour out of the holding file into the slice that now owns it.

        Behaviour is written before slicing exists, so it lands in
        `_unassigned.jsonl`. Ratification gives those entries an owner, and
        until this ran it gave them nothing else: the entry stayed in the
        holding file while the manifest said it belonged to a slice, so the
        tree and the manifest disagreed about where it lived.

        That was survivable only for as long as nothing rewrote a slice's
        files from membership. `split_slice` does exactly that, and wrote a
        second copy into the slice file while the first sat in the holding
        file -- at which point `load_all` returns the identifier twice and the
        graph refuses to load at all. Not a bad slice and not a bad split: the
        project simply stops opening.

        Idempotent, and safe to call when there is nothing to move.
        """
        path = self._project_dir(project) / LAYER_DIRS[BEHAVIOUR] / HOLDING_FILE
        held = self._read_file(path)
        if not held:
            return []
        manifest = self.load_manifest(project)
        staying: list[Entry] = []
        moving: dict[str, list[Entry]] = {}
        for entry in held:
            owner = manifest.slice_of(entry.id)
            if owner is None:
                staying.append(entry)
            else:
                moving.setdefault(owner, []).append(entry)
        if not moving:
            return []
        for name, entries in moving.items():
            target = self._file_for(project, BEHAVIOUR, name)
            existing = self._read_file(target)
            have = {e.id for e in existing}
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                render_entries(existing + [e for e in entries if e.id not in have]),
                encoding="utf-8",
            )
        path.write_text(render_entries(staying), encoding="utf-8")
        return [e.id for group in moving.values() for e in group]

    def load_all(self, project: str) -> list[Entry]:
        path = self._project_dir(project)
        entries = self._read_file(path / INTENT_FILE)
        entries.extend(self._read_file(path / TESTS_FILE))
        for layer_dir in LAYER_DIRS.values():
            directory = path / layer_dir
            if directory.exists():
                for file in sorted(directory.glob("*.jsonl")):
                    entries.extend(self._read_file(file))
        return entries

    def load_graph(self, project: str) -> Graph:
        return Graph(self.load_all(project))

    # -- inbox --------------------------------------------------------------

    def inbox_path(self) -> Path:
        """One inbox for the whole unit, not per project: an `initiate`
        message has no project yet by definition."""
        return self._root / "inbox.jsonl"

    # -- issues -------------------------------------------------------------

    def root(self) -> Path:
        """Where the gateway writes peers.json and the delivery policy. The
        only reason anything outside this module needs the path."""
        return self._root

    def proposal_path(self, project: str) -> Path:
        return self._project_dir(project) / PROPOSAL_FILE

    def units_path(self, project: str) -> Path:
        """The log of work-unit cuts. One line per cut, append-only.

        A cut is computed, not authored -- but it is *materialised* when a
        person decides the corpus is finished, which is not the same moment
        as the gates going green. Storing it is what makes a later
        projection comparable with the one work was handed out from."""
        return self._project_dir(project) / UNITS_FILE

    def emissions_path(self, project: str) -> Path:
        """The log of emission runs: which unit became which issue, and which
        orders were declared.

        Beside the cut log, because it answers the other half of the same
        question. A cut says what the work was when it went out; this says
        where it went."""
        return self._project_dir(project) / EMISSIONS_FILE

    def events_path(self) -> Path:
        """The lifecycle log. Alongside the other two and never loaded during
        ordinary work -- reading it is analysis, not operation."""
        return self._root / "lifecycle.jsonl"

    def issues_path(self) -> Path:
        """The internal queue, alongside the inbox and deliberately separate
        from it. The inbox is what the outside world wants; this is what one
        part of the pipeline needs from another. Conflating them would put a
        request nobody outside ever made into the queue a human reads."""
        return self._root / "issues.jsonl"

    # -- slices -------------------------------------------------------------

    def split_slice(
        self, project: str, source: str, target: str, moving: set[Identifier]
    ) -> None:
        """Move identifiers out of a slice into a NEW one.

        Slices split; they never merge. Targeting an existing slice is
        refused for exactly that reason -- merging destroys identifier
        locality, and there is deliberately no API for it.

        Nothing is renumbered. Membership moves; identifiers do not, so every
        historical reference still resolves.
        """
        manifest = self.load_manifest(project)
        if source not in manifest.slices:
            raise ValueError(f"unknown slice {source!r}")
        if target in manifest.slices:
            raise ValueError(
                f"slice {target!r} already exists -- slices split, never merge"
            )
        src = manifest.slices[source]
        if not moving <= src.members:
            raise ValueError(f"identifiers not in slice {source!r}: {moving - src.members}")

        src.members -= moving
        manifest.slices[target] = Slice(name=target, members=set(moving))
        self.save_manifest(project, manifest)

        # Rewrite the affected files so each entry sits in its slice's file.
        #
        # Only the layers something MOVES in. Rewriting every layer from
        # membership wrote dark's B-43 and B-45 -- legibility members still
        # sitting in the behaviour holding file -- into behaviour/legibility.jsonl
        # beside their originals, when the split moved two SPEC entries, and
        # the graph refused to load: "identifier B-43 used twice".
        entries = {e.id: e for e in self.load_all(project)}
        for layer in {i.layer for i in moving} & set(LAYER_DIRS):
            if layer == BEHAVIOUR:
                # Whatever this rewrite places in a slice file leaves the
                # holding file, or it is held twice.
                placed = manifest.slices[source].members | moving
                held = self._file_for(project, layer, None)
                if held.exists():
                    held.write_text(render_entries(
                        [e for e in self._read_file(held) if e.id not in placed]
                    ), encoding="utf-8")
            for name in (source, target):
                members = [
                    entries[i]
                    for i in manifest.slices[name].members
                    if i in entries and i.layer == layer
                ]
                path = self._file_for(project, layer, name)
                if members or path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(render_entries(members), encoding="utf-8")
