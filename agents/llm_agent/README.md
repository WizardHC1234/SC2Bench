# llm_agent

仓库附带的 LLM Agent。它实现 `AgentInput → AgentTurn`，在一个实例内保存单局消息历史，调用 OpenAI 兼容 Chat Completions，只使用平台 Read / Knowledge / Action 工具。查询可以先进行多轮；这一步的动作必须在同一次回复里一起给出，并以一次 `advance` 结束。每次调用工具前，回复正文里要先写一句依据，查询和动作都一样。模型正文不会被解析成动作，也不会自动增删动作。

Agent 默认加载从 SC2-Commander 适配来的 [`skills/tank.md`](skills/tank.md)。它使用两基地 Marine–Siege Tank 战略，并针对 SC2Bench 的手动补给、增量生产请求、Knowledge Tool 查询和编组增援方式进行了调整。使用 `--skill none` 可以运行无 Skill 基线。

## 运行

从仓库根目录：

```bash
python -m agents.llm_agent --help
python -m agents.llm_agent --dry-run --opponent mediumhard
python -m agents.llm_agent --opponent mediumhard
python -m agents.llm_agent --skill none --opponent mediumhard
python -m agents.llm_agent --race protoss --skill none --opponent mediumhard
```

`--race` 选择己方种族，对手仍是内置电脑。`--enemy-race` 只改电脑的种族。神族和虫族没有对应 Skill，必须加 `--skill none`。

```bash
python -m agents.llm_agent --race zerg --skill none --opponent mediumhard
```


用 BenchmarkRunner 加载工厂：

```python
from sc2bench_env import BenchmarkRunner, EpisodeConfig
from agents.llm_agent import create_agent

BenchmarkRunner().run(
    [EpisodeConfig(opponent="mediumhard")],
    create_agent,
)
```

`create_agent()` 默认加载 `tank`。无 Skill 对局使用 `create_agent(skill_name="none")`。

每调用一次 `create_agent()` 得到一个新实例；一局不要复用上一局的实例。
