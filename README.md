# SC2Bench

SC2Bench 是一个面向外部 Agent 的 StarCraft II 高层交互与评测平台。平台把实时对局转换为文本观测和统一工具调用：Agent 负责生产、科技、侦察和军队调度，平台负责校验请求、维护持续任务，并通过 Sharpy 完成寻路、建造放置和单位级微操。

SC2Bench 不绑定具体模型、提示方法或 Skill。不同模型与 Agent Harness 可以使用同一套环境接口、对局配置、记录格式和评测流程进行比较。目前平台支持人族、神族和虫族，以及 Agent 对战内置 AI 和 Agent 对战 Agent。

## 整体架构

```text
外部 Agent（模型 + Harness，可选 Skill / Memory）
                    ⇅
          LLM Adapter / Agent 接口
                    ⇅
      AgentInput · ToolCall · ToolResult
                    ⇅
       SC2Bench Environment + 统一工具
                    ↓
               Task Runtime
                    ↓
           Sharpy Backend → StarCraft II

Observation、Feedback、任务进度和对局结果沿相反方向返回
```

平台与 Agent 保持隔离。平台只提供基础游戏规则、当前观测、工具定义和执行反馈；外部 Agent 决定怎样组织提示词、是否查询知识、怎样规划以及何时提交动作。LLM Agent 可以通过 `LLMAdapter` 将这些平台对象转换为模型消息和 Tool Schema，非 LLM Agent 也可以直接使用公开 Python 接口。

## 决策循环

一次决策从当前 `Brief Observation` 和上一轮执行反馈开始：

1. 环境创建本轮 `ToolTurn`，并冻结供查询使用的状态快照。
2. Agent 可以调用零次或多次 Knowledge Tool、Read Tool 和 Action Tool；它们使用相同的 `ToolCall` / `ToolResult` 接口。
3. 查询立即返回文本化结果；动作先经过校验并暂存，同时立即返回是否接受及可能的阻塞原因。
4. Agent 最后调用 `advance(seconds=...)`，提交本轮暂存动作并指定游戏推进时间。
5. `Task Runtime` 持续执行已提交的生产、建造和军队任务，Sharpy 负责底层操作。
6. 到达下一决策点后，环境返回新的观测、任务反馈和终局状态。

`advance` 是每轮决策的结束工具。平台不强制查询与动作的固定顺序；单轮、ReAct、Plan-and-Execute 等交互方式由外部 Agent 的 Harness 决定。

## 平台能力

- **统一工具接口**：知识查询、状态查询和游戏动作都通过同一种工具调用协议完成。
- **按需信息获取**：默认提供紧凑但完整的 Brief Observation；单位数值、建筑数据、升级、前置路径、地图区域和详细生产状态可以按需查询。
- **持续任务执行**：`build`、`train`、`research` 和军队命令会被维护为任务，无需 Agent 每轮重复提交；Agent 可以查询进度或使用 `cancel` 调整未完成需求。
- **军队编组控制**：新生产的可作战单位进入默认防御兵力池 `group_0`，Agent 可以创建、增援、调度、合并、撤退和重新指派编队。
- **三种族支持**：动作目录、生产机制、科技、种族能力、观测和底层执行覆盖人族、神族与虫族。
- **标准化评测**：支持单局、Suite 批量运行、多进程并行、内置 AI 难度与风格配置，以及 LLM 对 LLM 对局。
- **完整可审计记录**：保存真实模型消息、工具调用、执行结果、用量、胜负和运行日志，供模型、Harness 与 Skill 方案比较。

## 安装

建议在项目根目录创建虚拟环境。核心接口支持 Python 3.9 及以上；实机运行当前建议使用 Windows x64 的 CPython 3.9，以匹配项目提供的寻路组件。

```powershell
python -m venv venv
venv\Scripts\activate
python -m pip install -e .
python -m sc2bench_env doctor --backend fake
```

接入本机 StarCraft II 和 Sharpy：

```powershell
python -m pip install -e dependencies/sc2-pathlib -e dependencies/sharpy-sc2 -e ".[sharpy,llm]"
python -m sc2bench_env doctor --suite benchmarks/terran_pilot.json
```

如 SC2 未安装在默认位置，请设置 `SC2PATH`。Linux、地图目录、原生寻路组件和打包说明见[安装文档](docs/installation.md)。发行包名是 `sc2bench-env`，Python 导入名是 `sc2bench_env`。

## 快速验证环境

下面的代码使用 Fake Backend 验证完整的工具事务，不启动游戏，也不调用模型：

```python
from sc2bench_env import Environment, EpisodeConfig, ToolCall

env = Environment("fake")
try:
    observation = env.reset(
        EpisodeConfig(
            race="terran",
            opponent="easy",
            enemy_style="macro",
            game_time_limit_seconds=30,
        )
    )

    turn = env.begin_tool_turn()
    race_data = turn.call(
        ToolCall(name="query_race_data", arguments={"race": "terran"})
    )
    print(race_data.data)

    turn.call(ToolCall(name="advance", arguments={"seconds": 5}))
    observation, feedback, terminated, info = env.step(turn.finish())
finally:
    env.close()
```

实机只需将 `Environment("fake")` 改为 `Environment("sharpy")`，并使用有效的 SC2 对局配置。

## 运行示例 LLM Agent

[`agents/llm_agent`](agents/llm_agent/README.md) 是一个可运行的外部 Agent 示例，用于演示模型、Harness、Skill 与平台接口如何组合。它不是平台内部的固定 Agent。

```powershell
# 查看最终配置，不启动游戏
python -m agents.llm_agent --dry-run --race zerg --skill none --opponent hard

# 启动一局无 Skill 的虫族对战
python -m agents.llm_agent --race zerg --skill none --opponent hard

# 使用一个可选策略 Skill
python -m agents.llm_agent --race terran --skill terran/two-base-tanks `
  --opponent mediumhard --enemy-style macro
```

示例 Agent 默认使用整局完整会话，保留本局连续的模型上下文。模型地址、模型名和密钥可通过 `LLM_BASE_URL`、`LLM_MODEL` 和 `LLM_API_KEY` 设置；使用 `--dry-run` 可以检查解析后的本局配置。

更多运行入口：

```powershell
python examples/llm_vs_ai.py --opponent easy --map KairosJunctionLE
python examples/agent_integration.py --opponent easy
python examples/run_llm_benchmark.py --repetitions 1 --max-parallel 2
python examples/llm_vs_llm.py --race protoss --enemy-race zerg `
  --skill none --opponent-skill none --map KairosJunctionLE
```

## 接入自己的 Agent

Runner 向 Agent 提供 `AgentInput`。其中包含当前观测、上一轮反馈、可用工具定义以及 `call_tool` 回调。Agent 通过回调完成本轮查询和动作，最后返回 `AgentTurn`：

```python
from sc2bench_env import AgentTurn, EpisodeConfig, ToolCall
from sc2bench_env.benchmark import BenchmarkRunner


class MyAgent:
    def __call__(self, request):
        request.call_tool(
            ToolCall(name="advance", arguments={"seconds": 10})
        )
        return AgentTurn()


def create_agent():
    return MyAgent()


runner = BenchmarkRunner(
    backend_factory=lambda: "fake",
    record_dir="records",
)
batch = runner.run(
    [EpisodeConfig(game_time_limit_seconds=30)],
    agent_factory=create_agent,
)
```

真实的 LLM Agent 应把实际发送给模型的消息与模型回复放入 `AgentTurn.agent_context`，记录器才会在 `session.json` 中保存准确的会话。完整的工厂接口、异常终止、批量 Runner 和 LLM Adapter 用法见[Agent 接入文档](docs/agent_integration.md)。

## Tool 与 Observation

所有工具在协议层都是平级的，仅按用途分组：

| 类型 | 作用 | 示例 |
| --- | --- | --- |
| Knowledge Tool | 查询相对稳定的游戏知识 | 单位、建筑、升级、前置路径、种族目录 |
| Read Tool | 查询当前对局的详细状态 | 地图、区域、路线、生产任务、军队编组 |
| Action Tool | 提交高层游戏意图 | 建造、训练、研究、侦察、编队、进攻、防守、撤退 |
| `advance` | 提交本轮动作并推进游戏 | 按秒推进至下一决策点 |

Brief Observation 默认包括游戏时间与对手信息、资源和收入、人口供给、工人与基地饱和度、生产任务、建筑与科技、当前可用目标、各军队编组、相关地图区域、能力状态和本轮变化。更详细的数据由 Read Tool 与 Knowledge Tool 返回，避免每轮重复发送完整数据表。

具体工具参数、动作完成边界、编队语义和观测字段见[接口文档](docs/interface.md)。

## 平台与底层控制边界

Agent 控制经济目标、生产需求、科技路线、侦察目标、军队组成和高层行动。平台负责将这些意图转换成可持续执行的任务，并处理不适合作为高层决策暴露的实时操作，例如工人采矿与分配、合法建造位置搜索、单位选择、局部移动与集火、攻城模式、运输执行、基地防御和种族机制的底层施放。

底层不会替 Agent 选择全局战略、自动补供给、决定下一处进攻目标或随意撤销任务。失败、阻塞与完成状态会通过工具结果和下一轮反馈返回，供 Agent 调整计划。

## 批量评测

命令行入口支持环境检查、Suite 运行和离线评估：

```powershell
python -m sc2bench_env doctor --suite benchmarks/terran_pilot.json
python -m sc2bench_env run --suite benchmarks/terran_pilot.json `
  --agent your_package.agent:create_agent --backend sharpy --max-parallel 1
python -m sc2bench_env evaluate records/path/to/batch.json
```

Suite 可以固定或枚举地图、己方种族、敌方种族、AI 难度、AI 风格、时间上限和重复次数。SC2 协议没有提供可控制所有游戏随机性的完整确定性种子，因此评测应使用重复对局，并在比较时保持地图、阵营、难度、风格和运行配置一致。

## 对局记录

源码安装默认写入 `records/`；普通安装默认写入 `~/.sc2bench/records/`。单局直接运行写入一个 Episode 目录，批量运行则在 Batch 目录下组织各个 Episode。

| 文件 | 内容 |
| --- | --- |
| `session.json` | 实际模型消息、工具定义与按决策组织的工具调用；只保存对话过程 |
| `metrics.json` | 紧凑的模型用量、工具调用状态和对局汇总，供评估器读取 |
| `episode.txt` | 本局配置、平台提示词和终局摘要 |
| `log.txt` | 本局运行日志和异常信息 |
| SC2 Replay | 后端成功保存时生成的可回放文件 |

批量目录还会保存 `batch.json`。新记录不再生成重复且体积较大的 `trajectory.json`；读取器和评估器仍兼容已有旧记录。

## 项目结构

```text
src/sc2bench_env/       平台环境、工具协议、运行时、记录与评测
agents/llm_agent/       可运行的外部 LLM Agent 示例
examples/               单局、Runner、批量和 LLM 对战示例
benchmarks/             可复用的评测 Suite 配置
dependencies/           Sharpy 与寻路依赖
docs/                   安装、接口、Agent 接入和架构文档
```

## 文档

| 内容 | 入口 |
| --- | --- |
| 安装、打包与 Linux 构建 | [docs/installation.md](docs/installation.md) |
| 外部 Agent、LLM Adapter、Runner、记录与评估 | [docs/agent_integration.md](docs/agent_integration.md) |
| Tool、Observation、任务和底层执行规则 | [docs/interface.md](docs/interface.md) |
| 示例脚本 | [examples/README.md](examples/README.md) |
| 示例 LLM Agent | [agents/llm_agent/README.md](agents/llm_agent/README.md) |

开发依赖可通过 `python -m pip install -e ".[dev]"` 安装。
