<div align="right"><sub><b>English</b> · <a href="./README.md">简体中文</a></sub></div>

<p align="center"><picture><source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="./assets/hero-mobile-dark.svg"><source media="(max-width: 640px)" srcset="./assets/hero-mobile-light.svg"><source media="(prefers-color-scheme: dark)" srcset="./assets/hero-dark.svg"><img src="./assets/hero-light.svg" width="1000" alt="DirtyGraph — Change one thing. Not the whole graph."></picture></p>

<p align="center"><b>Change one thing. Not the whole graph.</b><br><sub>Change tracking, dependency propagation and incremental re-derivation for existing code graphs.</sub></p>

<p align="center">
<a href="./LICENSE"><img src="./assets/badge-license.svg" alt="Apache-2.0"></a>
<a href="https://github.com/SuperMarioYL/dirtygraph/actions/workflows/ci.yml"><img src="./assets/badge-ci.svg" alt="CI"></a>
<img src="./assets/badge-python.svg" alt="Python 3.12+">
<a href="https://github.com/SuperMarioYL/dirtygraph/releases"><img src="./assets/badge-release.svg" alt="Latest release"></a>
</p>
<p align="center"><a href="https://dirtygraph.lei6393.com">Website</a> · <a href="#quick-start">Quick start</a> · <a href="#demo">Watch the demo</a> · <a href="./docs/usage.md">Command reference</a> · <a href="https://github.com/SuperMarioYL/dirtygraph/issues">Feedback</a></p>

## Why DirtyGraph

Your code has changed. The summary your agent reads may still describe yesterday's version. Edit `auth.py`, and the authentication node needs updating. Session logic, views and API descriptions that depend on it may be stale too. Regenerating the entire graph works, but processes a lot of unchanged content again.

DirtyGraph brings an established build-system idea to code graphs: **record which file a node comes from, track its dependencies, and restrict re-derivation to the affected subgraph.** Keep your existing graph builder. DirtyGraph answers the next question: which derived nodes need to be recomputed after this edit?

It fits workflows that already have a code graph, document summaries or other derivation tasks. Full rebuilds become less attractive as a graph grows, but the reach of an edit through your dependencies determines how much work can actually be skipped.

<p align="center"><picture><source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="./assets/process-mobile-dark.svg"><source media="(max-width: 640px)" srcset="./assets/process-mobile-light.svg"><source media="(prefers-color-scheme: dark)" srcset="./assets/process-dark.svg"><img src="./assets/process-light.svg" width="1000" alt="An auth change reaches session, views and api. Four unrelated nodes remain untouched."></picture></p>

In this eight-file example, editing `auth.py` affects `auth → session → views → api`. `invoice`, `billing`, `search` and `logger` stay untouched. After successful re-derivation, running again without a new edit performs zero updates.

## Contents

[Architecture](#architecture) · [Install](#install) · [Quick start](#quick-start) · [Usage](#usage) · [Demo](#demo) · [Capabilities & integrations](#capabilities--integrations) · [Configuration](#configuration) · [Roadmap](#roadmap)

## Architecture

One Python package, one CLI, no graph server to operate. Source-file BLAKE3 hashes, dirty bits and checkpoints live in a separate `.dirtygraph/` sidecar. The dependency graph finds the affected set; an adapter performs the actual derivation task.

<p align="center"><picture><source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="./assets/architecture-mobile-dark.svg"><source media="(max-width: 640px)" srcset="./assets/architecture-mobile-light.svg"><source media="(prefers-color-scheme: dark)" srcset="./assets/architecture-dark.svg"><img src="./assets/architecture-light.svg" width="1000" alt="Existing graph and source files feed change detection and dependency propagation, followed by ordered adapters and Store checkpoints."></picture></p>

| Module | Responsibility |
|---|---|
| `cli.py` | Commands including `init`, `status`, `rederive` and `watch` |
| `store.py` | Source paths, content hashes, dirty bits and successful checkpoints |
| `depgraph.py` | A `networkx.DiGraph` of propagation edges and forward-reachable dirty closures |
| `dirty.py` | File-hash comparison, direct changes and propagated effects |
| `rederive.py` | Topological ordering, per-node adapter calls and successful state updates |
| `adapters/codegraph.py` | Two graph loaders, local summaries and optional model summaries through a compatible endpoint |

A dirty node needs re-derivation. `status --why` tells you whether its source changed directly or an upstream change reached it through a dependency path. Nodes whose adapter calls fail remain dirty for a later attempt.

**Your integration owns derived-content storage.** The CLI updates its checkpoints; it does not write new summaries back into the imported JSON or SQLite database. To update application summaries, indexes or documents, persist the output in a custom adapter.

## Install

Requires **Python 3.12+**. Install from source to get the complete example used below:

```bash
git clone https://github.com/SuperMarioYL/dirtygraph.git
cd dirtygraph
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Both the default `echo` adapter and `codegraph` can run locally without a model service or API key.

## Quick start

Run one complete dependency chain:

```bash
python examples/demo.py
```

The example creates eight Python source files and a graph in a temporary directory. It initializes state, edits `auth.py`, explains the affected set, re-derives it and runs again. The temporary directory is cleaned up afterward.

```text
# Initial state
dirty closure: 0 dirty of 8

# After editing auth.py
dirty closure: 4 dirty of 8
  changed sources: 1
  direct hits: 1 | propagated: 3

# First re-derivation
re-derived 4 nodes (of 8)

# Run again with no further edit
re-derived 0 nodes (of 8)
```

Then import an existing graph from your own project directory:

```bash
cd /path/to/your-project
dirtygraph init ./graph.json
# Edit a source file referenced by a graph node.
dirtygraph status --why
dirtygraph rederive --adapter codegraph
```

`graph.json` must supply source-file paths and dependency edges. See the [usage reference](./docs/usage.md) for input formats, path resolution and edge orientation.

## Usage

### Explain the affected set

```bash
dirtygraph status --why
```

```text
  [direct]    auth  <- source: auth.py
  [propagated] session  via auth -> session
  [propagated] views  via auth -> session -> views
  [propagated] api  via auth -> session -> views -> api
```

By default, `status` persists detected dirty bits. Use `status --no-write` to inspect without writing state.

### Wire derivation tasks manually

Without an importable graph file, register nodes directly. `link` points from the source of a change to the node it affects:

```bash
dirtygraph add auth-node auth.py --label "auth module"
dirtygraph add views-node views.py --label "views layer"
dirtygraph link auth-node views-node --relation IMPORTS
dirtygraph touch auth.py
```

When importing a graph, code relations such as `CALLS`, `IMPORTS` and `INHERITS` are oriented for propagation. If A calls B, a change to B should invalidate A.

### Keep updating as you develop

```bash
# Watch and mark changes only
dirtygraph watch --root .

# Re-derive after file changes
dirtygraph watch --root . --rederive --adapter codegraph
```

| Command | Use |
|---|---|
| `init <graph>` | Import graphify JSON or code-review-graph SQLite into state and a propagation graph |
| `add <id> <src>` / `link <src> <tgt>` | Register nodes and propagation edges manually |
| `touch <file>` | Check only the specified file for content changes |
| `status --why` | Inspect the dirty closure and its causes |
| `rederive --adapter codegraph` | Re-derive affected nodes in dependency order |
| `watch --rederive` | Watch file events and trigger re-derivation |
| `reset` | Accept current file contents as new checkpoints and clear dirty bits |

## Demo

![Real CLI run: initialize, edit, explain, re-derive and run again](./assets/demo.gif)

The recording uses [docs/demo.tape](./docs/demo.tape). Inputs and execution live in [examples/demo.py](./examples/demo.py); full output is in [docs/demo-results.json](./docs/demo-results.json). This eight-file fixture illustrates behavior, not large-repository performance. The original 1,203-node example was also constructed data.

## Capabilities & integrations

<p align="center"><picture><source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="./assets/integrations-mobile-dark.svg"><source media="(max-width: 640px)" srcset="./assets/integrations-mobile-light.svg"><source media="(prefers-color-scheme: dark)" srcset="./assets/integrations-dark.svg"><img src="./assets/integrations-light.svg" width="1000" alt="graphify JSON, code-review-graph SQLite and manual registration connect to local, compatible model or custom adapters."></picture></p>

DirtyGraph shares the work with graph-building tools. For example, [graphify](https://github.com/safishamsi/graphify) supplies a node-link JSON graph. DirtyGraph reads node `source_file` paths and `links` / `edges`, then maintains change state and derived dependencies.

| What you need | Where it belongs |
|---|---|
| Entity extraction, relationship inference and graph queries | Your existing graph tooling |
| Import graphify JSON / code-review-graph SQLite | Built-in loaders |
| Detect file-content changes and propagate invalidation | DirtyGraph hash detection and dependency graph |
| Explain and schedule only affected nodes | `status --why` / `rederive` |
| Deterministic local summaries | Built-in `codegraph` adapter |
| DeepSeek / Qwen through a compatible endpoint | Configured `codegraph` model route |
| Persist results to application graphs, summary stores or indexes | A custom Python adapter |

Provenance currently means **one source file per node, tracked with a file-level content hash**. DirtyGraph does not infer missing dependencies or detect changes at AST level. Nodes without source paths need a traceable path through propagation edges or manual registration.

## Configuration

`codegraph` creates deterministic local summaries by default. Enable the OpenAI-compatible route for model-based re-derivation:

```bash
export DIRTYGRAPH_LLM=1
export DIRTYGRAPH_LLM_BASE_URL=https://api.deepseek.com/v1
export DIRTYGRAPH_LLM_MODEL=deepseek-chat
# Set DIRTYGRAPH_LLM_API_KEY in your environment.
dirtygraph rederive --adapter codegraph
```

| Variable | Default | Purpose |
|---|---|---|
| `DIRTYGRAPH_LLM` | `0` | Enable model summaries; an API key is also required |
| `DIRTYGRAPH_LLM_API_KEY` | Unset | Compatible endpoint credentials |
| `DIRTYGRAPH_LLM_BASE_URL` | `https://api.deepseek.com/v1` | Endpoint; configure the appropriate compatible URL for Qwen |
| `DIRTYGRAPH_LLM_MODEL` | `deepseek-chat` | Model name, such as `qwen-plus` |
| `DIRTYGRAPH_LLM_TIMEOUT` | `30` | Per-request timeout in seconds |

The model route summarizes the first 6,000 characters of the source file. Failed requests or unparseable responses fall back to a local summary, so a completed re-derivation does not mean every node used a model. Integrations can distinguish the routes through `DerivedContent.extra.mode`.

## Roadmap

Implemented:

- [x] Import nodes, source paths and dependencies from existing graphs.
- [x] BLAKE3 detection, dirty closures and topological re-derivation.
- [x] Checkpoint successful calls; retain dirty state on adapter failure.
- [x] Targeted `touch`, explanations with `status --why` and `reset`.
- [x] Automatic `watch --rederive`, ignoring its own state-file events.
- [x] Local summaries and optional OpenAI-compatible model calls.

Next directions:

- [ ] More graph inputs: GraphML, Neo4j exports and Obsidian vaults.
- [ ] Finer provenance: AST- or symbol-level change tracking.
- [ ] Easier adapter extension and examples of derived-content persistence.

These are directions to explore, not current features. Bring a real graph and derivation task to [Issues](https://github.com/SuperMarioYL/dirtygraph/issues): which updates cost the most, how dependencies are represented and where results need to be stored will shape what comes next.

## Development & license

```bash
python -m pip install -e '.[dev]'
python -m pytest
python examples/demo.py
python docs/render_neon_assets.py
```

[Apache-2.0](./LICENSE). Runs locally without an account. The animated figures and website share colors, dependency chains and example results. Relationships and states remain fully visible with reduced motion enabled.
