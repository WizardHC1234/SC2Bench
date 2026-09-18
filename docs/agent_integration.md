# Agent 接入

支持直接操作 Environment，也支持 Runner 管理对局。外部 Agent 管模型调用、上下文、Skill、Memory 和回复解析；平台管观测、动作执行和记录。是否使用 Runner 由调用方选择。

## 输入和输出

Agent 是接收 `AgentInput` 的可调用对象，不必继承基类：

| 输入 | 含义 |
| --- | --- |
| `request.observation` | 当前状态；`to_dict()` 取结构化数据，`section_lines()` 取文本 |
| `request.feedback` | 上次提交回执，首轮为 `None` |
| `request.platform_messages` | 规则和文本观测，已含上轮反馈，不必重复追加 |

返回动作数组即可。需要保存实际模型输入输出时，返回 `AgentTurn(decision, agent_context)`。当前任务进度看观测，不能把“已接受”当作完成。

```python
from sc2bench_env import BenchmarkRunner, EpisodeConfig

def create_agent():
    def decide(request):
        # 在这里调用自己的模型并解析动作；下面只演示接口。
        return [{"action": "wait"}]
    return decide

batch = BenchmarkRunner(backend_factory=lambda: "fake").run(
    [EpisodeConfig(opponent="easy", enemy_style="macro",
                   game_time_limit_seconds=2)],
    agent_factory=create_agent,
)
print(batch["summary_path"])
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

## 三个 LLM 示例

| 文件 | 用法 |
| --- | --- |
| [agent_integration.py](../examples/agent_integration.py) | 完整 LLM Agent 与 Runner 单局；先看 `main()`，再看 `LLMAgent` |
| [run_llm_benchmark.py](../examples/run_llm_benchmark.py) | 复用同一 Agent，运行 Suite 批量对局 |
| [llm_vs_ai.py](../examples/llm_vs_ai.py) | 手写 `reset/get_context/step/close` 循环，不使用 Runner |

```bash
python examples/agent_integration.py --dry-run
python examples/llm_vs_ai.py --opponent easy --enemy-style macro
python examples/agent_integration.py --difficulty mediumhard --enemy-style macro
python examples/agent_integration.py --skill examples/skills/tank.md
python examples/run_llm_benchmark.py --dry-run --repetitions 1
python examples/run_llm_benchmark.py --repetitions 1
```

API 设置在单局示例顶部，也可由 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` 覆盖。默认显示输入和回复，`--quiet` 收起全文。默认不加载 Skill；`--skill` 仅在 Runner 单局示例提供。项目外可运行脚本绝对路径，需先安装平台。

### 直接使用环境

`llm_vs_ai.py` 的 `run_episode()` 展示完整循环：`reset()` → `get_context()` → 调用 Agent → `step()` → 检查 `terminated`，最后 `close()`。API失败和非法回复也有处理，不经过 Runner；只生成单局记录，没有批次索引。模型客户端和解析复用 `agent_integration.py`，不会调用它的 Runner 入口。

具体运行命令和可复制的接入代码见 [示例说明](../examples/README.md)。

## 配置对手

- `opponent`：`veryeasy/easy/medium/mediumhard/hard/harder/veryhard/cheatvision/cheatmoney/cheatinsane`，默认 `easy`。
- `enemy_style`：`random/rush/timing/power/macro/air`，默认 `random`，与难度独立。
- `enemy_race`：`terran/protoss/zerg/random`；当前我方 `race` 仅支持 `terran`。

旧 `builtin_` 前缀及 `vision/money/insane` 别名仍兼容，新配置和记录使用短名称。风格是内置 AI 的倾向，不保证固定战术；平台不提前向 Agent 透露该设置。

## 批量与评估

```python
from sc2bench_env import BenchmarkRunner, BenchmarkSuite, Evaluator

suite = BenchmarkSuite.load("benchmarks/terran_pilot.json")
suite = suite.with_overrides(repetitions=1, enemy_style="macro")
batch = BenchmarkRunner().run(suite, create_agent)
report = Evaluator.evaluate_batch(batch["summary_path"])
```

默认套件为 Easy/Medium 各两局，仅作流程检查。Suite 保存地图、种族、难度、风格、时限、重复数和决策上限，不含模型或打法，不支持 seed。按 case 顺序执行各自全部重复局。批量示例的 `--opponent` 筛选已有 case，不修改难度。

```bash
python -m sc2bench_env run --suite benchmarks/terran_pilot.json --agent your_package.agent:create_agent
python -m sc2bench_env evaluate /absolute/path/to/run_file.json
python -m sc2bench_env paths
```

外部 Agent 模块应已安装或可正常导入。`evaluate` 只读记录，不调用模型或游戏，不覆盖原文件。汇总区分自然终局、超时平局、中断和故障，不统计主观战术成功率；版本指纹不保证轨迹严格复现。

## 记录和路径

每局保存 `episode.txt`、`interactions.jsonl` 和可用的 `replay.SC2Replay`；Fake 没有回放。批次索引在 `records/runs/`，每局仍有独立目录。

源码/可编辑安装默认使用项目 `records/`，普通包使用用户 `~/.sc2bench/records/`。环境变量 `SC2BENCH_OUTPUT_DIR` 可设置绝对输出根；显式 `record_dir/results_dir` 优先。只设置 `record_dir` 时，索引跟随到其 `runs/`。默认路径不随工作目录改变。

```python
from sc2bench_env.recording.reader import read_episode

episode = read_episode(episode_directory)
print(episode["summary"])
print(episode["interactions"])  # 恢复完整实际消息和回复
```

## 错误与清理

格式错误返回 `decision_rejected` 和 `info['error']`，整批不执行，旧任务保留。阻塞模式暂停；连续模式可能继续或自然结束，始终检查 `terminated/info`。

Runner 接收 `AgentTurn.call_failures` 保存失败调用；`stop_after_call_failures=True` 须同时提供非空失败记录，用于中断本局。主动停止可抛 `AgentStopped`；意外 Agent 异常归因 `agent_error`，API 中断不当作游戏败局。示例支持有限重试和 JSON repair，修复格式不等于修复动作语义。

直接使用 Environment 时，`step` 返回 `(obs, feedback, terminated, info)`，不是 Gym 的奖励五元组。自行传入 `agent_context`，并在 `finally` 中调用 `env.close()`；Runner 已负责清理。
