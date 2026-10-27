# SC2Bench 实验 Agent

独立于 `agents/llm_agent/` 的研究实现，只依赖平台的公开 Agent、工具和记录接口。
四种 Harness 共用模型客户端、种族规则、工具集、Full Session 会话和预算统计。
没有加载种族打法 skill、回放真值、外部记忆或前局经验。

## 四种流程

| 参数 | 单局内流程 |
| --- | --- |
| `act_only` | 每次调用前明确要求只返回工具调用，正文为空；收到工具结果后继续选择动作。带正文的工具回复会原样留档并返回修正提示，本批次不执行。 |
| `react` | 提示模型先给简短分析，再调用工具，根据返回结果继续决策。 |
| `plan_and_execute` | 首次观测调用 Planner；Executor 使用 ReAct 工具循环执行剩余计划的第一步；游戏时间推进后，新观测和该步骤的执行反馈触发 Replanner。计划步骤可以跨多次推进，执行器提交动作不等于步骤完成。 |
| `reflection` | 首次决策使用 ReAct；每次游戏时间推进、收到新观测后，先调用 Reflector，再继续 ReAct。反思针对本局实际反馈，随本局结束销毁。 |

所有模块使用同一模型、同一份本局历史和平台提供的有限视野信息。Planner、Replanner、Reflector
不执行环境工具；Executor/Actor 可以交错查询和动作。`advance` 必须是回复中的最后一个调用。
反思和重规划不在工具暂存反馈上触发；同一游戏时刻再次决策也不重复触发。
反思阶段若输出 DSML/XML 等工具调用文本，会被要求重写；这类文本从不解析为环境操作。
Act-only 请求使用 `tool_choice=required`，由支持该模式的服务约束为原生工具输出；其他 Harness 的执行阶段保持 auto。
Planner/Replanner 使用服务端 JSON 对象模式，再校验计划字段；Actor 与 Reflector 不使用 JSON 模式。

Reflection 是**局内适配版本**，借鉴反馈—反思—后续行动的组织方式，不是原始 Reflexion
跨次尝试、积累经验后重试的复现。当前触发规则固定为每个新决策观测，不按输赢或模型表现临时挑选。
ReAct 是统一平台接口下的提示词适配；Plan-and-Execute 采用 Planner → Executor → Replanner
结构，没有引入 LangChain 运行时，也不声称复现某个论文的全部实现细节。

公开代码依据与逐项检查见 [实现核查报告](../../analysis/metric_design/harness_audit/implementation_verification.md)。
本次核查固定了 ReAct 作者仓库、LangGraph 官方教程、Reflexion 作者仓库以及 τ-bench
的 Act/ReAct 实现版本。四种方法都适配为原生工具接口，允许一条回复包含多个调用；
Plan-and-Execute 在新观测到达后重规划，是连续 SC2 环境的适配。
Executor 的当前任务明确为 `plan[0]`；每次执行保留任务名、工具结果在完整会话中的位置及下一次观测位置。
这些记录只关联执行证据，不自动把 `advance`、暂存成功或模型自述改写为“步骤已完成”。

阶段指令直接说明任务，不添加 `[Stage: ...]` 前缀。日志和核查从 `metrics.json` 的调用角色关联模型回复，保留对旧记录的兼容。
工人计数修复的完整验证见 [记录核查](../../records/experiments/full_validation_20261027_210319/detailed_review.md)：原始四局发现气矿内部工人漏计并已修复，修复后的三局模型完整复测和一局固定工人请求验证单独保存。
当前 30 分钟配置和 Thinking 接口验证见 [平台复查](../../records/experiments/limited_recheck_20261027_232753/review_report.md)。

## 提示词与外围约束

共同提示词要求以最新观测为准：历史状态、计划、反思和动作暂存结果都不能替代实际执行反馈。
拒绝或 no-op 不会撤销此前暂存的工作；修正时只调整需要修正的部分，避免重复添加持续任务。
四种 Harness 共享这些规则，区别保留在各自的分析与执行流程中。

平台共同说明明确区分人口预占和单位完成、工人总数和采集分配、建筑出现和建筑就绪。
扫描、MULE、空投补给、时空加速、注卵和菌毯瘤通过后续原生观测的效果确认施放，
不把 Python 本地队列接收命令当成施放完成。仍在移动或施法的原生命令会继续等待；
原生明确拒绝可重新尝试，无订单且连续 5 秒没有效果则报告确认失败，不自动重复不明施放。

- Planner 生成具有可观察完成条件的步骤；Replanner 的完成项必须逐字引用上一次计划，
  不能同时出现在剩余步骤中，也不能把放弃的项目写成已完成。程序校验结构和引用，实际执行状态仍由平台提供。
- 格式修正保留原阶段指令、旧计划和证据要求，不会退化为仅让模型随意重写一段 JSON。
- Actor/Executor 根据真实工具反馈修正动作；反思阶段只评估结果，不能代替 Actor 发出操作。
- 工具异常时保存此前已有的返回结果；报错调用标为执行状态不确定，后续未调用部分标为未提交，
  然后结束本局。程序不会自动重放可能已生效的调用。
- API 最终失败会留存调用记录和停止说明；服务端内容过滤也会停止。
  关闭 Thinking 时若返回原生 reasoning 或非零 reasoning token，记录后按配置不一致停止。
- JSON 重复键及 NaN/Infinity 不会被静默覆盖或转换；工具调用参数保留原文，交由修正流程处理。

这些处理不增加默认调用次数或输出 token 上限，也不截断 Full Session。

## 检查配置，不启动游戏或请求 API

在仓库根目录执行：

```powershell
.\venv\Scripts\python.exe -m agents.experiment_agent --provider deepseek --model deepseek-v4-pro --harness react --dry-run
```

不指定 `--suite` 时自动生成正式场景矩阵：

- 己方与对手均为 Terran、Protoss、Zerg。
- Ancient Cistern LE、Altitude LE，传入平台的地图名为 `AncientCisternLE`、`AltitudeLE`。
- MediumHard、Hard、Harder、VeryHard；Rush、Timing、Power、Macro、Air。
- 360 个场景，每场景 20 局，即每个模型 × Harness 为 7,200 局。
- 两个模型 × 四种 Harness 为 57,600 局。模型 ID 由命令行指定，不自动替换模型。
- 默认 30 分钟游戏时限，阻塞决策，非 realtime。
- 默认每个场景的重复编号对应 SC2 seed 0–19，各 Harness 复用同一组 seed。
  这只是可复现的模拟器种子安排，不控制服务端生成随机性。

正式运行前需要确认本机安装的两张地图名称、模型服务的实际版本和工具调用支持。

## 运行

客户端只从以下环境变量读取连接信息，不读取测试 Agent 的配置文件：

```powershell
$env:SC2BENCH_LLM_BASE_URL = 'https://api.deepseek.com'
$env:SC2BENCH_LLM_API_KEY = '<your-key>'
.\venv\Scripts\python.exe -m agents.experiment_agent --provider deepseek --model deepseek-v4-pro --harness react --record-dir records/experiments/deepseek_pro/react
```

将 `--harness` 分别设为 `act_only`、`react`、`plan_and_execute`、`reflection`。
默认串行运行；`--max-parallel` 可复用平台的进程隔离并行功能。
`--suite path.json` 可传入现有 BenchmarkSuite，包括小规模试跑套件。
`--backend fake` 替换游戏环境，但仍会请求配置的真实模型；完全离线验证使用下方测试。

本实现不自动补跑失败局。保留批次中每个预定试验的结果及失败原因，避免持续补跑到成功而改变样本。

## Thinking 与服务配置

默认关闭原生 Thinking，使用 `--thinking` 显式开启。服务配置按部署方式选择：

| `--provider` | 实际请求体字段 |
| --- | --- |
| `deepseek` | `thinking: {type: disabled}` |
| `deepseek_sglang` | `chat_template_kwargs: {thinking: false}` |
| `qwen_vllm` | `chat_template_kwargs: {enable_thinking: false}` |
| `qwen_dashscope` | `enable_thinking: false` |

Qwen 的模型 ID 使用部署服务暴露的名称。vLLM 服务还需要正确配置工具解析。
表中列出默认关闭值；`--thinking` 将对应开关设为开启。客户端保存返回的实际模型名。
关闭模式返回非空原生 reasoning 时，停止该局并记录 `native_thinking_returned`。

字段依据：[DeepSeek 官方文档](https://api-docs.deepseek.com/guides/thinking_mode/)、
[SGLang DeepSeek-V4 文档](https://github.com/sgl-project/sglang/blob/main/docs/cookbook/autoregressive/DeepSeek/DeepSeek-V4.mdx)、
[Qwen vLLM 文档](https://qwen.readthedocs.io/en/latest/deployment/vllm.html)、
[百炼 Thinking 文档](https://www.alibabacloud.com/help/en/model-studio/deep-thinking)。

## 预算和上下文

默认采用 Full Session；每个环境决策不设 API 调用次数上限，API 请求不传 `max_tokens`。
以下调用数、输出 token 上限均为可选项，不设置就不启用。
网络错误默认每个请求最多尝试 3 次，实验套件默认每局最多 500 次环境决策。

- `--max-calls-per-decision`：包括 Actor、Planner、Executor、Replanner、Reflector 和 API 重试。
- `--max-calls-per-episode`：可选整局调用上限，跨环境决策累计。
- `--max-output-tokens`：可选，显式设置时才向服务端传入每次输出上限。
- `--max-episode-output-tokens`：可选整局输出上限；后续请求的输出额度按余额收缩。
  设置此上限后，若供应商不返回 output usage，则停止，避免按零消耗继续运行。
- 输入与输出 token 均采用服务端返回值；缺失时保持 `null`，不估算。
  输入 token 当前用于统计，没有冒充精确的输入预算上限。
- Full Session 保存本局完整交互，不自动总结或截断；上下文超限按 API 失败记录。
- API 瞬时错误有限重试；非重试错误和预算耗尽停止本局，不自动伪造 `advance` 或记成败局。
- 模型决定 `advance.seconds`，不强制固定决策间隔；对比时需同时报告调用开销与游戏推进情况。

## 保存内容

复用平台现有 `session.json`、`metrics.json` 和批次文件，不新增每秒状态文件。
计划、重规划、反思和修正提示都在本局会话中；每次模型调用按
`actor / planner / executor / replanner / reflector` 标注角色并统计 token、延迟及失败。
批次元数据记录 Harness、预算、提供方配置、源码与提示词哈希。
连接地址和密钥不写入这些记录。具体 Harness 中断原因同时写入最终会话。

`log.txt` 默认详细记录每轮观测、模型调用阶段、完整模型正文、工具调用参数及工具返回，
包括 ReAct 的显式分析、Planner/Replanner 的计划和 Reflector 的反思。
日志标签统一采用英文，例如 `[Model output | reflector]` 和 `[Tool result | advance]`。
内容不截断，也不在每次调用时重复打印整局历史。
默认关闭原生 Thinking；显式开启时，服务返回的 `reasoning_content` 原样保存到 Session、写入日志，
并在后续请求中传回。关闭模式意外返回 reasoning 时，记录后按配置不一致停止。
DeepSeek 官方接口不支持 Thinking 下的 required 工具选择，因此该组合不用于 Act-only。

已有记录可以从保存的 Session 补充到日志末尾，保留原日志并注明来源，不重新调用模型或启动游戏：

```powershell
.\venv\Scripts\python.exe -m agents.experiment_agent.backfill_logs --records records/experiments/harness_smoke_20261027_155503
```

## 离线验证

```powershell
.\venv\Scripts\python.exe -m pytest tests/unit/test_experiment_agent.py -q
```

测试只使用脚本模型响应和 FakeBackend，覆盖多模块顺序、局间隔离、计划跨推进持久化、
工具调用边界、调用预算、API 重试、token 缺失、Thinking 字段、完整实验矩阵和平台记录兼容性。

实机短测入口为 `scripts/smoke_experiment_harnesses.py`，沿用上述连接环境变量：

```powershell
.\venv\Scripts\python.exe -u scripts/smoke_experiment_harnesses.py --model DeepSeek-V4-Flash --provider deepseek --game-seconds 120
```

该命令会启动真实 SC2，默认在已安装的 KairosJunctionLE 上测试四种 Harness；
可用 `--harnesses react reflection` 选择部分流程，用 `--map` 指定其他已安装地图。
记录写入独立的 `records/experiments/harness_smoke_*`，用途标记为 `workflow_pilot`。
