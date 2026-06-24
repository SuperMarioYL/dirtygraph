# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/SuperMarioYL/dirtygraph/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/SuperMarioYL/dirtygraph/releases/tag/v0.1.0
