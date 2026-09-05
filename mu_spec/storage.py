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
from pathlib import Path

from mu_spec.graph import Entry, Graph
from mu_spec.identifiers import (
    LAYERS,
    Identifier,
    InvalidIdentifier,
    parse,
    sort_key,
)

INTENT_LAYER = "I"
LAYER_DIRS = {"B": "behaviour", "A": "architecture", "S": "spec"}
INTENT_FILE = "intent.jsonl"
MANIFEST_FILE = "manifest.json"

# Slice types. Cross-cutting is a TYPE, not a reserved slice name: audit
# logging deserves its own architecture and its own specs, and is as complex
# as any slice. There can be several, each with a full column at every layer,
# stored exactly like everything else. What distinguishes one is which edges
# are legal and whose context it lands in -- not where it is filed.
SLICE = "slice"
CROSS_CUTTING = "cross_cutting"
SLICE_TYPES = (SLICE, CROSS_CUTTING)


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
                body=str(raw.get("body", "")),
                supersedes=(
                    _ids([supersedes], "supersedes", where)[0]
                    if supersedes is not None
                    else None
                ),
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
        if entry.body:
            record["body"] = entry.body
        if entry.supersedes is not None:
            record["supersedes"] = str(entry.supersedes)
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

    def cross_cutting(self) -> tuple[str, ...]:
        """Every cross-cutting slice, in name order. These land in every
        slice's context whether or not anything declared a dependency on
        them: their behaviour ranges over other slices rather than naming a
        subject, so requiring n identical declarations would fill the
        dependency graph with edges that are always true and carry no
        signal."""
        return tuple(
            sorted(n for n, sl in self.slices.items() if sl.type == CROSS_CUTTING)
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
        )


# -- the store --------------------------------------------------------------


class ProjectStore:
    """Every filesystem access in this unit goes through here. Nothing else
    opens a file, which is what makes the backing store swappable later."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

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
        for sub in ("behaviour", "architecture", "spec", "history"):
            (path / sub).mkdir(parents=True, exist_ok=True)
        (path / INTENT_FILE).write_text("", encoding="utf-8")
        self._write_manifest(path, Manifest(project=project))

    # -- manifest -----------------------------------------------------------

    def load_manifest(self, project: str) -> Manifest:
        return Manifest.from_json(
            (self._project_dir(project) / MANIFEST_FILE).read_text(encoding="utf-8")
        )

    def _write_manifest(self, path: Path, manifest: Manifest) -> None:
        (path / MANIFEST_FILE).write_text(manifest.to_json(), encoding="utf-8")

    def save_manifest(self, project: str, manifest: Manifest) -> None:
        self._write_manifest(self._project_dir(project), manifest)

    # -- identifier allocation ----------------------------------------------

    def allocate(self, project: str, layer: str) -> Identifier:
        """Hand out the next identifier for a layer and record it. The mark
        only ever moves up: this is where "never reused" is actually
        enforced, rather than merely documented."""
        if layer not in LAYERS:
            raise ValueError(f"unknown layer {layer!r}")
        manifest = self.load_manifest(project)
        nxt = manifest.allocation.get(layer, 0) + 1
        manifest.allocation[layer] = nxt
        self.save_manifest(project, manifest)
        return Identifier(layer=layer, number=nxt)

    def set_module(self, project: str, path: str, implements: list[str]) -> None:
        """Declare which spec entries a module implements.

        Replaces rather than merges: a module that has stopped implementing
        something must be able to say so, or the write set keeps handing out
        files nobody needs. Declaring nothing removes it.

        Every identifier is checked against the live graph, and must be at
        spec. Code implements spec; a module claiming an architecture entry
        has skipped the layer that says how, and the backlink would point at
        a decision rather than an instruction.
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
            if identifier.layer != "S":
                raise ValueError(
                    f"{identifier} is at {identifier.layer_name}; a module "
                    "implements spec entries, not the layers above them"
                )
            if identifier not in graph:
                raise ValueError(f"{identifier} does not exist")
            parsed.add(identifier)

        manifest = self.load_manifest(project)
        if parsed:
            manifest.modules[path.strip()] = parsed
        else:
            manifest.modules.pop(path.strip(), None)
        self.save_manifest(project, manifest)

    def set_slice_type(self, project: str, slice_name: str, slice_type: str) -> None:
        """Mark a slice as cross-cutting, or back to ordinary. A classification
        the agent proposes and a human rules on -- the unit only records it,
        and refuses a value that is not one of the two."""
        if slice_type not in SLICE_TYPES:
            raise ValueError(
                f"unknown slice type {slice_type!r}, expected one of {SLICE_TYPES}"
            )
        manifest = self.load_manifest(project)
        if slice_name not in manifest.slices:
            raise ValueError(f"unknown slice {slice_name!r}")
        manifest.slices[slice_name].type = slice_type
        self.save_manifest(project, manifest)

    # -- entry files --------------------------------------------------------

    def _file_for(self, project: str, layer: str, slice_name: str | None) -> Path:
        path = self._project_dir(project)
        if layer == INTENT_LAYER:
            return path / INTENT_FILE
        if not slice_name:
            raise ValueError(f"layer {layer!r} is sliced -- a slice name is required")
        return path / LAYER_DIRS[layer] / f"{slice_name}.jsonl"

    def _read_file(self, path: Path) -> list[Entry]:
        if not path.exists():
            return []
        return parse_entries(path.read_text(encoding="utf-8"))

    def append(
        self, project: str, entries: list[Entry], slice_name: str | None = None
    ) -> None:
        """Append-only: existing entries in the target file are read, the new
        ones added, and the whole file rewritten. Nothing is ever removed."""
        if not entries:
            return
        existing_ids = {e.id for e in self.load_all(project)}
        for entry in entries:
            if entry.id in existing_ids:
                raise ValueError(f"identifier {entry.id} already exists in {project}")

        by_file: dict[Path, list[Entry]] = {}
        for entry in entries:
            path = self._file_for(project, entry.id.layer, slice_name)
            by_file.setdefault(path, []).append(entry)

        for path, new in by_file.items():
            merged = self._read_file(path) + new
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(render_entries(merged), encoding="utf-8")

        if slice_name:
            manifest = self.load_manifest(project)
            sl = manifest.slices.setdefault(slice_name, Slice(name=slice_name))
            sl.members.update(e.id for e in entries)
            self.save_manifest(project, manifest)

    def load_all(self, project: str) -> list[Entry]:
        path = self._project_dir(project)
        entries = self._read_file(path / INTENT_FILE)
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
        entries = {e.id: e for e in self.load_all(project)}
        for layer in LAYER_DIRS:
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
