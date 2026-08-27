# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-08-27

File-only bug-hunt release: no live feature-request signal, so this mirrors the
sibling-repo file-only posture rather than inventing new scope.

### Fixed
- **stale dirty bits on revert** — `Store.mark_dirty` only flipped dirty bits
  `False→True` and never cleared them, so a source edited then reverted to its
  baseline (byte-identical) hash dropped out of the content-hash closure but
  kept its dirty bit. `dirtygraph status` persisted those stale bits while
  printing the smaller computed-closure headline, and a later `rederive` redid
  nodes whose source was now byte-identical to the baseline (wasted work, wasted
  LLM calls under the `codegraph` adapter + `DIRTYGRAPH_LLM`). `mark_dirty` now
  reconciles the persisted dirty set to the computed closure — clearing bits
  for nodes outside it — so the dirty set is always exactly the content-hash
  closure (true Bazel-style content invalidation). Safe for failed-rederive
  retry: a failed node's hash is never re-stamped, so it stays in the closure.
- **stale `.build_metadata.yaml` `published_version`** — stuck at `v0.1.0`
  while `version` was `v0.3.0`; cleared so the local cache no longer advertises
  a pre-`v0.3.0` release as the latest published truth.

## [0.3.0] - 2026-08-22

Incremental re-derive-on-event + build/publish reconciliation + a CRG-init
root-resolution fix.

### Fixed
- **`init` used the wrong sidecar root when pointed at a `.code-review-graph`
  store directory** — `_resolve_root` returned the store dir itself, so CRG
  node `file_path` values (relative to the repo root) were joined under
  `.code-review-graph/` where no source exists, every node read as
  `MISSING_HASH`, and a change could never be detected. `_resolve_root` now
  returns the store dir's parent (the repo root) for a `.code-review-graph`
  directory; the explicit `--root` option still takes precedence.
- **stale `.build_metadata.yaml` version** — still recorded `v0.1.0` while
  `VERSION`/`pyproject.toml` were at `0.2.0`, risking a re-tag of `v0.1.0` on
  publish. Bumped to `0.3.0` to match.

### Added
- `dirtygraph watch --rederive` — opt-in auto-re-derivation: on a debounced
  file-change event the dirty closure is re-derived through `--adapter` and the
  before/after is printed (`N dirty of TOTAL` then `re-derived M nodes`). Reuses
  m3's adapter and m5's targeted single-path hashing so only the changed files
  are re-read from disk. Off by default to avoid surprising CPU/LLM calls.

### Changed
- README license references reconciled to Apache-2.0: the English license
  badge and both License sections previously mentioned MIT while the LICENSE
  file is Apache-2.0.

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

[Unreleased]: https://github.com/SuperMarioYL/dirtygraph/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/SuperMarioYL/dirtygraph/releases/tag/v0.4.0
[0.3.0]: https://github.com/SuperMarioYL/dirtygraph/releases/tag/v0.3.0
[0.2.0]: https://github.com/SuperMarioYL/dirtygraph/releases/tag/v0.2.0
[0.1.0]: https://github.com/SuperMarioYL/dirtygraph/releases/tag/v0.1.0
