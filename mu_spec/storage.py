"""The only module that touches the filesystem.

Two responsibilities, kept together because they are two halves of the same
thing: the on-disk *format* of an entry, and the *layout* of a project.

Layout, per the design doc:

    <root>/<project>/
      manifest.json          slices, membership sets, declared dependencies,
                             and the identifier high-water marks
      intent.md              intent is not sliced -- short by nature, everyone
                             reads it
      behaviour/<slice>.md   one file per slice per layer. Not one file per
      architecture/<slice>.md  entry (per-file overhead kills you at fifty
      spec/<slice>.md          reads); not one file per layer (large systems
                               drown the context)
      history/               amendment log, never loaded by default

The manifest is JSON rather than the design doc's markdown: it is the unit's
own bookkeeping -- membership sets and high-water marks it maintains and
must parse exactly -- not human prose. Everything a human reads or edits
stays markdown.
"""

from __future__ import annotations

import dataclasses
import json
import re
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
INTENT_FILE = "intent.md"
MANIFEST_FILE = "manifest.json"
CROSS_CUTTING = "cross-cutting"

# "## B·14 · A buyer can search listings"
_HEADING = re.compile(r"^##\s+(\S+)\s+·\s+(.+?)\s*$")
_META = re.compile(r"^(derives-from|supersedes):\s*(.*?)\s*$")


class MalformedEntryFile(ValueError):
    """A file could not be parsed. A hard error, never a skipped entry: a
    silently dropped entry means the graph comes back missing an edge, and a
    missing edge is the failure this unit exists to prevent."""


class UnknownProject(KeyError):
    pass


# -- the file format --------------------------------------------------------


def parse_entries(text: str) -> list[Entry]:
    """Parse every entry in one file. Content before the first heading is
    ignored -- files carry a human-facing title line, and it is not an
    entry."""
    entries: list[Entry] = []
    current: dict | None = None
    body: list[str] = []

    def flush() -> None:
        if current is not None:
            entries.append(
                Entry(
                    id=current["id"],
                    derives_from=current["derives_from"],
                    title=current["title"],
                    body="\n".join(body).strip() + "\n" if body else "",
                    supersedes=current["supersedes"],
                )
            )

    for line in text.splitlines():
        heading = _HEADING.match(line)
        if heading:
            flush()
            body = []
            try:
                identifier = parse(heading.group(1))
            except InvalidIdentifier as exc:
                raise MalformedEntryFile(str(exc)) from exc
            current = {
                "id": identifier,
                "title": heading.group(2),
                "derives_from": (),
                "supersedes": None,
            }
            continue

        if current is None:
            # Preamble: a human-facing file title, notes. Not an entry.
            if line.startswith("## "):
                raise MalformedEntryFile(f"heading without a title separator: {line!r}")
            continue

        meta = _META.match(line) if not body else None
        if meta:
            key, raw = meta.group(1), meta.group(2)
            try:
                if key == "derives-from":
                    current["derives_from"] = tuple(
                        parse(p.strip()) for p in raw.split(",") if p.strip()
                    )
                else:
                    current["supersedes"] = parse(raw) if raw else None
            except InvalidIdentifier as exc:
                raise MalformedEntryFile(f"in {current['id']}: {exc}") from exc
            continue

        if line.strip() or body:
            body.append(line)

    if current is None and "## " in text:
        raise MalformedEntryFile("heading without a title separator")
    flush()
    return entries


def render_entries(entries: list[Entry], header: str = "") -> str:
    """Serialise entries back to the file format. Round-trips with
    parse_entries."""
    out: list[str] = [f"# {header}\n"] if header else []
    for entry in sorted(entries, key=lambda e: sort_key(e.id)):
        out.append(f"## {entry.id} · {entry.display_title}")
        if entry.derives_from:
            out.append(
                "derives-from: " + ", ".join(str(d) for d in entry.derives_from)
            )
        if entry.supersedes is not None:
            out.append(f"supersedes: {entry.supersedes}")
        out.append("")
        if entry.body.strip():
            out.append(entry.body.strip())
            out.append("")
    return "\n".join(out)


# -- the manifest -----------------------------------------------------------


@dataclasses.dataclass
class Slice:
    name: str
    # A SET, never a range. Identifiers encode layer and creation order only,
    # never slice, so a slice's members are scattered across the number line
    # and a gap is the ordinary case. This is what lets a slice split without
    # renumbering anything.
    members: set[Identifier] = dataclasses.field(default_factory=set)
    depends_on: tuple[str, ...] = ()


@dataclasses.dataclass
class Manifest:
    project: str
    slices: dict[str, Slice] = dataclasses.field(default_factory=dict)
    # High-water mark per layer. Only ever moves up -- superseding an entry
    # retires it but never frees its number, because every historical
    # reference must keep meaning what it meant.
    allocation: dict[str, int] = dataclasses.field(default_factory=dict)

    def slice_of(self, identifier: Identifier) -> str | None:
        for name, sl in self.slices.items():
            if identifier in sl.members:
                return name
        return None

    def to_json(self) -> str:
        return json.dumps(
            {
                "project": self.project,
                "slices": {
                    name: {
                        "members": sorted((str(m) for m in sl.members)),
                        "depends_on": list(sl.depends_on),
                    }
                    for name, sl in sorted(self.slices.items())
                },
                "allocation": self.allocation,
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
                    depends_on=tuple(body.get("depends_on", [])),
                )
                for name, body in raw.get("slices", {}).items()
            },
            allocation=dict(raw.get("allocation", {})),
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
        (path / INTENT_FILE).write_text(
            f"# intent — {project}\n", encoding="utf-8"
        )
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

    # -- entry files --------------------------------------------------------

    def _file_for(self, project: str, layer: str, slice_name: str | None) -> Path:
        path = self._project_dir(project)
        if layer == INTENT_LAYER:
            return path / INTENT_FILE
        if not slice_name:
            raise ValueError(f"layer {layer!r} is sliced -- a slice name is required")
        return path / LAYER_DIRS[layer] / f"{slice_name}.md"

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
            path.write_text(
                render_entries(merged, header=path.stem), encoding="utf-8"
            )

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
                for file in sorted(directory.glob("*.md")):
                    entries.extend(self._read_file(file))
        return entries

    def load_graph(self, project: str) -> Graph:
        return Graph(self.load_all(project))

    # -- comments -----------------------------------------------------------

    def comments_path(self, project: str) -> Path:
        """Comments are annotations, not entries -- they never enter the
        graph, so they live in their own append-only log rather than in any
        layer's file."""
        return self._project_dir(project) / "comments.jsonl"

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
                    path.write_text(
                        render_entries(members, header=path.stem), encoding="utf-8"
                    )
