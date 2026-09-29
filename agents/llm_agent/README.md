# llm_agent

仓库附带的 LLM Agent。它实现 `AgentInput → AgentTurn`，在一个实例内保存单局消息历史，调用 OpenAI 兼容 Chat Completions。它只转换这一轮 `request.tool_specs`，不按种族重新取全量工具。查询、动作和 `advance` 走同一个循环，可以交错。`advance` 才结束这一决策。示例 Agent 自己要求工具回复先写一句依据；这不是平台合同。模型正文不会被解析成动作。

## 目录结构

```text
agents/llm_agent/
├── agent.py       # Agent 会话、消息历史和工具调用循环
├── client.py      # OpenAI 兼容 Chat Completions 客户端
├── config.py      # 模型默认配置和环境变量读取
├── prompts.py     # Agent 自己的查询/动作回复约束
├── cli.py         # 单局命令行入口
├── skills/
│   ├── __init__.py  # Skill 发现与加载
│   ├── terran/      # 10 个人族策略
│   ├── protoss/     # 11 个神族策略
│   └── zerg/        # 11 个虫族策略
├── __init__.py    # 稳定的公开导入入口
└── __main__.py    # `python -m agents.llm_agent`
```

平台负责提供 Observation、Feedback 和工具；Agent 负责保存单局消息历史、按需查询、调用模型并提交动作。模型接口、命令行启动和可选 Skill 与决策循环分开，便于替换模型或编写新的 Agent，而不需要修改平台代码。

这 32 个 Skill 来自 [Sharpy `develop` 分支的 `dummies`](https://github.com/DrInfy/sharpy-sc2/tree/develop/dummies) 策略脚本，并固定记录了所检查的源提交 `d9577a00ee47634b56ff7ee0740c6ed3043659a2`。文件中的经济、科技、生产、出兵阈值和条件按源代码整理。随机选择器和调试脚本没有转换成策略；代理建筑位置、工人进攻和单位级技能若不能由当前高层动作直接表达，会在对应 Skill 中明确标出，不另行编造替代策略。

Agent 默认不加载 Skill。选择 Skill 时使用 `race/strategy` 名称，`--help` 会列出当前全部名称。

## 运行

从仓库根目录：

```bash
python -m agents.llm_agent --help
python -m agents.llm_agent --dry-run --opponent mediumhard
python -m agents.llm_agent --opponent mediumhard
python -m agents.llm_agent --skill terran/two-base-tanks --opponent mediumhard
python -m agents.llm_agent --race protoss --skill protoss/macro-stalkers --opponent mediumhard
python -m agents.llm_agent --race zerg --skill zerg/roach-hydra --opponent mediumhard
```

`--race` 选择己方种族，对手仍是内置电脑；`--enemy-race` 只改电脑种族。Skill 的目录前缀必须与己方种族相同。省略 `--skill` 或使用 `--skill none` 都运行无 Skill 基线。
该命令直接运行一局 `Environment`，记录文件夹直接写入 `records/`，不会创建 `batch_*`、`episodes/` 或 `batch.json`。批量评测才使用 `BenchmarkRunner` 的批次目录。


用 BenchmarkRunner 加载工厂：

```python
from sc2bench_env import EpisodeConfig
from sc2bench_env.benchmark import BenchmarkRunner
from agents.llm_agent import create_agent

BenchmarkRunner().run(
    [EpisodeConfig(opponent="mediumhard")],
    create_agent,
)
```

`create_agent()` 默认运行无 Skill 基线。加载策略时传入完整名称，例如 `create_agent(skill_name="terran/two-base-tanks")`。

每调用一次 `create_agent()` 得到一个新实例；一局不要复用上一局的实例。
