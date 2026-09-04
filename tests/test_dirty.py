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

import os
import time
from pathlib import Path

import pytest

from dirtygraph.depgraph import DepGraph
from dirtygraph.dirty import compute_dirty_closure, explain_dirty, mark_dirty
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


def test_reverted_source_clears_its_stale_dirty_bit(chain):
    """A source edited then reverted to its baseline hash must drop out of the
    dirty set, not keep a stale dirty bit (fix-stale-dirty-bits-on-revert).

    mark_dirty reconciles the persisted dirty set to the content-hash closure,
    so a reverted source's bit is cleared and a later rederive redoes zero
    nodes instead of redoing byte-identical-to-baseline nodes."""
    store, graph, files = chain
    original = files["b"].read_text(encoding="utf-8")

    # Edit B's source -> mark_dirty sets bits for B's forward closure {B, C, D}.
    _write(files["b"], original + "# scratch\n")
    mark_dirty(store, graph, persist=False)
    assert set(store.dirty_nodes()) == {"B", "C", "D"}

    # Revert B to its exact baseline bytes -> its hash matches the recorded one
    # again, so the content-hash closure is empty. The stale bits must clear.
    _write(files["b"], original)
    mark_dirty(store, graph, persist=False)
    assert store.dirty_nodes() == []
    assert store.dirty_count == 0

    # A subsequent rederive redoes zero nodes (no stale dirty set to act on).
    adapter = callable_adapter(lambda node: node.node_id)
    result = rederive(store, graph, adapter, persist=False)
    assert result.rederived_count == 0


def test_mark_dirty_keeps_failed_direct_hit_in_closure(chain):
    """A failed direct-hit node (source edited, adapter raised, hash NOT
    re-stamped) stays IN the content-hash closure on a later mark_dirty: its
    source still differs from the recorded hash, so the reconciliation does
    not clear it — the failed-rederive-retry safety of fix-stale-dirty-bits."""
    store, graph, files = chain
    _write(files["d"], "# edited d\n")  # D is a direct hit (its own source)
    mark_dirty(store, graph, persist=False)
    assert "D" in store.dirty_nodes()

    def boom(node):
        if node.node_id == "D":
            raise RuntimeError("boom")
        return node.node_id

    adapter = callable_adapter(boom)
    result = rederive(store, graph, adapter, persist=False)
    assert "D" in result.failed
    assert "D" in store.dirty_nodes()  # failed -> dirty bit kept

    # Retry: D's source hash is still un-stamped (it failed), so its on-disk
    # hash still differs from the recorded one -> D stays in the closure and
    # the reconciliation does NOT clear it.
    mark_dirty(store, graph, persist=False)
    assert "D" in store.dirty_nodes()


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


# --------------------------------------------------------------------------- #
# m4: Store.save() no-op guard + watch ignores .dirtygraph/                     #
# --------------------------------------------------------------------------- #


def test_save_is_noop_when_content_unchanged(tmp_path: Path):
    """A second save() with identical entries must NOT rewrite the file
    (mtime stable) — this is what stops the watch loop's save->event->save
    thrash."""
    store = Store(root=tmp_path)
    store.add("A", "src/a.py", label="A")
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "a.py").write_text("# a\n", encoding="utf-8")
    store.stamp_hashes(store.compute_hashes())
    first = store.save()
    mtime_before = os.stat(first).st_mtime_ns
    # Force the filesystem clock to advance so a real write would be visible.
    time.sleep(0.02)
    store.save()  # identical payload
    mtime_after = os.stat(first).st_mtime_ns
    assert mtime_after == mtime_before  # no rewrite happened


def test_save_writes_when_content_changes(tmp_path: Path):
    store = Store(root=tmp_path)
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "a.py").write_text("# a\n", encoding="utf-8")
    store.add("A", "src/a.py")
    store.save()
    mtime_before = os.stat(store.state_path(tmp_path)).st_mtime_ns
    time.sleep(0.02)
    store.add("B", "src/b.py")  # genuinely new content
    store.save()
    mtime_after = os.stat(store.state_path(tmp_path)).st_mtime_ns
    assert mtime_after > mtime_before


def test_is_sidecar_path_filter():
    from dirtygraph.cli import _is_sidecar_path

    sep = os.sep
    assert _is_sidecar_path(f"/tmp/x{sep}.dirtygraph{sep}state.json")
    assert _is_sidecar_path(f"/tmp/x{sep}.dirtygraph")
    assert not _is_sidecar_path(f"/tmp/x{sep}src{sep}a.py")
    assert not _is_sidecar_path("")
    # A sibling named .dirtygraph_backup must NOT match (component match only).
    assert not _is_sidecar_path(f"/tmp/x{sep}src{sep}.dirtygraph_backup{sep}a.py")


# --------------------------------------------------------------------------- #
# m5: touch is a cheap targeted re-hash; rederive hashes the tree once         #
# --------------------------------------------------------------------------- #


def _wide_graph(tmp_path: Path, n: int = 60):
    """A store with `n` independent nodes, each from its own file."""
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    store = Store(root=tmp_path)
    for i in range(n):
        (src / f"f{i}.py").write_text(f"# {i}\n", encoding="utf-8")
        store.add(f"N{i}", f"src/f{i}.py")
    store.stamp_hashes(store.compute_hashes())
    store.save()
    graph = DepGraph.from_edges(list(store.node_ids), [])
    return store, graph, src


def test_targeted_current_hashes_only_mark_one_path(tmp_path: Path, monkeypatch):
    """A targeted current_hashes dict (recorded hashes + one fresh) makes
    change detection touch only that one path, and does NOT invoke a full
    compute_hashes on the store."""
    store, graph, src = _wide_graph(tmp_path, n=40)
    (src / "f5.py").write_text("# edited\n", encoding="utf-8")

    import dirtygraph.store as store_mod
    from dirtygraph.store import MISSING_HASH, hash_file

    calls = {"n": 0}
    real = store_mod.Store.compute_hashes

    def counting(self):
        calls["n"] += 1
        return real(self)

    # Patch at class level (Store is a slotted dataclass: instance assignment
    # of a method is rejected, so monkeypatch the class itself).
    monkeypatch.setattr(store_mod.Store, "compute_hashes", counting)

    targeted = {
        s: (store.recorded_hash(s) or MISSING_HASH) for s in store.source_paths()
    }
    targeted["src/f5.py"] = hash_file(store.resolve("src/f5.py"))

    result = mark_dirty(store, graph, current_hashes=targeted, persist=False)
    assert result.changed_paths == {"src/f5.py"}
    # mark_dirty with an explicit current_hashes must NOT hash the whole tree.
    assert calls["n"] == 0


def test_touch_cli_hashes_only_the_named_file(tmp_path: Path, monkeypatch):
    """`dirtygraph touch <file>` must read exactly one source file from disk,
    not the whole tree — the docstring's 'cheap targeted re-hash' promise."""
    from typer.testing import CliRunner

    import dirtygraph.cli as cli_mod
    import dirtygraph.store as store_mod

    store, graph, src = _wide_graph(tmp_path, n=50)
    # Persist edges so the CLI can rebuild the graph.
    (tmp_path / "src" / "f9.py").write_text("# edited\n", encoding="utf-8")

    real_hash_file = store_mod.hash_file
    calls = {"n": 0}

    def counting_hash_file(p):
        calls["n"] += 1
        return real_hash_file(p)

    monkeypatch.setattr(cli_mod, "hash_file", counting_hash_file)

    runner = CliRunner()
    res = runner.invoke(cli_mod.app, ["touch", str(src / "f9.py"), "--root", str(tmp_path)])
    assert res.exit_code == 0, res.output
    # Exactly one disk read for the named file; no full-tree scan.
    assert calls["n"] == 1, f"expected 1 hash_file call, got {calls['n']}"


def test_rederive_engine_does_not_rehash_when_current_hashes_given(chain, monkeypatch):
    """When the caller hands a precomputed hash snapshot, the engine must NOT
    call store.compute_hashes a second time."""
    store, graph, files = chain
    _write(files["c"], "# edited c\n")

    mark_dirty(store, graph, persist=False)
    snapshot = store.compute_hashes()

    import dirtygraph.store as store_mod

    calls = {"n": 0}
    real = store_mod.Store.compute_hashes

    def counting(self):
        calls["n"] += 1
        return real(self)

    monkeypatch.setattr(store_mod.Store, "compute_hashes", counting)

    adapter = callable_adapter(lambda node: node.node_id)
    rederive(store, graph, adapter, persist=False, current_hashes=snapshot)
    assert calls["n"] == 0  # snapshot reused, no second full hash


# --------------------------------------------------------------------------- #
# m6: explain_dirty — direct vs propagated provenance                         #
# --------------------------------------------------------------------------- #


def test_explain_dirty_direct_and_propagated(chain):
    store, graph, files = chain
    _write(files["b"], "# edited b\n")
    result = compute_dirty_closure(store, graph)

    causes = {c.node_id: c for c in explain_dirty(store, graph, result)}

    # B is the direct hit (its own source changed).
    assert causes["B"].kind == "direct"
    assert causes["B"].sources == ["src/b.py"]
    # C and D are propagated via B -> C -> D.
    assert causes["C"].kind == "propagated"
    assert causes["C"].via[0] == "B"
    assert causes["C"].via[-1] == "C"
    assert causes["D"].kind == "propagated"
    assert causes["D"].via[0] == "B"
    assert causes["D"].via[-1] == "D"
    # A is upstream of the change — not in the closure at all.
    assert "A" not in causes


def test_explain_dirty_leaf_is_direct_with_no_path(chain):
    store, graph, files = chain
    _write(files["d"], "# edited d\n")
    result = compute_dirty_closure(store, graph)
    causes = {c.node_id: c for c in explain_dirty(store, graph, result)}
    assert causes["D"].kind == "direct"
    assert causes["D"].sources == ["src/d.py"]
    assert causes["D"].via == []


# --------------------------------------------------------------------------- #
# m7: Store.reset re-baselines clean + idempotent                              #
# --------------------------------------------------------------------------- #


def test_reset_clears_dirty_and_restamps(chain):
    store, graph, files = chain
    _write(files["a"], "# edited a\n")
    mark_dirty(store, graph, persist=False)
    assert store.dirty_count > 0

    cleared = store.reset()
    assert cleared > 0
    assert store.dirty_count == 0
    # Re-detection against the freshly-stamped hashes sees a clean tree.
    assert compute_dirty_closure(store, graph).is_clean


def test_reset_is_idempotent(chain):
    store, graph, files = chain
    store.reset()
    cleared = store.reset()
    assert cleared == 0
    assert store.dirty_count == 0


# --------------------------------------------------------------------------- #
# m8: targeted single-path hashing shared by touch + watch --rederive          #
# --------------------------------------------------------------------------- #


def test_targeted_current_hashes_only_reads_changed_paths(tmp_path: Path, monkeypatch):
    """The watch loop's targeted snapshot (shared with ``touch``) re-hashes
    ONLY the changed files; every other tracked source keeps its recorded hash
    (no disk read). This is m5's targeted single-path hashing, reused by m8's
    ``watch --rederive`` so the live loop never full-scans the tree per event.
    """
    import dirtygraph.cli as cli_mod

    store, graph, src = _wide_graph(tmp_path, n=30)
    (src / "f3.py").write_text("# edited\n", encoding="utf-8")
    (src / "f7.py").write_text("# edited\n", encoding="utf-8")

    real = cli_mod.hash_file
    calls = {"n": 0}

    def counting(p):
        calls["n"] += 1
        return real(p)

    monkeypatch.setattr(cli_mod, "hash_file", counting)

    snapshot = cli_mod._targeted_current_hashes(
        store, tmp_path, [src / "f3.py", src / "f7.py"]
    )
    # Exactly the two changed files were hashed from disk.
    assert calls["n"] == 2, f"expected 2 hash_file calls, got {calls['n']}"
    # Changed paths carry their fresh hash; untouched paths keep the recorded one.
    assert snapshot["src/f3.py"] == real(src / "f3.py")
    assert snapshot["src/f7.py"] == real(src / "f7.py")
    assert snapshot["src/f0.py"] == store.recorded_hash("src/f0.py")
    # Feeding it to mark_dirty dirties only the two changed files' closure.
    result = mark_dirty(store, graph, current_hashes=snapshot, persist=False)
    assert result.changed_paths == {"src/f3.py", "src/f7.py"}


# --------------------------------------------------------------------------- #
# v0.5.0: status --write reconciliation + shared-source failed-retry +        #
#         add targeted hash + rederive before-count                           #
# --------------------------------------------------------------------------- #


def test_status_write_reconciles_dirty_bits_on_revert(chain):
    """`status --write` (the default) must reconcile the dirty set to the
    content-hash closure: a source edited then reverted to its baseline hash
    has its stale dirty bit cleared. Regression for
    fix-status-write-stale-dirty-bits — the v0.4.0 engine fix only covered the
    `dirty.mark_dirty` path; `status` previously called `Store.mark_dirty`
    directly, which only sets bits and never clears them."""
    from typer.testing import CliRunner

    import dirtygraph.cli as cli_mod

    store, graph, files = chain
    # Persist the propagation edges so the CLI can rebuild the graph.
    cli_mod._save_edges(store.root, [("A", "B", None), ("B", "C", None), ("C", "D", None)])

    original = files["b"].read_text(encoding="utf-8")

    # Edit B -> status --write marks {B, C, D} dirty and persists.
    _write(files["b"], original + "# scratch\n")
    runner = CliRunner()
    res = runner.invoke(cli_mod.app, ["status", "--root", str(store.root)])
    assert res.exit_code == 0, res.output
    assert "3 dirty of 4" in res.output
    assert set(Store.load(store.root).dirty_nodes()) == {"B", "C", "D"}

    # Revert B to baseline -> status --write must clear the stale bits
    # (reconcile to the now-empty closure), not leave them set.
    _write(files["b"], original)
    res2 = runner.invoke(cli_mod.app, ["status", "--root", str(store.root)])
    assert res2.exit_code == 0, res2.output
    assert "0 dirty of 4" in res2.output
    assert Store.load(store.root).dirty_nodes() == []


def test_failed_node_sharing_source_survives_reconciliation(tmp_path: Path):
    """When two nodes share a source file and one's adapter raises while the
    other succeeds, the failed node's recorded hash must NOT be re-stamped by
    its sibling's success. Regression for fix-rederive-shared-source-checkpoint:
    without the fix, stamp_hashes re-stamps the shared path, the next
    mark_dirty reconciliation sees the file as clean, clears the failed node's
    dirty bit, and silently drops it from retry."""
    src = tmp_path / "src"
    src.mkdir()
    shared = src / "shared.py"
    _write(shared, "# original\n")

    store = Store(root=tmp_path)
    store.add("X", "src/shared.py", label="X")
    store.add("Y", "src/shared.py", label="Y")
    store.stamp_hashes(store.compute_hashes())

    # X -> Y: a change to X restages Y (and both are direct hits on shared.py).
    graph = DepGraph.from_edges(["X", "Y"], [("X", "Y")])

    # Edit the shared source -> both X and Y are direct hits.
    _write(shared, "# edited\n")
    mark_dirty(store, graph, persist=False)
    assert set(store.dirty_nodes()) == {"X", "Y"}

    # X succeeds, Y fails.
    def boom(node):
        if node.node_id == "Y":
            raise RuntimeError("boom")
        return node.node_id

    adapter = callable_adapter(boom)
    result = rederive(store, graph, adapter, persist=False)
    assert "X" in result.rederived
    assert "Y" in result.failed

    # Y's recorded hash must NOT have been re-stamped by X's success (they
    # share shared.py), so its on-disk hash still differs from the recorded one.
    recorded_y = store.get("Y").content_hash
    current = store.compute_hashes()["src/shared.py"]
    assert recorded_y != current, "failed node's hash was re-stamped by a sibling"

    # Retry: mark_dirty reconciles, but Y's source still differs from its
    # (un-stamped) recorded hash -> Y stays in the closure and is retriable.
    mark_dirty(store, graph, persist=False)
    assert "Y" in store.dirty_nodes(), "failed node was dropped from retry"
    # X re-enters too: its path wasn't checkpointed either (shared with Y).
    assert "X" in store.dirty_nodes()


def test_add_cli_hashes_only_the_new_file(tmp_path: Path, monkeypatch):
    """`dirtygraph add` must hash exactly one source file from disk regardless
    of how many nodes are already tracked. Regression for fix-add-full-tree-hash:
    previously it called store.compute_hashes(), a full-tree scan that re-reads
    every tracked file on each add."""
    from typer.testing import CliRunner

    import dirtygraph.cli as cli_mod
    import dirtygraph.store as store_mod

    store, graph, src = _wide_graph(tmp_path, n=50)
    new_src = src / "new_node.py"
    _write(new_src, "# new\n")

    real_hash_file = store_mod.hash_file
    calls = {"n": 0}

    def counting_hash_file(p):
        calls["n"] += 1
        return real_hash_file(p)

    monkeypatch.setattr(cli_mod, "hash_file", counting_hash_file)

    runner = CliRunner()
    res = runner.invoke(
        cli_mod.app, ["add", "NEW", str(new_src), "--root", str(tmp_path)]
    )
    assert res.exit_code == 0, res.output
    # Exactly one disk read (the new file); no full-tree scan.
    assert calls["n"] == 1, f"expected 1 hash_file call, got {calls['n']}"

    # The new node's recorded hash matches the file (not MISSING_HASH).
    reloaded = store_mod.Store.load(tmp_path)
    assert reloaded.recorded_hash("src/new_node.py") == real_hash_file(new_src)


def test_rederive_cli_prints_before_and_after_lines(chain):
    """`dirtygraph rederive` prints the dirty-closure before-count line AND the
    re-derived after-count line, matching the watch --rederive two-line
    benchmark (feature-rederive-benchmark-clarity)."""
    from typer.testing import CliRunner

    import dirtygraph.cli as cli_mod

    store, graph, files = chain
    cli_mod._save_edges(store.root, [("A", "B", None), ("B", "C", None), ("C", "D", None)])

    _write(files["c"], "# edited c\n")
    runner = CliRunner()
    res = runner.invoke(cli_mod.app, ["rederive", "--root", str(store.root)])
    assert res.exit_code == 0, res.output
    # Before-count line (dirty closure size).
    assert "dirty closure:" in res.output
    assert "2 dirty of 4" in res.output
    # After-count line (re-derived).
    assert "re-derived" in res.output
    assert "2 nodes" in res.output


def test_rederive_cli_surfaces_failed_nodes_by_default(chain):
    """Failed node details print without --verbose under the rederive CLI
    (feature-rederive-benchmark-clarity) — under DIRTYGRAPH_LLM a failure is
    actionable, not verbose detail."""
    from typer.testing import CliRunner

    import dirtygraph.cli as cli_mod

    store, graph, files = chain
    cli_mod._save_edges(store.root, [("A", "B", None), ("B", "C", None), ("C", "D", None)])

    _write(files["c"], "# edited c\n")
    # Inject a failing codegraph adapter via the registry so the CLI picks it up.
    from dirtygraph.adapters.base import Adapter
    from dirtygraph.rederive import NodeView

    class BoomAdapter(Adapter):
        name = "boom"

        def re_derive(self, node: NodeView):
            if node.node_id == "D":
                raise RuntimeError("boom")
            return node.node_id

    import dirtygraph.adapters as adapters_mod

    monkeypatch_backup = dict(adapters_mod.ADAPTERS)
    adapters_mod.ADAPTERS["boom"] = BoomAdapter
    try:
        runner = CliRunner()
        res = runner.invoke(
            cli_mod.app,
            ["rederive", "--adapter", "boom", "--root", str(store.root)],
        )
        assert res.exit_code == 0, res.output
        # The failed node is named without --verbose.
        assert "D" in res.output
        assert "boom" in res.output
    finally:
        adapters_mod.ADAPTERS.clear()
        adapters_mod.ADAPTERS.update(monkeypatch_backup)


