# SC2Bench

将星际争霸 II 封装为面向 Agent 的高层交互环境。Agent 决定生产、科技、侦察和军队行动；平台提供规则、客观观测、任务执行、记录和批量评测，Sharpy 负责底层控制。

## 安装

核心包无需游戏或模型 API，Python 3.9 及以上可用：

```bash
python -m pip install -e .
python -m sc2bench_env doctor --backend fake
```

实机使用 Python 3.9、已安装的 SC2 客户端和地图：

```bash
python -m pip install -e dependencies/sc2-pathlib -e dependencies/sharpy-sc2 -e '.[sharpy,llm]'
python -m sc2bench_env doctor --suite benchmarks/terran_pilot.json
```

Windows 已有安装和短对局验证；Linux 需自行编译寻路组件，见安装文档。发行包名为 `sc2bench-env`，导入名为 `sc2bench_env`，不依赖旧 Commander 项目。

## 最小用法

```python
from sc2bench_env import Environment, EpisodeConfig

env = Environment("fake")  # 实机改为 "sharpy"
try:
    obs = env.reset(EpisodeConfig(opponent="easy", enemy_style="macro",
                                  game_time_limit_seconds=2))
    messages = env.get_context()  # 规则和文本观测，不调用模型
    obs, feedback, terminated, info = env.step([{"name": "advance", "arguments": {"seconds": 5}}])
finally:
    env.close()
```

## LLM 示例

仓库附带的普通 Agent：

```bash
python -m agents.llm_agent --help
python -m agents.llm_agent --dry-run --opponent mediumhard
```

默认对接 Commander 的 DeepSeek-V4-Flash；可用 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` 覆盖。`examples/` 演示接法，模型都用上面的 Agent：

```bash
python examples/llm_vs_ai.py --dry-run
python examples/llm_vs_ai.py --opponent easy --enemy-style macro
python examples/agent_integration.py --opponent easy
python examples/run_llm_benchmark.py --repetitions 1 --max-parallel 2
python examples/llm_vs_llm.py --dry-run
```

`--dry-run` 只预览。前三个文件是手写环境循环、Runner 单局和 Suite 批量。`llm_vs_llm.py` 是两个已支持种族的 Agent 对战，各自的 `advance` 到点才询问那一边。普通安装包不包含示例。

直接接入先看 [llm_vs_ai.py](examples/llm_vs_ai.py) 的 `run_episode()`：开始对局 → 取出上下文和工具 → 调用模型 → 提交动作 → 判断终局，最后关闭环境。

运行方法和代码讲解见 [示例说明](examples/README.md)。

## 范围与记录

当前支持我方人族、神族和虫族、内置 AI 对手，以及这些种族之间的 Agent 对战。Runner 可配置串行或多局并行。默认阻塞决策，保留连续模式；超时未分胜负记平局。平台不内置策略、Skill、Memory 或训练算法，不自动建补给站、水晶塔或领主，也不选择下一进攻目标，也不会因为局部战力偏低就撤回进攻编队。撤回由 Agent 发出 `retreat`。RL 适配尚未实现。神族 `chrono_boost`、虫族 `inject_larva` 和 `spawn_creep_tumor` 由 Agent 显式发出，底层选择施放目标，不会自动施放。

并行使用独立进程，`max_parallel` 设置同时进行的对局上限。仅使用核心包时需加装 `.[parallel]`，`.[llm]` 已包含并行依赖；详见 [Agent 接入](docs/agent_integration.md)。

源码安装默认输出到 `records/`，普通安装默认输出到 `~/.sc2bench/records/`。每局一个文件夹，保存 `episode.txt`、`session.json`、`log.txt`，以及可用回放。`log.txt` 是这一局终端输出的副本。

## 文档入口

| 内容 | 文档 |
| --- | --- |
| 安装、打包、Linux 构建 | [安装](docs/installation.md) |
| Agent、Runner、记录和评估用法 | [Agent 接入](docs/agent_integration.md) |
| 动作、观测和执行规则 | [动作接口](docs/interface.md) |

开发依赖：`python -m pip install -e '.[dev]'`。
