# 示例用法

三个示例都使用外部 LLM Agent，不内置默认打法。先按 [安装文档](../docs/installation.md) 安装平台、实机依赖和SC2客户端/地图。

## 直接调用环境，不使用 Runner

[llm_vs_ai.py](llm_vs_ai.py) 展示完整单局交互。由脚本自行调用环境，不创建 BenchmarkRunner，也不生成批次索引。

### 运行

模型、API地址和密钥配置在 `agent_integration.py` 顶部，或用 `LLM_MODEL`、`LLM_BASE_URL`、`LLM_API_KEY` 覆盖。直接环境示例复用这些配置和模型客户端，不重复维护密钥。

从项目根目录运行：

```bash
# 只预览配置，不调用模型或启动游戏
python examples/llm_vs_ai.py --dry-run
# 正常对局：难度和敌方风格独立指定
python examples/llm_vs_ai.py --opponent mediumhard --enemy-style macro
```

默认显示观测和模型回复，`--quiet` 收起全文。常用选项：

| 参数 | 含义 |
| --- | --- |
| `--opponent` | 内置AI难度，如 easy、mediumhard |
| `--enemy-style` | 敌方风格，如 random、macro、rush |
| `--enemy-race` / `--map` | 对方种族和地图 |
| `--game-time-limit` | 对局上限，单位为游戏秒 |
| `--decision-interval` | 裸wait的默认间隔，仍受60游戏秒兜底限制 |
| `--non-blocking` | 模型思考期间游戏继续，默认不开启 |
| `--record-dir` | 指定记录目录 |

完整参数用 `python examples/llm_vs_ai.py --help` 查看。此脚本正常运行使用真实SC2；预览不代表API和游戏已经可用。

### 代码从哪里看

先看 `main()` 的客户端、Agent和环境创建，再看 `run_episode()`：

1. `env.reset(config)` 开始对局，获得初始观测。
2. `env.get_context()` 生成规则和文本观测；这里只提供消息，不调用模型。
3. `agent(request)` 调用模型并解析回复，返回动作及实际输入输出。
4. `env.step(turn.decision, agent_context=turn.agent_context)` 提交动作并等待，返回新状态。
5. 检查 `terminated`；未结束则继续，最终在 `finally` 中 `env.close()`。

`step()` 返回 `(observation, feedback, terminated, info)`。反馈说明上次提交结果，当前任务进度看观测；已接受的生产需求会跨轮继续，无需每轮重提。

### 在自己的代码中使用

在项目根目录可直接复用本示例的LLM Agent和手写循环：

```python
from sc2bench_env import Environment, EpisodeConfig
from examples.agent_integration import create_agent
from examples.llm_vs_ai import run_episode

agent = create_agent()  # 创建客户端，实际模型调用发生在交互循环中
env = Environment("sharpy")
config = EpisodeConfig(opponent="mediumhard", enemy_style="macro",
                       game_time_limit_seconds=1200,
                       decision_interval_seconds=60)
try:
    result = run_episode(env, agent, config)
    print(result)
finally:
    env.close()
```

`run_episode()` 是示例里的手写循环，不是平台Runner。需要定制时复制其流程，替换Agent或上下文组织。普通wheel不包含examples，部署自己的Agent时请自行保存这些示例文件。

### 错误与记录

脚本处理API有限重试、格式拒绝反馈和决策次数上限；不会在API失败时偷偷替模型提交wait。JSON repair只能修复部分格式问题，不能保证动作合法。超时未分胜负记平局，中断不记游戏败局。

每局一个目录，保存 `episode.txt`、`interactions.jsonl` 和可用回放。源码安装默认在项目 `records/`，普通包默认在用户 `~/.sc2bench/records/`；本例不创建 `records/runs/` 索引。请勿把凭证写入交互记录。

## 需要 Runner 或批量时

- [agent_integration.py](agent_integration.py)：Runner管理单局生命周期，支持显式 `--skill`。
- [run_llm_benchmark.py](run_llm_benchmark.py)：读取Suite，逐局创建Agent并保存批次索引。

这两种方式是可选入口，不是直接调用环境的前置步骤。Skill原文保持不变，默认不加载。
