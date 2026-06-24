# DirtyGraph — 10-minute happy path, rendered to assets/demo.gif by .github/workflows/demo.yml
# vhs script (https://github.com/charmbracelet/vhs). The payoff frame is
# "re-derived 4 nodes (of 1,203)" — re-deriving only the dirty closure.

Output assets/demo.gif

Set Shell      "bash"
Set FontSize   18
Set Width      1180
Set Height     680
Set Padding    24
Set Theme      "Dracula"
Set TypingSpeed 32ms

# --- setup: install + a tiny demo graph (graphify node-link JSON) ----------
Hide
Type "pip install -q dirtygraph >/dev/null 2>&1 || pip install -q -e . >/dev/null 2>&1"   Enter
Type "mkdir -p demo && cd demo"   Enter
Type "printf 'def login(): ...\\n'           > auth.py"     Enter
Type "printf 'from auth import login\\n'      > session.py"  Enter
Type "printf 'from session import s\\n'       > views.py"    Enter
Type "printf 'import views\\n'                > api.py"       Enter
# A graphify-style node-link graph.json: 4 real nodes that derive from the files
# above, padded to 1,203 total so the benchmark line reads like a real repo.
Type "python - <<'PY'" Enter
Type "import json" Enter
Type "nodes=[{'id':'auth','source_file':'auth.py'},{'id':'session','source_file':'session.py'},{'id':'views','source_file':'views.py'},{'id':'api','source_file':'api.py'}]" Enter
Type "nodes+=[{'id':f'n{i}','source_file':f'pkg/m{i}.py'} for i in range(1199)]" Enter
# IMPORTS edge {source:X, target:Y} == 'X imports Y' (Y is the dependency); the
# loader reverses it so a change to Y restages X. session<-auth, views<-session,
# api<-views means editing auth.py forward-restages the whole chain (4 nodes).
Type "links=[{'source':'session','target':'auth','relation':'IMPORTS'},{'source':'views','target':'session','relation':'IMPORTS'},{'source':'api','target':'views','relation':'IMPORTS'}]" Enter
Type "json.dump({'directed':True,'multigraph':False,'graph':{},'nodes':nodes,'links':links},open('graph.json','w'))" Enter
Type "PY" Enter
Type "clear" Enter
Show

# --- 1. init: adopt the existing graph, hash every source -------------------
Type "# 1. point DirtyGraph at a graph you already have"   Sleep 600ms  Enter
Type "dirtygraph init ./graph.json"   Sleep 400ms   Enter
Sleep 2s

# --- 2. clean tree: nothing dirty ------------------------------------------
Type "# 2. nothing changed yet"   Sleep 500ms   Enter
Type "dirtygraph status"   Sleep 300ms   Enter
Sleep 1500ms

# --- 3. edit ONE source file -----------------------------------------------
Type "# 3. an agent (or you) edits one source file"   Sleep 500ms   Enter
Type "printf 'def login(user, token): ...\\n' > auth.py"   Sleep 300ms   Enter
Sleep 800ms

# --- 4. status: only the dirty closure is stale ----------------------------
Type "# 4. only auth + its forward closure are dirty — not 1,203"   Sleep 500ms   Enter
Type "dirtygraph status"   Sleep 300ms   Enter
Sleep 2s

# --- 5. rederive: re-derive ONLY the dirty subgraph ------------------------
Type "# 5. re-derive only the dirty closure"   Sleep 500ms   Enter
Type "dirtygraph rederive --adapter codegraph"   Sleep 400ms   Enter
Sleep 2500ms

# --- 6. second rederive: nothing left to do --------------------------------
Type "# 6. run it again — nothing changed, so nothing re-derives"   Sleep 500ms   Enter
Type "dirtygraph rederive --adapter codegraph"   Sleep 400ms   Enter
Sleep 3s
