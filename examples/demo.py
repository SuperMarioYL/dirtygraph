"""Run an eight-node example with real files, without network or API credentials.

    python examples/demo.py
    python examples/demo.py --json-output docs/demo-results.json

All graph state lives in a temporary directory and is removed after the run.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from dirtygraph import __version__


def run_demo(pause=0):
    files = {
        "auth": "def login(user): return bool(user)\n",
        "session": "from auth import login\n",
        "views": "from session import login\n",
        "api": "import views\n",
        "invoice": "def total(items): return sum(items)\n",
        "billing": "from invoice import total\n",
        "search": "def find(query): return query\n",
        "logger": "def log(message): print(message)\n",
    }
    dependencies = [("session", "auth"), ("views", "session"),
                    ("api", "views"), ("billing", "invoice")]
    steps = []
    with tempfile.TemporaryDirectory(prefix="dirtygraph-demo-") as directory:
        root = Path(directory).resolve()
        for name, source in files.items():
            (root / f"{name}.py").write_text(source)
        graph = {"directed": True, "multigraph": False, "graph": {},
                 "nodes": [{"id": name, "source_file": f"{name}.py"} for name in files],
                 "links": [{"source": source, "target": target, "relation": "IMPORTS"}
                           for source, target in dependencies]}
        (root / "graph.json").write_text(json.dumps(graph))
        env = {**os.environ, "DIRTYGRAPH_LLM": "0", "NO_COLOR": "1", "COLUMNS": "100"}

        def command(*args):
            result = subprocess.run([sys.executable, "-m", "dirtygraph.cli", *args],
                                    cwd=root, env=env, check=True, capture_output=True, text=True)
            output = result.stdout.replace(str(root), ".").strip()
            if pause:
                print("\033[2J\033[H", end="")
            print(f"$ dirtygraph {' '.join(args)}\n{output}\n", flush=True)
            if pause:
                time.sleep(pause)
            return {"command": f"dirtygraph {' '.join(args)}", "output": output}

        command("init", "graph.json")
        steps.append(command("status"))
        (root / "auth.py").write_text("def login(user, token): return bool(user and token)\n")
        print("# Edit auth.py: add a token argument.\n", flush=True)
        steps.append(command("status", "--why"))
        steps.append(command("rederive", "--adapter", "codegraph", "--verbose"))
        steps.append(command("rederive", "--adapter", "codegraph"))
        assert "0 dirty of 8" in steps[0]["output"]
        assert "4 dirty of 8" in steps[1]["output"]
        assert "re-derived 4 nodes (of 8)" in steps[2]["output"]
        updated = [line.strip()[2:] for line in steps[2]["output"].splitlines()
                   if line.strip().startswith("+ ")]
        assert updated == ["auth", "session", "views", "api"]
        assert "re-derived 0 nodes (of 8)" in steps[3]["output"]
    return {"version": __version__, "fixture": "eight real source files; constructed example, not a performance benchmark",
            "nodes": list(files), "dependencies": dependencies,
            "changed_file": "auth.py", "affected": ["auth", "session", "views", "api"], "steps": steps}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--pause", type=float, default=0, help="Pause between terminal scenes when recording")
    args = parser.parse_args()
    result = run_demo(args.pause)
    if args.json_output:
        args.json_output.write_text(json.dumps(result, indent=2) + "\n")
