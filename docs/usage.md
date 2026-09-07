# Usage reference

Use `dirtygraph --help` or `dirtygraph <command> --help` for the complete CLI options.

## Input and state

`dirtygraph init <graph>` reads graphify node-link JSON or a code-review-graph SQLite store. `source_file` paths in graphify JSON are resolved relative to the graph root. An explicit `--root` selects another root. For a `.code-review-graph/` directory, sources resolve relative to its parent repository.

IMPORTS edges express importer → dependency. The loader reverses them for change propagation: if session imports auth, editing auth marks session for re-derivation. Other relation types are handled by the loader; inspect `dirtygraph/adapters/codegraph.py` when adapting another graph format.

State is stored separately in `.dirtygraph/state.json` and `.dirtygraph/edges.json`. The input graph is not rewritten. One source may back multiple graph nodes.

## Commands

```bash
dirtygraph init ./graph.json
dirtygraph status --why
dirtygraph status --no-write
dirtygraph rederive --adapter codegraph --verbose
dirtygraph touch auth.py
dirtygraph watch --root .
```

`status` checks source hashes and persists current dirty bits by default. `--no-write` inspects without saving. `rederive` checks changes before calling adapters, so a separate `status` invocation is optional. `watch` marks changes; use `--rederive` only when the selected adapter is ready to execute automatically.

Manual registration:

```bash
dirtygraph add auth-node auth.py --label "auth module"
dirtygraph add views-node views.py --label "views layer"
dirtygraph link auth-node views-node --relation IMPORTS
```

Manual `link` uses propagation direction: a change to the first node marks the second. This differs from the IMPORTS direction in an imported graphify file.

## Optional model summaries

The `codegraph` adapter runs locally by default. To enable model calls, set `DIRTYGRAPH_LLM=1` and configure your endpoint, model and key in the environment:

| Variable | Purpose |
|---|---|
| `DIRTYGRAPH_LLM` | `1` enables model calls; otherwise local summaries |
| `DIRTYGRAPH_LLM_API_KEY` | Your provider credential |
| `DIRTYGRAPH_LLM_BASE_URL` | Chat Completions compatible API base URL |
| `DIRTYGRAPH_LLM_MODEL` | Model identifier available at that endpoint |

Do not commit credentials. On a model request error, the current adapter falls back to its local summary. A successful re-derivation therefore does not prove that a model request succeeded.

## Persisting derived content

The engine's `rederive` result includes adapter outputs in memory. The CLI reports counts and checkpoints DirtyGraph state; it does not save those summaries back to the imported graph. An integrating application must save derived content in its adapter or consume the engine result. Review the adapter and engine interfaces before wiring a production update job.
