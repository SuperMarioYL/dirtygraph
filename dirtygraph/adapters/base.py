"""The adapter interface — re-derive one node.

An adapter's single job is: given a node (its id, source path, label) and the
current on-disk source file, recompute that node's derived content. DirtyGraph
only ever calls this for nodes in the dirty closure, so an adapter never needs to
know about graphs, hashes, or dirty bits — the engine handles all of that.

:class:`Adapter` is the abstract base. :class:`EchoAdapter` is the zero-config
default: it re-reads the source file and returns a small deterministic summary, so
the full ``init -> status -> rederive`` happy path (and the benchmark line) runs
with no network and no API keys. Real re-summarisation lives in
:class:`~dirtygraph.adapters.codegraph.CodeGraphAdapter`.
"""

from __future__ import annotations

import abc
from pathlib import Path
from typing import Any, Optional

from ..rederive import NodeView

__all__ = ["Adapter", "EchoAdapter", "DerivedContent"]


class DerivedContent:
    """The value an adapter returns for one re-derived node.

    The engine treats the return value opaquely (it only cares that
    ``re_derive`` returned without raising), but a structured object makes the
    echo/test output legible and gives downstream adapters a stable shape to
    extend.
    """

    __slots__ = ("node_id", "summary", "source_path", "byte_len", "extra")

    def __init__(
        self,
        node_id: str,
        summary: str,
        *,
        source_path: str,
        byte_len: int,
        extra: Optional[dict] = None,
    ) -> None:
        self.node_id = node_id
        self.summary = summary
        self.source_path = source_path
        self.byte_len = byte_len
        self.extra = extra or {}

    def __repr__(self) -> str:  # pragma: no cover - debug convenience
        return (
            f"DerivedContent(node_id={self.node_id!r}, "
            f"byte_len={self.byte_len}, summary={self.summary!r})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DerivedContent):
            return NotImplemented
        return (
            self.node_id == other.node_id
            and self.summary == other.summary
            and self.source_path == other.source_path
            and self.byte_len == other.byte_len
        )


class Adapter(abc.ABC):
    """Abstract re-derivation adapter.

    Subclasses implement :meth:`re_derive`. The base provides
    :meth:`read_source` (resolve + read the node's source file, tolerating a
    missing file) so concrete adapters share one consistent I/O path, and a
    ``name`` used in CLI output.
    """

    #: Short adapter name, surfaced by ``dirtygraph rederive`` output.
    name: str = "adapter"

    @abc.abstractmethod
    def re_derive(self, node: NodeView) -> Any:
        """Recompute and return ``node``'s derived content.

        Called once per dirty node, in topological order. Must be deterministic
        for a given source file when no external service is involved (so a
        no-change ``rederive`` is a true no-op). Raising marks the node failed;
        the engine leaves its dirty bit set for a later retry.
        """
        raise NotImplementedError

    # ----- shared helpers ---------------------------------------------------

    @staticmethod
    def read_source(node: NodeView) -> Optional[bytes]:
        """Read the node's source file from its already-resolved path.

        Returns ``None`` when the file is absent (e.g. the source was deleted
        but the graph still references it) so adapters can degrade gracefully
        rather than crash the whole re-derive batch.
        """
        p = Path(node.resolved_path)
        if not p.is_file():
            return None
        return p.read_bytes()

    def describe(self) -> str:
        """Human-readable adapter identity for CLI/status output."""
        return self.name


class EchoAdapter(Adapter):
    """Zero-config default adapter — re-read the source, return a stub summary.

    No network, no keys, fully deterministic: the same source bytes always
    produce the same :class:`DerivedContent`, which is what makes a second
    ``rederive`` (with no edits) re-derive zero nodes. This is the adapter the
    quickstart and the demo benchmark run against.
    """

    name = "echo"

    #: How many leading characters of the source to fold into the stub summary.
    PREVIEW_CHARS = 80

    def re_derive(self, node: NodeView) -> DerivedContent:
        data = self.read_source(node)
        if data is None:
            return DerivedContent(
                node_id=node.node_id,
                summary=f"[source missing] {node.source_path}",
                source_path=node.source_path,
                byte_len=0,
                extra={"missing": True},
            )
        text = data.decode("utf-8", errors="replace")
        preview = " ".join(text.split())[: self.PREVIEW_CHARS]
        label = node.label or node.node_id
        summary = f"{label} :: {len(data)}B :: {preview}"
        return DerivedContent(
            node_id=node.node_id,
            summary=summary,
            source_path=node.source_path,
            byte_len=len(data),
        )
