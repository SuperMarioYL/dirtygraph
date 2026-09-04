"""DirtyGraph — incremental dirty-marking for agent code knowledge-graphs.

DirtyGraph attaches a content-hash provenance sidecar to an existing codebase
knowledge-graph (the kind graphify or code-review-graph produces) and treats the
graph's code-relation edges as propagation edges. When a source file changes, it
computes the forward-reachable dirty closure and re-derives only those nodes,
instead of re-scanning the whole graph.
"""

__version__ = "0.5.0"

__all__ = ["__version__"]
