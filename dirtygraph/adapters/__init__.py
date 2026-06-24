"""Pluggable re-derivation adapters.

An adapter is the thing DirtyGraph calls per node when it re-derives the dirty
closure. The engine (:mod:`dirtygraph.rederive`) does not care *how* a node's
derived content is recomputed — it only walks the dirty subgraph in topological
order and hands each node to ``adapter.re_derive(NodeView)``. That keeps the
invalidation primitive (the actual contribution) decoupled from whatever produces
the derived content downstream.

Two adapters ship in v0.1:

* :class:`~dirtygraph.adapters.base.Adapter` — the abstract base every adapter
  subclasses, plus :class:`~dirtygraph.adapters.base.EchoAdapter`, the zero-config
  default that re-reads the source file and returns a deterministic stub. It needs
  no network and makes the benchmark (``re-derived 4 nodes (of 1,203)``) runnable
  out of the box.
* :class:`~dirtygraph.adapters.codegraph.CodeGraphAdapter` — the primary use case:
  it loads a graphify NetworkX node-link ``graph.json`` or a code-review-graph
  ``.code-review-graph/`` SQLite store, and re-summarises a node either with a
  built-in deterministic summary or — when ``DIRTYGRAPH_LLM`` env vars are set —
  through a DeepSeek / Qwen endpoint.

Resolve an adapter by name with :func:`get_adapter`.
"""

from __future__ import annotations

from typing import Dict, Type

from .base import Adapter, EchoAdapter
from .codegraph import CodeGraphAdapter

__all__ = [
    "Adapter",
    "EchoAdapter",
    "CodeGraphAdapter",
    "get_adapter",
    "ADAPTERS",
    "DEFAULT_ADAPTER",
]

DEFAULT_ADAPTER = "echo"

# Name -> adapter class. The CLI's ``--adapter`` flag indexes this.
ADAPTERS: Dict[str, Type[Adapter]] = {
    "echo": EchoAdapter,
    "codegraph": CodeGraphAdapter,
}


def get_adapter(name: str, **kwargs) -> Adapter:
    """Instantiate a registered adapter by name.

    Raises ``KeyError`` (wrapped as ``ValueError`` with the valid names) for an
    unknown adapter so the CLI can print a helpful message instead of a stack
    trace.
    """
    key = (name or DEFAULT_ADAPTER).strip().lower()
    try:
        cls = ADAPTERS[key]
    except KeyError:
        valid = ", ".join(sorted(ADAPTERS))
        raise ValueError(
            f"unknown adapter {name!r}; choose one of: {valid}"
        ) from None
    return cls(**kwargs)
