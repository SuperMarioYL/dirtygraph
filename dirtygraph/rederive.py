"""Re-derive ONLY the dirty subgraph.

Once :mod:`dirtygraph.dirty` has marked the relation-edge dirty closure, this
module replays just those nodes — in topological order, so a node is recomputed
only after the nodes it depends on — through a pluggable adapter. Clean nodes
are never touched. After a node is successfully re-derived its dirty bit is
cleared and its source hash is re-stamped, so a second ``rederive`` with no new
edits re-derives zero nodes.

The adapter contract is structural (a ``re_derive(node) -> content`` callable);
the shipped implementations live in :mod:`dirtygraph.adapters`. Keeping the
contract structural here means the engine has no hard import dependency on the
adapter package and can be driven by any callable in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

from .depgraph import DepGraph
from .store import SidecarEntry, Store

__all__ = [
    "Adapter",
    "NodeView",
    "RederiveResult",
    "rederive",
    "callable_adapter",
]


@dataclass(slots=True)
class NodeView:
    """The read-only slice of a node handed to an adapter.

    Adapters re-derive content from this; they never mutate the store directly.
    ``source_path`` is graph-relative as recorded; ``resolved_path`` is the
    absolute on-disk path the engine already resolved against the graph root.
    """

    node_id: str
    source_path: str
    resolved_path: str
    label: Optional[str] = None
    dirty: bool = True

    @classmethod
    def from_entry(cls, entry: SidecarEntry, resolved_path: str) -> "NodeView":
        return cls(
            node_id=entry.node_id,
            source_path=entry.source_path,
            resolved_path=resolved_path,
            label=entry.label,
            dirty=entry.dirty,
        )


@runtime_checkable
class Adapter(Protocol):
    """Anything that can recompute a node's derived content.

    The shipped ``dirtygraph.adapters.base.Adapter`` satisfies this Protocol.
    ``re_derive`` returns the new derived content (a string/summary/etc.); the
    engine ignores the value's shape and only cares that it returned without
    raising. Raising marks the node as failed (its dirty bit stays set).
    """

    def re_derive(self, node: NodeView) -> Any:  # pragma: no cover - protocol
        ...


def callable_adapter(fn: Callable[[NodeView], Any]) -> Adapter:
    """Wrap a bare ``fn(node) -> content`` into an :class:`Adapter`.

    Lets tests and the default echo path drive re-derivation with a plain
    function instead of a class.
    """

    class _Wrapped:
        def re_derive(self, node: NodeView) -> Any:
            return fn(node)

    return _Wrapped()


@dataclass(slots=True)
class RederiveResult:
    """Outcome of a re-derivation pass — the source of the benchmark line.

    ``rederived`` / ``total`` give the headline ``re-derived 4 nodes (of 1,203)``.
    ``failed`` maps node_id -> error string for nodes whose adapter raised.
    ``outputs`` keeps the adapter return value per node (in-memory only).
    """

    rederived: List[str] = field(default_factory=list)
    skipped_clean: int = 0
    total: int = 0
    failed: Dict[str, str] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)

    @property
    def rederived_count(self) -> int:
        return len(self.rederived)

    @property
    def failed_count(self) -> int:
        return len(self.failed)

    def headline(self) -> str:
        """e.g. ``re-derived 4 nodes (of 1,203)`` — the star-the-repo moment."""
        n = self.rederived_count
        word = "node" if n == 1 else "nodes"
        return f"re-derived {n:,} {word} (of {self.total:,})"


def rederive(
    store: Store,
    graph: DepGraph,
    adapter: Adapter,
    *,
    persist: bool = True,
    current_hashes: Optional[Dict[str, str]] = None,
) -> RederiveResult:
    """Re-derive the currently-dirty closure and clear it.

    Steps:
      1. Read the dirty node set from ``store`` (set by ``dirtygraph status`` /
         the ``watch`` loop via :func:`dirtygraph.dirty.mark_dirty`).
      2. Topologically order ONLY those nodes via the propagation graph, so a
         dependency is recomputed before its dependents.
      3. Call ``adapter.re_derive(NodeView)`` per node. On success, clear the
         node's dirty bit and re-stamp its source hash (checkpoint clean). On
         failure, record the error and leave the dirty bit set so a retry picks
         it up.
      4. Persist the store (unless ``persist=False``).

    Clean nodes are NEVER passed to the adapter — that is the whole product.
    Returns a :class:`RederiveResult` carrying the before/after benchmark.

    ``current_hashes`` — when the caller (e.g. the CLI) already hashed the
    tree once for change detection, pass that dict here so the engine does NOT
    re-hash the whole tree a second time. ``None`` (default) hashes on demand,
    preserving the standalone API.
    """
    dirty_ids = set(store.dirty_nodes())
    total = store.total

    if not dirty_ids:
        return RederiveResult(rederived=[], skipped_clean=total, total=total, order=[])

    # Order only the dirty subgraph. Nodes the propagation graph doesn't know
    # about (isolated sidecar nodes) are appended deterministically after the
    # ordered ones so they are still re-derived.
    in_graph = [n for n in dirty_ids if n in graph]
    ordered = graph.topo_order(in_graph)
    isolated = sorted(dirty_ids - set(ordered))
    order = ordered + isolated

    result = RederiveResult(total=total, order=list(order))

    # Hash once up front so every re-derived node checkpoints against the same
    # on-disk snapshot (and so two nodes sharing a file don't double-hash it).
    # Reuse the caller's snapshot when provided to avoid a second full-tree
    # hash (the CLI marks dirty AND re-derives in one pass).
    if current_hashes is None:
        current_hashes = store.compute_hashes()
    cleaned_paths: Dict[str, str] = {}

    for node_id in order:
        entry = store.get(node_id)
        if entry is None:
            continue
        resolved = str(store.resolve(entry.source_path))
        view = NodeView.from_entry(entry, resolved)
        try:
            output = adapter.re_derive(view)
        except Exception as exc:  # noqa: BLE001 - surface, don't crash the batch
            result.failed[node_id] = f"{type(exc).__name__}: {exc}"
            continue
        result.outputs[node_id] = output
        result.rederived.append(node_id)
        # Clear the dirty bit and remember the path's fresh hash to checkpoint.
        store.clear_dirty([node_id])
        new_hash = current_hashes.get(entry.source_path)
        if new_hash is not None:
            cleaned_paths[entry.source_path] = new_hash

    # Re-stamp hashes for every successfully re-derived source so the next
    # change-detection pass sees these files as clean (no-op on second run).
    if cleaned_paths:
        store.stamp_hashes(cleaned_paths)

    if persist:
        store.save()

    return result
