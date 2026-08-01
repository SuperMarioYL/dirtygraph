"""Persistent provenance sidecar for DirtyGraph.

The store owns the data the input graph does **not** carry: for every node it
records the single source-file it was derived from, DirtyGraph's own blake3
content hash of that file, and a one-bit dirty flag. This sidecar lives next to
the user's graph at ``.dirtygraph/state.json`` and is the single source of truth
for "has the file behind this node changed since we last derived it?".

Nothing here reads node/edge content from the input graph; that ingestion lives
in the adapters. The store only knows about ``node_id -> (source_path,
content_hash, dirty)`` rows plus a little provenance metadata.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional

try:  # blake3 is a hard dependency (see pyproject); guard only for a clean error.
    from blake3 import blake3 as _blake3
except ImportError as exc:  # pragma: no cover - exercised only without the dep
    _blake3 = None
    _BLAKE3_IMPORT_ERROR = exc
else:
    _BLAKE3_IMPORT_ERROR = None

__all__ = [
    "SidecarEntry",
    "Store",
    "STATE_DIRNAME",
    "STATE_FILENAME",
    "hash_file",
    "hash_bytes",
    "MissingSourceError",
]

# The sidecar always lives in this directory beside the graph the user pointed
# ``dirtygraph init`` at, mirroring the ``.git`` / ``.code-review-graph`` pattern.
STATE_DIRNAME = ".dirtygraph"
STATE_FILENAME = "state.json"

_STATE_FORMAT_VERSION = 1
_HASH_CHUNK = 1 << 20  # 1 MiB streaming chunk; large source files stay off-heap.

# Sentinel hash used when a node's source file does not exist on disk. A real
# blake3 digest is 64 hex chars, so this can never collide with a computed hash,
# and it lets a later re-appearance of the file register as a genuine change.
MISSING_HASH = "<missing>"


class MissingSourceError(FileNotFoundError):
    """Raised when a referenced source path cannot be read and missing files
    are not being tolerated."""


def hash_bytes(data: bytes) -> str:
    """Return the lowercase hex blake3 digest of ``data``."""
    if _blake3 is None:  # pragma: no cover - exercised only without the dep
        raise RuntimeError(
            "the 'blake3' package is required; install dirtygraph's dependencies "
            "(pip install dirtygraph)"
        ) from _BLAKE3_IMPORT_ERROR
    return _blake3(data).hexdigest()


def hash_file(path: os.PathLike[str] | str) -> str:
    """Stream ``path`` through blake3 and return its lowercase hex digest.

    Returns :data:`MISSING_HASH` when the file does not exist so callers can
    record "this node's source is currently absent" without crashing init.
    """
    p = Path(path)
    if not p.exists() or not p.is_file():
        return MISSING_HASH
    if _blake3 is None:  # pragma: no cover - exercised only without the dep
        raise RuntimeError(
            "the 'blake3' package is required; install dirtygraph's dependencies "
            "(pip install dirtygraph)"
        ) from _BLAKE3_IMPORT_ERROR
    hasher = _blake3()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_HASH_CHUNK), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


@dataclass(slots=True)
class SidecarEntry:
    """One row of the provenance sidecar.

    ``content_hash`` is DirtyGraph's OWN blake3 of the source file at init/last
    re-derive time. ``dirty`` is the single propagation bit. ``label`` is kept
    only for human-readable status output and is never used for invalidation.
    """

    node_id: str
    source_path: str
    content_hash: str = MISSING_HASH
    dirty: bool = False
    label: Optional[str] = None

    @property
    def source_missing(self) -> bool:
        return self.content_hash == MISSING_HASH

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Drop a null label to keep the on-disk JSON compact and stable.
        if d.get("label") is None:
            d.pop("label")
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SidecarEntry":
        return cls(
            node_id=str(data["node_id"]),
            source_path=str(data["source_path"]),
            content_hash=str(data.get("content_hash", MISSING_HASH)),
            dirty=bool(data.get("dirty", False)),
            label=data.get("label"),
        )


@dataclass(slots=True)
class Store:
    """In-memory view of the sidecar plus load/save to ``.dirtygraph/state.json``.

    The store is keyed by ``node_id``. A node may share a ``source_path`` with
    many other nodes (e.g. several functions in one file), which is exactly why
    the hash is tracked per source path, not per node — see
    :meth:`changed_paths`.
    """

    root: Path
    entries: Dict[str, SidecarEntry] = field(default_factory=dict)
    graph_path: Optional[str] = None
    graph_kind: Optional[str] = None
    format_version: int = _STATE_FORMAT_VERSION

    # ----- construction -----------------------------------------------------

    @classmethod
    def state_dir(cls, root: os.PathLike[str] | str) -> Path:
        return Path(root) / STATE_DIRNAME

    @classmethod
    def state_path(cls, root: os.PathLike[str] | str) -> Path:
        return cls.state_dir(root) / STATE_FILENAME

    @classmethod
    def exists(cls, root: os.PathLike[str] | str) -> bool:
        return cls.state_path(root).is_file()

    @classmethod
    def load(cls, root: os.PathLike[str] | str) -> "Store":
        """Read an existing sidecar. Raises ``FileNotFoundError`` if absent."""
        path = cls.state_path(root)
        if not path.is_file():
            raise FileNotFoundError(
                f"no DirtyGraph state at {path}; run 'dirtygraph init <graph>' first"
            )
        raw = json.loads(path.read_text(encoding="utf-8"))
        entries = {
            str(row["node_id"]): SidecarEntry.from_dict(row)
            for row in raw.get("entries", [])
        }
        return cls(
            root=Path(root),
            entries=entries,
            graph_path=raw.get("graph_path"),
            graph_kind=raw.get("graph_kind"),
            format_version=int(raw.get("format_version", _STATE_FORMAT_VERSION)),
        )

    # ----- persistence ------------------------------------------------------

    def save(self) -> Path:
        """Atomically persist the sidecar; returns the written path.

        No-op guard: when the on-disk file is already byte-identical to the
        payload we are about to write, skip the atomic replace entirely. This
        keeps mtimes stable on a "nothing changed" run and — critically —
        prevents the ``watch`` loop's save -> filesystem-event -> re-save
        thrash: an ``os.replace`` fires a watchdog event even when the content
        is unchanged, so without this guard the observer would re-arm on its
        own state write every debounce cycle.
        """
        path = self.state_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format_version": self.format_version,
            "graph_path": self.graph_path,
            "graph_kind": self.graph_kind,
            # Stable ordering keeps diffs small and the file reviewable.
            "entries": [
                self.entries[node_id].to_dict()
                for node_id in sorted(self.entries)
            ],
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)
        content = text + "\n"
        if path.is_file() and path.read_text(encoding="utf-8") == content:
            # Identical payload already on disk — do not touch it.
            return path
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)  # atomic on POSIX + Windows
        return path

    # ----- node access ------------------------------------------------------

    def __len__(self) -> int:
        return len(self.entries)

    def __contains__(self, node_id: object) -> bool:
        return node_id in self.entries

    def __iter__(self) -> Iterator[SidecarEntry]:
        return iter(self.entries.values())

    def __getitem__(self, node_id: str) -> SidecarEntry:
        return self.entries[node_id]

    def get(self, node_id: str) -> Optional[SidecarEntry]:
        return self.entries.get(node_id)

    @property
    def node_ids(self) -> Iterable[str]:
        return self.entries.keys()

    def add(
        self,
        node_id: str,
        source_path: str,
        *,
        content_hash: Optional[str] = None,
        dirty: bool = False,
        label: Optional[str] = None,
    ) -> SidecarEntry:
        """Insert or replace a node row. ``content_hash`` defaults to the
        sentinel so an init that hasn't hashed yet stays consistent."""
        entry = SidecarEntry(
            node_id=str(node_id),
            source_path=str(source_path),
            content_hash=content_hash if content_hash is not None else MISSING_HASH,
            dirty=dirty,
            label=label,
        )
        self.entries[entry.node_id] = entry
        return entry

    # ----- source-path views ------------------------------------------------

    def source_paths(self) -> set[str]:
        """The distinct source files this graph derives from."""
        return {e.source_path for e in self.entries.values()}

    def nodes_for_path(self, source_path: str) -> list[SidecarEntry]:
        """All node rows whose provenance is ``source_path``."""
        return [e for e in self.entries.values() if e.source_path == source_path]

    def recorded_hash(self, source_path: str) -> Optional[str]:
        """The hash DirtyGraph last recorded for ``source_path`` (any node that
        references it shares the same recorded hash), or ``None`` if unknown."""
        for e in self.entries.values():
            if e.source_path == source_path:
                return e.content_hash
        return None

    # ----- hashing / change detection --------------------------------------

    def resolve(self, source_path: str) -> Path:
        """Resolve a (possibly graph-relative) source path against the graph root."""
        p = Path(source_path)
        if p.is_absolute():
            return p
        return (self.root / p).resolve()

    def compute_hashes(self, *, tolerate_missing: bool = True) -> Dict[str, str]:
        """Hash every distinct source file once and return ``{path -> hash}``.

        Hashing per *path* (not per node) means a file referenced by 200 nodes
        is read from disk exactly once.
        """
        result: Dict[str, str] = {}
        for src in self.source_paths():
            digest = hash_file(self.resolve(src))
            if digest == MISSING_HASH and not tolerate_missing:
                raise MissingSourceError(f"source file not found: {src}")
            result[src] = digest
        return result

    def stamp_hashes(self, hashes: Dict[str, str]) -> None:
        """Write ``{path -> hash}`` onto every node sharing that path. Used by
        init and after a successful re-derive to checkpoint the clean state."""
        for node_id, entry in self.entries.items():
            if entry.source_path in hashes:
                self.entries[node_id] = replace(
                    entry, content_hash=hashes[entry.source_path]
                )

    def changed_paths(self, current: Optional[Dict[str, str]] = None) -> set[str]:
        """Return the set of source paths whose on-disk hash differs from the
        recorded one. A path whose hash is unchanged is never returned, so it
        cannot dirty its dependents (the core no-op guarantee)."""
        if current is None:
            current = self.compute_hashes()
        changed: set[str] = set()
        for src in self.source_paths():
            now = current.get(src, MISSING_HASH)
            was = self.recorded_hash(src)
            if was is None or now != was:
                changed.add(src)
        return changed

    # ----- dirty-bit management --------------------------------------------

    def mark_dirty(self, node_ids: Iterable[str]) -> int:
        """Set the dirty bit on the given nodes; returns how many flipped."""
        flipped = 0
        for node_id in node_ids:
            entry = self.entries.get(node_id)
            if entry is not None and not entry.dirty:
                self.entries[node_id] = replace(entry, dirty=True)
                flipped += 1
        return flipped

    def clear_dirty(self, node_ids: Iterable[str]) -> int:
        """Clear the dirty bit on the given nodes; returns how many flipped."""
        flipped = 0
        for node_id in node_ids:
            entry = self.entries.get(node_id)
            if entry is not None and entry.dirty:
                self.entries[node_id] = replace(entry, dirty=False)
                flipped += 1
        return flipped

    def dirty_nodes(self) -> list[str]:
        """Node ids whose dirty bit is currently set, in stable order."""
        return sorted(nid for nid, e in self.entries.items() if e.dirty)

    @property
    def dirty_count(self) -> int:
        return sum(1 for e in self.entries.values() if e.dirty)

    @property
    def total(self) -> int:
        return len(self.entries)

    def reset(self) -> int:
        """Re-baseline the sidecar as clean.

        Clears every dirty bit and re-stamps every source hash from the
        current on-disk content (so the sidecar reflects a known-good tree
        without editing any source file or re-reading the input graph).
        Returns the number of dirty bits that were cleared. Idempotent: a
        clean store resets to clean and clears zero bits.
        """
        cleared = self.clear_dirty(self.dirty_nodes())
        self.stamp_hashes(self.compute_hashes())
        return cleared
