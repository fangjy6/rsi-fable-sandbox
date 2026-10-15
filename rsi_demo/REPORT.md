# 独立评审报告：spec v4.8 作为通用 RSI 内核

> 评审对象：`docs/spec-v4.8.md`（下文写作「规格 L行号」）；参照 `docs/rsi-review.md` §3/§4/§5/§7/§9。
> 验证物：`rsi_demo/`。内核 `kernel.py` 有效代码 841 行，7 个 demo 加上 `toy.py`、`common.py`、`run_all.py` 合计 561 行。只用标准库，LLM 全部用确定性规则函数 mock。
> 复现命令：`python3 rsi_demo/run_all.py`。7 个 demo 全部通过，总耗时约 2.5 秒。

## 0. 结论

**能作为通用 RSI 内核的骨架，把握约七成。** v4.8 的方向是对的：版本、执行、测量三种身份分立；produce 记录；执行者与目标分开记录；预算先预留再派发；协调者嵌套；选择协议；证据视图。调研 §5 要求内核 K 承担的五件事（版本身份、执行记录、资源记账、证据来源、显式激活），规格里都有对应落点。§9 第 11 步中我选测的 D1、D3、D4、D5、D6 主干语义，以及可选的在线学习，在一个约 840 行的内核里能够自洽实现，且断言全部通过。也就是说，这些契约**不是互相矛盾、无法实现的**。

剩下三成不确定，主要来自三处：

1. **T0 冻结的内容里有若干处写得含糊，会直接导致持久化格式不兼容。** 包括规范化 JSON、`measurement_key` 的输入集合、pending 记录如何表达执行者、summary 视图的允许列表。两个实现者照规格各写一份，`snapshot_id` 或 `measurement_key` 会对不上，而这些恰恰是「晚改就要迁移」的 T0 字段。详见 §2「阻塞」。
2. **有两类方法目前接不进来：「运行中的改进器需要调用评估」（STOP 原型）和「评估器本身是版本化制品」（evaluator 共同演化）。** 前者被 T1 推到了 T2，后者在记录层没有一等身份。详见 §1。
3. demo 没有覆盖的部分：真正的 SubprocessBackend worker（只验证了恢复边界上的 probe/terminate 与登记文件语义）、D2、D3 的「子树清理式恢复」、AnytimeBudget、取消令牌。这些地方的实现量最大，也最可能暴露新问题。

**总体判断：** 在冻结 T0 之前，先修 §5 列出的 6 处「阻塞」级文本问题，再把 §3 列出的若干项降到 T2。这样就可以按 T1 动工。

---

## 1. A 部分：够不够、是否必要（逐类「纯扩展」检查）

| 方法 | 能否纯扩展 | 依据与实现方式 | 缺什么 |
|---|---|---|---|
| **Promptbreeder**（mutation prompt 共同进化） | ⚠️ 能跑，但达不到硬性要求 | 规格 §5 L1458：「改进器配置是一个 unit；宿主侧 `propose` 读它……谁的改进器被用了，由提案时选中的 `source_snapshot_id` 与 `metadata` **声明**」 | 这与工作假设第一条硬性要求「不能只写在 metadata 里」正面冲突：宿主侧 `propose` 用了哪一版 mutation prompt，引擎观测不到。**要满足要求，应改写为 produce**：mutation prompt 所在快照作为 `executor_snapshot_id`，用 `CallProducer` 产出变体。demo2 证明这条路可行。建议在 §5 表格里把这一行改成「若需要可查询的使用事实，走 produce」，并删掉「v4.7 已可」这一说法 |
| **STOP**（改进器以 utility 为输入，再改进自身） | ⚠️ 部分可以 | 元评估用 `FunctionEvaluator(coordinator=True)` 加 `spawn` 已验证（demo2）：同一内层预算下，G1 的 improvement utility 为 0.75，G2 为 1.0 | STOP 的改进器在**自己运行时**会反复调用 utility，也就是在执行中请求评估。规格只允许可信的宿主协调者发起嵌套（L1380：「沙箱里的不可信改进器自己再发起嵌套……属 T2」），而 LLM 写出的改进器恰恰是不可信代码。缺的是「执行中请求测量」这条通道：一个由执行者发起、由引擎派发的子 measure，需要子树资源预留。目前的写法只能把 STOP 改造成由宿主策略驱动、改进器只负责一次提案，这已经不是 STOP 原型 |
| **DGM** | ✅ | 自修改走 produce + `CallProducer`（执行者即目标，`capture=snapshot`），demo2 已验证；archive 与入档规则放在 `optimizer_state`；「archive ≠ history」由 `retained_snapshot_ids` 只控制保留、不删除事实来保证（L1046–1048） | 无 |
| **Hyperagents** | ✅ | task agent 与 meta agent 放在同一个快照里，属于执行者即目标的情形；meta agent 改 task agent 时，`executor_snapshot_id` 指向 meta 所在快照。D1 已验证执行者与目标分别入账、互不混用 | 当 meta 与 task 分属两个快照、又需要整体提交时，规格自承是「剩余风险二」（L1546），未验证 |
| **HGM 后代信用** | ✅（略繁琐） | `producers_of` 与 `source_snapshot_id` 构成谱系，`measurements_of` 给出补测记录，`Window` 加上 `ctx.pending` 支持异步扩展 | `HistoryView.records()` 只能按 `kind/status/snapshot_id/executor` 过滤（L256），**不能按 `source_snapshot_id` 查「谁以 X 为基底」**。求某个 clade 的后代只能全表扫描。建议加一个过滤参数，这只是便利性改进，不阻塞 |
| **ReasoningBank / ACE 在线学习** | ✅ | `StreamProducer` 按「预测→打分→learn」逐条执行，最后 `take_snapshot`，demo7 已验证；ACE 的增量 patch 用自定义 `operation` 加 `merge_changes` 实现 | ① T1 不支持流的中途恢复（L1000），长时间在线学习一旦崩溃就从头再来；② ReasoningBank 用自判成功，没有 reference。`StreamProducer(data, scorer)` 默认需要 scorer，需要说明「无参照打分器」也是合法的 scorer |
| **SEAL 类训练作业** | ✅（T1 本地，远端 T2） | 内层 SFT 是一条 produce，执行者是训练程序，输出把 model unit 换成新的 checkpoint `ArtifactRef`；外层「学编辑策略」是另一条 produce，`inputs` 引用内层各记录的下游分数 | 远端作业的幂等与对账属 T2（L1521）。T1 下训练作业只能是本地进程；对 SEAL 来说这是规模问题，不是契约缺口 |
| **evaluator 共同演化** | ❌ 缺一等身份 | 规格 L1464 说「被演化的 judge 是普通 unit；用于选 best 的选择协议固定」 | **引擎做测量时用的 evaluator 是 run 级的活对象（`optimize(evaluator=...)`），不是快照。** 「这次测量用的是 judge 快照 J7」只能通过 `describe()` 自报，或者由 `ScoreResult.observed` 间接出现在 manifest 里；记录上没有 `evaluator_snapshot_id`。这违反了 §0.6 自己的规则（「用 metadata 冒充引擎观测到的条件……都是分层违规」，L1482）。同时，RQGM 那种按 epoch 换 utility 的做法只能「每个 epoch 一个子 run」，但子 run 的 evaluator 由协调者代码构造，judge 版本同样不进记录。**建议：** measure 记录加 `evaluator_snapshot_id`（可空），允许 evaluator 由引擎从快照构造（`Evaluator.from_snapshot`）；manifest 的 observed 段自动收录它 |

**必要性：** 三种身份、produce、执行者与目标分立、预算预留、协调者规则、选择协议、manifest，这几项在 demo 中**每一项都有断言依赖**，删掉任何一项都会有对应的 D 项失败，所以都是必要的。冗余的部分见 §3。

---

## 2. 发现表（按严重度）

### 阻塞（T0 冻结前必须改，否则两份实现的持久化格式不兼容）

| # | 位置 | 问题 |
|---|---|---|
| B1 | L745「float 用最短往返表示」 | 规范化规则没有冻结以下几点：`1` 与 `1.0` 是否相同、`-0.0`、大整数、非 ASCII 字符是否转义、NaN。Python 的 `json.dumps` 与 JS 的 `JSON.stringify` 在这些点上给出的字节不同，`snapshot_id` 就会不同（摩擦 F1）。**应直接引用 RFC 8785（JCS）** |
| B2 | L1252–1261 `measurement_key` | 规格写「取 evaluator 与 scorer 的身份（observed + declared）」，但 observed 段里有「实际样本清单摘要与条数」和「值→次数」（L1244–1246），同时又说 seed 与清单「只进 manifest」。究竟 observed 里哪些字段进 key，没有逐项列出。条件值进 key 时，次数要不要算进去也没写（如果算，同一个 judge 测 8 条与测 10 条就会落入不同组）。key 是持久化字段，两个实现必然产出不同的 key（F14） |
| B3 | L589–590 与 L636 | pending 记录要求「执行者均已解析」，但自修改时执行者 = apply 之后的目标，而 apply 在派发时才做，所以 `executor_snapshot_id` 在 pending 阶段必然为空。「空」到底表示「目标自身」还是「未解析」，靠约定区分（F4）。应加一个显式字段 `executor: "target" \| <snapshot_id>` |
| B4 | L1395 summary 允许列表 | 列表里没有 `record_id`、`kind`、`snapshot_id`、`outputs`、`eval_spec`。照字面实现，summary 角色下的 `observe` 无法知道是哪条记录、产出了什么，produce 型记录（例如在 train 角色上的 `StreamProducer`）完全没法用（F12）。应把「身份字段永远可见」写进表里 |
| B5 | L1206–1207 嵌套预算 | 「子 run 的 Budget 是父 run 预留出来的额度」与「同一个成本事件沿作用域链到达每一层 Budget」放在一起，没有说明父级的花费与子预留是什么关系：子 run 花的钱在父级是**冲抵**预留，还是在预留之外**再计**一次？两种实现会让父 run 的准入判断相差一倍（F9）。demo 采用冲抵：父级可用额 = 上限 − 已花 − Σ max(0, 子预留 − 子已花) |
| B6 | L1266–1268 绑定条件「eval_spec 等于该角色的基准 spec」 | 「等于」的含义没有定义：spec 有多个可空字段，seed 还会被派生并写回（L316），`example_ids` 的顺序也可能不同。锚点一旦绑定就不会再改（L1272）。两个实现对「等于」的理解不同，绑定到的锚点就会不同。应改为「规范化 JSON 字节相等，`None` 与缺省视为同一」 |

### 应改

| # | 位置 | 问题 |
|---|---|---|
| S1 | §5 L1458 | Promptbreeder 一行与「改进器必须被实际调用、不能只写 metadata」矛盾（见 §1） |
| S2 | §2 RunContext L191 与 L279 | `spawn` 挂在 **run 作用域**上（`ctx.run.runs`），但调用资格（必须是协调者记录）和幂等键（协调者的 `record_id` + 序号）都是**记录作用域**的。实现时只能用 contextvar 隐式找出「当前是哪条记录在调用」（F7），这正是 §6 禁止的隐式传参。应改为 `TrialContext.runs`，或者给 `spawn` 显式传入 `ctx` |
| S3 | L1196 CostBudget「预留用请求的估计值」 | `Proposal` 和 `PendingSummary` 都没有估计值字段（F8），这一条无从落地。应给 `Proposal` 加 `estimate: dict | None` |
| S4 | §3 调度 L1345 Window「有空位就再 propose」 | 没有说明 propose 的触发点。我第一版实现是「先等一条完成再 propose」，结果 D6 断言失败，窗口永远填不满两条在途（F6）。应写明：「每次提交或交付之后，只要窗口内的在途数加排队数小于 k，就立即再调用 propose」 |
| S5 | §2 评估侧 / 比较侧 | 「各次打分的 `ScoreResult.observed` 汇总」与「采样总体身份由 DataEvaluator 计算」（L1245、L1255）都需要从 evaluator 传给引擎，但 `EvalResult` 上没有承载它们的字段（F13）。应加 `EvalResult.observed: dict` |
| S6 | §2 生产侧 L990、D1 | 「对 G2 请求 `capture="snapshot"` 被拒绝」是在提交时抛 `ProposalError`，还是派发后记 `failed(stage=produce)`？没有写（F5）。我选了后者，因为只有 apply 之后才能确定执行者是否等于目标 |
| S7 | L644 failure_class | `BudgetRefused`、`NotSupported`、`NestingRefused` 这几种框架拒绝属于哪一类，没有写（F10）。它们直接决定是否计账 |
| S8 | §3 嵌套 | 子 run 是否与父 run 共享快照存储没有写（F16）。子 run 需要读到父 run 的改进器快照，而 `state.json` 是按 run 存的。demo 让整棵 run 树共用一个内容寻址的存储 |
| S9 | L1035–1042 / L1057 | 选择协议始终没有绑定时（所有测量都失败），`optimize()` 的返回值没有定义（F17） |
| S10 | evaluator 共同演化 | 见 §1：缺 `evaluator_snapshot_id` |

### 建议

- `HistoryView.records` 增加 `source_snapshot_id=` 过滤（HGM、GEA 用得上）。
- `HiddenEvidence` 应明确规定**不得**继承 `AttributeError`，否则 `getattr(v, "items", None)` 会把「隐藏」静默变成「缺省」（F11）。
- 子 `run_id` 的生成规则需要写明，`record_id` 才能在整棵 run 树内唯一（F15）。demo 用的是 `<父 run>.<trial>.<序号>`。
- 一条 measure 记录的全部观测都失败时，记录状态是 `ok`（且 errors==total）还是 `failed`？只能从 L1269 反推是 `ok`，建议在 kind×status 表里写明。

---

## 3. A2 过度设计：可以删除或推迟到 T2 的内容

| 项 | 位置 | 理由 |
|---|---|---|
| `TrialRecord.execution_ref` 副本 | L579 | 权威信息在 `execution.json`，恢复也只读那份（L803–804）。多存一份副本只会增加「哪份是对的」的问题。可以删掉，或降为非持久的诊断字段 |
| `replay_projection` | L1049 | 默认值就是原样返回；「返回 None 表示不可重放」可以用一个类属性 `replayable=False` 表达。属于 T2 |
| 可见性的 `none` 档 | L1396 | RSI 场景用到的是 full 与 summary；`none` 需要 Objective、finalize 与 sinks 另开一条消费路径。可推迟 |
| `HistoryView.export(dir)` | L264, L1430 | 规格自己承认目录布局「T1 实现时定，不冻结」，那么它就不该出现在 T0 的协议表里 |
| `Window(max_queued)` | L1345 | 没有任何 D 项依赖它 |
| `StreamProducer` 作为 T1 标准实现 | L991 | 它只是 `CallProducer` 加循环。demo 里只用了 25 行。可以放进 contrib |
| `AnytimeBudget` 进 T1 | L1198 | 只要 `ProgressNote` 契约冻结，具体预算类型可以后补 |
| STAGES 里的 `persist` / `shutdown` | L624 | 这两类失败没有对应的计账语义，也没有 D 项用到 |
| §0.5 与 §0.6「表格回答所有争论」式的论证段落 | 全文 | 不属于契约，但让规格变长了大约 30%。建议把论证移到附录 |

**结论：** T0 应只保留三种身份、状态机、记录字段、预算预留与 `event_id` 去重、协调者规则、manifest 与 key 的算法、可见性的 full/summary 两档。其余内容可以全部降到 T1 或 T2。

---

## 4. A4 心智负担

- **§1 最短路径确实一个字都没变。** demo1 的 `Script(...).optimize(evaluator, objective, eval_spec=..., max_trials=...)` 只用默认值（Barrier、select_on=val）就跑通了，独立评估 `evaluator.evaluate(best, spec)` 也只需一行。
- **但有三处行为差异会让普通用户意外：**
  1. baseline 全部失败时不会有 best，直到出现一条合格记录。`optimize` 可能返回空值（S9）。
  2. 用户只是想顺手多监控一个 `specs={"test": ...}` 角色，它默认是 summary 可见性。算法在 observe 里读 items 会直接抛 `HiddenEvidence`，报错信息需要非常友好。
  3. 换 judge 后分数「消失」：记录不 eligible，只留下一个 `protocol_drift` 事件。默认 Tracker 必须把它显示出来，否则用户会以为是 bug。
- **对算法作者的负担明显变重了。** Proposal 有 8 个字段，还要理解 kind/role/visibility/protocol，外加 Producer 与协调者。规格 L1535 说「三行文档的代价」，这是低估了。建议提供一个 `RSIOptimizer` 基类，把「测 → 产 → 测产出」的常见循环（demo 里的 `ImproveLoop` 只有约 25 行）作为现成实现提供。

---

## 5. B 部分：demo 验证了什么

| demo | 覆盖 | 关键断言（全部 PASS） |
|---|---|---|
| `demo1_basics.py` | 基础闭环与三种身份 | 规范化 JSON 的 sha256 满足同内容同 id；读取返回独立对象；`$ref` 保留键强校验；加载时重算 digest；baseline 全部失败时不绑定协议、由下一条合格记录绑定；train 与 val 绑同一 spec 时，train 记录的 key 相同但 role 不同，高分也不抢 best；best 只在协议内产生；返回 dict 的 `FunctionEvaluator` baseline 能绑定；独立评估不需要 ctx |
| `demo2_d1_improver.py` | **D1 + 元优化** | G1 自修改产出 G2（新内容 id，`producers_of` 可查）；后续请求固定为执行者 G2、目标 A；**Backend 的 launch 日志证明实际启动的是 G2**；G2 返回的 `seen_target` 是 A，代码是 G2 的，两者没有混用；对 G2→A 请求 `capture=snapshot` 被拒绝；外层用协调者 `FunctionEvaluator` 在固定的内层预算（5 trials / 1 usd）下比较 G1 与 G2，**外层记录只有 `improvement_utility`（0.75 对 1.0），内层记录只有 `acc`**，评估用独立的 test split；内层记录的 `parent_scope` 指回外层记录 |
| `demo3_d3_nested.py` | **D3** | 三层嵌套，外层 `Window(1)`，全局 `Limiter(trial=1,item=1)`，10 秒内完成，池峰值不超过 1；同一 event_id 重复回报 48 次，7 个 run 的 ledger 与 Budget 都等于各自子树的唯一成本之和；`spawn` 超额时**立即** `BudgetRefused`（< 50ms），记录失败、run 继续；在钩子里或非协调者记录里 `spawn` 时抛 `NestingRefused` |
| `demo4_d6_admission.py` | **D6** | Window(2, cancel)、剩 2 次额度：第二条在途执行不被取消，第三条被拒准入但不触发停止，耗尽后以 `cancelled(stop)` 交付；Barrier 一批 10 条、额度 3：3 条 ok、7 条 cancelled，整组交付一次；apply 必败的一批只计 3 次，未派发的 2 条没有做 apply |
| `demo5_d5_recovery.py` | **D5** | 子进程在 4 个点 `os._exit`（running 已落盘 launch 前；两条乱序完成后 observe 前；observe 中；策略状态更新后 checkpoint 前）。resume 之后：launch 日志里每条记录最多出现一次；消费序列等于交付日志，且没有重复；重放旧的 event_id 不计费；launch 前崩溃的记录变为 `cancelled(lost)` 且不会重跑。启动握手：无登记判为 dead，转为 lost；有登记且进程存活时 terminate，转为 lost，孤儿进程被回收；登记文件损坏判为 **unknown**，保留预留、不交付 |
| `demo6_d4_protocol.py` | **D4** | 子集测量的 key 与全量不同，不抢 best、也不算漂移；reference 改动后 key 随之改变并发 `protocol_drift`；第 27 次打分起 judge 悄悄换成宽松版，manifest 记下 `judge-v2: 8`，发 `protocol_drift`，宽松 judge 打出的 1.0 不替换旧 best；漂移记录的 ProgressNote 是 neutral |
| `demo7_stream.py` | 可选：在线学习 | `StreamProducer` 产出学会全部算子的新快照；实际消费顺序写进 stats；执行者 ≠ 目标时被拒绝；train 角色为 summary 时，observe 与 `HistoryView` 都拿不到 items 和 feedback，只能看到 metrics |

**demo 过程中暴露的实现陷阱（规格应当提前写明）：**

- Window 的 propose 时机（S4）：第一版实现导致 D6 失败。
- D5 的「两条乱序完成后、observe 前」只有在 observe 本身较慢（异步 LLM 调用）、或者引擎合并同一时刻的完成事件时才能复现。规格应把「交付在什么时刻发生」写成可测试的语义。

**未覆盖：** D2；D3 的子树清理式恢复；真正的 SubprocessBackend（stdio JSON-RPC、父死信号、加载候选前的握手确认，只验证了登记文件语义与进程组终止）；report.jsonl 与 `keep_items`；AnytimeBudget；CancelToken。

### run_all.py 输出（汇总部分；完整逐条输出见本地运行 `python3 rsi_demo/run_all.py`）

```
汇总
  PASS  demo1_basics.py            PASS (16/16)    0.14s
  PASS  demo2_d1_improver.py       PASS (14/14)    0.14s
  PASS  demo3_d3_nested.py         PASS (8/8)      0.12s
  PASS  demo4_d6_admission.py      PASS (8/8)      0.26s
  PASS  demo5_d5_recovery.py       PASS (8/8)      1.63s
  PASS  demo6_d4_protocol.py       PASS (8/8)      0.11s
  PASS  demo7_stream.py            PASS (6/6)      0.12s
总计：7/7 个 demo 通过
```

---

## 6. 规格摩擦清单

编号与 `kernel.py` 中的注释对应。

| # | 章节 | 遇到的问题 | 我的做法 | 建议怎么改规格 |
|---|---|---|---|---|
| F1 | §2 程序侧 L745 | 规范化规则不完整（int 与 float、unicode、NaN） | 用 Python `json.dumps(sort_keys, separators, ensure_ascii=False, allow_nan=False)` | 改为「规范化 = RFC 8785 JCS」 |
| F2 | §2 上下文侧 L232 | `$ref` 保留键的校验时机：快照、记录、manifest 是否都要校验 | 所有持久对象写入前都用同一个 `check_json` | 写明「所有持久 JSON 走同一个校验器」 |
| F3 | §2 结果对象 L634 | 全部观测失败时记录的状态 | 记为 `ok`，`errors==total` | 在 kind×status 表里补一行 |
| F4 | §2 结果对象 L589 / L636 | pending 时自修改的执行者无法解析 | `executor_snapshot_id=None` 表示「目标自身」，apply 后写入 | 加字段 `executor: "target" \| id` |
| F5 | §2 生产侧 L990 | `capture` 不合法时在哪一层拒绝 | 派发后记 `failed(stage=produce, NotSupported)` | 写明「执行者是否等于目标要到 apply 后才能确定，因此在派发时拒绝」 |
| F6 | §3 调度 L1345 | Window 的 propose 触发点 | 每次提交后立即再判断是否还有空位 | 见 S4 |
| F7 | §2 L191/L279 | `spawn` 的调用资格是记录级的，入口却挂在 run 级 | 用 contextvar 记住当前协调者记录，item 执行时清空 | 移到 `TrialContext.runs` |
| F8 | §2 Budget L1196 | 没有字段承载预留估计值 | 只按 trials 数预留，usd 按「已花 + 子预留」软判 | 给 Proposal 加 `estimate` |
| F9 | §2 Budget L1206 | 子 run 的花费与父级子预留的关系 | 冲抵 | 写明冲抵公式 |
| F10 | §2 结果对象 L644 | 框架拒绝属于哪个 failure_class | `NotSupported` 记 candidate，`BudgetRefused` 记 infra | 列一张异常到 failure_class 的表 |
| F11 | §3 证据视图 L1400 | `HiddenEvidence` 的异常基类 | 继承 `Exception` | 明确规定不得继承 `AttributeError` |
| F12 | §3 证据视图 L1395 | summary 允许列表缺身份字段 | 加入身份字段 | 见 B4 |
| F13 | §2 比较侧 L1245 / L1255 | evaluator 到引擎之间没有字段承载 observed | `EvalResult.observed` | 加字段 |
| F14 | §2 比较侧 L1253 | observed 里哪些字段进 key | 取 evaluator/scorer 类名、declared、split、repeat、backend、总体身份、sample_k、条件值**集合**（不含次数）；不取 seed、清单、条数、role | 逐项列出 key 的输入，并给出一个参考实现的 hash 向量作为测试用例 |
| F15 | §3 嵌套 | 子 run_id 的生成规则 | `<父>.<trial>.<序号>` | 写进规格 |
| F16 | §3 嵌套 / §4 | 子 run 能否读到父 run 的快照 | 整棵 run 树共用一个快照存储 | 写明「快照存储按 run 树共享（内容寻址，没有冲突）」 |
| F17 | §2 算法侧 L1035 | 没有 best 时 `optimize` 返回什么 | 返回 `None` | 写明返回值，或抛 `NoEligibleResult` |

---

## 7. 建议的规格修改（逐句）

1. **L745**：「规范化规则（冻结）：键排序、无多余空白、float 用最短往返表示；不做任何结构改写。」改为「规范化规则（冻结）：RFC 8785（JCS）；数值一律按 IEEE-754 双精度解释，`1` 与 `1.0` 同字节；NaN/Inf 拒绝；不做任何结构改写。」
2. **L1253**：「默认策略 "default/1" 取 evaluator 与 scorer 的身份（observed + declared）……」改为「默认策略 "default/1" 的输入恰好是以下字段的规范化 JSON：`evaluator.class`、`scorer.class`、`declared`、`eval_spec.split`、`eval_spec.repeat`、`backend.class`、`population_digest`、`eval_spec.sample_k`、`{cond_key: sorted(set(values))}`。其余 observed 字段（样本清单、条数、seed、次数）不进 key。附录给出 3 组测试向量。」
3. **L589–590**：把「`executor_snapshot_id: str | None`」改为两个字段：`executor_ref: Literal["target"] | str`（提交时固定）与 `executor_snapshot_id: str | None`（apply 后写入）。
4. **L1395 summary 行**：句首加上「始终可见的身份字段：`record_id`、`trial_id`、`kind`、`status`、`source_snapshot_id`、`snapshot_id`、`executor_snapshot_id`、`outputs`、`eval_spec`、`producer`；」
5. **L1206**：后面加一句：「父 Budget 对子预留的占用 = max(0, 预留额 − 子树已到达本层的成本)；子树成本到达父层时计入 `spent`，同时冲抵对应的子预留，不重复占额。」
6. **L1268**：「eval_spec 等于该角色的基准 spec」改为「`canonical_json(eval_spec)` 与 `canonical_json(基准 spec)` 字节相等（基准 spec 在 run 开始时已写回派生的 seed，缺省字段按默认值补齐）」。
7. **L1458（§5 Promptbreeder 行）**：判定从「✅ 纯扩展（v4.7 已可）」改为「✅ 纯扩展：需要『改进器被实际调用』这一事实时，mutation 走 produce（`executor_snapshot_id` = mutation prompt 所在快照）；宿主侧 propose 直接读取 unit 只构成声明」。
8. **L1464（§5 评估器共演化行）**：加一句：「measure 记录可带 `evaluator_snapshot_id`，由引擎经 `Evaluator.from_snapshot` 构造并写进 manifest observed 段；未带时 evaluator 视为 run 级固定组件。」并在 §0.6 表格的「测量」行加上这一载体。
9. **L191 / L279**：把 `runs: RunSpawner` 从 `RunContext` 移到 `TrialContext`，并注明「只在协调者记录的 TrialContext 上是可用实现，其余位置是拒绝型 spawner」。
10. **L1345 Window 行**：加一句：「每次提交或交付后，只要在途数加排队数小于 k，引擎就立即再调用一次 propose（仍满足单写者约束）。」
11. **L1060 Proposal**：加字段 `estimate: dict[str, float] | None = None  # CostBudget 等按它预留`。
12. **L1380 后**：加一条 T2 条目「执行中请求测量（STOP 原型）：不可信执行者经句柄提交子 measure，由引擎派发并按子树预留计账」。L1521 的 T2 列表同步更新。
13. **L1521 T2 列表**：补入 `replay_projection`、`HistoryView.export`、`Window.max_queued`、可见性 `none` 档、`execution_ref` 副本。
