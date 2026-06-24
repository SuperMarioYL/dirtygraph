"""The propagation graph.

DirtyGraph does not build its own dependency model. The input graph already
encodes *code relations* between nodes (graphify's top-level ``links``,
code-review-graph's ``edges`` table: CALLS / IMPORTS_FROM / INHERITS /
REFERENCES). DirtyGraph reinterprets each such relation edge as a *propagation
edge*: if a node's source file changes, every node reachable from it along these
edges is presumed to derive stale content.

``DepGraph`` is a thin, dirty-marking-flavoured wrapper over a
``networkx.DiGraph``. It owns exactly two operations the rest of the package
needs: the **forward-reachable closure** of a changed-node set, and a
**topological order** of any sub-set of nodes (so re-derivation visits a node
only after the nodes it depends on).
"""

from __future__ import annotations

from typing import Dict, Iterable, Iterator, List, Optional, Set, Tuple

try:
    import networkx as nx
except ImportError as exc:  # pragma: no cover - exercised only without the dep
    nx = None
    _NX_IMPORT_ERROR = exc
else:
    _NX_IMPORT_ERROR = None

__all__ = ["DepGraph", "EDGE_RELATION_KEY"]

# Edge attribute under which we stash the original code-relation kind
# (CALLS / IMPORTS_FROM / ...). Kept for status/debug; never affects propagation.
EDGE_RELATION_KEY = "relation"


def _require_nx():
    if nx is None:  # pragma: no cover - exercised only without the dep
        raise RuntimeError(
            "the 'networkx' package is required; install dirtygraph's "
            "dependencies (pip install dirtygraph)"
        ) from _NX_IMPORT_ERROR
    return nx


class DepGraph:
    """A directed propagation graph over the input graph's relation edges.

    Edge direction follows the relation as the input graph stores it
    (``source -> target``). Reachability is computed *forward* along that
    direction: the loader is responsible for orienting edges so that
    ``source -> target`` means "a change to ``source`` should restage
    ``target``". For caller/callee semantics ("if a callee changes, its callers
    are stale") the loader reverses CALLS edges; that policy lives in the
    adapter, not here — :class:`DepGraph` just walks whatever it is given.
    """

    def __init__(self) -> None:
        self._g = _require_nx().DiGraph()

    # ----- construction -----------------------------------------------------

    @classmethod
    def from_edges(
        cls,
        nodes: Iterable[str],
        edges: Iterable[Tuple[str, str]],
        *,
        relations: Optional[Iterable[Optional[str]]] = None,
    ) -> "DepGraph":
        """Build a graph from a node iterable and ``(source, target)`` edges.

        ``relations``, if given, is a parallel iterable of relation-kind labels
        for each edge (for status output only). Unknown endpoints referenced by
        an edge are added as nodes so the graph is always self-consistent.
        """
        graph = cls()
        for node_id in nodes:
            graph.add_node(node_id)
        rel_iter = iter(relations) if relations is not None else None
        for source, target in edges:
            rel = next(rel_iter) if rel_iter is not None else None
            graph.add_edge(source, target, relation=rel)
        return graph

    # ----- mutation ---------------------------------------------------------

    def add_node(self, node_id: str) -> None:
        self._g.add_node(node_id)

    def add_edge(
        self, source: str, target: str, *, relation: Optional[str] = None
    ) -> None:
        """Add a propagation edge ``source -> target``. Self-loops are dropped
        (a node can't make itself stale through propagation)."""
        if source == target:
            self._g.add_node(source)
            return
        self._g.add_edge(source, target, **{EDGE_RELATION_KEY: relation})

    def remove_edge(self, source: str, target: str) -> None:
        if self._g.has_edge(source, target):
            self._g.remove_edge(source, target)

    def remove_node(self, node_id: str) -> None:
        if self._g.has_node(node_id):
            self._g.remove_node(node_id)

    # ----- introspection ----------------------------------------------------

    def __len__(self) -> int:
        return self._g.number_of_nodes()

    def __contains__(self, node_id: object) -> bool:
        return self._g.has_node(node_id)

    def __iter__(self) -> Iterator[str]:
        return iter(self._g.nodes)

    @property
    def graph(self):
        """The underlying ``networkx.DiGraph`` (read-only intent)."""
        return self._g

    @property
    def node_count(self) -> int:
        return self._g.number_of_nodes()

    @property
    def edge_count(self) -> int:
        return self._g.number_of_edges()

    def nodes(self) -> List[str]:
        return list(self._g.nodes)

    def successors(self, node_id: str) -> List[str]:
        """Nodes one propagation hop downstream of ``node_id``."""
        if not self._g.has_node(node_id):
            return []
        return list(self._g.successors(node_id))

    def dependents(self, node_id: str) -> List[str]:
        """Reverse-dependents: nodes whose staleness ``node_id`` would *not*
        cause, but which point at ``node_id``. Exposed for query/debug
        (``dirtygraph status --why``) — invalidation itself uses the forward
        :meth:`reachable` walk."""
        if not self._g.has_node(node_id):
            return []
        return list(self._g.predecessors(node_id))

    # ----- the two core operations -----------------------------------------

    def reachable(self, sources: Iterable[str]) -> Set[str]:
        """Forward-reachable closure of ``sources`` (inclusive).

        This is the dirty closure: the changed nodes plus everything downstream
        of them along propagation edges. A breadth-first descendant walk that
        is cycle-safe (relation graphs frequently contain cycles, e.g. mutual
        recursion), so it terminates even on strongly-connected components.
        """
        g = self._g
        seen: Set[str] = set()
        frontier: List[str] = []
        for s in sources:
            if g.has_node(s) and s not in seen:
                seen.add(s)
                frontier.append(s)
        while frontier:
            node = frontier.pop()
            for succ in g.successors(node):
                if succ not in seen:
                    seen.add(succ)
                    frontier.append(succ)
        return seen

    def topo_order(self, subset: Optional[Iterable[str]] = None) -> List[str]:
        """Topologically order ``subset`` (or the whole graph) so a node always
        follows the nodes it depends on.

        Cycles cannot be fully ordered; we fall back to a deterministic order
        for the nodes inside each cycle (sorted by id) while still respecting
        all acyclic constraints, so re-derivation never crashes on a recursive
        relation graph.
        """
        g = self._g
        if subset is None:
            sub = g
        else:
            wanted = {n for n in subset if g.has_node(n)}
            sub = g.subgraph(wanted)
        return self._stable_topo(sub)

    @staticmethod
    def _stable_topo(sub) -> List[str]:
        nxmod = _require_nx()
        if nxmod.is_directed_acyclic_graph(sub):
            # Lexicographic tie-break → deterministic, reproducible benchmarks.
            return list(nxmod.lexicographical_topological_sort(sub, key=str))
        # Cyclic: condense SCCs, order the DAG of components, then expand each
        # component in sorted-id order. Every cross-component edge is respected;
        # only intra-cycle order is arbitrary (and unavoidable).
        condensed = nxmod.condensation(sub)
        order: List[str] = []
        for comp_id in nxmod.lexicographical_topological_sort(condensed, key=str):
            members = condensed.nodes[comp_id]["members"]
            order.extend(sorted(members, key=str))
        return order

    # ----- relation metadata (status/debug only) ---------------------------

    def relation_of(self, source: str, target: str) -> Optional[str]:
        if self._g.has_edge(source, target):
            return self._g.edges[source, target].get(EDGE_RELATION_KEY)
        return None

    def relation_histogram(self) -> Dict[str, int]:
        """Count edges per relation kind — handy for ``status`` output."""
        hist: Dict[str, int] = {}
        for _, _, data in self._g.edges(data=True):
            rel = data.get(EDGE_RELATION_KEY) or "<none>"
            hist[rel] = hist.get(rel, 0) + 1
        return hist
