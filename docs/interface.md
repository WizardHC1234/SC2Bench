# 动作与观测接口

固定规则由 `PromptParts` 渲染。`LLMAdapter` 把同一份模块、`AgentInput`、`ToolSpec` 和 `ToolResult` 转成模型文本。Environment 提供 `tool_specs()` 和 `begin_tool_turn()`。模型提示词仍为英文；本文档为中文，不规定打法。

## 动作格式

每个动作是一次 `call_tool`。查询和动作可以交错。`advance` 提交已经暂存的动作，并填写正数游戏秒。下面的数组只说明一次决策里可以包含哪些调用，不是 Agent 的返回值。

```json
[
  {"name": "build", "arguments": {"target": "barracks"}},
  {"name": "train", "arguments": {"target": "marine", "count": 8}},
  {"name": "advance", "arguments": {"seconds": 30}}
]
```

示例仅说明格式，不写入系统提示词。无需新动作时返回 `[{"name": "advance", "arguments": {"seconds": 5}}]`。

## 支持的动作

| 动作 | 字段和含义 |
| --- | --- |
| `build` | `target`；追加一个建筑、附件或建筑形态。选址或变形来源由底层决定。基地按扩张放置 |
| `train` | `target/count`；`count` 是至少再获得的目标单位数。底层负责训练、折跃、孵化、单位变异或融合。一次游戏批次可能多于请求，反馈同时给出请求数和实际数 |
| `research` | `target`；已接受、排队或完成时重复提交不新增 |
| `cancel` | 使用 `target_action/target` 调整同动作、同目标的任务队列；可用 `count` 从最新请求开始撤销指定数量，省略时撤销全部匹配的未启动工作 |
| `morph_townhall` | `target/to`；人族和虫族。`target` 是观测中的 `townhall_<n>`。神族没有这个动作 |
| `scan` | `target` 为 Zone；仅人族。底层用可用热点选点，不保证扫描全区 |
| `call_mule` | 无额外字段；仅人族。矿区由底层选择。与扫描、补给投送共用轨道指挥部能量 |
| `supply_drop` | 无额外字段；仅人族。平台选择一座已完成且未投送过的补给站。与扫描、矿骡共用轨道指挥部能量，并按当前版本增加补给上限 |
| `chrono_boost` | 无额外字段；仅神族。消耗一座就绪星灵枢纽的 50 能量，底层选择正在生产或研究的建筑。不会自动施放 |
| `inject_larva` | 无额外字段；仅虫族。消耗一只就绪女王的 25 能量，底层选择一座主基地。不会自动施放 |
| `spawn_creep_tumor` | 无额外字段；仅虫族。消耗一只就绪女王的 25 能量，在菌毯上朝敌方种一个瘤。不会自动施放 |
| `scout` | `route` 为有序 Zone 数组，或字符串 `"all"` |
| `dispatch_group` | `source/units/order/target`；从 `group_0` 或活动外派编队拆出指定兵力，建立新的进攻或防守编队 |
| `attack_group` | `group/target`；把已有外派编队改为进攻指定区域，不改变成员，也不新建编队 |
| `defend_group` | `group/target`；把已有外派编队改为防守指定区域，不改变成员，也不新建编队 |
| `reinforce_group` | `source/destination/units`；在 `group_0` 和活动外派编队之间转移指定单位，不改变目标编队的当前命令 |
| `merge_groups` | `destination/sources`；把一个或多个活动编队的存活成员转入目标编队，目标编队保留 ID 和当前命令 |
| `retreat_group` | `group`；可选 `method`。省略 `method` 时按移动撤回，到家后归并。神族 `method` 为 `recall` 时只使用当前主基地星灵枢纽召回，半径外的成员继续移动撤回；枢纽死亡、未完成、能量不足或技能不可用时失败，编队保持原命令 |
| `advance` | 必填正数 `seconds`；推进指定游戏秒后返回下一次观测 |

生产和科技的目标、成本、前提与参考耗时由 Knowledge Tools（`query_unit_data`、`query_building_data`、`query_research_data`、`query_prerequisite_path`、`query_race_data`）从 Catalog 读取，不再写入固定系统提示词。耗时是单项开始后到就绪的近似游戏秒，不含排队、资源等待和工人行走。

Agent 只提交规范化 Tool Call。Read / Knowledge 立即查询；Action Tools 进入本轮批次，最后以 `advance(seconds)` 提交。

### 生产与取消

重复 `build/train` 会追加；省略旧需求不取消。按接受顺序调度，不自动补前提或重排阵容；兼容产能可并行，不要求前一项完成后才执行下一项。

命令完成边界：建造或建筑形态转换实际开始、训练达到请求的最少单位、研究进入游戏队列、基地形态转换发令。建筑就绪和科技生效继续看观测。

`cancel(target_action=..., target=..., count=...)` 用于主动调整跨轮次维护的任务队列。提供 `count` 时，从最新加入的匹配请求开始撤销，直到达到指定数量或没有更多可撤销工作；省略 `count` 时撤销全部匹配的未启动工作。已派工人、施工、已付费或已完成的训练、折跃、融合、变异和研究不取消，也不退款。工具结果会立即给出实际撤销量、剩余未启动数量和保留的已开始或已付费数量；调用 `advance` 后修改才正式提交。观测的 `Cancellable` 显示当前可撤销数量，未知不猜。

### 军队与侦察

军队目标使用观测里的 Zone。`dispatch_group` 从指定来源拆出兵力并建立新编队；`attack_group` 和 `defend_group` 只对已有外派编队改令。`reinforce_group` 可以从 `group_0` 或任意可转移的活动编队向另一编队增援，也可以把部分成员送回 `group_0`。兵力不足时整项拒绝，不部分派遣。

`merge_groups(destination=..., sources=[...])` 显式整合已有外出编队。所有参与编队会先统一校验；任意 ID 不存在、重复、正在撤退或召回时整项拒绝。合并完成后，source 编队以 `merged_into:<destination>` 结束，成员不会中途释放到 `group_0`。

`group_0` 是默认防守和可派兵力池：二基地完成后驻守己方 natural，二基地未完成或失守时回退主基地，且不能整体改令。外出组不自动补员；新兵默认归入 `group_0`，需显式再次派遣。进攻组到达目标并连续确认该区域已清空后，改为驻守并留在该区域，直到新指令或 `retreat_group`。`defend_group` 持续驻守，直到改令或撤回。`retreat_group` 立刻开始回家；幸存单位到家后才并入 `group_0`，原编组随之结束。阵亡、撤回、成员转移和当前运输状态在观测显示；执行中不表示交火或成功。运输、移动和交战微操由底层处理，不自动选择下一进攻目标。

`scout.route` 数组按序检查选定矿区；`"all"` 请求一轮非己方扩张矿区中心巡查。仅检查中心，不保证全区可见或敌人找全；探员死亡失败，不自动替换，新侦察替换旧任务。侦察不自动改军队目标。

### 时间推进

`advance.seconds` 是必填正数。前面的动作先按顺序注册，游戏再运行指定秒数；生产、建造、移动和战斗继续执行。指定时间到达后返回最新观测；对局提前结束时立即返回终局。旧 `wait`/`any_of`/`all_of` 条件不再接受。平台不规定固定推进时长：战斗观察可用较短秒数，宏观生产可用较长秒数。

## 观测和反馈

平台内部保留 Full Observation，Read Tools 在一个决策中读取同一份冻结快照。Runner 和 Versus 交给 `AgentInput.observation` 的是结构化 Brief Observation；`LLMAdapter.render_input` 将它渲染为 `[Game]`、`[Economy]`、`[Production]`、`[Infrastructure]`、`[Available Targets]`、`[Army]`、`[Relevant Zones]`、`[Abilities]`、`[Changes]` 和 `[Decision]`。Brief 仍保留普通决策所需的资源、收入、饱和度、供给、持续生产任务、建筑规模和每个活动 Group 的实际兵力，但不展开完整地图拓扑、生产槽位和低频编队诊断。相同 action、target、状态和阻塞原因的持久生产请求在 Brief 中合并显示总请求数、剩余量和付费量，最早一项的位置保留其优先级；`query_production_state` 仍返回未合并的完整顺序。

Read Tools 包括 `query_map_overview`、`query_zone_state`、`query_route`、`query_production_state` 和 `query_army_state`。前两类动态详情查询可按生产动作/目标或 Group ID 过滤。所有查询结果都标明 `as_of_game_time`，不会推进游戏、执行侦察或获得战争迷雾中的隐藏信息。Knowledge Tools 继续提供静态单位、建筑、升级和前置关系。

| 内容 | 关注信息 |
| --- | --- |
| 游戏和经济 | 游戏时间、种族、时限、资源、供给、实际矿物/瓦斯/其他工人分配、矿物与瓦斯工人饱和值、每分钟收入 |
| 地图和基地 | Zone、角色、已知归属、矿气剩余/可靠容量、中心视野和地形关系 |
| 生产 | 实际建筑/训练/研究、设施和槽位、持久任务优先级摘要、出生进度、付费队列及阻塞 |
| 军队 | 总兵力和可派兵力、编队、成员损失、目标、执行及运输状态 |
| 侦察和能力 | 路线、路点、探员状态、本族能量与施法条件、近期事件 |

未知用 `null/unknown/?`，不替换成零；当前可见敌情和带时间的历史记忆分开。中心可见不等于全区看清，当前没有敌人不等于永久清空。对局未结束时，隐藏基地仍可能存在，平台不泄露位置或自动清图。

反馈解释上次提交是否接受、追加、无变化或拒绝；当前执行进度以观测为准。近期事件可能跨轮重复，不按展示次数统计产出或成功。

## 控制边界

Agent 负责生产、供给、科技、基地形态转换、侦察，以及本族的扫描、MULE、时空增压、注卵、菌毯肿瘤和军队宏观行动。底层负责选址、工人分配、采矿、维修、续建、集结、局部战斗和运输。自动补给、自动基地形态转换、自动 MULE、自动时空增压、自动注卵和自动种瘤都关闭。

统一 Tool 接口逐次校验：Action 会在冻结状态上预演当前事务，立即返回 staged、rejected 或 no-op，以及相关的待办数量、兵力缺口或取消数量。被拒绝和 no-op 的调用不进入 staged actions，之前已暂存的调用仍保留；Agent 可以根据结果修正，最终由 `advance` 原子提交并推进游戏。执行期间发生的开始、阻塞、完成和失败在下一 Observation 的 Changes 中返回。直接调用 `Environment.step()` 并传入旧式动作数组时，数组 Schema 非法则整批不提交。旧任务保留；默认阻塞模式暂停，连续模式仍可能推进或结束。超时为 `Tie/time_limit`，Agent 中断和故障不伪装成游戏败局。
