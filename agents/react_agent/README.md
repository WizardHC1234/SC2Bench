# ReAct agent

独立的零样本 ReAct 入口，支持 Terran、Protoss、Zerg。参考作者 ALFWorld 的推理、动作、观测循环，使用原生工具调用；不加示例轨迹、独立规划器或反思阶段，不限制分析篇幅，不要求输出阶段标签。

`agent.py` 实现单会话循环，复用 `llm_agent` 的客户端、API 重试和记录。默认 Full Session、不加载策略 Skill。现有 `llm_agent` 和平台代码不变。

模型接收初始任务与平台规则，随后接收真实观测。它可以先输出分析，也可以直接调用工具。纯分析回复得到 `OK.` 确认，游戏时间不变；工具返回作为真实观测进入历史，再由模型选择后续行动。每次新的游戏观测直接进入同一会话，不再追加 ACT 催促指令。

查询与动作可交错，提交动作后仍可查询；同一条回复可包含多个工具，`advance` 必须是最后且唯一的推进调用。动作被拒不会撤销之前暂存的工作。保留当前平台种族规则、24 轮/决策的终止上限，以及 Thinking 字段的保存。游戏版本、执行器和模型服务没有回退。

## 完整提示词与方法来源

模型收到的是六部分平台 system 提示词、当前观测与反馈，以及此前模型输出和工具结果。`prompts.py` 只声明替换部分，不是完整输入。工具定义通过 API 的 tools 字段另行发送，完整会话保存在实际 `session.json`。

作者 [ALFWorld 代码](https://github.com/ysymyth/ReAct/blob/6bdb3a1fd38b8188fc7ba4102969fe483df8fdc9/alfworld.ipynb) 的 `alfworld_run` 保留推理、动作和观测历史，`think:` 步得到 `OK.`，其他动作得到环境结果。此处将文本 `think:` 映射为正文分析，将文本环境动作映射为原生工具调用。工具可以批量发送，时间仅由 SC2 的 `advance` 推进，这两点属于平台接口适配。

原始提示词加入两条 [示例轨迹](https://github.com/ysymyth/ReAct/blob/6bdb3a1fd38b8188fc7ba4102969fe483df8fdc9/prompts/alfworld_3prompts.json)。按本项目的零样本设置，此处不加示例，也不复制作者的换行停止符、100 token 输出上限或 49 步限制。初始任务说明针对 SC2 编写，因此是参考作者机制的零样本适配，不是原始 few-shot 实验复现。

## 运行

从仓库根目录运行：

```powershell
.\venv\Scripts\python.exe -m agents.react_agent --help
.\venv\Scripts\python.exe -m agents.react_agent --model-key deepseek-v4-flash --opponent hard --enemy-style rush --dry-run
.\venv\Scripts\python.exe -m agents.react_agent --model-key deepseek-v4-flash --opponent hard --enemy-style rush
```

`--model-key` 复用 `agents/config.json` 中的模型配置；不传时使用现有 `llm_agent` 的模型环境变量及默认配置。`--thinking` / `--no-thinking` 和 `--temperature` 可以覆盖所选配置。

记录默认写入 `records/react_agent/` 下每局独立目录，可用 `--record-dir` 更改。平台保存 `session.json` 和 `metrics.json`，正常启动时 `log.txt` 记录模型正文和工具结果；`--quiet` 关闭正文打印。API 单独返回的 reasoning 字段也保存在会话中并打印到日志，供开启 Thinking 时使用。默认游戏上限 30 分钟、500 次环境决策、每次决策最多 24 轮模型回复。不额外设置输出 token 上限。

## 接入评测

```python
from agents.react_agent import create_agent

# 每局调用一次工厂，创建独立会话。
agent = create_agent(call_llm=your_model_callback, verbose=True)
```

`BenchmarkRunner` 可直接使用工厂。将 agent metadata 的 `name` 设为 `react_agent`，`implementation` 设为 `agents.react_agent.ReActAgent`，并记录模型、temperature、thinking、conversation_mode、max_tool_rounds，便于与原始 agent 区分。

方法依据：[ReAct 作者说明与代码入口](https://react-lm.github.io/)。本实现使用原生工具调用，无固定示例轨迹和跨局经验。
