# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-08-01

Stability + targeted-perf release. Fixes a watch-loop write-thrash and two
incremental-value-prop gaps; adds `status --why` explanation and a `reset`
re-baseline command.

### Fixed
- **watch loop state.json write-thrash** — `mark_dirty(persist=True)` saved
  `.dirtygraph/state.json` every debounce cycle even when nothing changed, and
  the watchdog observer watched `.dirtygraph/`, so the save re-armed the loop
  infinitely. `Store.save()` now no-ops when the payload is byte-identical to
  the on-disk file, and the `watch` handler ignores events under
  `.dirtygraph/`.
- **`touch` full-scanned instead of hashing one path** — its docstring promised
  a cheap targeted re-hash but it hashed the whole tree. `touch` now builds a
  targeted `current_hashes` dict (recorded hashes for every path + a fresh
  hash for only the named file), so change detection reads exactly one file.
- **`rederive` double-hashed the tree** — the CLI hashed once in `mark_dirty`
  and again in the engine. The engine now accepts a precomputed
  `current_hashes` snapshot, and the CLI computes once and threads it through.

### Added
- `dirtygraph status --why` — explains WHY each dirty node is in the closure:
  the changed source file (direct hits) or the shortest propagation path from
  a direct-hit node down to it (propagated). Uses the existing
  `reachable`/`dependents`/`path_from_any` primitives.
- `dirtygraph reset` — re-baseline the sidecar as clean: clear every dirty bit
  and re-stamp all content hashes from disk, without editing a source file or
  re-running `init`. Idempotent.

## [0.1.0] - 2026-06-24

Initial release. DirtyGraph adapts an existing codebase knowledge-graph and
re-derives only the stale closure when a source file changes.

### Added
- `dirtygraph init ./graph.json` — ingest an existing graph (graphify NetworkX
  node-link JSON or a `.code-review-graph/` SQLite store), record each node's
  single `source_file`/`file_path` provenance, compute a blake3 content hash per
  source file, and persist `.dirtygraph/state.json`.
- `dirtygraph status` — detect changed source files by content hash and report
  the dirty closure (e.g. `4 dirty of 1,203`).
- `dirtygraph rederive` — topo-sort the forward-reachable dirty closure over the
  graph's code-relation edges, re-derive only those nodes through a pluggable
  adapter, and print the before/after benchmark (`re-derived 4 nodes (of 1,203)`).
- `dirtygraph watch ./graph.json` — live file-change watching via `watchdog`.
- `codegraph` adapter with two loaders (graphify node-link JSON and
  code-review-graph SQLite) plus an optional DeepSeek/Qwen re-summarize path
  behind an environment variable.

[Unreleased]: https://github.com/SuperMarioYL/dirtygraph/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/SuperMarioYL/dirtygraph/releases/tag/v0.2.0
[0.1.0]: https://github.com/SuperMarioYL/dirtygraph/releases/tag/v0.1.0
