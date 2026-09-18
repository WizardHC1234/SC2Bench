# SC2Bench

将星际争霸 II 封装为面向 Agent 的高层交互环境。Agent 决定生产、科技、侦察和军队行动；平台提供规则、客观观测、任务执行、记录和串行评测，Sharpy 负责底层控制。

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
    obs, feedback, terminated, info = env.step([{"action": "wait"}])
finally:
    env.close()
```

## LLM 示例

先在 `examples/agent_integration.py` 配置 API，或设置 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`。分享前移除密钥。

```bash
python examples/agent_integration.py --dry-run
# 直接使用环境，不经过 Runner
python examples/llm_vs_ai.py --opponent easy --enemy-style macro
# 使用 Runner 管理单局
python examples/agent_integration.py --opponent easy --enemy-style macro
python examples/run_llm_benchmark.py --repetitions 1
```

`--dry-run` 只预览；正常运行调用模型并启动游戏。三个示例分别展示 Runner 单局、Suite 批量和直接操作 Environment，参数用 `--help` 查看。普通安装包不包含示例或套件，外部 Agent 由调用方维护。

直接接入先看 [llm_vs_ai.py](examples/llm_vs_ai.py) 的 `run_episode()`：开始对局 → 获取上下文 → 调用模型 → 提交动作 → 判断终局，最后关闭环境。与 Runner 接入共用模型客户端，不重复保存密钥。

运行方法和代码讲解见 [示例说明](examples/README.md)。

## 范围与记录

当前支持我方人族、内置 AI 对手和串行运行。默认阻塞决策，保留连续模式；超时未分胜负记平局。平台不内置策略、Skill、Memory 或训练算法，不自动建补给站或选择下一进攻目标。其他己方种族、外部 Agent 对抗、并行和 RL 适配尚未实现。

源码安装默认输出到 `records/`，普通安装默认输出到 `~/.sc2bench/records/`。每局一个文件夹，保存 `episode.txt`、`interactions.jsonl` 和可用回放；批次索引在 `records/runs/`。

## 文档入口

| 内容 | 文档 |
| --- | --- |
| 安装、打包、Linux 构建 | [安装](docs/installation.md) |
| Agent、Runner、记录和评估用法 | [Agent 接入](docs/agent_integration.md) |
| 动作、观测和执行规则 | [动作接口](docs/interface.md) |

[进度](PROGRESS.md) · [计划](PLATFORM_PLAN.md)

开发检查：`python -m pip install -e '.[dev]'`，然后 `python -m pytest -q`。默认测试不启动游戏或调用模型。
