# Agent 接入

支持直接操作 Environment，也支持 Runner 管理对局。外部 Agent 管模型调用、上下文、Skill、Memory 和回复解析；平台管观测、动作执行和记录。是否使用 Runner 由调用方选择。

## 输入和输出

Agent 是接收 `AgentInput` 的可调用对象，不必继承基类：

| 输入 | 含义 |
| --- | --- |
| `request.observation` | 当前状态；`to_dict()` 取结构化数据，`section_lines()` 取紧凑文本 |
| `request.feedback` | 上次提交回执，首轮为 `None` |
| `request.platform_messages` | 规则和文本观测，已含上轮反馈，不必重复追加 |
| `request.tool_schemas` | 可选；Read / Knowledge / Action 工具 Schema |
| `request.call_tool` | 可选；执行 Read / Knowledge 查询，Action 工具只返回待提交条目 |

仓库附带 `agents.llm_agent.create_agent`。该 Agent 在实例内保持单局会话，只通过 Tool Call 查询或行动。查询可以先进行多轮；这一步的动作必须在同一次回复里一起给出，并以一次 `advance` 结束。每次调用工具前，回复正文里要先写一句依据。无状态 callable 应返回规范化 Tool Call 数组 `{"name", "arguments"}`。需要保存实际模型输入输出时，返回 `AgentTurn(decision, agent_context)`。当前任务进度看观测，不能把“已接受”当作完成。

```python
from sc2bench_env import BenchmarkRunner, EpisodeConfig

def create_agent():
    def decide(request):
        # 在这里调用自己的模型并解析动作；下面只演示接口。
        return [{"name": "advance", "arguments": {"seconds": 5}}]
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
from sc2bench_env import AgentTurn

return AgentTurn(decision, agent_context={
    "messages": actual_messages,
    "assistant_content": original_reply,
})
```

不要保存未发送的模板、密钥或请求头。平台不会自动清洗任意自由文本。

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

`llm_vs_ai.py` 的 `run_episode()` 是完整循环：`reset()` → `get_context()` 加上工具清单 → 调用 Agent → `step()` → 检查 `terminated`，最后 `close()`。API 失败停在记录里，不经过 Runner。

具体运行命令和可复制的接入代码见 [示例说明](../examples/README.md)。

## 配置对手

- `opponent`：`veryeasy/easy/medium/mediumhard/hard/harder/veryhard/cheatvision/cheatmoney/cheatinsane`，默认 `easy`。
- `enemy_style`：`random/rush/timing/power/macro/air`，默认 `random`，与难度独立。
- `enemy_race`：`terran/protoss/zerg/random`；当前我方 `race` 仅支持 `terran`。

`vision/money/insane` 会收成 `cheatvision/cheatmoney/cheatinsane`。记录里只保存短名称。风格是内置 AI 的倾向，不保证固定战术；平台不提前向 Agent 透露该设置。

## 批量、并行与评估

```python
from sc2bench_env import BenchmarkRunner, BenchmarkSuite

suite = BenchmarkSuite.load("benchmarks/terran_pilot.json")
suite = suite.with_overrides(repetitions=1, enemy_style="macro")
# 同时最多进行两局，默认 max_parallel=1 为串行
batch = BenchmarkRunner().run(suite, create_agent, max_parallel=2)
print(batch["aggregate"])
```

默认套件为 Easy/Medium 各两局，仅作流程检查。Suite 保存地图、种族、难度、风格、时限、重复数和决策上限，不含模型或打法，不支持 seed。按 case 顺序排定各自全部重复局。批量示例的 `--opponent` 筛选已有 case，不修改难度。

`max_parallel` 是同时进行的对局上限，不是总局数；默认1，空出名额即启动下一局，不等待整组结束。并行每局使用独立 spawn 进程、新 Agent 和新环境，SC2 清理注册表、端口选择和模型上下文不共享。记录仍每局一目录，批次汇总只留在内存里，结果按计划顺序排列，单局失败不自动重跑。

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

每局保存 `episode.txt`、`session.json`、`log.txt` 和可用的 `replay.SC2Replay`；Fake 没有回放。`episode.txt` 记录配置、平台提示词和终局摘要。`session.json` 包含 `episode_id`、`model`、`result`、这一局发给模型的 `tools`，以及模型实际看到的 `messages`（系统提示、观测、tool call 和 tool result）。`log.txt` 复制这一局写到终端的内容，环境关闭后不再追加。Runner 不另写批次索引。

源码/可编辑安装默认使用项目 `records/`，普通包使用用户 `~/.sc2bench/records/`。环境变量 `SC2BENCH_OUTPUT_DIR` 可设置绝对输出根；显式 `record_dir` 优先。默认路径不随工作目录改变。`Evaluator.evaluate_batch` 读取一份你自己保存的批次 JSON，并按其中的 `record_directory` 回读 `episode.txt`。

```python
from sc2bench_env.recording.reader import read_episode

episode = read_episode(episode_directory)
print(episode["summary"])
print(episode["messages"])  # 这一局最终保存的完整 session
```

## 错误与清理

格式错误返回 `decision_rejected` 和 `info['error']`，整批不执行，旧任务保留。阻塞模式暂停；连续模式可能继续或自然结束，始终检查 `terminated/info`。

Runner 接收 `AgentTurn.call_failures` 保存失败调用；`stop_after_call_failures=True` 须同时提供非空失败记录，用于中断本局。主动停止可抛 `AgentStopped`；意外 Agent 异常归因 `agent_error`，API 中断不当作游戏败局。示例支持有限重试和 JSON repair，修复格式不等于修复动作语义。

直接使用 Environment 时，`step` 返回 `(obs, feedback, terminated, info)`，不是 Gym 的奖励五元组。自行传入 `agent_context`，并在 `finally` 中调用 `env.close()`；Runner 已负责清理。
