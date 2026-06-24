"""The codegraph adapter — DirtyGraph's primary use case.

This module does two distinct jobs:

1. **Loaders (init-time)** — read an *existing* codebase knowledge-graph into the
   three things DirtyGraph needs: per-node ``(node_id, source_path, label)``
   provenance and the propagation edges. Two input formats ship:

   * **graphify** exports a NetworkX node-link ``graph.json`` (envelope keys
     ``directed/multigraph/graph/nodes/links/...``). Each node carries a single
     ``source_file`` and the relations live under top-level ``links``.
   * **code-review-graph** stores its graph as a **SQLite DB** under
     ``.code-review-graph/``: a ``nodes`` table (``file_path``, ``qualified_name``)
     and an ``edges`` table (``source_qualified -> target_qualified``,
     ``kind`` = CALLS / IMPORTS_FROM / INHERITS / REFERENCES).

   DirtyGraph reads these but never *builds* them — adapting an existing graph is
   the whole point (building one is explicitly out of scope).

2. **Re-derivation (rederive-time)** — recompute a node's derived content. By
   default this is a deterministic local summary (no network). When the
   ``DIRTYGRAPH_LLM`` env vars are present it routes through a DeepSeek / Qwen
   OpenAI-compatible endpoint to re-summarise the source — the China-surface
   adapter the existing catalogs don't ship. The LLM path fails soft: any error
   falls back to the deterministic summary so a re-derive batch never dies on a
   flaky endpoint.

Edge orientation: a code relation ``A CALLS B`` means B is a *dependency* of A, so
a change to B should restage A. The loaders therefore orient propagation edges
as ``dependency -> dependent`` (i.e. they reverse CALLS/IMPORTS_FROM/REFERENCES,
which point dependent -> dependency in the source graph) so that the engine's
forward-reachable walk restages the right side. INHERITS is treated the same way
(a base-class change restages subclasses).
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..depgraph import DepGraph
from ..rederive import NodeView
from .base import Adapter, DerivedContent

__all__ = [
    "CodeGraphAdapter",
    "LoadedGraph",
    "NodeRecord",
    "detect_format",
    "load_graph",
    "load_graphify_json",
    "load_crg_sqlite",
    "CRG_DIRNAME",
]

# code-review-graph keeps its SQLite store under this directory next to the repo.
CRG_DIRNAME = ".code-review-graph"

# Relation kinds whose edge points dependent -> dependency in the source graph;
# we reverse them so propagation flows dependency -> dependent.
_REVERSED_RELATIONS = {
    "CALLS",
    "IMPORTS_FROM",
    "IMPORTS",
    "REFERENCES",
    "INHERITS",
    "EXTENDS",
    "USES",
}


# --------------------------------------------------------------------------- #
# Loaded-graph data structures                                                #
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class NodeRecord:
    """One node lifted from the input graph: just the provenance DirtyGraph needs."""

    node_id: str
    source_path: str
    label: Optional[str] = None


@dataclass(slots=True)
class LoadedGraph:
    """The result of loading an input graph.

    ``nodes`` carries provenance for the sidecar; ``edges`` are already oriented
    as propagation edges (``dependency -> dependent``) with their relation kind.
    ``kind`` is ``"graphify"`` or ``"crg"`` for status output.
    """

    nodes: List[NodeRecord] = field(default_factory=list)
    edges: List[Tuple[str, str, Optional[str]]] = field(default_factory=list)
    kind: str = "unknown"

    def node_ids(self) -> List[str]:
        return [n.node_id for n in self.nodes]

    def to_depgraph(self) -> DepGraph:
        """Build the propagation :class:`DepGraph` from the oriented edges."""
        edge_pairs = [(s, t) for (s, t, _rel) in self.edges]
        relations = [rel for (_s, _t, rel) in self.edges]
        return DepGraph.from_edges(
            self.node_ids(), edge_pairs, relations=relations
        )


# --------------------------------------------------------------------------- #
# Format detection + dispatch                                                 #
# --------------------------------------------------------------------------- #


def detect_format(path: os.PathLike[str] | str) -> str:
    """Classify an input graph path as ``"graphify"`` or ``"crg"``.

    A directory named ``.code-review-graph`` (or one containing a ``*.db`` /
    ``*.sqlite``) is code-review-graph; a ``.json`` file is graphify node-link.
    """
    p = Path(path)
    if p.is_dir():
        if p.name == CRG_DIRNAME or (p / "graph.db").exists():
            return "crg"
        # A directory that *contains* a .code-review-graph store.
        if (p / CRG_DIRNAME).is_dir():
            return "crg"
        if any(p.glob("*.db")) or any(p.glob("*.sqlite")):
            return "crg"
        raise ValueError(f"cannot classify graph directory: {p}")
    suffix = p.suffix.lower()
    if suffix in {".db", ".sqlite", ".sqlite3"}:
        return "crg"
    if suffix == ".json":
        return "graphify"
    raise ValueError(
        f"unrecognised graph format for {p}; expected a graphify .json or a "
        f"code-review-graph SQLite store"
    )


def load_graph(path: os.PathLike[str] | str) -> LoadedGraph:
    """Detect the input format and load it into a :class:`LoadedGraph`."""
    fmt = detect_format(path)
    if fmt == "graphify":
        return load_graphify_json(path)
    return load_crg_sqlite(path)


# --------------------------------------------------------------------------- #
# graphify NetworkX node-link JSON loader                                     #
# --------------------------------------------------------------------------- #

_GRAPHIFY_SOURCE_KEYS = ("source_file", "source_path", "file_path", "file")
_GRAPHIFY_LABEL_KEYS = ("label", "norm_label", "name", "qualified_name")


def _first_present(d: Dict[str, Any], keys: Tuple[str, ...]) -> Optional[str]:
    for k in keys:
        v = d.get(k)
        if v:
            return str(v)
    return None


def load_graphify_json(path: os.PathLike[str] | str) -> LoadedGraph:
    """Load a graphify NetworkX node-link ``graph.json``.

    Envelope: ``{directed, multigraph, graph, nodes:[...], links:[...]}``. Each
    node has an ``id`` and a single ``source_file``; relations are under
    ``links`` as ``{source, target, relation, ...}``. Nodes without a source
    file are loaded (so edges stay consistent) but get an empty source path.
    """
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    raw_nodes = data.get("nodes", [])
    raw_links = data.get("links", data.get("edges", []))

    nodes: List[NodeRecord] = []
    known: set[str] = set()
    for n in raw_nodes:
        node_id = str(n.get("id"))
        if node_id == "None":
            continue
        source = _first_present(n, _GRAPHIFY_SOURCE_KEYS) or ""
        label = _first_present(n, _GRAPHIFY_LABEL_KEYS)
        nodes.append(NodeRecord(node_id=node_id, source_path=source, label=label))
        known.add(node_id)

    edges: List[Tuple[str, str, Optional[str]]] = []
    for link in raw_links:
        src = link.get("source")
        tgt = link.get("target")
        if src is None or tgt is None:
            continue
        src, tgt = str(src), str(tgt)
        relation = link.get("relation") or link.get("kind")
        rel_norm = str(relation).upper() if relation else None
        oriented = _orient(src, tgt, rel_norm)
        edges.append((oriented[0], oriented[1], relation if relation else None))

    return LoadedGraph(nodes=nodes, edges=edges, kind="graphify")


# --------------------------------------------------------------------------- #
# code-review-graph SQLite loader                                             #
# --------------------------------------------------------------------------- #


def _resolve_crg_db(path: os.PathLike[str] | str) -> Path:
    """Resolve an input path to the actual SQLite file inside a CRG store."""
    p = Path(path)
    if p.is_file():
        return p
    candidates: List[Path] = []
    if p.name == CRG_DIRNAME:
        candidates.append(p)
    candidates.append(p / CRG_DIRNAME)
    candidates.append(p)
    for base in candidates:
        if not base.is_dir():
            continue
        for name in ("graph.db", "graph.sqlite", "code-review-graph.db"):
            db = base / name
            if db.is_file():
                return db
        found = sorted(base.glob("*.db")) + sorted(base.glob("*.sqlite"))
        if found:
            return found[0]
    raise FileNotFoundError(f"no SQLite store found under {p}")


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def load_crg_sqlite(path: os.PathLike[str] | str) -> LoadedGraph:
    """Load a code-review-graph SQLite store.

    Reads the ``nodes`` table (id/qualified_name + ``file_path``) and the
    ``edges`` table (``source_qualified -> target_qualified``, ``kind``). Column
    names are probed via PRAGMA so minor schema drift (``id`` vs ``qualified_name``
    as the key) is tolerated. Edges are oriented to ``dependency -> dependent``.
    """
    db = _resolve_crg_db(path)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        node_cols = _table_columns(conn, "nodes")
        id_col = "qualified_name" if "qualified_name" in node_cols else "id"
        label_col = (
            "name" if "name" in node_cols
            else ("qualified_name" if "qualified_name" in node_cols else id_col)
        )
        path_col = "file_path" if "file_path" in node_cols else "source_file"

        nodes: List[NodeRecord] = []
        for row in conn.execute(f"SELECT * FROM nodes"):
            node_id = row[id_col]
            if node_id is None:
                continue
            nodes.append(
                NodeRecord(
                    node_id=str(node_id),
                    source_path=str(row[path_col] or ""),
                    label=str(row[label_col]) if row[label_col] is not None else None,
                )
            )

        edge_cols = _table_columns(conn, "edges")
        src_col = "source_qualified" if "source_qualified" in edge_cols else "source"
        tgt_col = "target_qualified" if "target_qualified" in edge_cols else "target"
        kind_col = "kind" if "kind" in edge_cols else (
            "relation" if "relation" in edge_cols else None
        )

        edges: List[Tuple[str, str, Optional[str]]] = []
        for row in conn.execute(f"SELECT * FROM edges"):
            src = row[src_col]
            tgt = row[tgt_col]
            if src is None or tgt is None:
                continue
            relation = row[kind_col] if kind_col else None
            rel_norm = str(relation).upper() if relation else None
            oriented = _orient(str(src), str(tgt), rel_norm)
            edges.append((oriented[0], oriented[1], relation if relation else None))
    finally:
        conn.close()

    return LoadedGraph(nodes=nodes, edges=edges, kind="crg")


# --------------------------------------------------------------------------- #
# Edge orientation                                                            #
# --------------------------------------------------------------------------- #


def _orient(src: str, tgt: str, relation: Optional[str]) -> Tuple[str, str]:
    """Orient a source-graph edge into a propagation edge (dependency -> dependent).

    Source graphs encode ``A CALLS B`` as ``A -> B`` (dependent -> dependency).
    For propagation we want a change to B to restage A, so we reverse those
    relations. Unknown relations are left as given (best-effort forward flow).
    """
    if relation in _REVERSED_RELATIONS:
        return tgt, src
    return src, tgt


# --------------------------------------------------------------------------- #
# The adapter                                                                 #
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _LLMConfig:
    """OpenAI-compatible endpoint config for the optional DeepSeek/Qwen path."""

    base_url: str
    api_key: str
    model: str
    timeout: float = 30.0

    @classmethod
    def from_env(cls) -> Optional["_LLMConfig"]:
        """Build config from ``DIRTYGRAPH_LLM_*`` env vars, or ``None`` if unset.

        ``DIRTYGRAPH_LLM`` must be a truthy switch; ``DIRTYGRAPH_LLM_API_KEY`` is
        required. ``BASE_URL`` defaults to DeepSeek; ``MODEL`` to ``deepseek-chat``.
        Set ``BASE_URL`` to a Qwen-compatible (DashScope) endpoint + ``MODEL`` to
        ``qwen-plus`` to route through Qwen instead.
        """
        switch = os.environ.get("DIRTYGRAPH_LLM", "").strip().lower()
        if switch in {"", "0", "false", "no", "off"}:
            return None
        api_key = os.environ.get("DIRTYGRAPH_LLM_API_KEY", "").strip()
        if not api_key:
            return None
        base_url = os.environ.get(
            "DIRTYGRAPH_LLM_BASE_URL", "https://api.deepseek.com/v1"
        ).rstrip("/")
        model = os.environ.get("DIRTYGRAPH_LLM_MODEL", "deepseek-chat").strip()
        try:
            timeout = float(os.environ.get("DIRTYGRAPH_LLM_TIMEOUT", "30"))
        except ValueError:
            timeout = 30.0
        return cls(base_url=base_url, api_key=api_key, model=model, timeout=timeout)


class CodeGraphAdapter(Adapter):
    """Re-derive a codebase-graph node's summary.

    Default path is a deterministic local summary (no network). When the
    ``DIRTYGRAPH_LLM`` env switch + an API key are set, re-derivation routes
    through a DeepSeek / Qwen OpenAI-compatible chat endpoint and re-summarises
    the node's source. The LLM path fails soft: on any network/parse error it
    falls back to the deterministic summary, so a re-derive batch never dies on a
    flaky endpoint.

    The class also exposes the loaders as static methods so ``cli init`` can build
    the sidecar + propagation graph from the same module that re-derives.
    """

    name = "codegraph"

    #: Chars of source folded into the deterministic summary.
    SUMMARY_CHARS = 160

    def __init__(self, *, llm: Optional[_LLMConfig] = None) -> None:
        # Resolve LLM config at construction so the adapter is cheap to call in a
        # loop; ``None`` means "deterministic local summary only".
        self._llm = llm if llm is not None else _LLMConfig.from_env()

    # ----- loaders (re-exported for the CLI) -------------------------------

    @staticmethod
    def load(path: os.PathLike[str] | str) -> LoadedGraph:
        return load_graph(path)

    @staticmethod
    def detect_format(path: os.PathLike[str] | str) -> str:
        return detect_format(path)

    # ----- re-derivation ----------------------------------------------------

    @property
    def uses_llm(self) -> bool:
        return self._llm is not None

    def re_derive(self, node: NodeView) -> DerivedContent:
        data = self.read_source(node)
        if data is None:
            return DerivedContent(
                node_id=node.node_id,
                summary=f"[source missing] {node.source_path}",
                source_path=node.source_path,
                byte_len=0,
                extra={"missing": True, "mode": "missing"},
            )
        text = data.decode("utf-8", errors="replace")

        if self._llm is not None:
            llm_summary = self._summarise_via_llm(node, text)
            if llm_summary is not None:
                return DerivedContent(
                    node_id=node.node_id,
                    summary=llm_summary,
                    source_path=node.source_path,
                    byte_len=len(data),
                    extra={"mode": "llm", "model": self._llm.model},
                )
            # LLM failed — fall through to the deterministic summary.

        return DerivedContent(
            node_id=node.node_id,
            summary=self._summarise_local(node, text),
            source_path=node.source_path,
            byte_len=len(data),
            extra={"mode": "local"},
        )

    # ----- summary strategies ----------------------------------------------

    def _summarise_local(self, node: NodeView, text: str) -> str:
        """Deterministic, network-free re-summary used by default and as the
        LLM fallback."""
        lines = text.splitlines()
        loc = len(lines)
        head = " ".join(text.split())[: self.SUMMARY_CHARS]
        label = node.label or node.node_id
        return f"{label} ({loc} LOC) :: {head}"

    def _summarise_via_llm(self, node: NodeView, text: str) -> Optional[str]:
        """Re-summarise ``text`` through the configured DeepSeek/Qwen endpoint.

        Returns the summary string, or ``None`` on any error so the caller can
        fall back. Uses only the stdlib (urllib) — no extra dependency.
        """
        cfg = self._llm
        assert cfg is not None
        # Keep the prompt small; truncate very large sources so one node never
        # blows the context window.
        snippet = text[:6000]
        label = node.label or node.node_id
        payload = {
            "model": cfg.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You re-summarise a single source file behind one node of "
                        "a code knowledge-graph. Reply with one concise sentence "
                        "describing what this code does."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Node: {label}\nFile: {node.source_path}\n\n{snippet}",
                },
            ],
            "temperature": 0.0,
            "stream": False,
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{cfg.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {cfg.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=cfg.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError):
            return None
        try:
            obj = json.loads(raw)
            content = obj["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            return None
        summary = str(content).strip()
        return summary or None
