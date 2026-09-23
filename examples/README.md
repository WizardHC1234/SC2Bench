# 示例

这些脚本只演示怎么接平台。模型调用、工具循环和单局记录都在 `agents.llm_agent`，示例里不再写第二套客户端或正文解析。

先按 [安装文档](../docs/installation.md) 装好平台和实机依赖。模型地址用 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` 覆盖，默认与 `python -m agents.llm_agent` 相同。

## 直接调用环境

[llm_vs_ai.py](llm_vs_ai.py) 自己写对局循环，不创建 Runner。

```bash
python examples/llm_vs_ai.py --dry-run
python examples/llm_vs_ai.py --opponent mediumhard --enemy-style macro --map KairosJunctionLE
```

`--opponent` 是内置电脑难度短名称，`--enemy-style` 是 `random`、`rush`、`timing`、`power`、`macro`、`air`。`--map` 是 SC2 `Maps` 目录中 `.SC2Map` 的文件名。这个脚本的己方种族固定为人族，模型仍是 [`agents.llm_agent`](../agents/llm_agent/README.md)。

`run_episode()` 的顺序：

1. `env.reset(config)` 开始一局。
2. `env.get_context()` 取出规则和文本观测。同时把 `tool_schemas` 和 `call_tool` 交给 Agent。
3. `agent(request)` 调用模型。查询可以多轮；这一步的动作在同一次回复里给出，并以一次 `advance` 结束。
4. `env.step(turn.decision, agent_context=turn.agent_context)` 提交动作。
5. 看 `terminated`。结束或中断时 `env.close()`。

```python
from sc2bench_env import Environment, EpisodeConfig
from agents.llm_agent import create_agent
from examples.llm_vs_ai import run_episode

env = Environment("sharpy")
try:
    result = run_episode(
        env, create_agent(),
        EpisodeConfig(opponent="mediumhard", enemy_style="macro",
                      game_time_limit_seconds=1200),
    )
    print(result)
finally:
    env.close()
```

API 失败会停下来，不会替模型提交 `advance`。超时未分胜负记平局。每局目录里有 `episode.txt`、`session.json`、`log.txt` 和可用回放。

## 两个 Agent 对战

[llm_vs_llm.py](llm_vs_llm.py) 开一局两个已支持种族的 Agent。一边的 `advance` 到点，只询问这一边；另一边还停在自己的决策上时，游戏时间不会往前走。

```bash
python examples/llm_vs_llm.py --dry-run
python examples/llm_vs_llm.py --game-time-limit 1200
python examples/llm_vs_llm.py --race protoss --enemy-race zerg --skill none --opponent-skill none --map KairosJunctionLE
```

`--race` 和 `--enemy-race` 都是 `terran`、`protoss`、`zerg`。默认 Skill 是人族的 `tank`。神族或虫族要写成 `--skill none` 和 `--opponent-skill none`。两边可以分别用 `--model`、`--opponent-model`、`--api-base-url`、`--opponent-api-base-url` 指向不同模型。

```python
from sc2bench_env import EpisodeConfig, VersusMatch, run_versus
from agents.llm_agent import create_agent

match = VersusMatch(backend="sharpy")
run_versus(
    match, create_agent(), create_agent(skill_name="none"),
    EpisodeConfig(game_time_limit_seconds=1200),
)
```

两个参数是各自的 Agent。可以是不同的类，只要都能接收 `AgentInput` 并返回动作。

记录在同一个对局目录下的 `player_0` 和 `player_1`。回放一份。

## Runner 和批量

- [agent_integration.py](agent_integration.py)：同一 Agent，改由 Runner 管单局。
- [run_llm_benchmark.py](run_llm_benchmark.py)：读取 Suite，每局新建一个 Agent。`--max-parallel` 大于 1 时并行。

```bash
python examples/agent_integration.py --dry-run
python examples/agent_integration.py --opponent easy
python examples/run_llm_benchmark.py --dry-run --repetitions 1
python examples/run_llm_benchmark.py --repetitions 1 --max-parallel 2
```

普通安装包不包含 `examples/`。要改循环时复制 `run_episode()`，换掉 Agent 即可。
