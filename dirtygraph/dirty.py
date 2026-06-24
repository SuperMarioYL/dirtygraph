"""The invalidation primitive — *dirty-mark the derived subgraph*.

This is the heart of DirtyGraph. Given a :class:`~dirtygraph.store.Store`
(provenance + content hashes + dirty bits) and a
:class:`~dirtygraph.depgraph.DepGraph` (propagation edges), it answers one
question:

    "Which nodes are now stale, and only those?"

The algorithm, lifted straight from Make/Bazel and dropped onto an agent graph:

1. **Change detection** — hash every distinct source file with blake3 and
   compare to the recorded hash. A file whose hash is *unchanged* is ignored,
   so it can never dirty its dependents (the no-op guarantee).
2. **Direct hits** — the nodes whose ``source_path`` is one of the changed
   files. These are the only nodes a file edit directly invalidates.
3. **Forward closure** — walk the propagation edges forward from the direct
   hits; everything reachable is presumed to derive stale content.
4. **Mark** — set the dirty bit on the closure (and only the closure).

The result is the *relation-edge dirty closure*: changed ∪ reachable(changed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .depgraph import DepGraph
from .store import Store

__all__ = ["DirtyResult", "compute_dirty_closure", "mark_dirty"]


@dataclass(slots=True)
class DirtyResult:
    """The outcome of an invalidation pass.

    ``changed_paths``  – source files whose blake3 differs from the recorded one.
    ``direct``         – nodes directly derived from a changed file.
    ``closure``        – direct hits plus every node reachable downstream of them.
    ``total``          – total node count in the graph (for the "N of TOTAL" line).
    """

    changed_paths: Set[str] = field(default_factory=set)
    direct: Set[str] = field(default_factory=set)
    closure: Set[str] = field(default_factory=set)
    total: int = 0

    @property
    def dirty_count(self) -> int:
        return len(self.closure)

    @property
    def propagated(self) -> Set[str]:
        """Nodes pulled in *only* by edge propagation, not by a direct file
        change — useful for explaining why a node is dirty."""
        return self.closure - self.direct

    @property
    def is_clean(self) -> bool:
        return not self.closure

    def headline(self) -> str:
        """The one-line benchmark hook, e.g. ``4 dirty of 1,203``."""
        return f"{self.dirty_count:,} dirty of {self.total:,}"

    def ordered_closure(self) -> List[str]:
        """The closure as a stable, sorted list (deterministic CLI output)."""
        return sorted(self.closure)


def compute_dirty_closure(
    store: Store,
    graph: DepGraph,
    *,
    current_hashes: Optional[Dict[str, str]] = None,
) -> DirtyResult:
    """Compute the dirty closure WITHOUT mutating the store.

    Pure function: hashes the tree (or uses ``current_hashes`` if provided —
    handy for the ``watch`` loop, which already knows which path fired), finds
    the directly-changed nodes, and forward-walks the propagation graph.

    A changed file with no surviving node (its nodes were deleted from the
    graph) contributes nothing; a node whose file is unchanged is never in the
    result. Both guarantees fall out of set construction below.
    """
    if current_hashes is None:
        current_hashes = store.compute_hashes()

    changed_paths = store.changed_paths(current_hashes)

    direct: Set[str] = set()
    for path in changed_paths:
        for entry in store.nodes_for_path(path):
            if entry.node_id in graph:
                direct.add(entry.node_id)
            else:
                # Node exists in the sidecar but not the propagation graph
                # (isolated node with no relations). It is still its own
                # direct hit and belongs in the closure.
                direct.add(entry.node_id)

    # Forward-reachable closure over propagation edges. Nodes that the graph
    # doesn't know about are their own closure (no edges to walk).
    in_graph = {n for n in direct if n in graph}
    closure: Set[str] = set(direct)
    if in_graph:
        closure |= graph.reachable(in_graph)

    return DirtyResult(
        changed_paths=changed_paths,
        direct=direct,
        closure=closure,
        total=store.total,
    )


def mark_dirty(
    store: Store,
    graph: DepGraph,
    *,
    current_hashes: Optional[Dict[str, str]] = None,
    persist: bool = False,
) -> DirtyResult:
    """Compute the dirty closure and set the dirty bit on it in ``store``.

    This is the mutating entry point used by ``dirtygraph status`` (persist the
    flags so a later ``rederive`` sees them) and by the ``watch`` loop on each
    file event. Nodes outside the closure are left untouched — a node that was
    already dirty from a previous pass stays dirty (its file may still be
    unhashed/clean-pending until re-derived).

    Returns the :class:`DirtyResult` so the caller can print the benchmark line.
    """
    result = compute_dirty_closure(store, graph, current_hashes=current_hashes)
    store.mark_dirty(result.closure)
    if persist:
        store.save()
    return result
