<div align="right"><a href="./README.en.md">English</a> · <strong>简体中文</strong></div>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/hero-dark.svg">
  <img src="assets/hero-light.svg" width="880" alt="DirtyGraph">
</picture>

**修改源文件后，只重算受影响的图谱节点。** DirtyGraph 为已有代码图谱追踪文件变化，找出受影响的节点并按依赖顺序调用重算逻辑。无关节点保持原样。

[交互演示](https://dirtygraph.lei6393.com/#demo) · [快速开始](#快速开始) · [使用说明](docs/usage.md) · [测试状态](https://github.com/SuperMarioYL/dirtygraph/actions/workflows/ci.yml) · [Apache-2.0](LICENSE)

## 看一次实际运行

下面的样例有 **8 个真实源文件、8 个图谱节点**。修改 `auth.py` 后，`auth → session → views → api` 这条链上的 4 个节点需要重算，其余 4 个节点不受影响。箭头表示变更传播方向。

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/flow-dark.svg">
  <img src="assets/flow-light.svg" width="880" alt="修改 auth 后，auth、session、views、api 受影响；invoice、billing、search、logger 保持不变">
</picture>

```text
$ dirtygraph status --why
dirty closure: 4 dirty of 8
  changed sources: 1
  direct hits: 1 | propagated: 3

$ dirtygraph rederive --adapter codegraph --verbose
dirty closure: 4 dirty of 8
re-derived 4 nodes (of 8)
  + auth
  + session
  + views
  + api

$ dirtygraph rederive --adapter codegraph
dirty closure: 0 dirty of 8
re-derived 0 nodes (of 8)
```

这是[示例脚本](examples/demo.py)的实际输出，完整记录在 [demo-results.json](docs/demo-results.json)。样例用于验证变更检测和重算范围，不是耗时、token 节省或摘要质量的性能基准。

<details><summary>观看终端录制</summary>

![运行示例](assets/demo.gif)

</details>

## 快速开始

需要 Python 3.12 或更高版本。以下命令适用于 macOS / Linux：

```bash
git clone https://github.com/SuperMarioYL/dirtygraph.git
cd dirtygraph
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python examples/demo.py
```

脚本会创建临时源文件与依赖图，依次执行初始化、修改文件、检查影响、重算和再次运行，结束后自动清理。**不需要预先准备 `graph.json`，也不需要 API Key 或模型服务。**

## 接入自己的图谱

适用于已经有代码图谱、需要在文件变化后维护派生节点的开发者。支持 graphify node-link JSON 和 code-review-graph SQLite；源文件路径与依赖关系必须由输入图谱提供。

```bash
dirtygraph init /path/to/graph.json
cd /path/to
# 修改图谱引用的一个源文件后：
dirtygraph status --why
dirtygraph rederive --adapter codegraph
```

| 操作 | 命令 |
|---|---|
| 解释每个受影响节点的原因 | `dirtygraph status --why` |
| 只检查刚修改的文件 | `dirtygraph touch auth.py` |
| 监听文件变化并标记节点 | `dirtygraph watch --root .` |
| 手动注册节点与传播关系 | `dirtygraph add` / `dirtygraph link` |

完整参数、图谱格式和可选模型设置见[使用说明](docs/usage.md)。

## 工作原理与限制

DirtyGraph 比较源文件的内容哈希，沿输入图谱的依赖关系传播变更，再对受影响节点按拓扑顺序调用 adapter。成功调用会更新 `.dirtygraph/` 中的状态，失败节点保留待重算标记。

- **依赖关系来自输入。** DirtyGraph 不从源代码推断依赖，也不构建图谱；影响范围是否正确取决于输入图谱的关系和源文件映射。
- **导入的原图不会被修改。** CLI 更新自身状态，但不会把重算内容写回原图。应用需要在 adapter 中保存派生内容。
- **本地示例不调用模型。** `codegraph` adapter 默认生成本地摘要；启用模型调用需要单独配置。本示例不验证模型摘要质量。
- **重算节点减少不等于固定性能收益。** 实际开销取决于图结构、文件规模和 adapter 的工作量。

## 开发

```bash
python -m pip install -e '.[dev]'
pytest -q
python examples/demo.py
```

更新演示时，先运行 `python examples/demo.py --json-output docs/demo-results.json`，再运行 `python docs/render_demo_assets.py`。网站与 SVG 使用同一份已验证结果；终端录制由 `vhs docs/demo.tape` 生成。

问题与建议请提交 [Issue](https://github.com/SuperMarioYL/dirtygraph/issues)，附上输入图谱格式、执行命令和预期结果。

[Apache-2.0](LICENSE) © 2026 SuperMarioYL
