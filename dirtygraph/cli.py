"""DirtyGraph command-line interface.

A single Typer app wiring the core engine into commands:

* ``init``     — load an existing graph (graphify JSON / code-review-graph SQLite),
                 build the sidecar provenance + propagation graph, hash sources.
* ``add``      — register one derived node + its source file (manual / scripted).
* ``link``     — register a propagation edge (source dep -> dependent node).
* ``touch``    — re-hash one path and, if it changed, mark its dirty closure.
* ``scan``     — re-hash the whole tree, mark every dirty closure (== ``status`` write).
* ``status``   — show the dirty closure (``4 dirty of 1,203``) without re-deriving.
* ``rederive`` — re-derive ONLY the dirty closure through an adapter; print the
                 before/after benchmark (``re-derived 4 nodes (of 1,203)``).
* ``watch``    — live file-event loop (watchdog) that marks dirty on each change.

The propagation graph (edges) is persisted next to the sidecar as
``.dirtygraph/edges.json`` so ``status`` / ``rederive`` can rebuild it without
re-reading the original input graph every time.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

import typer

from . import __version__
from .adapters import DEFAULT_ADAPTER, get_adapter
from .adapters.codegraph import load_graph
from .depgraph import DepGraph
from .dirty import compute_dirty_closure, explain_dirty, mark_dirty
from .rederive import rederive as run_rederive
from .store import MISSING_HASH, STATE_DIRNAME, Store, hash_file

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Bazel-style dirty-marking for agent code knowledge-graphs.",
)

# Edges are persisted beside the sidecar so they survive between invocations.
EDGES_FILENAME = "edges.json"


# --------------------------------------------------------------------------- #
# Edge persistence (the propagation graph)                                    #
# --------------------------------------------------------------------------- #


def _edges_path(root: Path) -> Path:
    return Store.state_dir(root) / EDGES_FILENAME


def _save_edges(root: Path, edges: List[Tuple[str, str, Optional[str]]]) -> None:
    path = _edges_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {"source": s, "target": t, "relation": r} for (s, t, r) in edges
    ]
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _load_edges(root: Path) -> List[Tuple[str, str, Optional[str]]]:
    path = _edges_path(root)
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [(str(e["source"]), str(e["target"]), e.get("relation")) for e in raw]


def _build_depgraph(store: Store, root: Path) -> DepGraph:
    """Reconstruct the propagation graph from persisted edges + sidecar nodes."""
    edges = _load_edges(root)
    edge_pairs = [(s, t) for (s, t, _r) in edges]
    relations = [r for (_s, _t, r) in edges]
    return DepGraph.from_edges(
        list(store.node_ids), edge_pairs, relations=relations
    )


def _resolve_root(graph_path: Path) -> Path:
    """The sidecar lives in the directory that holds the graph (or the dir itself)."""
    p = graph_path
    return p if p.is_dir() else p.parent


def _open_store(root: Path) -> Store:
    if not Store.exists(root):
        typer.secho(
            f"no DirtyGraph state under {root}; run 'dirtygraph init <graph>' first",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    return Store.load(root)


def _fmt(n: int) -> str:
    return f"{n:,}"


def _is_sidecar_path(src_path: str) -> bool:
    """True if a filesystem event path is inside the ``.dirtygraph`` sidecar dir.

    The ``watch`` loop persists its own state writes there; without this filter
    a save fires a watchdog event that re-arms the loop (the save no-op guard
    in :meth:`Store.save` covers the byte-identical case, but a genuine state
    change would still echo). Ignoring the sidecar dir entirely closes it.
    Matches ``.dirtygraph`` as a whole path component so a sibling named
    ``.dirtygraph_backup`` does NOT false-positive.
    """
    if not src_path:
        return False
    return STATE_DIRNAME in Path(src_path).parts


# --------------------------------------------------------------------------- #
# init                                                                        #
# --------------------------------------------------------------------------- #


@app.command()
def init(
    graph: Path = typer.Argument(
        ..., exists=True, help="Path to a graphify graph.json or a .code-review-graph store."
    ),
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        help="Directory to resolve node source paths against (default: graph's dir).",
    ),
) -> None:
    """Load an existing graph, hash every source file, and write the sidecar."""
    loaded = load_graph(graph)
    base = root if root is not None else _resolve_root(graph)
    base = base.resolve()

    store = Store(root=base, graph_path=str(graph), graph_kind=loaded.kind)
    for rec in loaded.nodes:
        store.add(rec.node_id, rec.source_path, label=rec.label)

    # Hash every distinct source once and stamp the clean baseline.
    hashes = store.compute_hashes()
    store.stamp_hashes(hashes)
    store.save()

    _save_edges(base, loaded.edges)

    typer.secho(
        f"initialised {_fmt(len(store))} nodes "
        f"({_fmt(len(loaded.edges))} edges, {_fmt(len(store.source_paths()))} sources) "
        f"from {loaded.kind} graph",
        fg=typer.colors.GREEN,
    )
    typer.echo(f"  state: {Store.state_path(base)}")


# --------------------------------------------------------------------------- #
# add / link                                                                  #
# --------------------------------------------------------------------------- #


@app.command()
def add(
    node_id: str = typer.Argument(..., help="Id of the derived node to register."),
    source: Path = typer.Argument(..., help="Source file this node derives from."),
    root: Path = typer.Option(
        Path("."), "--root", help="DirtyGraph root holding the sidecar."
    ),
    label: Optional[str] = typer.Option(None, "--label", help="Human-readable label."),
) -> None:
    """Register one derived node + its source dependency (creates state if absent).

    Lets a script wire up a graph node-by-node without an importable graph file —
    the manual counterpart to ``init``.
    """
    base = root.resolve()
    store = Store.load(base) if Store.exists(base) else Store(root=base)
    rel = _as_relative(source, base)
    entry = store.add(node_id, rel, label=label)
    # Hash the new source immediately so a later status only fires on a real edit.
    h = store.compute_hashes()
    store.stamp_hashes({entry.source_path: h.get(entry.source_path, "")})
    store.save()
    typer.secho(
        f"added node {node_id!r} <- {rel}", fg=typer.colors.GREEN
    )


@app.command()
def link(
    source: str = typer.Argument(..., help="Dependency node id (the one that changes)."),
    target: str = typer.Argument(..., help="Dependent node id (restaged on change)."),
    root: Path = typer.Option(
        Path("."), "--root", help="DirtyGraph root holding the sidecar."
    ),
    relation: Optional[str] = typer.Option(
        None, "--relation", help="Relation kind label (CALLS / IMPORTS / ...)."
    ),
) -> None:
    """Register a propagation edge ``source -> target``.

    Direction is propagation direction: a change to ``source`` restages
    ``target``. Persisted to ``.dirtygraph/edges.json``.
    """
    base = root.resolve()
    edges = _load_edges(base)
    if (source, target, relation) not in edges and not any(
        s == source and t == target for (s, t, _r) in edges
    ):
        edges.append((source, target, relation))
        _save_edges(base, edges)
        typer.secho(f"linked {source} -> {target}", fg=typer.colors.GREEN)
    else:
        typer.echo(f"edge {source} -> {target} already present")


def _as_relative(path: Path, base: Path) -> str:
    """Store a path relative to the root when possible, else absolute."""
    p = path.resolve()
    try:
        return str(p.relative_to(base.resolve()))
    except ValueError:
        return str(p)


# --------------------------------------------------------------------------- #
# touch / scan                                                                #
# --------------------------------------------------------------------------- #


@app.command()
def touch(
    path: Path = typer.Argument(..., help="A single source file to re-check."),
    root: Path = typer.Option(
        Path("."), "--root", help="DirtyGraph root holding the sidecar."
    ),
) -> None:
    """Re-hash ONE path; if it changed, dirty-mark its closure and persist.

    The targeted counterpart to ``scan`` — cheap when you already know which
    file an editor / agent just wrote. Only the named file is read from disk
    (every other tracked source keeps its recorded hash, so it reads as
    "unchanged"); change detection therefore touches one file, not the whole
    tree.
    """
    base = root.resolve()
    store = _open_store(base)
    graph = _build_depgraph(store, base)

    # Targeted re-hash: start from the RECORDED hashes (no disk reads) so
    # every tracked path reads as "unchanged", then hash ONLY the named file
    # and stamp it into the dict. Key it by both the relative form and any
    # stored source path that resolves to the same absolute file (covers paths
    # stored absolutely or with a different relative shape).
    targeted: dict[str, str] = {
        src: (store.recorded_hash(src) or MISSING_HASH)
        for src in store.source_paths()
    }
    new_hash = hash_file(path)
    rel = _as_relative(path, base)
    targeted[rel] = new_hash
    abs_path = str(path.resolve())
    for src in store.source_paths():
        if str(store.resolve(src)) == abs_path:
            targeted[src] = new_hash

    result = mark_dirty(store, graph, current_hashes=targeted, persist=True)
    if rel in result.changed_paths or str(path) in result.changed_paths:
        typer.secho(
            f"{rel} changed -> {_fmt(result.dirty_count)} dirty", fg=typer.colors.YELLOW
        )
    else:
        typer.echo(f"{rel} unchanged ({_fmt(result.dirty_count)} dirty total)")


@app.command()
def scan(
    root: Path = typer.Option(
        Path("."), "--root", help="DirtyGraph root holding the sidecar."
    ),
) -> None:
    """Re-hash the whole tree and dirty-mark every affected closure (persisted)."""
    base = root.resolve()
    store = _open_store(base)
    graph = _build_depgraph(store, base)
    result = mark_dirty(store, graph, persist=True)
    color = typer.colors.YELLOW if result.dirty_count else typer.colors.GREEN
    typer.secho(
        f"{_fmt(len(result.changed_paths))} changed sources -> "
        f"{result.headline()}",
        fg=color,
    )


# --------------------------------------------------------------------------- #
# status                                                                      #
# --------------------------------------------------------------------------- #


@app.command()
def status(
    root: Path = typer.Option(
        Path("."), "--root", help="DirtyGraph root holding the sidecar."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="List each dirty node id."
    ),
    write: bool = typer.Option(
        True, "--write/--no-write", help="Persist the dirty bits to the sidecar."
    ),
    why: bool = typer.Option(
        False,
        "--why",
        help="Explain WHY each node is dirty: the changed source (direct) or the propagation path (propagated).",
    ),
) -> None:
    """Show the dirty closure without re-deriving — the ``N dirty of TOTAL`` line."""
    base = root.resolve()
    store = _open_store(base)
    graph = _build_depgraph(store, base)
    result = compute_dirty_closure(store, graph)

    if write:
        store.mark_dirty(result.closure)
        store.save()

    color = typer.colors.YELLOW if not result.is_clean else typer.colors.GREEN
    typer.secho(f"dirty closure: {result.headline()}", fg=color)
    if result.changed_paths:
        typer.echo(f"  changed sources: {_fmt(len(result.changed_paths))}")
        typer.echo(
            f"  direct hits: {_fmt(len(result.direct))} | "
            f"propagated: {_fmt(len(result.propagated))}"
        )
    if verbose or why:
        causes = explain_dirty(store, graph, result) if why else None
        for nid in result.ordered_closure():
            mark = "*" if nid in result.direct else " "
            if why and causes is not None:
                cause = next((c for c in causes if c.node_id == nid), None)
                if cause is not None and cause.kind == "direct":
                    srcs = ", ".join(cause.sources) or "?"
                    typer.echo(f"  [direct]    {nid}  <- source: {srcs}")
                elif cause is not None and cause.kind == "propagated":
                    chain = " -> ".join(cause.via) if cause.via else "?"
                    typer.echo(f"  [propagated] {nid}  via {chain}")
                else:
                    typer.echo(f"  [{mark}] {nid}")
            else:
                typer.echo(f"  [{mark}] {nid}")


# --------------------------------------------------------------------------- #
# reset                                                                       #
# --------------------------------------------------------------------------- #


@app.command()
def reset(
    root: Path = typer.Option(
        Path("."), "--root", help="DirtyGraph root holding the sidecar."
    ),
) -> None:
    """Re-baseline the sidecar as clean: clear all dirty bits + re-stamp hashes.

    Useful when the tree is known-good (fresh clone, after a pull, before a
    benchmark) and you want a clean baseline without editing any source file
    or re-running ``init`` (which re-reads the input graph). Idempotent: a
    clean sidecar resets to clean and writes nothing new.
    """
    base = root.resolve()
    store = _open_store(base)
    cleared = store.reset()
    store.save()
    typer.secho(
        f"reset: cleared {_fmt(cleared)} dirty bit(s), "
        f"re-stamped {_fmt(len(store.source_paths()))} source hash(es)",
        fg=typer.colors.GREEN,
    )


# --------------------------------------------------------------------------- #
# rederive                                                                    #
# --------------------------------------------------------------------------- #


@app.command()
def rederive(
    root: Path = typer.Option(
        Path("."), "--root", help="DirtyGraph root holding the sidecar."
    ),
    adapter: str = typer.Option(
        DEFAULT_ADAPTER, "--adapter", "-a", help="Adapter name (echo / codegraph)."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="List each re-derived node id."
    ),
) -> None:
    """Re-derive ONLY the dirty closure and print the before/after benchmark.

    Marks dirty first (so a fresh ``rederive`` after an edit picks up changes),
    then re-derives just the dirty subgraph through the chosen adapter, clears
    the bits, and re-stamps hashes. A second run with no edits re-derives 0.
    The tree is hashed exactly ONCE per invocation (the snapshot is shared
    between change detection and the re-derive checkpoint), not twice.
    """
    base = root.resolve()
    store = _open_store(base)
    graph = _build_depgraph(store, base)

    # Detect changes + mark before re-deriving so the engine has a dirty set.
    # Hash the tree ONCE and thread the snapshot into the engine so it does not
    # re-hash on the checkpoint pass.
    current_hashes = store.compute_hashes()
    mark_dirty(store, graph, current_hashes=current_hashes, persist=False)

    try:
        impl = get_adapter(adapter)
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    result = run_rederive(
        store, graph, impl, persist=True, current_hashes=current_hashes
    )

    typer.secho(result.headline(), fg=typer.colors.GREEN, bold=True)
    if result.failed:
        typer.secho(
            f"  {_fmt(result.failed_count)} failed (dirty bit kept)",
            fg=typer.colors.RED,
        )
        if verbose:
            for nid, err in result.failed.items():
                typer.echo(f"    ! {nid}: {err}")
    if verbose:
        for nid in result.rederived:
            typer.echo(f"  + {nid}")


# --------------------------------------------------------------------------- #
# watch                                                                       #
# --------------------------------------------------------------------------- #


@app.command()
def watch(
    root: Path = typer.Option(
        Path("."), "--root", help="DirtyGraph root holding the sidecar."
    ),
    debounce: float = typer.Option(
        0.4, "--debounce", help="Seconds to coalesce rapid file events."
    ),
) -> None:
    """Live file-event loop: on each source change, recompute + print the closure.

    Uses watchdog to observe the root tree; everything else in DirtyGraph is
    one-shot. Press Ctrl-C to stop.
    """
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:  # pragma: no cover - watchdog is a declared dependency
        typer.secho(
            "the 'watchdog' package is required for 'watch'; pip install dirtygraph",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    base = root.resolve()
    store = _open_store(base)
    graph = _build_depgraph(store, base)

    typer.secho(
        f"watching {base} ({_fmt(len(store))} nodes); Ctrl-C to stop",
        fg=typer.colors.BLUE,
    )

    state = {"pending": False, "last": 0.0}

    class _Handler(FileSystemEventHandler):
        def on_any_event(self, event) -> None:  # noqa: D401
            if getattr(event, "is_directory", False):
                return
            # Ignore our own sidecar writes (.dirtygraph/) so a save never
            # re-arms the loop — the save no-op guard covers the
            # byte-identical case, but a genuine state change would still echo.
            src = getattr(event, "src_path", "") or ""
            if _is_sidecar_path(src):
                return
            state["pending"] = True
            state["last"] = time.monotonic()

    observer = Observer()
    observer.schedule(_Handler(), str(base), recursive=True)
    observer.start()
    try:
        while True:
            time.sleep(debounce / 2)
            if state["pending"] and (time.monotonic() - state["last"]) >= debounce:
                state["pending"] = False
                reloaded = Store.load(base)
                result = mark_dirty(reloaded, graph, persist=True)
                if not result.is_clean:
                    typer.secho(
                        f"dirty closure: {result.headline()}",
                        fg=typer.colors.YELLOW,
                    )
    except KeyboardInterrupt:  # pragma: no cover - interactive
        typer.echo("\nstopped")
    finally:
        observer.stop()
        observer.join()


# --------------------------------------------------------------------------- #
# version                                                                     #
# --------------------------------------------------------------------------- #


@app.command()
def version() -> None:
    """Print the DirtyGraph version."""
    typer.echo(__version__)


def main(argv: Optional[List[str]] = None) -> None:  # pragma: no cover - entrypoint
    """Console-script entry point."""
    app(args=argv if argv is not None else sys.argv[1:])


if __name__ == "__main__":  # pragma: no cover
    main()
