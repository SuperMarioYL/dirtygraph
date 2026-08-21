"""Tests for the adapter interface + the codegraph adapter.

Covers:

* The base :class:`Adapter` contract — abstract, subclassable, ``read_source``
  helper, and Protocol conformance against the engine.
* :class:`EchoAdapter` — deterministic, network-free re-derivation.
* :class:`CodeGraphAdapter` — both loaders (graphify NetworkX JSON +
  code-review-graph SQLite), format detection, propagation-edge orientation, and
  the deterministic re-derivation path (the LLM path falls back without keys).
* ``get_adapter`` registry resolution.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from dirtygraph.adapters import (
    ADAPTERS,
    CodeGraphAdapter,
    EchoAdapter,
    get_adapter,
)
from dirtygraph.adapters.base import Adapter, DerivedContent
from dirtygraph.adapters.codegraph import (
    detect_format,
    load_crg_sqlite,
    load_graph,
    load_graphify_json,
)
from dirtygraph.rederive import Adapter as AdapterProtocol
from dirtygraph.rederive import NodeView


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _node(tmp_path: Path, name: str, text: str) -> NodeView:
    f = tmp_path / f"{name}.py"
    f.write_text(text, encoding="utf-8")
    return NodeView(
        node_id=name,
        source_path=f"{name}.py",
        resolved_path=str(f),
        label=name.upper(),
    )


# --------------------------------------------------------------------------- #
# Base interface contract                                                      #
# --------------------------------------------------------------------------- #


def test_adapter_is_abstract():
    with pytest.raises(TypeError):
        Adapter()  # type: ignore[abstract]


def test_subclass_must_implement_re_derive():
    class Incomplete(Adapter):
        pass

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]


def test_minimal_subclass_satisfies_engine_protocol(tmp_path: Path):
    class Tiny(Adapter):
        name = "tiny"

        def re_derive(self, node: NodeView):
            return node.node_id

    adapter = Tiny()
    # The shipped adapter must satisfy the engine's structural Protocol.
    assert isinstance(adapter, AdapterProtocol)
    assert adapter.describe() == "tiny"
    view = _node(tmp_path, "x", "print('hi')\n")
    assert adapter.re_derive(view) == "x"


def test_read_source_returns_none_for_missing_file(tmp_path: Path):
    view = NodeView(
        node_id="ghost",
        source_path="gone.py",
        resolved_path=str(tmp_path / "gone.py"),
    )
    assert Adapter.read_source(view) is None


def test_read_source_reads_bytes(tmp_path: Path):
    view = _node(tmp_path, "y", "data\n")
    assert Adapter.read_source(view) == b"data\n"


# --------------------------------------------------------------------------- #
# EchoAdapter                                                                  #
# --------------------------------------------------------------------------- #


def test_echo_adapter_is_deterministic(tmp_path: Path):
    view = _node(tmp_path, "mod", "def f():\n    return 1\n")
    echo = EchoAdapter()
    first = echo.re_derive(view)
    second = echo.re_derive(view)
    assert isinstance(first, DerivedContent)
    # Same bytes -> identical derived content (the no-op-on-second-rederive basis).
    assert first == second
    assert first.byte_len == len(b"def f():\n    return 1\n")
    assert "MOD" in first.summary  # label folded into the summary


def test_echo_adapter_handles_missing_source(tmp_path: Path):
    view = NodeView(
        node_id="m",
        source_path="missing.py",
        resolved_path=str(tmp_path / "missing.py"),
    )
    out = EchoAdapter().re_derive(view)
    assert out.extra.get("missing") is True
    assert out.byte_len == 0


def test_echo_summary_changes_when_source_changes(tmp_path: Path):
    view = _node(tmp_path, "z", "version one\n")
    a = EchoAdapter().re_derive(view)
    (tmp_path / "z.py").write_text("version two is longer\n", encoding="utf-8")
    b = EchoAdapter().re_derive(view)
    assert a != b


# --------------------------------------------------------------------------- #
# get_adapter registry                                                         #
# --------------------------------------------------------------------------- #


def test_get_adapter_resolves_registered_names():
    assert isinstance(get_adapter("echo"), EchoAdapter)
    assert isinstance(get_adapter("codegraph"), CodeGraphAdapter)
    assert "echo" in ADAPTERS and "codegraph" in ADAPTERS


def test_get_adapter_is_case_insensitive():
    assert isinstance(get_adapter("ECHO"), EchoAdapter)
    assert isinstance(get_adapter("  CodeGraph "), CodeGraphAdapter)


def test_get_adapter_rejects_unknown_name():
    with pytest.raises(ValueError) as exc:
        get_adapter("nope")
    assert "unknown adapter" in str(exc.value)
    assert "codegraph" in str(exc.value)  # lists the valid options


# --------------------------------------------------------------------------- #
# graphify NetworkX node-link JSON loader                                      #
# --------------------------------------------------------------------------- #


def _graphify_doc():
    return {
        "directed": True,
        "multigraph": False,
        "graph": {},
        "nodes": [
            {"id": "auth", "label": "auth", "source_file": "auth.py"},
            {"id": "api", "label": "api", "source_file": "api.py"},
            {"id": "util", "label": "util", "source_file": "util.py"},
        ],
        # api CALLS auth: in the source graph that's api -> auth; oriented for
        # propagation it must become auth -> api (a change to auth restages api).
        "links": [
            {"source": "api", "target": "auth", "relation": "CALLS"},
            {"source": "api", "target": "util", "relation": "CALLS"},
        ],
    }


def test_load_graphify_json_reads_nodes_and_sources(tmp_path: Path):
    gp = tmp_path / "graph.json"
    gp.write_text(json.dumps(_graphify_doc()), encoding="utf-8")

    loaded = load_graphify_json(gp)
    assert loaded.kind == "graphify"
    by_id = {n.node_id: n for n in loaded.nodes}
    assert by_id["auth"].source_path == "auth.py"
    assert by_id["api"].label == "api"
    assert set(by_id) == {"auth", "api", "util"}


def test_graphify_calls_edges_are_reversed_for_propagation(tmp_path: Path):
    gp = tmp_path / "graph.json"
    gp.write_text(json.dumps(_graphify_doc()), encoding="utf-8")
    loaded = load_graphify_json(gp)

    pairs = {(s, t) for (s, t, _r) in loaded.edges}
    # CALLS api->auth reversed to auth->api (dependency -> dependent).
    assert ("auth", "api") in pairs
    assert ("util", "api") in pairs
    assert ("api", "auth") not in pairs


def test_graphify_to_depgraph_propagates_correctly(tmp_path: Path):
    gp = tmp_path / "graph.json"
    gp.write_text(json.dumps(_graphify_doc()), encoding="utf-8")
    loaded = load_graphify_json(gp)
    graph = loaded.to_depgraph()

    # A change to auth must reach api (api calls auth), but not util.
    assert graph.reachable(["auth"]) == {"auth", "api"}
    assert "util" not in graph.reachable(["auth"])


# --------------------------------------------------------------------------- #
# code-review-graph SQLite loader                                              #
# --------------------------------------------------------------------------- #


def _make_crg_store(root: Path) -> Path:
    crg_dir = root / ".code-review-graph"
    crg_dir.mkdir()
    db = crg_dir / "graph.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE nodes (qualified_name TEXT PRIMARY KEY, name TEXT, "
        "file_path TEXT, kind TEXT)"
    )
    conn.execute(
        "CREATE TABLE edges (source_qualified TEXT, target_qualified TEXT, kind TEXT)"
    )
    conn.executemany(
        "INSERT INTO nodes VALUES (?,?,?,?)",
        [
            ("pkg.auth", "auth", "auth.py", "module"),
            ("pkg.api", "api", "api.py", "module"),
            ("pkg.util", "util", "util.py", "module"),
        ],
    )
    conn.executemany(
        "INSERT INTO edges VALUES (?,?,?)",
        [
            ("pkg.api", "pkg.auth", "IMPORTS_FROM"),  # api imports auth
            ("pkg.api", "pkg.util", "CALLS"),
        ],
    )
    conn.commit()
    conn.close()
    return crg_dir


def test_load_crg_sqlite_reads_nodes_and_edges(tmp_path: Path):
    crg_dir = _make_crg_store(tmp_path)
    loaded = load_crg_sqlite(crg_dir)
    assert loaded.kind == "crg"
    by_id = {n.node_id: n for n in loaded.nodes}
    assert by_id["pkg.auth"].source_path == "auth.py"
    assert by_id["pkg.auth"].label == "auth"
    assert len(loaded.edges) == 2


def test_crg_imports_edges_reversed_for_propagation(tmp_path: Path):
    crg_dir = _make_crg_store(tmp_path)
    loaded = load_crg_sqlite(crg_dir)
    graph = loaded.to_depgraph()
    # api IMPORTS_FROM auth -> a change to auth restages api.
    assert graph.reachable(["pkg.auth"]) == {"pkg.auth", "pkg.api"}


def test_load_crg_via_parent_directory(tmp_path: Path):
    """Pointing at the repo root (which contains .code-review-graph) works."""
    _make_crg_store(tmp_path)
    loaded = load_crg_sqlite(tmp_path)
    assert loaded.kind == "crg"
    assert len(loaded.nodes) == 3


# --------------------------------------------------------------------------- #
# format detection + unified load_graph                                        #
# --------------------------------------------------------------------------- #


def test_detect_format_graphify(tmp_path: Path):
    gp = tmp_path / "graph.json"
    gp.write_text("{}", encoding="utf-8")
    assert detect_format(gp) == "graphify"


def test_detect_format_crg_dir(tmp_path: Path):
    crg_dir = _make_crg_store(tmp_path)
    assert detect_format(crg_dir) == "crg"
    assert detect_format(crg_dir / "graph.db") == "crg"


def test_detect_format_rejects_unknown(tmp_path: Path):
    weird = tmp_path / "graph.txt"
    weird.write_text("nope", encoding="utf-8")
    with pytest.raises(ValueError):
        detect_format(weird)


def test_load_graph_dispatches_on_format(tmp_path: Path):
    gp = tmp_path / "graph.json"
    gp.write_text(json.dumps(_graphify_doc()), encoding="utf-8")
    assert load_graph(gp).kind == "graphify"

    crg_dir = _make_crg_store(tmp_path)
    assert load_graph(crg_dir).kind == "crg"


# --------------------------------------------------------------------------- #
# CodeGraphAdapter re-derivation                                               #
# --------------------------------------------------------------------------- #


def test_codegraph_adapter_local_summary_is_deterministic(tmp_path: Path, monkeypatch):
    # Ensure no LLM env leaks in from the host.
    monkeypatch.delenv("DIRTYGRAPH_LLM", raising=False)
    view = _node(tmp_path, "svc", "class Service:\n    pass\n")
    adapter = CodeGraphAdapter()
    assert adapter.uses_llm is False
    out = adapter.re_derive(view)
    assert isinstance(out, DerivedContent)
    assert out.extra["mode"] == "local"
    assert out == adapter.re_derive(view)  # deterministic


def test_codegraph_adapter_handles_missing_source(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DIRTYGRAPH_LLM", raising=False)
    view = NodeView(
        node_id="g",
        source_path="gone.py",
        resolved_path=str(tmp_path / "gone.py"),
    )
    out = CodeGraphAdapter().re_derive(view)
    assert out.extra.get("mode") == "missing"
    assert out.byte_len == 0


def test_codegraph_llm_path_falls_back_when_endpoint_fails(tmp_path: Path, monkeypatch):
    """With LLM env set but the endpoint unreachable, re-derive must fall back to
    the deterministic summary instead of raising — a flaky endpoint never kills a
    re-derive batch."""
    monkeypatch.setenv("DIRTYGRAPH_LLM", "1")
    monkeypatch.setenv("DIRTYGRAPH_LLM_API_KEY", "test-key")
    # Point at an unroutable endpoint with a short timeout.
    monkeypatch.setenv("DIRTYGRAPH_LLM_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("DIRTYGRAPH_LLM_TIMEOUT", "1")

    view = _node(tmp_path, "svc", "def handler():\n    return 200\n")
    adapter = CodeGraphAdapter()
    assert adapter.uses_llm is True
    out = adapter.re_derive(view)
    # Fell back to the local summary, did not raise.
    assert out.extra["mode"] == "local"
    assert out.byte_len > 0


def test_codegraph_uses_stubbed_llm_when_endpoint_responds(tmp_path: Path, monkeypatch):
    """Stub the urllib call to confirm the LLM branch wires the response through."""
    import io

    monkeypatch.setenv("DIRTYGRAPH_LLM", "1")
    monkeypatch.setenv("DIRTYGRAPH_LLM_API_KEY", "test-key")
    monkeypatch.setenv("DIRTYGRAPH_LLM_MODEL", "deepseek-chat")

    fake = {
        "choices": [{"message": {"content": "Re-summarised by the model."}}]
    }

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=None):
        return _Resp(json.dumps(fake).encode("utf-8"))

    monkeypatch.setattr(
        "dirtygraph.adapters.codegraph.urllib.request.urlopen", _fake_urlopen
    )

    view = _node(tmp_path, "svc", "def f():\n    return 1\n")
    out = CodeGraphAdapter().re_derive(view)
    assert out.extra["mode"] == "llm"
    assert out.summary == "Re-summarised by the model."
    assert out.extra["model"] == "deepseek-chat"


def test_codegraph_adapter_satisfies_engine_protocol():
    assert isinstance(CodeGraphAdapter(), AdapterProtocol)


# --------------------------------------------------------------------------- #
# fix-crg-init-wrong-root: init pointed at a .code-review-graph store dir      #
# --------------------------------------------------------------------------- #


def test_resolve_root_returns_parent_for_crg_store_dir(tmp_path: Path):
    """A ``.code-review-graph`` store dir is NOT the repo root — its node
    ``file_path`` values are relative to the parent, so ``_resolve_root`` must
    return the parent. Regression for fix-crg-init-wrong-root."""
    from dirtygraph.cli import _resolve_root

    crg_dir = _make_crg_store(tmp_path)  # tmp_path/.code-review-graph
    assert crg_dir.name == ".code-review-graph"
    # Pointing directly at the store dir resolves to the repo root (its parent).
    assert _resolve_root(crg_dir) == tmp_path
    # A plain repo dir (containing the store) resolves to itself.
    assert _resolve_root(tmp_path) == tmp_path
    # A graphify .json file resolves to its parent dir.
    gp = tmp_path / "graph.json"
    gp.write_text("{}", encoding="utf-8")
    assert _resolve_root(gp) == tmp_path


def test_init_crg_store_dir_resolves_sources_against_repo_root(tmp_path: Path):
    """``dirtygraph init <repo>/.code-review-graph`` (no --root) must hash the
    real source files at the repo root, not ``MISSING_HASH`` for every node.

    Before the fix, base was the store dir, so ``auth.py`` resolved to
    ``<repo>/.code-review-graph/auth.py`` (absent) -> ``MISSING_HASH`` -> the
    tool could never detect a real change.
    """
    from typer.testing import CliRunner

    import dirtygraph.cli as cli_mod
    from dirtygraph.store import MISSING_HASH, Store

    crg_dir = _make_crg_store(tmp_path)  # nodes reference auth.py/api.py/util.py
    # Create the actual source files at the repo root (the CRG file_path base).
    for name in ("auth.py", "api.py", "util.py"):
        (tmp_path / name).write_text(f"# {name}\n", encoding="utf-8")

    runner = CliRunner()
    res = runner.invoke(cli_mod.app, ["init", str(crg_dir)])
    assert res.exit_code == 0, res.output

    # The sidecar lives at the repo root, NOT inside the .code-review-graph dir.
    assert Store.exists(tmp_path)
    assert not Store.exists(crg_dir)

    store = Store.load(tmp_path)
    # Every source resolved against the repo root and hashed (not MISSING_HASH).
    for src in store.source_paths():
        assert store.recorded_hash(src) != MISSING_HASH, f"{src} resolved as missing"

    # A clean tree reports 0 dirty.
    res2 = runner.invoke(cli_mod.app, ["status", "--root", str(tmp_path)])
    assert res2.exit_code == 0, res2.output
    assert "0 dirty of 3" in res2.output
