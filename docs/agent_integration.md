# Agent 接入

支持直接操作 Environment，也支持 Runner 管理对局。外部 Agent 管模型调用、上下文、Skill、Memory 和回复解析；平台管观测、动作执行和记录。是否使用 Runner 由调用方选择。

## 输入和输出

Agent 是接收 `AgentInput` 的可调用对象，不必继承基类：

| 输入 | 含义 |
| --- | --- |
| `request.observation` | 当前状态；`to_dict()` 取结构化数据。紧凑文本用 `observation_lines(observation)` |
| `request.feedback` | 上次提交回执，首轮为 `None` |
| `request.tool_specs` | 当前这一局暴露的工具说明 |
| `request.call_tool` | 调用一个工具并立刻得到 `ToolResult` |

仓库附带 `agents.llm_agent.create_agent`。该 Agent 在实例内保持单局会话。Knowledge、Read、Action 和 `advance` 走同一个工具接口。平台不规定查询和动作的先后；示例 Harness 有自己的查询、动作和 `advance` 顺序，外部 Harness 可以换掉默认可替换指导。`staged` 只表示这一决策先记下，还没扣资源、没开工。调用 `advance` 后，平台才提交已暂存的动作并推进游戏时间。提示词分成平台合同和可替换的决策指导；工具参数写在 ToolSpec 里。需要保存实际模型输入输出时，返回 `AgentTurn(agent_context)`。其中的 `agent_calls` 记录每次模型调用的 token 和延迟；供应商没给 token 时填 `null`，不要估算。决策本身留在平台的 ToolTurn 上。当前任务进度看观测，不能把“已接受”当作完成。

```python
from sc2bench_env import AgentTurn, EpisodeConfig, ToolCall
from sc2bench_env.benchmark import BenchmarkRunner

def create_agent():
    def decide(request):
        request.call_tool(ToolCall("advance", {"seconds": 5}))
        return AgentTurn()
    return decide

batch = BenchmarkRunner(backend_factory=lambda: "fake").run(
    [EpisodeConfig(opponent="easy", enemy_style="macro",
                   game_time_limit_seconds=2)],
    agent_factory=create_agent,
)
print(batch["aggregate"])
```

Runner 默认实机 `sharpy`，上例显式选 `fake`。每局调用一次无参数工厂，创建独立 Agent。Fake 只模拟协议；若传入 LLM Agent，仍会调用模型。

实际模型调用后可返回：

```python
from sc2bench_env.interface.agent import AgentTurn

return AgentTurn(agent_context={
    "messages": actual_messages,
    "assistant_content": original_reply,
    "agent_calls": [{
        "call_id": "call-1",
        "role": "main",
        "model": "provider-model-name",
        "status": "ok",
        "input_tokens": None,
        "output_tokens": None,
        "latency_seconds": 0.4,
    }],
})
```

`role` 可以是 `main`、`planner`、`checker` 或 `sub-agent`。失败调用用同一格式，`status` 为 `failure`，并带上错误类型和 HTTP 状态。`session.json` 继续保存实际发送的 messages 和公开回复，不保存隐藏推理。

正式评测要提供 Agent 元数据。调试运行可以省略。`settings` 是 JSON 对象，平台只保存，不解释其中的 planning、memory 或 skill。不要写入密钥、令牌、服务地址、未发送的模板或请求头。平台不会自动清洗任意自由文本。

```python
batch = BenchmarkRunner(backend_factory=lambda: "fake").run(
    suite,
    agent_factory=create_agent,
    agent_metadata={
        "name": "agent-name",
        "version": "1",
        "model": "provider-model-name",
        "settings": {"temperature": 0.5, "planning": "none",
                     "memory": "episode", "skill": "none"},
    },
)
```

## LLM 示例

| 文件 | 用法 |
| --- | --- |
| [llm_vs_ai.py](../examples/llm_vs_ai.py) | 手写 `reset` / `step` / `close` |
| [agent_integration.py](../examples/agent_integration.py) | 同一 `agents.llm_agent`，交给 Runner 跑一局 |
| [run_llm_benchmark.py](../examples/run_llm_benchmark.py) | 按 Suite 批量跑，每局一个新 Agent |
| [llm_vs_llm.py](../examples/llm_vs_llm.py) | 两个已支持种族的 Agent 对战；各自的 `advance` 到点才询问那一边 |

```bash
python examples/llm_vs_ai.py --dry-run
python examples/llm_vs_ai.py --opponent easy --enemy-style macro
python examples/agent_integration.py --opponent mediumhard
python examples/run_llm_benchmark.py --dry-run --repetitions 1
python examples/run_llm_benchmark.py --repetitions 1 --max-parallel 2
python examples/llm_vs_llm.py --dry-run
```

示例不另写模型客户端。地址和密钥用 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`，与 `python -m agents.llm_agent` 相同。`--quiet` 收起全文。

### 直接使用环境

`llm_vs_ai.py` 的 `run_episode()` 是完整循环：`reset()` → `begin_tool_turn()` → 把观测、`tool_specs()` 和 `call_tool` 交给 Agent → `finish()` 后 `step()` → 检查 `terminated`，最后 `close()`。API 失败停在记录里，不经过 Runner。

具体运行命令和可复制的接入代码见 [示例说明](../examples/README.md)。

## 配置对手

- `opponent`：`veryeasy/easy/medium/mediumhard/hard/harder/veryhard/cheatvision/cheatmoney/cheatinsane`，默认 `easy`。
- `enemy_style`：`random/rush/timing/power/macro/air`，默认 `random`，与难度独立。
- `race`：`terran/protoss/zerg`，默认 `terran`。
- `enemy_race`：`terran/protoss/zerg/random`。

`vision/money/insane` 会收成 `cheatvision/cheatmoney/cheatinsane`。记录里只保存短名称。风格是内置 AI 的倾向，不保证固定战术；平台不提前向 Agent 透露该设置。

## 批量、并行与评估

```python
from sc2bench_env.benchmark import BenchmarkRunner, BenchmarkSuite

suite = BenchmarkSuite.load("benchmarks/terran_pilot.json")
suite = suite.with_overrides(repetitions=1, enemy_style="macro")
# 同时最多进行两局，默认 max_parallel=1 为串行
batch = BenchmarkRunner().run(suite, create_agent, max_parallel=2)
print(batch["aggregate"])
```

默认套件为 Easy/Medium 各两局，仅作流程检查。Suite 保存地图、种族、难度、风格、时限、重复数和决策上限，不含模型或打法。可选 `game_seeds` 的长度必须等于 `repetitions`；每个 case 的第 n 次重复使用第 n 个 seed，并写入现有的 `EpisodeConfig.seed`。这个 seed 只控制 SC2 模拟器，不控制模型、Harness、Python 随机数或外部服务。不配置时仍由 SC2 自己随机。按 case 顺序排定各自全部重复局。批量示例的 `--opponent` 筛选已有 case，不修改难度。

`max_parallel` 是同时进行的对局上限，不是总局数；默认1，空出名额即启动下一局，不等待整组结束。并行每局使用独立 spawn 进程、新 Agent 和新环境，SC2 清理注册表、端口选择和模型上下文不共享。每次运行建立一个批次目录，里面是 `batch.json` 和 `episodes/` 下的各局记录。每完成一局就原子更新 `batch.json`，中断后已完成的结果还在。结果按计划顺序排列，单局失败不自动重跑。同一批次不能混用 blocking 和 realtime：blocking 在推理期间暂停游戏，用于比较模型和 Harness；realtime 让推理耗时进入游戏，用于比较完整部署系统。第一阶段的正式对比使用 blocking。

并行需安装 `.[parallel]`，LLM 示例的 `.[llm]` 已包含。脚本入口应放在 `if __name__ == "__main__":` 下；工厂可使用普通函数或可序列化闭包，在工厂内创建客户端、锁和环境，不捕获已启动的游戏、会话或不可序列化对象。工厂在子进程执行，对父进程变量的修改不会回传。

Ctrl+C 停止继续派发，先请求活动局关闭，再清理无响应的本批次工作进程及其子进程；已完成记录保留。强制停止留下的未终结记录标为 incomplete，不冒充游戏败局。多局终端输出可能交错，批量 LLM 示例建议 `--quiet` 后查看逐局记录。并行占用更多 CPU/内存，也会同时请求模型 API；平台不提供跨局 API 限流器。

```bash
python -m sc2bench_env run --suite benchmarks/terran_pilot.json --agent your_package.agent:create_agent
python -m sc2bench_env run --suite benchmarks/terran_pilot.json --agent your_package.agent:create_agent --max-parallel 2
python -m sc2bench_env evaluate /absolute/path/to/batch.json
python -m sc2bench_env paths
```

外部 Agent 模块应已安装或可正常导入。`evaluate` 只读记录，不调用模型或游戏，不覆盖原文件。汇总区分自然终局、超时平局、中断和故障，不统计主观战术成功率；版本指纹不保证轨迹严格复现。

## 记录和路径

每局保存 `episode.txt`、`session.json`、`log.txt` 和可用的 `replay.SC2Replay`；Fake 没有回放。`episode.txt` 记录配置、平台合同和终局摘要，其中包括实际使用的 seed。`session.json` 包含 `episode_id`、`model`、`result`、这一局发给模型的 `tools`，以及模型实际看到的 `messages`（系统提示、观测、tool call 和 tool result）。`log.txt` 复制这一局写到终端的内容，环境关闭后不再追加。Runner 把完整批次写在 `records/<batch-directory>/batch.json`，不在记录根目录散落 `run_*.json`。

源码/可编辑安装默认使用项目 `records/`，普通包使用用户 `~/.sc2bench/records/`。环境变量 `SC2BENCH_OUTPUT_DIR` 可设置绝对输出根；显式 `record_dir` 优先。默认路径不随工作目录改变。`Evaluator.evaluate_batch` 只读 `batch.json` 和各局记录，不调用游戏或模型，并重新计算胜负、工具次数、模型调用、token 和延迟。blocking 与 realtime 不会合成一个胜率。供应商没返回的 token 保持空值。

```python
from sc2bench_env.recording.reader import read_episode

episode = read_episode(episode_directory)
print(episode["summary"])
print(episode["messages"])  # 这一局最终保存的完整 session
```

## 错误与清理

格式错误返回 `decision_rejected` 和 `info['error']`，整批不执行，旧任务保留。阻塞模式暂停；连续模式可能继续或自然结束，始终检查 `terminated/info`。

Runner 接收 `AgentTurn.call_failures` 保存失败调用；`stop_after_call_failures=True` 须同时提供非空失败记录，用于中断本局。主动停止可抛 `AgentStopped`；意外 Agent 异常归因 `agent_error`，API 中断不当作游戏败局。示例对 API 失败做有限重试。模型正文不会被解析成动作，也不会自动改写工具调用。

直接使用 Environment 时，`step` 返回 `(obs, feedback, terminated, info)`，不是 Gym 的奖励五元组。自行传入 `agent_context`，并在 `finally` 中调用 `env.close()`；Runner 已负责清理。
