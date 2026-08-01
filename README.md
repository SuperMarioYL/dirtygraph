<div align="right"><sub><a href="./README.en.md">English</a>&nbsp;&nbsp;⇄&nbsp;&nbsp;<b>简体中文</b></sub></div>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./assets/hero-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="./assets/hero-light.svg">
    <img src="./assets/hero-light.svg" width="880" alt="DirtyGraph — 给 Agent 代码知识图谱接上 Bazel 式脏标记">
  </picture>
</p>

<p align="center">
  <img src="https://readme-typing-svg.demolab.com/?font=Inter&weight=600&size=20&pause=1200&color=5E5CE6&center=true&vCenter=true&width=720&lines=%E6%96%87%E4%BB%B6%E4%B8%80%E6%94%B9%EF%BC%8C%E5%8F%AA%E9%87%8D%E7%AE%97%E8%84%8F%E9%97%AD%E5%8C%85%EF%BC%8C%E4%B8%8D%E6%98%AF%E6%95%B4%E5%BA%93%E9%87%8D%E6%89%AB;re-derived+4+nodes+(of+1%2C203);%E5%9B%BD%E4%BA%A7%E6%A8%A1%E5%9E%8B+DeepSeek+%2F+Qwen+%E9%87%8D%E7%AE%97%E9%80%82%E9%85%8D" alt="DirtyGraph">
</p>

<p align="center"><sub>给 Agent 代码知识图谱（graphify / code-review-graph 那一类）接上 Bazel 式脏标记：文件一改，只重算被它派生的脏闭包。</sub></p>

<p align="center">
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-0071E3.svg" alt="license"></a>
  <a href="https://github.com/SuperMarioYL/dirtygraph/releases"><img src="https://img.shields.io/github/v/release/SuperMarioYL/dirtygraph" alt="release"></a>
  <a href="https://github.com/SuperMarioYL/dirtygraph/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/SuperMarioYL/dirtygraph/ci.yml?branch=main&label=CI" alt="CI"></a>
  <img src="https://img.shields.io/badge/python-3.12-3776AB.svg" alt="python">
  <img src="https://img.shields.io/badge/Agent-ready-5E5CE6.svg" alt="Agent-ready">
  <img src="https://img.shields.io/badge/Cursor-friendly-10A37F.svg" alt="Cursor-friendly">
</p>

---

**每次 push 都让 Agent 把整个代码知识图谱重扫一遍？DirtyGraph 只把改动文件派生的那几个节点标脏，沿依赖边算出受影响闭包，然后只重算这个脏子图——`重算 4 个节点，而不是 1,203 个`。**

DirtyGraph 不构建图谱，它给你**已有**的图谱接上 Make / Bazel 几十年前就在用的增量失效原语。你照常用 graphify 或 code-review-graph 产出图，DirtyGraph 负责在文件变更时回答唯一一个问题：*哪些派生节点现在脏了？*

## 目录

- [架构](#架构)
- [安装](#安装)
- [快速开始](#快速开始)
- [用法](#用法)
- [Demo](#demo)
- [为什么需要它](#为什么需要它)
- [vs graphify](#vs-graphify)
- [配置](#配置)
- [路线图](#路线图)
- [许可证](#许可证)

<h2 id="架构"><img src="https://api.iconify.design/tabler:topology-star-3.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> 架构</h2>

DirtyGraph 是单一 Python 包 + 单一 CLI，无服务端。读入你已有的图，把每个节点的单一源文件 provenance 旁挂一个自己算的 blake3 哈希 sidecar，再把图里的代码关系边当成传播边——文件一改，沿这些边前向可达的节点就是脏闭包。

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./assets/atlas-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="./assets/atlas-light.svg">
    <img src="./assets/atlas-light.svg" width="880" alt="架构：源文件 → store+depgraph → dirty 标记闭包 → 仅重算脏子图">
  </picture>
</p>

| 模块 | 职责 |
|---|---|
| `cli.py` | Typer 命令：`init` / `add` / `link` / `status` / `rederive` / `watch` |
| `store.py` | 读写 `.dirtygraph/state.json` sidecar（源路径 + 我们自己的 blake3 哈希 + 脏位） |
| `depgraph.py` | `networkx.DiGraph`：节点 + 关系边，对脏集做前向可达闭包 |
| `dirty.py` | blake3 比对检测变更文件 → 计算脏闭包 |
| `rederive.py` | 对脏闭包做拓扑排序，逐节点调用 adapter |
| `adapters/codegraph.py` | 两个 loader（graphify node-link JSON / code-review-graph SQLite）+ 可选 DeepSeek / Qwen 重算 |

<h2 id="安装"><img src="https://api.iconify.design/tabler:rocket.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> 安装</h2>

```bash
pip install dirtygraph        # 或 uv pip install dirtygraph
```

需要 Python ≥ 3.12。零网络依赖即可跑 `echo` adapter——国产模型重算（DeepSeek / Qwen）只在你设置环境变量后才走网络。

<h2 id="快速开始"><img src="https://api.iconify.design/tabler:player-play.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> 快速开始</h2>

三步从冷启动到看见 benchmark：

```bash
dirtygraph init ./graph.json     # 1. 接上你已有的图，逐源文件算 blake3 哈希
# 2. 编辑任意一个源文件……
dirtygraph status                # 3. 看脏闭包：4 dirty of 1,203
dirtygraph rederive --adapter codegraph   # 只重算脏子图：re-derived 4 nodes (of 1,203)
```

<details><summary>样例输出</summary>

```text
$ dirtygraph init ./graph.json
initialised 1,203 nodes (3 edges, 1,203 sources) from graphify graph
  state: .dirtygraph/state.json

# 编辑 auth.py 之后：
$ dirtygraph status
dirty closure: 4 dirty of 1,203
  changed sources: 1
  direct hits: 1 | propagated: 3

$ dirtygraph rederive --adapter codegraph
re-derived 4 nodes (of 1,203)

# 没有新改动再跑一次：
$ dirtygraph rederive --adapter codegraph
re-derived 0 nodes (of 1,203)
```
</details>

<h2 id="用法"><img src="https://api.iconify.design/tabler:terminal-2.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> 用法</h2>

`init` 直接吃图文件；但你也可以脚本化地逐节点接线，不需要一个可导入的图：

```bash
# 手动接线一个传播链（source 变更会重置 target）
dirtygraph add  auth-node  auth.py    --label "auth 模块"
dirtygraph add  views-node views.py   --label "views 层"
dirtygraph link auth-node  views-node --relation IMPORTS

# 编辑器/Agent 刚写了某个文件 —— 只重新检查它
dirtygraph touch auth.py

# 实时跟踪整棵树的文件事件
dirtygraph watch --root .
```

| 命令 | 作用 |
|---|---|
| `init <graph>` | 接入 graphify `graph.json` 或 `.code-review-graph/` SQLite，建 sidecar + 传播图 |
| `add <id> <src>` | 手动注册一个派生节点 + 它的源文件 |
| `link <src> <tgt>` | 注册一条传播边（`src` 变更 → 重置 `tgt`） |
| `status` | 打印脏闭包 `N dirty of TOTAL`（不重算） |
| `rederive` | 仅重算脏闭包并打印 before/after benchmark |
| `watch` | watchdog 实时文件事件循环 |

**国产模型重算**：把脏闭包交给 DeepSeek / Qwen 重新总结，只需设置环境变量（OpenAI 兼容端点）：

```bash
export DIRTYGRAPH_LLM=1
export DIRTYGRAPH_LLM_API_KEY=sk-...
export DIRTYGRAPH_LLM_BASE_URL=https://api.deepseek.com/v1   # Qwen 改 DashScope 端点
export DIRTYGRAPH_LLM_MODEL=deepseek-chat                    # 或 qwen-plus
dirtygraph rederive --adapter codegraph
```

<h2 id="demo"><img src="https://api.iconify.design/tabler:photo.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> Demo</h2>

![demo](assets/demo.gif)

最后一帧就是 star 仓库的那一刻：`re-derived 4 nodes (of 1,203)`。

<h2 id="为什么需要它"><img src="https://api.iconify.design/tabler:bulb.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> 为什么需要它</h2>

Agent 维护的文档和代码知识图谱会和源文件悄悄失同步：你改了 `auth.py`，图里 auth 模块的节点、以及所有从它派生的摘要都该被标脏，但今天的工具要么信任过时节点、要么整库重扫。前者让 Agent 读到自信而错误的过时上下文，后者在大仓库上把重算延迟和 token 开销跟仓库大小（而非改动大小）绑在一起。

`graphify`（71k stars）和 `code-review-graph`（18.8k stars）把"folder → 可查询图谱"做得很好——但它们的构建模型是**扫描**，不是增量脏闭包更新。DirtyGraph 不和它们抢图谱构建，只补上那条没人接的边：**源文件 → 派生节点的依赖边失效**，让一次文件改动只标脏受影响的闭包。这正是 Cursor / 各类编码 Agent 在 IDE 里读项目图谱时缺的那块——图越大、读得越频繁，全量重扫就越浪费。

<h2 id="vs-graphify"><img src="https://api.iconify.design/tabler:scale.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> vs graphify</h2>

定位，不是吹牛——[graphify](https://github.com/safishamsi/graphify) 在它擅长的事上明显更强：

| 能力 | [graphify](https://github.com/safishamsi/graphify) | DirtyGraph |
|---|:---:|:---:|
| 从一堆文件构建可查询知识图谱 | ✓ | — （刻意不做，吃你已有的图） |
| 输入格式广度（代码 / SQL / docs / 图像 / 视频） | ✓ | partial（v0.1 接 graphify JSON + CRG SQLite） |
| 文件变更时的增量失效 | — （整库重扫） | ✓ （只标脏受影响闭包） |
| 仅重算脏子图（before/after benchmark） | — | ✓ |
| 国产模型（DeepSeek / Qwen）重算适配 | — | ✓ |

一句话：graphify 把图建得漂亮，DirtyGraph 让一次文件改动只重算它派生的那几个节点。

<h2 id="配置"><img src="https://api.iconify.design/tabler:adjustments.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> 配置</h2>

可选的国产模型重算通路由环境变量驱动：

| 变量 | 默认 | 含义 |
|---|---|---|
| `DIRTYGRAPH_LLM` | `0` | 设为 `1` 开启 LLM 重算；否则用零依赖 echo |
| `DIRTYGRAPH_LLM_API_KEY` | — | OpenAI 兼容端点的 API key（开启后必填） |
| `DIRTYGRAPH_LLM_BASE_URL` | `https://api.deepseek.com/v1` | 端点地址；Qwen 改成 DashScope 兼容地址 |
| `DIRTYGRAPH_LLM_MODEL` | `deepseek-chat` | 模型名；如 `qwen-plus` |
| `DIRTYGRAPH_LLM_TIMEOUT` | `30` | 单次请求超时（秒） |

<h2 id="路线图"><img src="https://api.iconify.design/tabler:map-2.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> 路线图</h2>

- [x] **m1 · provenance 追踪** — `init` 接入已有图，逐源文件算 blake3 哈希，落 `.dirtygraph/state.json`
- [x] **m2 · 脏闭包标记** — 用关系边建 `networkx` 图，文件变更时算前向可达闭包并置脏位
- [x] **m3 · 脏子图重算** — `rederive` 拓扑序遍历脏闭包，逐节点调 adapter，打印 before/after benchmark
- [x] **m4 · watch 稳定性修复** — `Store.save()` 内容未变时不落盘 + watch 忽略 `.dirtygraph/` 事件，消除无限写循环
- [x] **m5 · 定向 re-hash** — `touch` 只哈希命中的单个文件；`rederive` 单次哈希整树而非两次
- [x] **m6 · 脏因解释** — `status --why` 打印每个脏节点的来源（直命中源文件 / 传播路径）
- [x] **m7 · 干净重置** — `reset` 一键清脏位 + 重新盖哈希，无需改源码或重跑 `init`
- [ ] watch 变更即重算（v0.2 先修稳定性，事件级 rederive 是 v0.3 候选）
- [ ] 更多 loader（GraphML / Neo4j 导出 / Obsidian vault）
- [ ] AST / blame 级 provenance，替代 v0.1/v0.2 的"文件级内容哈希"
- [ ] 更多重算 adapter（本地 Ollama、自定义 HTTP 端点）

<h2 id="许可证"><img src="https://api.iconify.design/tabler:license.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> 许可证</h2>

MIT/Apache-2.0，免费开源，无付费功能。欢迎在 [Issues](https://github.com/SuperMarioYL/dirtygraph/issues) 反馈——尤其是你把 DirtyGraph 指向自己真实的 graphify / code-review-graph 输出之后。

> 注：自 v0.2.0 起仓库采用 Apache-2.0 许可（见 [LICENSE](./LICENSE)），徽章与页脚同步更新。

## Share this

```text
DirtyGraph — 给 Agent 代码知识图谱接上 Bazel 式脏标记：文件一改只重算脏闭包，re-derived 4 nodes instead of 1,203。内置 DeepSeek / Qwen 重算。 https://github.com/SuperMarioYL/dirtygraph
```

<p align="center"><sub><a href="./LICENSE">Apache-2.0</a> © 2026 SuperMarioYL</sub></p>
