"""Tests for the invalidation primitive — the relation-edge dirty closure.

These exercise the four behaviours the whole product rests on:

1. Changing one source file dirties only its node and that node's forward closure.
2. A source whose content hash is *unchanged* never dirties anything (the no-op
   guarantee that makes incremental re-derivation correct).
3. Dirtiness propagates *transitively* along propagation edges, but not beyond
   the reachable set.
4. On re-derive, clean nodes are skipped (never handed to the adapter), only the
   dirty closure is re-derived, and a second re-derive with no edits does 0 work.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dirtygraph.depgraph import DepGraph
from dirtygraph.dirty import compute_dirty_closure, mark_dirty
from dirtygraph.rederive import callable_adapter, rederive
from dirtygraph.store import Store


# --------------------------------------------------------------------------- #
# Fixtures: a tiny chain graph A -> B -> C -> D, each node from its own file.   #
# --------------------------------------------------------------------------- #


def _write(p: Path, text: str) -> None:
    p.write_text(text, encoding="utf-8")


@pytest.fixture()
def chain(tmp_path: Path):
    """Four source files + a sidecar with a chain propagation graph.

    Edges (propagation direction = "a change here restages there"):
        A -> B -> C -> D
    so editing A's file should dirty {A, B, C, D}; editing C's only {C, D}.
    """
    src = tmp_path / "src"
    src.mkdir()
    files = {}
    for name in ("a", "b", "c", "d"):
        f = src / f"{name}.py"
        _write(f, f"# original {name}\n")
        files[name] = f

    store = Store(root=tmp_path, graph_kind="test")
    for name in ("A", "B", "C", "D"):
        store.add(name, f"src/{name.lower()}.py", label=name)

    # Stamp the clean baseline (so an untouched tree reports 0 dirty).
    store.stamp_hashes(store.compute_hashes())
    store.save()

    graph = DepGraph.from_edges(
        ["A", "B", "C", "D"],
        [("A", "B"), ("B", "C"), ("C", "D")],
    )
    return store, graph, files


# --------------------------------------------------------------------------- #
# 1. one change -> only its dependents go dirty                                #
# --------------------------------------------------------------------------- #


def test_change_one_source_dirties_only_its_forward_closure(chain):
    store, graph, files = chain

    # Untouched tree: nothing dirty.
    clean = compute_dirty_closure(store, graph)
    assert clean.is_clean
    assert clean.dirty_count == 0
    assert clean.total == 4

    # Edit C's source only.
    _write(files["c"], "# edited c\n")

    result = compute_dirty_closure(store, graph)
    # C is the direct hit; D is downstream. A and B are upstream -> untouched.
    assert result.direct == {"C"}
    assert result.closure == {"C", "D"}
    assert "A" not in result.closure
    assert "B" not in result.closure
    assert result.dirty_count == 2
    assert result.headline() == "2 dirty of 4"


def test_edit_root_dirties_entire_chain(chain):
    store, graph, files = chain
    _write(files["a"], "# edited a\n")
    result = compute_dirty_closure(store, graph)
    assert result.direct == {"A"}
    assert result.closure == {"A", "B", "C", "D"}


def test_edit_leaf_dirties_only_itself(chain):
    store, graph, files = chain
    _write(files["d"], "# edited d\n")
    result = compute_dirty_closure(store, graph)
    # D is a leaf: no successors, so its closure is just {D}.
    assert result.closure == {"D"}
    assert result.propagated == set()


# --------------------------------------------------------------------------- #
# 2. unchanged-hash source does NOT dirty                                      #
# --------------------------------------------------------------------------- #


def test_unchanged_source_never_dirties(chain):
    store, graph, files = chain

    # Rewrite a file with byte-identical content -> same blake3 -> no change.
    original = files["b"].read_text(encoding="utf-8")
    _write(files["b"], original)

    result = compute_dirty_closure(store, graph)
    assert result.is_clean
    assert result.changed_paths == set()


def test_touch_then_revert_is_clean(chain):
    store, graph, files = chain
    original = files["b"].read_text(encoding="utf-8")

    _write(files["b"], original + "# scratch\n")
    assert compute_dirty_closure(store, graph).dirty_count > 0

    # Revert to the exact original bytes: hash matches the baseline again.
    _write(files["b"], original)
    assert compute_dirty_closure(store, graph).is_clean


# --------------------------------------------------------------------------- #
# 3. transitive propagation                                                   #
# --------------------------------------------------------------------------- #


def test_transitive_propagation_over_two_hops(chain):
    store, graph, files = chain
    _write(files["b"], "# edited b\n")
    result = compute_dirty_closure(store, graph)
    # B's change reaches C (1 hop) and D (2 hops), not A (upstream).
    assert result.closure == {"B", "C", "D"}
    assert result.propagated == {"C", "D"}


def test_propagation_is_cycle_safe(tmp_path: Path):
    """A cyclic relation graph (mutual recursion) must still terminate."""
    src = tmp_path / "src"
    src.mkdir()
    for name in ("x", "y"):
        _write(src / f"{name}.py", f"# {name}\n")

    store = Store(root=tmp_path)
    store.add("X", "src/x.py", label="X")
    store.add("Y", "src/y.py", label="Y")
    store.stamp_hashes(store.compute_hashes())

    graph = DepGraph.from_edges(["X", "Y"], [("X", "Y"), ("Y", "X")])

    _write(src / "x.py", "# edited x\n")
    result = compute_dirty_closure(store, graph)
    assert result.closure == {"X", "Y"}  # whole cycle, no infinite loop


def test_two_files_one_change_only_marks_one_branch(tmp_path: Path):
    """Two independent branches from a shared root; editing one branch's file
    must not dirty the other branch."""
    src = tmp_path / "src"
    src.mkdir()
    for name in ("root", "left", "right"):
        _write(src / f"{name}.py", f"# {name}\n")

    store = Store(root=tmp_path)
    store.add("ROOT", "src/root.py", label="ROOT")
    store.add("LEFT", "src/left.py", label="LEFT")
    store.add("RIGHT", "src/right.py", label="RIGHT")
    store.stamp_hashes(store.compute_hashes())

    # ROOT -> LEFT and ROOT -> RIGHT (two independent branches).
    graph = DepGraph.from_edges(
        ["ROOT", "LEFT", "RIGHT"], [("ROOT", "LEFT"), ("ROOT", "RIGHT")]
    )

    _write(src / "left.py", "# edited left\n")
    result = compute_dirty_closure(store, graph)
    assert result.closure == {"LEFT"}
    assert "RIGHT" not in result.closure
    assert "ROOT" not in result.closure


# --------------------------------------------------------------------------- #
# 4. clean nodes skipped on re-derive; second pass is a no-op                  #
# --------------------------------------------------------------------------- #


def test_rederive_only_touches_dirty_closure(chain):
    store, graph, files = chain
    _write(files["c"], "# edited c\n")

    # Mark, then re-derive through a recording adapter.
    mark_dirty(store, graph, persist=False)
    seen = []
    adapter = callable_adapter(lambda node: seen.append(node.node_id))

    result = rederive(store, graph, adapter, persist=False)

    # Only C and D (the closure) were re-derived; A and B were skipped.
    assert set(result.rederived) == {"C", "D"}
    assert set(seen) == {"C", "D"}
    assert "A" not in seen and "B" not in seen
    assert result.headline() == "re-derived 2 nodes (of 4)"


def test_second_rederive_with_no_edits_is_noop(chain):
    store, graph, files = chain
    _write(files["c"], "# edited c\n")

    mark_dirty(store, graph, persist=False)
    adapter = callable_adapter(lambda node: node.node_id)
    first = rederive(store, graph, adapter, persist=False)
    assert first.rederived_count == 2

    # No new edits: change detection finds nothing, so nothing is re-marked,
    # and a second rederive does zero work.
    mark_dirty(store, graph, persist=False)
    second = rederive(store, graph, adapter, persist=False)
    assert second.rederived_count == 0
    assert second.skipped_clean == second.total


def test_rederive_topo_order_respects_dependencies(chain):
    store, graph, files = chain
    _write(files["a"], "# edited a\n")  # dirties the whole chain

    mark_dirty(store, graph, persist=False)
    order = []
    adapter = callable_adapter(lambda node: order.append(node.node_id))
    rederive(store, graph, adapter, persist=False)

    # A before B before C before D (a node follows the nodes it depends on).
    assert order.index("A") < order.index("B") < order.index("C") < order.index("D")


def test_failed_node_keeps_its_dirty_bit(chain):
    store, graph, files = chain
    _write(files["c"], "# edited c\n")
    mark_dirty(store, graph, persist=False)

    def boom(node):
        if node.node_id == "D":
            raise RuntimeError("adapter exploded")
        return node.node_id

    adapter = callable_adapter(boom)
    result = rederive(store, graph, adapter, persist=False)

    assert "C" in result.rederived
    assert "D" in result.failed
    # The failed node stays dirty so a retry picks it up.
    assert "D" in store.dirty_nodes()
    assert "C" not in store.dirty_nodes()


def test_one_thousand_two_hundred_node_graph_marks_small_closure(tmp_path: Path):
    """The headline guarantee: on a 1,200-node graph, editing one leaf's file
    marks only the affected closure, never the whole graph."""
    src = tmp_path / "src"
    src.mkdir()

    store = Store(root=tmp_path)
    edges = []
    # 1,200 nodes: a wide fan of 400 independent 3-chains (n0->n1->n2).
    n_chains = 400
    for c in range(n_chains):
        files_in_chain = []
        for depth in range(3):
            name = f"f{c}_{depth}.py"
            f = src / name
            _write(f, f"# chain {c} depth {depth}\n")
            files_in_chain.append(name)
            nid = f"N{c}_{depth}"
            store.add(nid, f"src/{name}", label=nid)
        edges.append((f"N{c}_0", f"N{c}_1"))
        edges.append((f"N{c}_1", f"N{c}_2"))

    assert store.total == 1200
    store.stamp_hashes(store.compute_hashes())

    graph = DepGraph.from_edges(list(store.node_ids), edges)

    # Edit the ROOT of exactly one chain -> dirties that chain's 3 nodes only.
    _write(src / "f7_0.py", "# edited\n")
    result = compute_dirty_closure(store, graph)
    assert result.closure == {"N7_0", "N7_1", "N7_2"}
    assert result.dirty_count == 3
    assert result.total == 1200
    assert result.headline() == "3 dirty of 1,200"
