# 优化引擎新架构

> 目标架构规格 · v4.8 · 基线 `refactor_optimizers` @ cb3a335（含工作区第 4 步）

拆包后独立项目的目标形态：面向 agent 与 agentic workflow 优化的「PyTorch + wandb」。本文是自洽的架构规格——五个用户概念、三种身份（版本 / 执行 / 测量）、完整对象契约、单一运行时、三通道记录，以及对五项工程目标的达成度评估。本文只描述最终方案。

## 版本沿革

| 日期 | 版本 | 内容 |
|---|---|---|
| 2026-08-20 | v1 | 初版 |
| 2026-08-21 | v2 | 动态数据源 / rubric / 文本反馈 / 变更正则化 |
| 2026-08-21 | v3 | 三层上下文冻结 / fidelity 多方合成 / 重复观测 / 策略去不变量化 |
| 2026-08-21 | v3.1 | Limiter 分池 + 池间偏序（修一处死锁） |
| 2026-08-21 | v3.2 | 删除 Fidelity —— 可比性归 Objective 与算法作者 |
| 2026-08-21 | v3.3 | ProgramHandle 存根化 —— 与 adapter 逐字同签名 |
| 2026-08-26 | v3.4 | 上下文永不为 None —— 独立评估合成 ephemeral 上下文 · 评估层三档分工 |
| 2026-08-26 | v3.5 | Objective 收 TrialRecord |
| 2026-08-26 | v3.6 | 子集一致性不再是框架承诺 —— DataSource.revision 删除 |
| 2026-08-26 | v3.7 | 边界内侧的 ctx 是真 ItemContext，设施由 Backend 代理回父进程 |
| 2026-08-26 | v3.8 | validate_trial 镜像到 ProgramHandle —— 冒烟测试进隔离边界 |
| 2026-08-27 | v4.0 | propose 是状态变更的唯一生产者，evaluate 只测量 |
| 2026-08-27 | v4.1 | metrics 键集基数与数据规模无关 —— 逐 case 向量归可追溯信号 |
| 2026-08-27 | v4.2 | 失败即测量结果（引擎不重试，attempt 删除）；unit 类型开放 |
| 2026-08-27 | v4.3 | 原文随 `ItemResult` 走（每个用户函数两侧各留一份，落盘按 `keep_items` 剥离）；数据角色表 `specs` + `required_specs` 前置校验 |
| 2026-08-27 | v4.4 | `events.cost` 是唯一成本入口（`Budget.charge_cost` 转引擎面）；`budget_state` 进 `state.json` |
| 2026-08-27 | v4.5 | 产物只增不删——workspace 归引擎、沙箱归 Backend，`shutdown` 回收前先同步回 workspace |
| 2026-08-27 | v4.6 | 拆包切分线改为「协议随包走、实例留宿主」：`DataSource` / `Scorer` 协议进 `core/`，具体数据集与内建 scorer 留在 `benchmark/` |
| 2026-08-28 | v4.7 | 缺省表达为机制而非约定：无 `save_dir` 时 `trial_dir=None`（与内存 artifacts 同手法）；`AnytimeBudget` 的改进判定按可比测量分组；`ephemeral_trial_context` / `TrialOutcome` 落位修正 |
| 2026-10-14 | v4.8 | RSI 承载：版本 / 执行 / 测量三身份分立；`TrialRecord` 成为带 `kind` 判别的统一执行记录（measure / produce）；`Producer` 与输出接纳；内容寻址快照；submit/complete 调度（`Barrier` / `Window`）+ 有序交付日志 + 可重放输入；预算预留与成本事件去重；协调者嵌套；测量 manifest + 选择协议 + 进展通知；证据可见性；`Backend.launch` 收 adapter + snapshot 并在边界侧重建。2026-10-15 经六轮交叉评审（codex）定稿，评审补齐：数据角色随 `EvalSpec` 走、`Objective.eligible`、采样总体身份进分组 key、启动握手与清理式恢复、交付组与终态原子提交、准入与停止分开、`ArtifactRef.$ref` 即内容地址 |

**取代**：`evaluate_fn` 闭包 · Report/Result 双类 · 双份 sync/async 实现 · `Regularizer` · `ReferenceHandle` · `max_trials` · `Fidelity` · `evaluate(ctx=None)` · `is_better(metrics, metrics)` · `DataSource.revision` · `capture_after_eval` · `merge_captures` · `ItemContext.attempt` · 「凭 `example_id` 反查 `DataSource`」 · `optimize(eval_spec=)` 作为唯一数据入口 · 「propose 是状态变更的唯一生产者」 · 「一条 `TrialRecord` 只能是一次测量」 · 8 位随机 `snapshot_id` · `HistoryView.trial_of` · 「失败带 stage 即候选之过」的计账判据 · `AnytimeBudget` 自行比较分数 · 「新条件组按字典序优先」 · 批同步作为唯一主循环形状 · `Backend.launch(program)` 收已构造好的 adapter · 扁平覆盖的 `EventEmitter.bind` 作用域 · 存物理位置的 `ArtifactRef.$ref` · 够不到任何设施的 `Scorer.score(req)`

### v4.8 变更索引（评审用，定稿后可删）

v4.8 的出发点：把 RSI（递归自改进）一族方法当作设计压力测试。去掉方法名之后，学术与工业的自改进系统落在同一骨架上——**版本化制品 → 某个明确版本的提案者 → 隔离执行 → 独立评估门 → 证据库 → 选择 → 激活**，外面套一个固定的实验边界（预算、记录、权限、人定的方向）。改进器、评估器、课程、权重都可以是边界内的制品；递归成立于「新改进机制被后续调用实际使用」。v4.7 已经与这副骨架同构，但有几条不变量恰好挡住了它：状态只能由 propose 产生、一条记录只能是一次测量、主循环只有批同步一种形状、嵌套运行会撞上 Limiter 偏序、测量条件不入账。这些都落在序列化格式或已冻结的契约里，晚改即迁移，因此在第 5 步之前修订。

| 位置 | 变更 | 档位 |
|---|---|---|
| §0 / §0.6（新） | 可扩展判据纳入 RSI 通用形态；新增「三种身份」骨架规则 | T0 |
| §2 上下文侧 | `RunContext` 加 `parent` / `pending` / `runs`；`TrialContext` 加 `record_id` / `kind` / `executor`；`HistoryView` 以 `producers_of` / `measurements_of` / `records` 取代 `trial_of`，返回只读证据视图；事件作用域改为祖先链 + `event_id` 去重；`ArtifactRef.$ref` 改为内容地址、`$ref` 为保留键；`ArtifactStore.open` 校验 sha256 | T0 |
| §2 评估侧 | `Evaluator.describe()`；`FunctionEvaluator(coordinator=True)`；`EvalResult.spec` 可空（生产复用 `EvalResult`）；`EvalSpec.role`（数据角色随派生保留） | T0 |
| §2 打分侧 | `Scorer.score(req, ctx)`；`Scorer.describe()`；`ScoreResult.observed`（实际测量条件走返回值进 manifest） | T0 |
| §2 结果对象 | `TrialRecord` 统一执行记录字段表（一次执行只有 `record_id` 一个身份；请求部分是 `Proposal` 的已解析副本）与 kind/status 约束；apply 在派发时执行；`ItemResult.failure_class`；失败阶段扩充 | T0 |
| §2 程序侧 | `snapshot_id` 内容寻址与规范化规则（不做结构改写）；`execute` 只读契约限定到 measure；`validate_snapshot` / `learn` 两个可选钩子；`prepare_workspace(snapshot, ctx)` 不得 import 候选代码 | T0 |
| §2 执行侧 | `launch(adapter, snapshot, ctx)` 由 Backend 重建程序；`ProgramHandle` 镜像 `take_snapshot` / `learn`；启动握手（running 先于 launch 落盘、加载候选前登记所有权）与 `probe` / `terminate` 恢复边界 | T0 定语义与签名，T1 实现本地 |
| §2 生产侧（新） | `Producer` / `ProduceResult`（`EvalResult` 子类）/ 两种输出 / 输出接纳 / `CallProducer` / `StreamProducer` | T0 定语义，T1 实现 |
| §2 算法侧 | `Proposal` 加 `kind` / `executor_snapshot_id` / `producer` / `inputs`；`required_producers`；`replay_projection`（返回同类型的裁剪 outcome，编码归引擎）；`optimize()` 加 `producers` / `schedule`（含停止语义）/ `select_on` / `visibility` | T0 |
| §2 算法侧 · Budget | 预留 / 结算 / 释放；准入（`reserve`）与永久耗尽（`exhausted`）分开；按 `failure_class` 计账；`AnytimeBudget` 收进展通知；组合预算原子预留 | T0 定契约，T1 实现 |
| §2 比较侧 | 选择协议 =（角色, `measurement_key`）+ 锚定记录；`Objective.eligible` 统一 best 与进展的资格；分组策略含子集协议（采样总体身份 + 抽样规则）；`progress`；删除字典序新组优先 | T0 |
| §3 | 统一执行记录；版本两条产生路径；submit/complete 调度（`Barrier` 默认 / `Window`）；交付组提交时登记、完成时与终态原子关闭；准入与停止分开；checkpoint 只写已提交策略状态；恢复语义（含协调者子树核对）；协调者记录嵌套；证据视图 | T0 定语义，T1 实现本地 |
| §4 | `state.json` 新增 pending 请求、交付日志、消费游标、选择协议；目录布局含所有权登记；事件通道明确为尽力交付、非权威 | T0 |
| §5 | 新增 RSI 一族的落位；边界规则改写 | — |
| §6 | 改写或限定 7 条规则，新增 4 条 | — |
| §7 / §9 | 契约三档；插入第 4.5 步（v4.8 契约修订）；第 5–11 步按新契约调整，第 11 步加 D1–D6 验收；两个现存缺陷 | — |

档位：**T0** = 现在冻结（序列化字段、状态枚举、身份规则——晚改要迁移已存 run 或丢失不可补回的事实）；**T1** = 第 5–11 步只实现本地语义（`LocalBackend` / `SubprocessBackend`）；**T2** = 预留、按需实现（同 run 内切换选择协议、策略钩子直接嵌套、远端作业对账、带沙箱的协调者、item 隔离下的生产合并、模型网关、CLI/MCP 外部驱动、人工审批）。T1 遇到 T2 能力时一律**明确拒绝**，不静默降级。

---

## §0 设计判据

1. **易维护**：核心循环单份实现（async-first），每类职责一个归属，重复路径为零。
2. **可扩展**：新算法 = 继承 + 覆写少数钩子，不改框架；九类算法谱系（BO / MIPRO / memory / skills / harness / workflow / 正则化 / rubric judge / 程序形态演化）全部纯扩展可实现，且扩展所需的信息一律能从三层上下文里拿到。**RSI 的通用形态同样是纯扩展**：改进器本身是快照里的程序、一次运行产出新版本、嵌套的元优化、评估条件随时间演化、异步稳态搜索——新方法只新增表示（unit / codec）、`Producer`、策略或 `Backend`，引擎不认识任何论文名。
3. **易用、心智负担小**：用户面只有五个概念，最短路径约 15 行；每个概念都能映射到研究者已有的 PyTorch 心智模型。
4. **可观测**：跑完一轮能看清每个 candidate 好在哪、每条样本发生了什么、花了多少钱（*包括提案环节自身的开销*），每个版本由谁、基于什么产生，且可从任意已提交的检查点续跑（续跑承诺的精确范围见 §3「恢复」）。
5. **平台化余地**：观测、执行后端、产物存储各有预留接口，未来长成统一训练 + 观测平台不需要二次伤筋动骨。

---

## §0.5 三信号分流

骨架规则，先于所有对象契约声明。**评估同时产出三类性质不同的信号，每类只有一个去向。**

| 信号 | 载体 | 唯一去向 | 铁律 |
|---|---|---|---|
| 可比较的 | `metrics: dict[str, float]`（含 `reg/*`） | `Objective` 比较 · best 指针 · `state.json` | 只有它参与比较；键集基数与数据规模无关 |
| 可解读的 | `feedback`（trial 级 + per-item） | `Optimizer.observe` → 下一轮 `propose` | 永不参与比较 |
| 可追溯的 | `items`（含原文四件套）/ `trace` / `report.jsonl` | 复盘 · 正则化装饰器 · demo 筛选 · 反思型 `propose` | 不进 `state.json` |

这条规则一次性回答了四个否则会反复出现的争论：

- **LLM judge 的文字要不要当 best 指针**——不要，要可比较就产出 metrics。
- **轨迹要不要进检查点**——不进，它在 report.jsonl 里，正则装饰器按需读。
- **反馈越丰富是不是 checkpoint 越贵**——不是，只有 trial 级摘要进检查点，且带硬上限。
- **逐 case 指标要不要进 metrics**——不要，键集随子集变化，既让 `state.json` 随数据规模膨胀，也让两个 trial 的 metrics 形状不同而失去可比性。

§2 之后**测量结果上**的每一处新增字段都能在这张表里找到唯一归属，找不到就说明设计错了。这张表管的是测量产出的信号；版本从哪来、一次执行处于什么状态、哪个版本被后续调用使用——这些不是评价信号，不往这三类里塞，它们归 §0.6 的三种身份。

这张表回答的是「*评估产出什么*」。与它正交的另一问是「*每一层能拿到什么*」——前者决定信息往哪去，后者决定信息能不能到得了；两条规则合起来才构成完整的骨架，见 §2 上下文侧。

---

## §0.6 三种身份

第二条骨架规则，与 §0.5 正交。**一次优化里发生的每件事，都要能回答三个问题：这是什么版本、哪次执行产生或使用了它、这个分数在什么条件下取得。三者各有一个载体，不互相代替。**

| 身份 | 载体 | 回答的问题 | 铁律 |
|---|---|---|---|
| 版本 | `Snapshot`（内容寻址） | 这是什么 | 已提交内容不可变；同内容同 id；快照不带父指针——谱系由执行记录给出 |
| 执行 | `TrialRecord`（`kind` = measure / produce） | 谁、基于什么、跑了什么、产出了什么 | 一个请求一条记录；目标、执行者、输出、声明参考四种关系分开记 |
| 测量 | measurement manifest + `measurement_key` | 这个分数在什么条件下取得 | 只记录、不裁决；分组由版本化的分组策略生成 |

**为什么需要它。** v4.7 默认「状态只由 propose 产生、一条记录就是一次测量」，于是三种身份被一条 `TrialRecord` 隐式合并了：`snapshot_id` 既是被测对象也是「新版本」，`source_snapshot_id` 既是父版本也被当成生成者。只要被优化的是静态配置，这样合并没有损失；一旦改进机制本身进入快照，合并就开始丢事实：

- 改进器 G2 去修改目标 A7，产出 B8——**目标基底（A7）、实际执行者（G2）、输出（B8）、算法声明参考过的历史候选**是四种关系，压不进一个父指针；
- 一次运行可以产出零个、一个或多个版本，也可以只测量、不产出；
- 同一份内容可以被两次独立执行各自产出，同一个版本可以被测量多次。

**RSI 的递归在本框架里有确切的落点**：改进器是快照里的程序；它的新版本以 `executor_snapshot_id` 的身份出现在后续请求里，并被执行记录固定下来——「新改进机制被后续调用实际使用」因此是一条可查询的事实，而不是 metadata 里的一句声明。引擎固定的是身份、执行、资源与记录这些契约；archive、父代选择、预算分配、接受规则都是策略，策略本身也可以进快照、被演化。

---

## §1 心智模型：五个概念

用户从零到跑起来只需要认识五个东西。每个概念对应 PyTorch / wandb 世界里一个已有的心智锚点——这不是宣传口径，是 API 设计的约束：任何新增公开概念必须能在这张表里找到位置，找不到就说明设计错了。

| 概念 | 回答的问题 | PyTorch / wandb 锚点 | 用户要写的 |
|---|---|---|---|
| `ProgramAdapter` | 优化什么程序？哪些部分可变？ | `nn.Module` + `nn.Parameter` | 声明 units + 实现 `execute`（常见形态由 `DictProgramAdapter` 代劳，只剩 `execute`） |
| `Evaluator` | 怎么评？ | `DataLoader` + loss/metric | 装配 `DataEvaluator`（数据源 + scorer + I/O 两钩子）；只改管线某一块则覆写它的具名钩子；整条 harness 自己写走 `FunctionEvaluator`；正则等 trial 级度量包一层装饰器。**继承 `Evaluator` ABC 不是用户动作** |
| `EvalSpec` | 评什么数据、多大预算？ | batch / 验证集选择 | 一行声明（纯数据）；算法要 train + val 就写成 `specs={"train": …, "val": …}` |
| `Objective` | 什么算更好？ | 比较准则 / 早停 | 一行（`ScalarObjective` / `ParetoObjective` / `PenalizedObjective`） |
| `Optimizer` | 怎么搜？ | `torch.optim` | 选现成的；算法参数一律走 `__init__`；写新算法才需要覆写钩子 |

训练循环本身（`TrialExecutor`）、执行后端（`Backend` ≈ `device`，默认 `LocalBackend`）、预算与终止（`Budget`，`max_trials=N` 是它的糖）、上下文（`RunContext / TrialContext / ItemContext`）、检查点（`Snapshot` ≈ `state_dict`）、实验记录（events ≈ `wandb.log`）都是框架代管的，用户不接触；只有需要副作用隔离或非默认预算时，才像选 device 一样显式换一个实现。调度形状（`Barrier` / `Window`）同理，默认 `Barrier` 就是 v4.7 的批同步行为。

**`Producer` 也不是第六个用户概念。** 它是「一次运行产出新版本」的驱动者（§2 生产侧），只在写改进器自修改、在线学习、训练类算法时出现：有标准实现，不进最短路径——与 `Backend` / `Budget` 同一论证放行。普通的 prompt / 超参优化用户从头到尾见不到它。

### 最短路径（完整可跑的骨架）

```python
# 1) 程序：哪些部分可优化，程序怎么跑
class CodingProgram(DictProgramAdapter):
    units = {"instruction": Unit.prompt("Write code for the problem.")}
    async def execute(self, inputs, ctx): ...     # 用 self.values["instruction"] 跑程序；
                                                 # ctx 给出这是哪条观测、往哪写，用不到就忽略

# 2) 评估器：数据 + 打分 + I/O 适配（一次装配，处处复用）
evaluator = DataEvaluator(
    data=benchmark, scorer=benchmark.scorer,
    input_fn=lambda ex: {"question": ex["prompt"]},
    output_fn=extract_code, concurrency=10)

# 3) 优化：评什么、什么算好、搜多少次
best = await PromptVariantOptimizer(adapter=CodingProgram()).optimize(
    evaluator=evaluator,
    eval_spec=EvalSpec(split="dev", sample_k=30, seed=42),   # ≡ specs={"val": ...}
    objective=ScalarObjective("pass@1"),
    max_trials=10, save_dir="runs/demo")
#   要 train 之类的第二份数据时改写成 specs={"train": ..., "val": ...}；
#   算法用 required_specs 声明它要哪几个角色，引擎在第一个 trial 前就校验

# 优化前后对照与独立评估复用同一个 evaluator——「评估」只有一种做法
report = await evaluator.evaluate(best, EvalSpec(split="test", seed=42))
#   不给 ctx 时框架合成一个 ephemeral TrialContext，
#   Evaluator 内部与所有装饰器永远拿到完整上下文，签名与优化内完全一致
```

---

## §2 对象模型与契约

### 上下文侧

放在所有其它契约之前，因为它决定其它契约的上限。§1 的约束是「不加第六个用户概念」，§6 的约束是「零 kwargs 扇出、零签名嗅探」——两条合起来意味着：**未来所有扩展点能做什么，完全取决于框架递给它的上下文对象里有什么。**

上下文冻结成**三层嵌套作用域**，每层回答同样四个问题：我是谁、我能往哪写、我在什么条件下运行、我能调用什么。

```python
@dataclass
class RunContext:                              # run 作用域：整轮共享
    run_id: str; save_dir: Path
    artifacts: ArtifactStore                   # 大值出入口，ArtifactRef 的实现侧
    events: EventEmitter                       # 发事件 + 上报 cost —— 任何持有者都可用
    budget: BudgetView                         # 已耗 / 余额，只读
    history: HistoryView                       # 按 trial_id / snapshot_id 懒读历史
                                               #   record / result / snapshot
    limiter: Limiter                           # 按资源分池的并发配额，池间有偏序
    cancel: CancelToken
    specs: dict[str, EvalSpec]                 # 角色 → 基准 spec（"train" / "val" / …）。引擎逐角色复制后填好 role
                                               #   （两个角色绑同一个对象也互不覆盖）。
                                               #   propose 由此派生 Proposal.eval_spec（role 随 model_copy 保留）；
                                               #   引擎在 trial 0 前校验 required_specs ⊆ specs.keys()
    mode: Literal["optimize", "standalone"]    # 本 run 是优化还是一次独立评估。
                                               #   消费者 = sink 标注来源 / resume 拒绝续跑 standalone
    parent: ScopeRef | None                    # v4.8 · 嵌套 run 的父作用域（可序列化引用，不是活对象）；
                                               #   顶层 run 为 None。子 run 的目录、事件祖先链、预算都从它派生
    pending: PendingView                       # v4.8 · 本 run 在途请求的只读摘要（异步 BO、去重提案、
                                               #   资源分配要看得见「已经派出去但还没回来」的东西）
    runs: RunSpawner                           # v4.8 · 发起子 run 的唯一入口（§3「嵌套」）

@dataclass
class TrialContext:                            # trial 作用域：一次执行的一生（measure 或 produce）
    run: RunContext
    trial_id: int; record_id: str              # v4.8 · record_id = f"{run_id}/{trial_id}"：提交时分配、跨 run 唯一，
                                               #   关联、预留、恢复都以它为锚点——一次执行只有这一个身份
    kind: str                                  # v4.8 · "measure" | "produce"
    snapshot: Snapshot                         # 目标输入版本：measure 时就是被测版本；
                                               #   produce 时执行者经 ctx.trial.snapshot 读到它要改的目标
    executor: Snapshot                         # v4.8 · 实际启动的程序版本：measure 时与 snapshot 是同一个；
                                               #   produce 时是改进器 / 训练器 / 学习者本身
    source_snapshot_id: str; changes: list[UnitChange]
    trial_dir: Path | None; workspace: Path | None
    events: EventEmitter                       # 绑好本层作用域 id 的代理，转发 run.events。
                                               #   调用方永远写 ctx.events.cost(...)，归属不靠参数传

ScopeRef = {"run_id": str, "record_id": str}
                                               # v4.8 · 纯 JSON，可落盘、可跨进程；活的上下文对象不进持久层。
                                               #   子 run 只能由协调者**记录**发起（§3「嵌套」），所以 record_id 必有；
                                               #   不含观测主键：协调者不在任何一条观测里

@dataclass
class ItemContext:                             # item 作用域：一次观测
    trial: TrialContext
    key: ObservationKey                        # (example_id, repeat)
    item_dir: Path | None
    events: EventEmitter                       # 同上，绑 (trial_id, key)

ObservationKey = tuple[str, int]               # (example_id, repeat) —— 全库统一的观测主键，
                                               #   ItemContext / ItemResult / ScoreRequest 共用
```

`RunContext` 里那些是**运行时设施**，不是数据。它们各自的最小契约随上下文一起冻结——否则「上下文即扩展面」这条规则就没有可检查的对象；实现全部是正交推进项。

```python
class ArtifactStore(Protocol):
    put(data | path, *, kind: str) -> ArtifactRef      # 内容寻址，格式已冻结
    open(ref: ArtifactRef, *, verify: bool = True) -> IO
    # v4.8 · ArtifactRef = {"$ref": "sha256:<hex>", "sha256": "<hex>", "kind": str}：$ref 就是内容地址、
    #   与位置无关，物理位置由各 store 按 hash 解析——制品搬目录、换机器，引用不变，快照 id 也就不变。
    #   "$ref" 是保留键：快照与记录里任何含 "$ref" 的对象都必须是合法 ArtifactRef（$ref 与 sha256 一致、
    #   恰好三键），否则强校验拒绝——普通 JSON 永远不会被误认成引用
    # v4.8 · 默认校验 sha256（大文件流式校验）。引用可以被搬家、被篡改，
    #   ArtifactRef 里的 sha256 只有被检查时才是身份，不被检查时只是注释

class EventEmitter(Protocol):
    emit(kind: str, payload: dict) -> None             # run/propose/trial/batch/checkpoint
    cost(kind: str, amount: float, *, unit: str, event_id: str | None = None) -> None
    # cost 是**全库唯一的成本入口**：propose / evaluator / 装饰器 / scorer / producer 都只认它。
    #   emitter 收到后扇出三处 —— sink · Budget.charge_cost · 作用域链上每个 ledger。
    #   组件够不到 Budget 本体（上下文里只有只读的 BudgetView），双写因此不可能。
    # v4.8 · 每个成本事件有 event_id（不给则当场生成）；ledger 与 Budget **都**按 event_id 去重，
    #   已处理的 id 随 budget_state 落盘——远端重复回报、恢复后重放都不会重复计账。
    #   「单入口」与「只计一次」是两回事：前者管路径，后者管身份。
    # v4.8 · 作用域是**祖先链**（run → record → item，嵌套时向上延伸到父 run），
    #   bind 追加一帧而不是覆盖字典——子 run 的 run_id 不会抹掉父作用域。
    #   父级可以**显示**包含子级的汇总，但汇总值永不作为新事件再上报

class BudgetView(Protocol):                            # 只读投影，写入只经 Budget 本体
    spent: dict[str, float]; remaining: dict[str, float]; exhausted: bool
    reserved: dict[str, float]                         # v4.8 · 已预留未结算

class HistoryView(Protocol):                           # 懒读，不常驻内存
    record(record_id) -> RecordView                    # 也接受 run 内的 trial_id
    records(*, kind=None, status=None, snapshot_id=None, executor=None) -> Iterator[RecordView]
    result(record_id) -> ResultView                    # 复用 EvalResult.load(report_path)；produce 记录同样适用
                                                       #   （ProduceResult 是 EvalResult 的子类）
    # v4.8 · 返回的都是经 §3「证据视图」构造的只读视图：字段名与 TrialRecord / EvalResult 相同，
    #   可见性策略之外的字段访问即抛 HiddenEvidence；full 时字段齐全
    snapshot(snapshot_id) -> Snapshot
    producers_of(snapshot_id) -> list[RecordView]      # v4.8 · 产出过这份内容的执行（可多条）
    measurements_of(snapshot_id) -> list[RecordView]   # v4.8 · 测过这份内容的执行（可多条）
    export(dir) -> Path                                # v4.8 · 把**策略可见的**那部分历史（源码、分数、轨迹）物化成
                                                       #   目录，给 coding agent 型提案者浏览；与上面几个方法用同一个
                                                       #   视图构造器（§4「目录布局」）
    # trial_of 退役：它一身三用（谁产生的 / 最近一次测量 / 所有测量），内容寻址之后这三个问题答案不同

class PendingView(Protocol):                           # v4.8 · 在途请求只读摘要
    requests() -> list[PendingSummary]                 # record_id / kind / source / executor / 状态 / 已预留额度
    in_flight: int; queued: int

class RunSpawner(Protocol):                            # v4.8 · 子 run 的唯一入口
    async spawn(optimizer, *, evaluator, objective, specs, budget, backend=None,
                producers=None, schedule=None, select_on=None, visibility=None) -> ChildRun
    # 参数是 optimize() 显式参数的同名子集（零 kwargs 扇出）：不含 eval_spec / max_trials 两个糖（子 run 直接
    #   给 specs / budget），也不含 save_dir / resume_from（由父作用域派生）；None = 沿用父 run 的设置。
    #   await ChildRun 得到子 run 的 optimize() 返回值，ChildRun.run_id 供记录引用。
    # 只允许从协调者**记录**调用（§3「嵌套」）：声明 coordinator=True 的 Evaluator / Producer。
    #   Optimizer 钩子与持有 item 令牌的 execute 里调用 → 立即抛 NestingRefused。
    # 子 run 的最小额度拿不到 → 立即抛 BudgetRefused，由策略重新规划；永不排队等祖先释放

class Limiter(Protocol):                               # 一个 kind 一个池，池间偏序
    async acquire(kind: str, n: int = 1)               # 异步上下文管理器
    # 不变量（违反即死锁）：持有 kind=P 的令牌时不得再申请 P；
    #   申请只能沿偏序由外向内：trial → item → llm。
    # 默认三池：trial = 同时托得住几个 workspace / 子进程（默认 4）；
    #   item = 全局唯一的观测并发上限（默认 8），两级相乘由它兜住；
    #   llm = 叶子池，程序与 propose 内部的模型调用自愿来抢（默认不限）。
    # debug 模式用 contextvar 记录当前持有集并断言偏序——这类死锁
    #   的现场是「卡住且无日志」，事后查成本远高于事前断言。
    # v4.8 · 一棵 run 树（父 run 与它发起的所有子 run）共用**同一个** Limiter 实例：
    #   这三个池计量的是真实资源，子 run 另起一套 Limiter 等于把全局上限悄悄翻倍。
    #   偏序因此要配一条嵌套规则才完整——等待子 run 的协调者不持有任何池令牌（§3「嵌套」），
    #   池令牌只由叶子执行持有，而叶子执行从不等待别的执行，所以等待图里没有环。

class CancelToken(Protocol):
    cancelled: bool; raise_if_cancelled() -> None
    # v4.8 · 子 run 的 token 派生自父 token：父取消向下传播，子取消不向上
```

三条推论，各自在下文落地：

- **`Optimizer` 的三个钩子收 `RunContext`**（算法侧）：否则一个「自己就是 agent」的优化器发不了事件、报不了自己的 token 开销、拿不到并发配额、接不到取消信号。
- **`execute` 收 `ItemContext`**（执行侧，adapter 与句柄两侧同签名）：否则程序不知道自己在跑哪一条观测、该往哪个目录写，而这正是「一次评估内可能有 N 个程序实例」这件事被框架承认的地方。
- **`HistoryView` 让专用的参照物组件不必存在**（评估侧）：「读某个历史 trial 的结果」比「上一版的懒句柄」更小也更通用，同时服务 archive 型算法、多参照对比与跨 trial 统计。它连同 `ctx.changes` 一起，使 trial 级度量降级为一层 Evaluator 装饰器。v4.8 起它同时是**谱系的查询口**：`producers_of` 回答「这个版本是谁、基于什么产生的」，`measurements_of` 回答「它在哪些条件下被测过」，coding agent 型提案者也只经它（及 §4 固定的目录布局）浏览历史。
- **`TrialContext` 同时持有目标与执行者**（生产侧）：measure 时二者是同一个快照，什么都不多；produce 时改进器 G2 修改目标 A7，`executor` 用来启动程序、物化 workspace，`snapshot` 用来解释输出、做合并与校验——一个字段装不下两个身份，硬塞会让「谁在跑」和「谁被改」在历史里永久混淆。

### 评估侧

```python
class EvalSpec(BaseModel):                     # 纯 JSON；run 级默认，proposal 可覆盖
    split: str = "dev"
    example_ids: list[str] | None = None       # 显式子集（失败样本重采样、bootstrap）
    sample_k: int | None = None                # 随机子集大小；None = 全量
    seed: int | None = None                    # 子集抽样的随机种子；设了 sample_k 而 seed 为 None 时，
                                               #   run 开始派生一次并写回 spec（随 TrialRecord 持久化）
    repeat: int = 1                            # 同一样本重复观测次数（程序是随机的）
    role: str | None = None                    # v4.8 · 数据角色：引擎构造 ctx.specs 时填好，model_copy 派生时随之保留。
                                               #   可见性与选择协议按它施加（§3）。派生只能改子集与重复次数
                                               #   （example_ids / sample_k / seed / repeat），不能改 split 与 role——
                                               #   提交时对照该角色的基准 spec 校验。不靠 split 名或 spec 值相等反推角色：
                                               #   两个角色可以绑同一份 spec，派生出的 spec 又不等于任何基准
    extras: dict = {}                          # 预留
    # spec 只声明「评什么」。这次测量实际在什么条件下做的，由引擎写进测量 manifest
    #   （§2 比较侧）；两个分数能不能放一起比，框架不裁决——归 Objective 与算法作者。


class DataSource(Protocol):                    # evoagentx 的 Benchmark 只是一个满足者
    get_split(...) / get_id(...)
    get_reference(id) -> Any                   # 答案 | rubric | 检查清单


class MutableDataSource(DataSource, Protocol): # contrib 层，可选
    append(examples)                           # append-only
    freeze() -> DataSource                     # 定格一个不可变视图供整轮 run 使用


class Evaluator(ABC):                          # 唯一评估契约（引擎面），显式签名，无任何嗅探
    # 用户面入口：基类实现，子类不覆写。
    async def evaluate(self, program, spec: EvalSpec,
                       ctx: TrialContext | None = None,
                       *, save_dir: Path | None = None) -> EvalResult:
        if ctx is None:                        # 全库唯一的归一化点
            async with ephemeral_trial_context(program, spec=spec, save_dir=save_dir) as ctx:
                return await self.run_eval(program, spec, ctx)
        return await self.run_eval(program, spec, ctx)

    # 实现面：唯一的抽象方法。ctx 永不为 None ——
    #   实现者、装饰器作者、FunctionEvaluator 的 fn 都不写任何 None 分支。
    @abstractmethod
    async def run_eval(self, program, spec: EvalSpec,
                       ctx: TrialContext) -> EvalResult: ...
    @property
    def metric_keys(self) -> tuple[str, ...]: ...

    # v4.8 · 自述配置，进测量 manifest 的 declared 段（§2 比较侧）。纯 JSON，默认 {}。
    #   它是**声明渠道**，不是自动推导：闭包、远端服务、动态工具的完整配置推导不出来，
    #   缺失的部分在 manifest 里如实记为 unknown——缺失描述永不被解释为「条件相同」。
    #   刻意不叫 fingerprint：ProgramAdapter.fingerprint 已经表示「能否解释快照」，
    #   同名会诱发「相等就可比、不等就拒绝恢复」的误用。
    def describe(self) -> dict: return {}

    coordinator: ClassVar[bool] = False        # v4.8 · True = 本评估会经 ctx.run.runs.spawn 发起子 run，
                                               #   在宿主进程里运行、不占沙箱、不持有池令牌（§3「嵌套」）


class DataEvaluator(Evaluator):                # 标准实现 —— 绝大多数用户只装配它，不继承任何东西
    def __init__(self, data: DataSource,
                 scorer: Scorer,
                 *, input_fn=None, output_fn=None, trace_fn=None,
                 feedback_aggregator=None,     # 默认 None = 零额外 token
                 concurrency: int | None = None,  # 本 evaluator 自身上限，
                                               #   None = 不额外限制；实际并发 = min(它, run 级 item 池)
                 aggregation = "mean",         # 两级（观测 → 样本 → trial）
                 on_error = "zero",
                 keep_items: bool | int = True): ...
                                               # keep_items 只管**落盘**：True = 原文整条写 report.jsonl，
                                               #   int N = 超 N 字符截断，False = 只留 key/metrics/error。
                                               #   内存里的 items 永远带全原文，与本开关无关。
    # ── run_eval(program, spec, ctx) 内部做的全部事情 ──────────────────────
    #   用户通常一行都不用动；写在这里是为了划清它的职责边界：凡是这六步
    #   之内的，都不该由用户重新实现一遍。program 形参标注为 ProgramHandle，
    #   因此本类对「有没有隔离、在哪个进程里跑」完全无感。ProgramHandle 是结构
    #   协议、ProgramAdapter 逐字同签名，所以直接传 adapter 即可，无需先包装。
    #
    #   1) 定子集  spec(split / example_ids / sample_k / seed)
    #        → 确定性 example 列表 × repeat → list[ObservationKey]   ← 钩子 resolve_subset
    #   2) 并发跑  每次观测建一个 ItemContext(key / item_dir)：
    #        async with ctx.run.limiter.acquire("item"):        ← item 池，全局唯一
    #            raw_example = data.get(key.example_id)
    #            inputs      = input_fn(raw_example)
    #            raw_pred    = await program.execute(inputs, item_ctx) ← 用户写的那个 execute
    #            pred, trace = output_fn(raw_pred), trace_fn(raw_pred)
    #        实际并发 = min(self.concurrency, run 级 item 池)
    #        两个用户函数的两侧共五个值全部挂进 ItemResult（原文四件套 + reference），
    #          让 propose 不必持有 DataSource —— 见「原文字段的生命周期」
    #   3) 打分    ScoreRequest(key, inputs, prediction, reference, trace)
    #        → await scorer.score(req, item_ctx) → ScoreResult(metrics / feedback / details / observed)
    #   4) 容错    任一步抛错就地捕获，记 stage(program / output_fn / scorer) + traceback，
    #        以及与 stage 正交的 failure_class（candidate / infra / unknown；默认 unknown，
    #        抛出 CandidateError / InfraError 或由 on_item_error 显式分类时才落定），
    #        按 on_error 给分；单条观测失败不中断本次评估，更不中断 run  ← 钩子 on_item_error
    #        引擎不重试：失败就是这条观测的测量结果
    #        失败的 ItemResult 照样带上已经取到的那几个原文字段 —— 诊断失败正需要它们
    #   5) 落盘    ctx.trial_dir 存在则每条观测 append 进 report.jsonl（流式，不缓存整批）
    #        原文字段按 keep_items 剥离/截断，raw_prediction 默认只写 repr 摘要
    #   6) 聚合    观测 → 样本（repeat 之间）→ trial（样本之间）→ metrics；
    #        feedback_aggregator 非 None 时再合成 trial 级摘要   ← 钩子 aggregate / make_feedback
    #
    #   → EvalResult(metrics, spec, feedback,
    #                stats{total/errors/elapsed/cost},
    #                report_path, items)   —— items 由引擎剥离后才落 TrialRecord

    # 稳定的具名可覆写钩子 —— 「只想改管线里的一小块」唯一的落点。
    resolve_subset(spec, ctx) -> list[ObservationKey]  # 子集 / 重采样策略
    aggregate(items, ctx) -> dict[str, float]         # 两级聚合、命名空间前缀、指标筛选。
                                                      #   键集基数与数据规模无关（见 §0.5 铁律）
    make_feedback(items, ctx) -> TrialFeedback | None # trial 级反馈摘要
    on_item_error(err, key, stage, ctx) -> ItemResult # 单次观测失败 → 该观测的结果
    # 不在此列，覆写即分层违规：并发窗口、ItemContext 装配、limiter 取令牌、
    #   report.jsonl 流式落盘 —— 这四样是引擎正确性的一部分，不是策略。


class FunctionEvaluator(Evaluator):            # 逃生舱：自定义 harness、无 benchmark 评估
    def __init__(self, fn: Callable[[program, EvalSpec, TrialContext],
                                    Awaitable[EvalResult | dict]],
                 metric_keys: tuple[str, ...] = (),
                 *, describe: dict | None = None,   # v4.8 · fn 是闭包，配置只能由用户声明
                 coordinator: bool = False): ...    # v4.8 · 元评估（在 fn 里跑一整个内层 optimize）的落点
```

#### `ctx` 永不为 `None`

「上下文即扩展面」这条规则的前提是上下文*总在*；一个可能为 `None` 的扩展面等于没冻结，每个实现者都要各自决定缺省行为。缺省一律**表达在数据里**：独立评估合成一个字段齐全、取值退化的临时上下文。

| 字段 | ephemeral 上下文填什么 | 为什么是这个填法 |
|---|---|---|
| `run_id` / `mode` | `"standalone-<uuid8>"` / `"standalone"` | 产出可归因；resume 据 `mode` 拒绝续跑一次独立评估 |
| `save_dir` / `trial_dir` | 给了 `save_dir` 就落地（`report.jsonl` 照写）；没给则 `save_dir` 是临时目录（`finally` 清理）而 **`trial_dir = None`** | 与下一行 `artifacts` 是同一手法：指向一个即将被删的目录的指针，比没有指针更糟。且必须是**机制**不是约定——Evaluator 是**因为 `ctx.trial_dir` 存在**才写 `report.jsonl` 的，递给它一个注定被删的临时目录，等于让 `report_path=None` 依赖它记住一条规矩，而那正是 §6「缺省不表达在路径里」禁掉的特判 |
| `artifacts` | 有 `save_dir` → 常规 store；没有 → **带容量上限的内存 store**，超限报错并提示传 `save_dir` | 临时目录删掉后 `ArtifactRef` 会变悬垂指针，报错比留下坏引用诚实 |
| `events` | **真的 emitter，只是没挂 sink** | cost 照常汇进 `stats`——判据四在独立评估下同样成立 |
| `budget` | `UnlimitedBudget().view()`，`exhausted` 恒 `False` | 是一个真实的只读投影，不是 `None` |
| `history` | `EmptyHistoryView`：`record/result/snapshot` 抛 `KeyError`，`records` 为空迭代，`producers_of/measurements_of` 返 `[]` | 「没有这条历史」抛错，好过再传染一层 `None` |
| `parent` / `pending` / `runs` | `None` / 空 `PendingView` / **拒绝型 spawner**（调用即抛 `NestingRefused`） | `parent=None` 就是「顶层」的如实表达；spawner 是真对象，只是这个位置不允许嵌套——与「无限预算」同一手法 |
| `limiter` | 真 `Limiter`，默认三池 | 否则 `min(evaluator.concurrency, item 池)` 无从算起，独立评估的并发行为会与优化内不一致 |
| `cancel` | 永不取消的 token（或调用方传入的那个） | 同上，取真对象 |
| `specs` | `{"val": 本次传入的那个 spec}` | 独立评估只有一个角色；填真表而不是空 dict，装饰器与 contrib 因此不写「没有 specs」的分支。**`Evaluator.evaluate` 必须把自己收到的 `spec` 透传进工厂**，否则这一行落空、空表原样回来（`Backend.session` 没有 spec 可传，那里才是真的空表） |
| `trial_id` / `changes` / `source_snapshot_id` | `-1` / `[]` / `""` | **正则装饰器由此天然产出 0.0**：不是被特判掉的，是「没有改动、没有参照物」被如实表达的结果 |
| `record_id` / `kind` | `"<run_id>/-1"` / `"measure"` | 独立评估就是一次测量；身份照样齐全，report 与 sink 照样可归因 |
| `snapshot` / `executor` | `await program.take_snapshot()`（v4.8 起 adapter 与句柄同有此方法），二者为同一对象 | 测量时目标即执行者。v4.7 在这里要判 `isinstance(program, ProgramAdapter)`，是因为句柄取不到快照；句柄镜像 `take_snapshot` 之后，这处全库唯一的类型区分随之消失 |

**归一化只有一处，在基类。** 放进每个 `Evaluator` 实现里就是把同一段逻辑扇出到 `DataEvaluator`、`FunctionEvaluator`、每个装饰器和每个 contrib——「零重复路径」的字面反例。`Backend.session(ctx=None)` 因此也不自己合成，改调同一个 `ephemeral_trial_context`；连同 `LocalBackend` 直通路径，**三处由一个函数一次封住**。要自查的一条：这个工厂必须实现成「一个真实上下文取了退化值」，而不是一个 standalone 分支模式——执行路径仍然只有一条，退化只发生在数据上。

**为什么 `TrialContext` 就是对的类型**：它的作用域定义是「一个程序实例的一生」，而一次独立评估恰好就是一个程序实例的一生。名字里那个 trial 是可以接受的代价，另立一个 `EvalContext` 会凭空多出一层作用域，违反「三层上下文」的冻结。

#### 自定义评估的四条路，按代价排序

1. **换装配槽**：`DataEvaluator(...)` 的构造参数（数据源、scorer、`input_fn`/`output_fn`/`trace_fn`、聚合、错误策略、并发）。覆盖绝大多数场景。
2. **覆写 `DataEvaluator` 的具名钩子**：`resolve_subset` / `aggregate` / `make_feedback` / `on_item_error` 四个，且只有这四个是有稳定性承诺的。管线骨架不在其中。
3. **装饰**：`WithRegularization` 那一族——trial 级的额外度量、逐 case 非回归、成本与安全门。
4. **`FunctionEvaluator`**：整条评估就是你自己的 harness（SWE-bench runner、外部评测服务、整套件一次调用）。

**继承 `Evaluator` ABC 是「写一条新的执行管线」的动作**，属于框架 / contrib 作者，不是用户动作。

两个被明确拒绝的改法：**把 `DataEvaluator` 改名成 `Evaluator`**——`FunctionEvaluator` 与装饰器是同层公民，让其中一个独占基名会让另外几个显得像二等；而且 `Evaluator(data=..., scorer=...)` 这种签名对一个如此通用的名字来说太具体。**让 `Evaluator(...)` 构造时工厂式返回 `DataEvaluator`、继承时又是 ABC**——太聪明，读者永远猜不到一个类为什么两副面孔。

#### `repeat` 不是 `sample_k` 的变体，它改的是主键

被优化的程序是随机的，区分「真的变好了」与「这次运气好」只能靠重复观测——而这是*格式层*的能力，算法作者事后补不了。它也不能靠 `extras` 打发：`ItemResult` 的主键是 `(example_id, repeat)`，report.jsonl 的行标识、聚合层数、resume 的续跑锚点全部跟着它走。

#### trial 级的额外度量是 `Evaluator` 的一层装饰器，不是框架组件

自进化算法天然倾向于在*特定失败样例*上做局部修补而非提升泛化，对策是给「改动幅度」一个可测量的惩罚项；最便宜的一族做法是拿当前执行轨迹与上一版比较，把行为漂移本身当作正则项。这类度量需要的一切都已经在 Evaluator 手里：改动幅度在 `ctx.changes` 与 `ctx.source_snapshot_id`，参照物在 `ctx.run.history`（`report.jsonl` 在写下的那一刻就成了下一轮的轨迹参照库），评估配置在 `spec`，cost 与事件走 `ctx.run.events`。于是它是一层纯组合：

```python
evaluator = WithRegularization(base_evaluator, [
    ChangeSizeRegularizer(),                     # 只碰 ctx.changes（≈ weight_decay，零 LLM 调用）
    TrajectoryDivergence(),                      # 读 ctx.run.history（≈ RLHF 的 KL）
])                                               # 产出并入 metrics 的 reg/* 命名空间

best = await SomeOptimizer(adapter=...).optimize(
    evaluator=evaluator,
    objective=PenalizedObjective(ScalarObjective("pass@1"),
                                 penalties={"reg/traj_div": 0.1}), ...)
# 装饰器里没有任何独立评估的特判——ephemeral 上下文的 changes 为空、
#   history 为空，这两个正则于是天然产出 0.0。
```

关键是**它必须与搜索算法正交**：GEPA + 轨迹正则、AFlow + 轨迹正则、MIPRO + 轨迹正则都成立，塞进任一具体算法都会让每个算法作者重造一遍管线。装饰器给出这个正交性——包好的 evaluator 递给哪个优化器都行，而配置位置就是用户本来就在装配评估的地方。这也正是它不该是主循环一步的理由。至于「这两版的轨迹到底能不能对比」这类前提，由每个正则实现自己判断（它拿得到双方的 `spec`、`changes` 与历史记录）——框架只保证*参照物拿得到、产出能进 metrics 通道*。

同一条路子也承接**逐 case 非回归**（「原本通过的 case 不许变差」）：装饰器读 `result.items` 算出退化集合，只把**固定几个标量**并入 metrics（`nonreg/regressed`、`nonreg/rate`），逐 case 明细本就在 `report.jsonl` 里，大的另经 `ctx.run.artifacts` 外置成 `ArtifactRef`；`Objective` 比的是那个标量，要看「具体哪些 case 退了」时顺 `report_path` 取。**逐 case 向量不进 metrics**：`case/<id>/score` 这类键集随子集变化，既让 `state.json` 随数据规模膨胀，也让两个 trial 的 metrics 形状不同——它是可追溯信号，不是可比较信号。这也正是 `metric_keys` 能静态声明的前提：只有基数固定的键集才谈得上「全失败仍出 `{key: 0.0}`」。整件事仍是一个装饰器 + 一个 `Objective`，**零框架改动**——写在这里是为了挡住「给 `TrialRecord` 加一个 per-case 字典」和「给引擎加一个回归检查步骤」这两类提案。

### 打分侧

```python
@dataclass
class ScoreRequest:                            # 打分所需的一切，一个对象，零嗅探
    key: ObservationKey                        # (example_id, repeat)，judge 知道这是同题第几次
    inputs: dict                               # 原始输入 —— judge / rubric 必需
    prediction: Any
    reference: Any                             # label 的泛化：答案 | rubric 列表 | 检查清单
    trace: Any | None                          # 过程打分（judge 看轨迹而非只看终答）

@dataclass
class ScoreResult:                             # metrics 之外允许伴生文本
    metrics: dict[str, float]                  # → 可比较信号
    feedback: str | None = None                # → 可解读信号，只走 report.jsonl
    details: dict = {}                         # → 可追溯信号（rubric 逐项打分等）
    observed: dict = {}                        # v4.8 · 这次打分实际所处的条件（judge 返回的模型版本、外部服务
                                               #   revision……）→ 测量 manifest 的 observed 段（§0.6 测量身份，
                                               #   不是评价信号）。走返回值而不走事件：事件可丢，manifest 是权威记录

class Scorer(ABC):
    async def score(self, req: ScoreRequest, ctx: ItemContext) -> ScoreResult: ...
                                               # v4.8 · 收 ItemContext，与 execute 同一条规则（上下文即扩展面）：
                                               #   judge 型 scorer 经 ctx.events.cost 报自己的开销、从
                                               #   ctx.trial.run.limiter 取 llm 令牌。v4.7 的 score(req) 够不到任何设施，「scorer 只认
                                               #   cost 入口」因此无从兑现
    @property
    def metric_keys(self) -> tuple[str, ...]: ...  # 契约：全失败仍出 {key: 0.0}
    def describe(self) -> dict: return {}      # v4.8 · 自述配置（judge 模型、rubric 版本、prompt），进 manifest declared 段

# as_scorer(fn) 仍可包裸函数 (prediction, reference) -> dict（包装器替它收 ctx）；
# CompositeScorer(scorers, prefixes=[...]) 产出 "prefix/key"，observed 与 describe 按前缀合并。
```

**rubric 不是 label 的别名，而是 reference 的一种形态。** 把 rubric 硬塞进 label 会让「label = 正确答案」的语义崩掉，且仍解决不了 judge 看不到 input 的问题。`reference` 对框架完全不透明，因此 rubric benchmark 是**零框架改动**的：`get_reference` 返回 rubric 列表，配一个 `RubricJudgeScorer` 即可，逐项打分进 `details`、总分进 `metrics`、评语进 `feedback`——三类信号各就各位。

### 结果对象

```python
@dataclass
class EvalResult:                              # 单一结果对象，字段分持久/运行时两组
    metrics: dict[str, float]                  # 持久 → TrialRecord.metrics（含 reg/*）
    spec: EvalSpec | None                      # 持久 → TrialRecord.eval_spec（不读数据的生产为 None）
    feedback: TrialFeedback | None             # 持久 → TrialRecord，带硬长度上限
    stats: dict                                # 持久：total/errors/elapsed/cost。v4.8 · total / errors 对所有
                                               #   Evaluator 必填：DataEvaluator 按观测计；FunctionEvaluator 的 fn 返回
                                               #   dict 时归一化为 total=1、errors=0（fn 抛错即整条记录失败）。引擎只凭它
                                               #   判断「这次测量是否真的成立」（选择协议绑定，§2 比较侧），不认识具体管线
    report_path: str | None                    # 持久：逐条日志指针
    items: list[ItemResult]                    # 运行时：递给装饰器与 observe，
                                               #         剥离后落盘，永不进 state.json

    def save(path) / load(path) / aggregate(...)  # 独立评估场景直接用它复盘。v4.8 · 带结果类型标记：
                                               #   ProduceResult 往返后仍是 ProduceResult，outputs 不丢


@dataclass
class ItemResult:                              # 一次观测发生的一切
    key: ObservationKey                        # 主键 = (example_id, repeat)

    # ── 原文四件套：每个用户函数的两侧各留一份 ────────────────────────
    raw_example: Any | None                    # 数据源里的原始样本      —— input_fn 之前
    inputs: Any | None                         # 喂给 execute 的入参      —— input_fn 之后
    raw_prediction: Any | None                 # execute 的原样返回值    —— output_fn 之前
    prediction: Any | None                     # 打分用的预测            —— output_fn 之后
    reference: Any | None                      # 该样本的参照答案 / rubric / 检查清单

    metrics; trace; artifacts
    feedback: str | None                       # per-example 文本反馈，只走 report.jsonl
    details: dict                              # rubric 逐项、judge 结构化输出
    error: str | None; stage: str | None       # program / output_fn / scorer
    failure_class: str | None                  # v4.8 · candidate / infra / unknown，与 stage 正交
    traceback: str | None; elapsed_time: float


@dataclass
class TrialRecord:                             # state.json 里唯一的节点类型：一个请求的一次执行
    # ── 执行身份 ──────────────────────────────────────────────────────
    schema_version: int                        # v4.8 · 记录格式版本；不认识的版本拒绝续跑而不是猜
    trial_id: int                              # run 内序号；BASELINE_TRIAL_ID = 0
    record_id: str                             # v4.8 · f"{run_id}/{trial_id}"，跨 run、跨嵌套唯一
    kind: str = "measure"                      # v4.8 · "measure" | "produce"。不认识的 kind 只作为
                                               #   不透明历史读取，默认既不执行也不参与比较
    status: str                                # v4.8 · pending | running | ok | failed | cancelled | unknown
    parent_scope: ScopeRef | None              # v4.8 · 嵌套 run 时指向父作用域
    execution_ref: dict | None                 # v4.8 · 后端所有权登记的副本（launch 后随下一次 checkpoint 写入）；
                                               #   权威登记在 trial_dir/execution.json（§2 执行侧「启动握手」）

    # ── 请求：Proposal 的**已解析副本**，提交那一刻固定，执行时不再解析 ──────────
    #   只存角色名不够——恢复时同一个名字可能已经绑到另一个实现
    source_snapshot_id: str                    # 目标基底
    changes: list[UnitChange]                  # 提交前施加于基底的变更（可为空）
    eval_spec: EvalSpec | None                 # 解析后的 spec，含 role。measure 缺省取 ctx.specs[select_on]；
                                               #   produce 缺省保持 None（不读数据的生产）
    executor_snapshot_id: str | None           # v4.8 · 解析后的执行者：measure 与自修改 produce 时 = snapshot_id
                                               #   （提交时只固定「执行者即目标」，id 在 apply 后与 snapshot_id 一同写入）；
                                               #   否则提交时就固定为改进器 / 训练器 / 学习者
    producer: dict | None                      # v4.8 · produce：{role, impl, describe}
    inputs: Any | None                         # v4.8 · produce：交给 Producer 的 JSON 载荷（大值走 ArtifactRef）
    metadata: dict = {}                        # proposal.metadata 透传，纯 JSON。算法声明参考过的历史候选
                                               #   放 reference_snapshot_ids——那是声明，不是引擎观测到的事实

    # ── 版本结果 ─────────────────────────────────────────────────────
    snapshot_id: str | None                    # 目标输入版本 = apply(source, changes)；measure 时即被测版本。
                                               #   apply 在派发时（预留之后、启动之前）执行，一成功就写入，
                                               #   此后任何阶段失败都**保留**——候选身份不随测量失败消失
    outputs: list[str] = []                    # v4.8 · produce 已接纳并提交的输出 snapshot_id（0..n）
    # §0.6 的四种关系因此各占一处：目标基底 source_snapshot_id、实际执行者 executor_snapshot_id、
    #   输出 outputs、声明参考 metadata.reference_snapshot_ids

    # ── 测量 ─────────────────────────────────────────────────────────
    metrics: dict[str, float]                  # 聚合值 + reg/*；键集基数与数据规模无关。
                                               #   measure：评价 snapshot_id；produce：描述这次生产过程
    measurement_ref: ArtifactRef | None        # v4.8 · 测量 manifest（§2 比较侧）
    measurement_key: str | None                # v4.8 · 由分组策略从 manifest 算出，供比较分组
    feedback: TrialFeedback | None             # 唯一「进 state.json 但不参与比较」的测量字段
    stats: dict                                # total / errors / elapsed / cost
    report_path: str | None                    # → trials/<id>/report.jsonl，唯一的明细指针
    trial_dir: str | None; workspace_dir: str | None

    # ── 失败 ─────────────────────────────────────────────────────────
    error: str | None; stage: str | None       # 见下方 STAGES
    failure_class: str | None                  # v4.8 · candidate | infra | unknown，与 stage 正交
    traceback: str | None

TrialFeedback = {"summary": str, "truncated": bool}   # 超硬上限则截断并置位

STAGES = (                                     # v4.8 · 扩充，仍是封闭集合（Budget 与诊断都靠它）
    "apply", "validate", "program", "output_fn", "scorer",   # 测量路径（v4.7 原有）
    "produce", "accept",                                       # 生产路径：运行 Producer / 接纳输出
    "launch", "shutdown", "persist",                           # 执行与提交的框架侧阶段（launch 含 prepare_workspace）
)
```

**`TrialRecord` 的字段表就是序列化格式本身。** 它与 `Snapshot` 是全库仅有的两个「写错了就要迁移所有已存 run」的对象，所以它排在 §7 第 1 步。三条纪律：*只放纯 JSON + ArtifactRef*；*除 `feedback` 外每个字段要么参与比较、要么是指针、要么是身份*（v4.8：执行身份与版本关系属于第三类——它们不参与比较，但回答「谁、基于什么、产生了什么」，丢了就补不回来）；*逐条明细一律不进*——`report_path` 是它与 `report.jsonl` 之间唯一的连接。`EvalResult` 与它不是两个平行的类，而是「运行时对象 → 剥离 `items` 后的持久投影」。

**统一记录，但不是一组任意可空的字段。** v4.8 让一条记录既能表示测量、也能表示生产，代价是必须用 `kind × status` 约束字段组合，否则「单一类型」只是把矛盾藏进了 Optional。约束在模型校验里实现：

| 组合 | 最低约束 |
|---|---|
| `measure` + `ok` | `snapshot_id`、`executor_snapshot_id`（= `snapshot_id`）、`eval_spec`、`measurement_ref` 齐全；`outputs` 为空。metrics 可以为空（键集由 evaluator 决定） |
| `produce` + `ok` | `snapshot_id`、`executor_snapshot_id` 齐全；全部输出已校验、已提交；**允许零输出**（生产完成但没有产出可接纳的版本，也是一个事实） |
| `pending` | 请求已固定并持久化（`source_snapshot_id` / `changes` / `eval_spec`（含 `role`）/ `producer` / 执行者均已解析），尚无任何执行事实；`snapshot_id` 尚未写入——apply 在派发时、预留之后执行；metrics 必须为空 |
| `running` | **先于 launch 落盘**（§3 流程）；能存活于宿主之外的后端在 `trial_dir` 里有所有权登记，`execution_ref` 是它的副本 |
| `unknown` | **待核对，不是终态**：不释放预留额度、不交付 observe；只能经核对转为 `ok` / `failed` / `cancelled` |
| `failed` | 带 `stage` 与 `failure_class`；`snapshot_id` 若 apply 已成功则保留。派发阶段（apply、`prepare_workspace`）失败的请求从 `pending` 直接成为 `failed`，已预留的额度照常结算 |
| `cancelled` | `error` 写明原因（`stop` / `lost`），`stage` 可选（已知时填）；只在取消**被确认**后写入——「请求了取消」只是一个事件与 `PendingView` 上的标记，状态仍是 `running` |

状态转换只有这几条：`pending → running → {ok, failed, cancelled, unknown}`；`pending → failed`（派发阶段失败）；`pending → cancelled`（停止时从未派发）；`unknown → {ok, failed, cancelled}`（核对）。对同一 `record_id` 的重复完成通知，若记录已是终态则忽略并发一条告警事件——完成通知的去重靠 `record_id`，不靠「恰好只来一次」。

**失败的两个维度。** `stage` 回答「坏在哪一步」，`failure_class` 回答「是谁的问题」——v4.7 用前者推断后者（「有 stage 即候选之过」），但 scorer 背后的 judge 服务超时同样落在 `stage="scorer"`。默认归类：引擎自己检出的 schema / 可写范围违规 → `candidate`；用户或候选代码抛出的异常 → `unknown`，除非抛出带类型的 `CandidateError` / `InfraError`，或由 `on_item_error` / `Producer` 显式分类。item 级与记录级各有一份：记录级描述「导致整次执行失败的原因」，混合了多种 item 故障的聚合与计账由 `Budget` 策略决定（§2 算法侧 · Budget）。

设计规则：**「两个高度」由一个对象上的持久 / 运行时字段分组表达，而不是两个类。** 算法在 `observe` 里对 `items` 想看多细看多细、用完即弃；检查点里只留聚合值、摘要与指针，二者永远不需要谈判。

**唯一的例外要明说**：`feedback` 是*测量字段里唯一*进 `state.json` 却*不*参与比较的字段（v4.8 的执行身份与版本关系同样不参与比较，但它们是身份，见上文三条纪律）。理由是它是下一轮 `propose` 的直接养料，且 resume 之后必须仍然拿得到——每轮从磁盘捞回来会让反思型算法的主路径挂在 IO 上。代价用硬上限兜住：超限截断并留 `report_path` 指针。per-example 反馈没有这个豁免，一律只走 report.jsonl。

#### 原文字段的生命周期

`ItemResult` 上的五个原文字段（`raw_example` / `inputs` / `raw_prediction` / `prediction` / `reference`）解决的是一件事：**`propose` 要拿到样本原文，不该为此持有 `DataSource`。**

反思型 prompt 优化器要把「题目原文 + 模型输出 + 反馈」喂给 LLM 写梯度；MIPRO 的 demo 筛选要从高分轨迹里挑 `(input, output)` 对做 few-shot。这些信息在评估过程中**本来就在内存里**——`raw_example` 刚从数据源取出、`inputs` 刚由 `input_fn` 算出、`raw_prediction` 是 `execute` 的返回值、`reference` 刚喂给 scorer——直接挂到 `ItemResult` 上是零额外成本。而 `observe` / `propose` 消费的正是**内存里**的 `result.items`。

被明确否掉的写法是「凭 `key.example_id` 反查 `DataSource`」：它要求算法侧持有数据源，而 `propose(state, objective, ctx)` 收不到 evaluator；退而求其次让用户把 benchmark 同时传给 `Optimizer.__init__`，则同一份数据被塞给两个组件、由用户自己保证一致，且 `FunctionEvaluator` 场景下压根没有 `DataSource`，`example_id` 变成悬垂引用。**日志膨胀是磁盘侧的问题，就在磁盘侧解决**，不该反向污染内存对象的信息完整性。

**为什么每个用户函数的两侧都留一份。** `input_fn` 与 `output_fn` 都是有损变换，而丢掉哪一侧有用由算法定，格式层不替它选：

| 只留变换后 | 会瞎掉的场景 |
|---|---|
| 只有 `inputs` | `input_fn=lambda ex: {"question": ex["prompt"]}` 丢掉了 id、难度、标签、领域——data-aware proposer 与失败样本聚类要的正是这些 |
| 只有 `prediction` | `output_fn=extract_code` 抽空时 `prediction=""`、`metrics=0`，算法看到的是「答错了」，真相是「答对了但格式没对上」——**这两件事要改的 prompt 完全相反**，而后者恰恰是 prompt 优化器最该修的一类 |

`trace` 补不了后一个洞：`trace_fn` 默认是 `None`，用户不配就什么都没有；且 `trace` 的语义是「算法要消费的轨迹」，是用户挑过的结果，不是未经处理的原文。

**落盘由 `keep_items` 一个开关统一决定**，三档语义：

| `keep_items` | `report.jsonl` 里的原文字段 |
|---|---|
| `True`（默认） | `raw_example` / `inputs` / `prediction` / `reference` 整条写入；`raw_prediction` 写摘要（见下） |
| `int N` | 同上，但每个字段序列化后超过 N 字符即截断并置 `truncated` 位 |
| `False` | 五个字段一律不写，只留 `key` / `metrics` / `error` |

`raw_prediction` 是**唯一默认就截断**的那个，两条它独有的性质决定：agentic 程序的原样返回值可能是整段多轮 transcript，**体积不由数据集规模决定而由程序话痨程度决定**；而且它常常不是 JSON 可序列化的对象（Message 列表、自定义结果类）。所以它的落盘策略是「`repr` 摘要 + 硬上限」，要完整留档走 `ctx.artifacts` 出 `ArtifactRef`。

不变的一条：**这五个字段没有一个进 `state.json` 或 `TrialRecord`**。它们随 `items` 一起在引擎剥离那一步消失，`report_path` 仍是明细的唯一指针。§0.5 的三信号分流一个字不用改——原文属于「可追溯的」那一路。

v4.8 补一条：`report.jsonl` 是**给人看的**明细，`keep_items` 可以截断它，所以它不能充当「恢复后重新喂给 `observe`」的输入。恢复所需的那一份由引擎另行持久化（§3「恢复」的 outcome 编码），是一个 artifact，不受 `keep_items` 影响，也同样不进 `state.json`。

### 程序侧

```python
class ProgramAdapter(ABC):
    # 必须实现（DictProgramAdapter 对 JSON 值程序全部代劳，只剩 execute）：
    register_units() -> list[OptimizationUnit] # 声明可优化单元（≈ nn.Parameter）。
                                               #   unit_type 是开放字符串：内建的 prompt / model /
                                               #   memory / file / code / field 只是常量集合，不是封闭枚举；
                                               #   第三方带命名空间前缀自定义（"myplugin/sql_schema"）。
                                               #   与 operation 同一手法：类型开放，校验仍归 supported_unit_types。
    take_snapshot() -> Snapshot                # 当前状态 → 纯 JSON 检查点（≈ state_dict）
    merge_changes(snapshot, changes) -> Snapshot
    from_snapshot(snapshot) -> ProgramAdapter  # 纯函数式：输入不可变，返回新实例
    execute(inputs, ctx: ItemContext)          # 与 ProgramHandle.execute 逐字同签名。
                                               #   同步或异步皆可，框架自适应。
                                               #   契约（v4.8 限定作用域）：
                                               #   · measure 实例的保留状态只读 —— 一次评估内 N 条观测并发跑在
                                               #     同一个实例上，而它们必须是同一个候选的 N 次测量；
                                               #   · produce 实例可以按生产契约更新**自己独占的**状态
                                               #     （在线学习、自修改），引擎保证它不与任何测量共享实例；
                                               #   · 已提交的输入快照在两种情况下都不可变。
                                               #   程序状态的变化进入下一版，只有两条路：propose 的 changes，
                                               #     或 produce 记录的输出（§3「版本的两条产生路径」）。

    # 可选钩子（有默认实现）：
    validate_trial(ctx: TrialContext)          # 评估前冒烟测试；与 ProgramHandle 上的
                                               #   同名方法逐字同签名，引擎一律经句柄调用，
                                               #   因此它总是在隔离边界内侧执行
    validate_snapshot(snapshot, base) -> None  # v4.8 · 接纳一个**完整快照**输出前的校验（§2 生产侧）：
                                               #   unit 集合、program_config 的哪些部分允许被生产者改写，
                                               #   由 adapter 声明并在此检查。默认实现：unit 集合与
                                               #   program_config 都必须与 base 相同，只许 unit 值变化——
                                               #   完整快照不能成为绕过可写范围的后门
    learn(feedback: ScoreResult, ctx: ItemContext)
                                               # v4.8 · 只在 produce 实例上由 StreamProducer 调用：把这条观测的
                                               #   打分结果交给学习者（预测 → 打分 → 学习，严格按序）。
                                               #   默认空操作——只从自身轨迹学习的程序不必实现
    prepare_workspace(snapshot, ctx)           # 文件型程序：把 snapshot 物化到 per-trial 沙箱。
                                               #   v4.8 · 只写文件，不 import / 不执行任何候选代码——
                                               #   它在宿主侧运行，候选代码只能在 Backend 边界内加载
    fingerprint() -> str                       # resume 兼容校验，语义见下（与内容 id 是两回事）
    pre_apply_hook / post_apply_hook

@dataclass
class UnitChange:                              # 对一个 unit 的一次变更，纯 JSON。增量形态的
    uid: str                                   #   状态变更载体：由 propose 或 Producer 的 DeltaOutput 产出，
    value: Any | None                          #   经 merge_changes 落成快照，持久化于 Proposal / TrialRecord
    old_value: Any | None = None
    operation: ChangeOperation | str = "replace"
    metadata: dict = {}
    # uid       register_units 声明的那个 unit
    # value     replace 时是新值；其余 operation 可解释为 patch / 追加项 /
    #           删除选择器，无载荷的 operation 取 None
    # operation 非 replace 与 adapter 自定义 operation 的合并语义由
    #           merge_changes 定义，且目标 unit 必须允许该 operation
    # metadata  算法侧的来由 / 置信度 / 记账

# Snapshot：unit_values + program_config，纯 JSON 强校验。
# 大值（技能库、权重）用 ArtifactRef {"$ref": "sha256:<hex>", "sha256": ..., "kind": ...} 外置，实体落 artifacts/。
#
# v4.8 · snapshot_id 内容寻址：
#   snapshot_id = sha256(canonical_json({
#       "format": SNAPSHOT_FORMAT_VERSION,          # 解释这份内容所需的格式身份
#       "unit_values": ..., "program_config": ...}))  # 全长 64 位十六进制
#   规范化规则（冻结）：键排序、无多余空白、float 用最短往返表示；**不做任何结构改写**。
#     ArtifactRef 的 $ref 本身就是内容地址（§2 上下文侧），引用原样参与即与位置无关，无须特判；
#     "$ref" 是保留键，普通 JSON 不可能与引用同形——规范化是单射，不存在编码碰撞。
#     其他不透明 JSON 原样参与，不按字段名猜测要忽略什么。
#   不进身份的只有框架自己的运行元数据（trial_dir 等运行路径、创建时间、父记录、记录 id）——它们本来
#     就不在 unit_values / program_config 里。program_config 里用户写的路径字符串是普通 JSON，照常参与。
#   快照不带父指针。
#   权威内容是**已提交的规范化字节**：读取返回独立的新对象，提交与加载时重算并校验 digest。
#     pydantic 的 frozen=True 只冻结顶层，挡不住改内部 dict / list，不能拿来当不可变保证。
#   承诺的范围要说准：「规范化描述相同 → id 相同」。它不承诺外部依赖相同、行为相同——
#     模型别名、可变路径不会因为进了一个 JSON hash 就被固定。
```

**`fingerprint` 校验的是「这个 adapter 能否解释这些快照」，不是「程序长得一样」。** 否则一整类场景会被 resume 兼容校验挡在门外：**程序形态本身在演化**——冷启动自由探索时被评估的是一个通用 agent，第 1 轮之后它被编译成代码化工作流；agent 架构搜索里每个候选是一张不同的图；工具与技能从无到有。机制上这已经成立（`program_config` 对框架完全不透明，`from_snapshot` 可以据此分派出不同形态的程序），靠的是这条明文规则加一个可覆写的 `fingerprint()`。

**三个容易混的标识，各回答一个问题。** `fingerprint()` 回答「这个 adapter 能否解释这些快照」（兼容性）；`snapshot_id` 回答「描述字节是否相同」（内容身份）；测量 manifest 回答「这个分数在什么条件下取得」（测量身份，§2 比较侧）。框架不解释 `program_config` 的领域语义，照样可以对它的规范化字节求摘要——内容寻址与「program_config 对框架不透明」并不冲突。

**内容寻址之后，「版本 DAG」要换个说法。** 程序从 A 改成 B 再改回 A，内容 id 依次是 A → B → A；NOOP 甚至给出 A → A。**内容关系图允许重复、允许成环；有时间方向的谱系由执行记录给出**——每条 produce / apply 记录都是一次「产生」事件，按提交顺序排列。这不需要第二种节点：事件就是 `TrialRecord`。v4.7 说的「快照 DAG」在 v4.8 里精确地指「执行记录构成的谱系」。

### 执行侧

大量 agent 程序带副作用（写文件、改环境变量、可变全局状态），harness 自进化的被优化对象干脆就是一份会被反复重写的源码目录。「程序是什么」与「程序在哪跑」必须分离——前者归 `ProgramAdapter`，后者归 `Backend`。否则每个副作用程序都要在 adapter 里自己重新发明进程管理，隔离逻辑散落到用户代码里。

```python
class Backend(ABC):                            # 执行平面：程序在哪跑、副作用关在哪（≈ device）
    # 所有权：trial_dir / workspace / item_dir 归**引擎**，run 结束也不删；
    #   执行沙箱（进程、容器、每观测目录）归 **Backend**，shutdown 回收。
    #   shutdown 回收沙箱前先同步（见下），二者因此不冲突。
    async def launch(self, adapter: ProgramAdapter, snapshot: Snapshot,
                     ctx: TrialContext) -> ProgramHandle: ...
                                                  # v4.8 · 收「可信的 run 级 adapter（工厂）+ 要启动的快照」，
                                                  #   由 Backend 在**自己那一侧**调 from_snapshot 重建程序：
                                                  #   LocalBackend 在本进程重建，SubprocessBackend 在 worker 里重建。
                                                  #   v4.7 收的是宿主侧已经 from_snapshot 好的 adapter——那意味着
                                                  #   候选代码可能在父进程被加载，validate_trial 搬进子进程也晚了。
                                                  #   produce 时 snapshot 是 ctx.executor，measure 时是 ctx.snapshot。
    async def shutdown(self, handle: ProgramHandle,
                       ctx: TrialContext): ...    # 回收沙箱**之前**先把沙箱内的 workspace 子树
                                                  #   同步回 ctx.workspace（isolation="item" 时回 ctx.item_dir）。
                                                  #   同步的是 workspace 子树，不是整个沙箱镜像。
                                                  #   ctx 与 launch 对称传入，Backend 不持有注册表。

    # ── v4.8 · 恢复边界（T0 定签名；T1 实现本地两种后端；远端对账属 T2）────────
    capabilities: ClassVar[frozenset[str]]     # 例："probe" / "terminate" / "item_isolation" /
                                               #   "strong_isolation"。引擎按能力决定恢复时怎么处理在途执行，
                                               #   不支持的能力被请求时明确拒绝
    def execution_ref(self, handle) -> dict | None
                                               # 所有权登记的副本，launch 后随下一次 checkpoint 进记录（供人看、供对账）：
                                               #   Subprocess = {pid, pgid, start_time}；远端 = {job_id, ...}；
                                               #   进程内执行 = None（宿主死则它必死，无需关联）
    async def probe(self, record: TrialRecord) -> Literal["alive", "dead", "unknown"]
    async def terminate(self, record: TrialRecord) -> bool   # True = 确认已终止并回收
    # 「宿主被 kill」推不出「子进程也没了」：SubprocessBackend 的子、孙进程可能存活。
    #   恢复时 probe → alive 则 terminate；能确认终止才记 cancelled，确认不了记 unknown。
    # v4.8 · 启动握手（T0 定语义）：引擎先把记录写成 running 并 checkpoint，再调 launch。能存活于宿主之外的
    #   后端在加载任何候选代码之前，先把所有权登记（pid / pgid / start_time 或 job_id）落盘到
    #   trial_dir/execution.json，再等宿主确认才加载——宿主已不在就自行退出。于是恢复时「无登记」⇒ 候选
    #   代码从未加载（probe 返回 dead）；有登记 ⇒ 按登记探测与终止。登记须原子发布（写临时文件再改名）
#   并落盘后才发确认；文件存在但损坏或读不出 ⇒ unknown，不按「无登记」处理。probe / terminate 因此收整条记录
    #   （record_id + trial_dir + execution_ref），不只收那份可能还没来得及写进 state.json 的副本。

    def session(self, program, ctx: TrialContext | None = None)
                                               # → AsyncContextManager[ProgramHandle]
                                               # launch/shutdown 的成对糖。只在「独立评估 + 需要隔离」时用；
                                               #   不给 ctx 时调用与 Evaluator 同一个 ephemeral_trial_context，
                                               #   合成逻辑全库只有一份。
                                               #   本地独立评估不需要它——见下方接线规则。


class ProgramHandle(Protocol):                 # ProgramAdapter 的远程存根（stub）。
    # 它镜像 adapter 上那些「必须在隔离边界内侧执行」的方法，逐字同名同签名——
    # 用户永远不实现它，只有写新 Backend 的人才实现。
    async def execute(self, inputs, ctx: ItemContext) -> Any
    async def validate_trial(self, ctx: TrialContext) -> None
                                               # 评估前的冒烟测试 —— import 候选模块、跑一个最小 case。
                                               #   不通过则本 trial 直接失败（stage="validate"），不花评估那份钱；
                                               #   失败的 stage 与 traceback 同时是下一轮 propose 的养料。
                                               #   为什么必须在边界内侧：理由与 execute 逐条相同 —— 候选的依赖装在
                                               #   workspace / 容器里（父进程根本 import 不到，会误报失败）、两个 trial
                                               #   的同名模块不能共存于一个解释器、LLM 写的候选可能直接杀掉进程。
                                               #   纯静态校验不走这里：那是 apply + Snapshot 强校验的事（stage="apply"）。
    async def take_snapshot(self) -> Snapshot   # v4.8 · 镜像 adapter.take_snapshot。Producer 在句柄关闭前
                                               #   用它捕获执行者改过的自己——那份状态在边界内侧，只能在那里取
    async def learn(self, feedback, ctx: ItemContext) -> None   # v4.8 · 镜像 adapter.learn

# 不变量：边界内侧的 ctx 必须是一个真的 ItemContext —— events / cancel / limiter /
#   artifacts 由 Backend 实现成回调父进程的代理，history 在边界内侧允许抛
#   NotImplementedError。wire 上传什么是 Backend 的私事，不构成公开协议。

# LocalBackend（默认）：launch = return adapter.from_snapshot(snapshot) —— 重建出的 ProgramAdapter
#   天然满足 ProgramHandle 协议，不包装、不适配，行为与不存在 Backend 完全一致。
#   capabilities 不含 strong_isolation：同进程的代码能直接读文件，API 层的过滤（§3「证据视图」）
#   对它是接口边界，不是安全边界。
# SubprocessBackend：默认每 trial 一个工作进程，根目录 = TrialWorkspace，stdio JSON-RPC；
#   isolation="item" 时按 ctx.key 每次观测一个新沙箱（往 workspace 写东西的 agent 是常态）。
#   约束：execute 的 I/O 必须可 JSON 序列化。子进程崩溃 = 该观测失败（stage="program"），
#   父进程与 run 不受影响；并发实例各占独立进程，副作用互不污染。
#   前置条件：adapter 类必须可 import（不能定义在 __main__），launch 时 fail-fast。
#   v4.8 · worker 以独立进程组启动并设父死信号；按上面的启动握手先登记 {pid, pgid, start_time} 再加载
#   候选，probe / terminate 按登记的进程组实现。produce 记录禁止 isolation="item"（T1 明确拒绝，§2 生产侧）。
# Container / Remote 是同协议的未来实现，不改任何既有对象。
```

**只收任务数据是不够的。** `inputs` 是 `input_fn` 的产出——纯任务数据，句柄无从知道：这是哪一次观测、该往哪个目录写。需要按样本分目录的程序、需要按观测决定「复用还是新建实例」的隔离型 Backend，都只能让用户把 item 身份塞进 `inputs` 字典偷渡，绕过类型化契约。有了 `ItemContext` 之后，**隔离粒度（run / trial / item）纯粹变成 Backend 的实现选择，Evaluator 与 Adapter 一行都不用改**。这同时承认了一件事：一次评估内可以有 N 个程序实例，它们是同一个候选的 N 份副本。

**`ProgramHandle` 不是第二个 `execute`，它是 adapter 的远程存根。** 正确的心智模型是 RPC 的 client stub——`handle.execute` 就是*你写的那个* `adapter.execute`，只是可能隔着一个进程边界。`SubprocessBackend` 下它序列化 inputs、发 JSON-RPC、由 worker 进程里的 adapter 实例执行用户代码、再把结果拿回来；`LocalBackend` 下这中间什么都没有，`launch` 只做一次 `from_snapshot`，把重建出的 adapter 本身还回去。规则一句话：**句柄是 adapter 中必须在边界内侧执行的那个子集，方法名与签名逐字相同**；要往 handle 上加东西，先问它在 adapter 上叫什么，adapter 上没有的就不该出现在 handle 上。

**接线规则**：`Evaluator.evaluate` 只认 `ProgramHandle`，所以 Evaluator 的契约与实现完全不感知隔离的存在。而 `ProgramAdapter` *结构上就满足*该协议（两侧逐字同签名），`LocalBackend.launch` 又只做 `from_snapshot`、不包装，于是**本地独立评估直接把程序传进去就行，那一行字面上就是 LocalBackend 路径**，不是绕过 Backend（`session(program)` 内部即 `launch(program, await program.take_snapshot(), ctx)`）：

```python
report = await evaluator.evaluate(best, EvalSpec(split="test"))   # 本地：一行，ctx 由框架合成

async with SubprocessBackend().session(best) as handle:           # 要隔离才多这一行
    report = await evaluator.evaluate(handle, EvalSpec(split="test"))
```

代价与收益因此对齐：不需要隔离的人一行都不多写，需要隔离的人多一行换来进程管理。**反过来说，`evaluate(program, spec, backend=...)` 这种写法被明确拒绝**：一是第一个参数会变成「handle 或 program 二选一」；二是生命周期打架——一个 trial 的完整括号是 `launch → validate_trial → evaluate → shutdown`，`evaluate` 只是中间一个阶段，若由它 launch/shutdown，句柄会在它返回时就死，而括号的闭合与记账（引擎的事，不是评估的事）都在其后。**Evaluator 不该持有一个比自己作用域更大的资源。**

**`Backend` 不是第六个用户概念**：它有默认值、不出现在最短路径里，只在需要隔离时作为 `optimize(backend=...)` 的一个参数出现。**`Budget` 用同一套论证放行**：有默认值（`Budget(trials=N)`），最短路径里不出现。

### 两个例子：Backend 在场与不在场

**用户侧的差别只有一个参数，Evaluator 侧一个字都没变。**

**例一 · 常规情况。** 用户完全不知道有 Backend：

```python
class CodingProgram(DictProgramAdapter):
    units = {"instruction": Unit.prompt("Write code for the problem.")}

    async def execute(self, inputs, ctx):
        prompt = f"{self.values['instruction']}\n\n{inputs['question']}"
        return await llm.acomplete(prompt)

best = await PromptVariantOptimizer(adapter=CodingProgram()).optimize(
    evaluator=evaluator, objective=ScalarObjective("pass@1"),
    eval_spec=EvalSpec(split="dev", sample_k=30, seed=42), max_trials=10)

# 框架内部走的是：
#   handle = await backend.launch(adapter, snapshot, ctx)
#                                                  # LocalBackend: return adapter.from_snapshot(snapshot)
#   await evaluator.evaluate(handle, spec, ctx)
#       └── 每条观测: await handle.execute(inputs, item_ctx)
#                     ↑ 就是你写的 CodingProgram.execute，中间没有任何一层
#   await backend.shutdown(handle, ctx)            # LocalBackend: pass
```

这个例子里 Backend 的**全部实现是一次 `from_snapshot` 加一个空函数**。它在调用链里，但不在用户脑子里——和 PyTorch 里 `device` 永远存在、默认是 `cpu` 是同一回事。

**例二 · 必须有 Backend。** 被优化对象是一个 agent harness 的源码目录，每个候选是被改写过的一版源码。本地跑不了有三个具体原因：**模块名冲突**（两个 trial 的 `agent_harness.planner` 没法同时存在于一个解释器，`importlib.reload` 在并发下也救不了）、**cwd 与环境污染**（harness 自己 chdir、写 scratch 文件、设环境变量）、**崩溃传染**（候选代码是 LLM 写的，一个 `sys.exit()` 就带走整个 run）。

```python
class HarnessProgram(ProgramAdapter):
    def register_units(self):
        return [Unit.file("agent_harness/planner.py"),
                Unit.file("agent_harness/tools.py")]

    async def prepare_workspace(self, snapshot, ctx):  # 这一版源码物化到 trial 沙箱
        write_tree(ctx.workspace, snapshot.unit_values)  # v4.8 · 在宿主侧、由可信的 run 级 adapter 执行：
                                                       #   只写文件，不为此 from_snapshot、不 import 候选代码

    async def execute(self, inputs, ctx):            # ← 在 worker 进程里执行
        from agent_harness.run import solve          #   import 到的是本进程 workspace 那一版
        return await solve(inputs["task"], scratch=ctx.item_dir)

    # take_snapshot / merge_changes / from_snapshot 略。注意：一行进程管理都没有。

best = await HarnessEvolver(adapter=HarnessProgram()).optimize(
    evaluator=evaluator, objective=ScalarObjective("solve_rate"),
    backend=SubprocessBackend(isolation="item"),     # ← 与例一唯一的差别
    budget=CostBudget(usd=50))

# 框架内部（父进程）：
#   await adapter.prepare_workspace(snapshot, ctx) # 源码落到 trials/7/workspace/（只写文件）
#   handle = await backend.launch(adapter, snapshot, ctx)
#       └── spawn: python -m engine.worker, cwd=trials/7/workspace
#           worker 内: HarnessProgram.from_snapshot(snapshot)  ← 边界内侧重建 adapter
#   await evaluator.evaluate(handle, spec, ctx)    # ← 与例一逐字相同的代码
#       └── handle.execute(inputs, item_ctx)
#             父: {"m":"execute", "inputs":…, "item":{"key":…, "dir":…}}
#             子: isolation="item" → 新沙箱 → adapter.execute(...) → 回传 JSON
#   await backend.shutdown(handle, ctx)            # 先把沙箱内 workspace 子树同步回
#                                                  #   trials/7/workspace（与每观测的 item_dir），
#                                                  #   再杀进程、回收沙箱。trial_dir 本身永不删除
```

### 为什么是两个概念而不是一个

常见的追问是：把 handle 去掉，让 `Backend` 直接提供 `execute(program, inputs, ctx)` 不就少一个概念？会立刻长出四个问题。**Backend 被迫变成有状态的注册表**——子进程是 per-trial 的、`execute` 是 per-observation 的，它内部必须维护 `trial_id → 进程` 并自己加锁，于是还得加 `open_trial()`，而那个返回值就是 handle，只是形状更差。**生命周期从显式变隐式**——`launch … shutdown` 是一对括号，异常路径上有明确的关闭点；注册表泄漏一个子进程不会有任何人报错。**例一的零成本性质会丢**——`return adapter.from_snapshot(snapshot)` 之所以可能，正因为它返回的是一个东西而不是转发一次调用。**两个数量级不同的东西被绑死**——Backend 是策略/工厂（一个 run 一个，用户配置），handle 是实例（一个 trial 一个，甚至一个观测一个，框架持有）；把实例方法挂在工厂上，就得靠参数把实例身份传回去，正是 §6 禁掉的偷渡写法的变体。

反过来看这一对，各自去掉复杂度会落到谁头上：**去掉 `Backend`**，隔离逻辑回到 adapter，每个带副作用的程序作者自己重新发明一遍进程管理；**去掉 `ProgramHandle`**，生命周期回到 Backend 的隐式注册表，框架作者写锁、写泄漏。两者因不同的理由各自成立。

| 概念 | 回答什么 | PyTorch 锚点 | 谁写 |
|---|---|---|---|
| `ProgramAdapter` | 程序是什么、哪些部分可优化 | `nn.Module` | 用户 |
| `Backend` | 程序在哪跑、副作用关在哪 | `device` | 用现成的，默认 `LocalBackend` |
| `ProgramHandle` | 这一个 trial 的那个程序实例 | `module.to(device)` 的返回值 | 框架内部，用户不碰 |

**顺带划清一条常被混淆的界**：memory 那一族（ReasoningBank / MemRL）既不是 Backend 解决的，也不是 adapter 解决的。`MEMORY` unit 只声明「这块状态可优化」；*怎么从上一轮的轨迹里长出下一版 bank* 是算法，落在 `propose`，在 `LocalBackend` 下完全成立。v4.8 补另一半：*程序边跑边写 bank*（在线学习）不再需要「跑一个 `changes=[]` 的 trial、下一轮再从 workspace 捞出来」这种绕法，它是一次 produce 记录，由 `StreamProducer` 驱动、句柄关闭前捕获学到的状态。**adapter 声明状态是什么，optimizer 决定下一步做什么（直接给 changes，或派一次生产），Producer 驱动一次生产，Backend 决定程序在哪跑。**

### 生产侧

v4.8 新增。回答一个 v4.7 刻意没有承接的问题：**在版本 A 上运行一段程序，产出版本 B**——改进器修改目标（DGM / Hyperagents 一类的自修改、AIDE² 一类的外层改内层）、学习者边跑边更新自己（在线 memory / skill）、训练器从 checkpoint 训出新 checkpoint。三者在引擎眼里是同一件事：一条 `kind="produce"` 的执行记录。

```python
class Producer(ABC):                           # 与 Evaluator 对称：Evaluator 驱动一次测量，Producer 驱动一次生产
    async def run_produce(self, program: ProgramHandle, inputs: Any,
                          spec: EvalSpec | None, ctx: TrialContext) -> ProduceResult: ...
                                               # program 是执行者的句柄；目标在 ctx.snapshot，执行者在 ctx.executor
    def describe(self) -> dict: return {}      # 进执行记录的 producer 字段，恢复时据此核对绑定
    coordinator: ClassVar[bool] = False        # 同 Evaluator（§3「嵌套」）

@dataclass
class ProduceResult(EvalResult):               # 就是一个 EvalResult，多一个 outputs：
    outputs: list[DeltaOutput | SnapshotOutput]  #   0..n 个，**针对 ctx.snapshot（目标）**
    # 继承来的 metrics 是过程指标（生成器表现、学习曲线终值……），不评价任何输出；
    #   items / feedback / report_path 与测量同一套落盘、剥离与可见性规则。
    #   于是 TrialOutcome、HistoryView.result、observe 的签名都不必为生产另开一条路。

@dataclass
class DeltaOutput:                             # 增量形态：最常见，改进器 / 训练器都用它
    changes: list[UnitChange]                  #   基底恒为本次记录的 snapshot_id（目标输入版本）
    name: str | None = None

@dataclass
class SnapshotOutput:                          # 完整形态：只有改 program_config、增删 unit、换程序形态时才需要
    snapshot: Snapshot
    name: str | None = None
```

**输出接纳：两个入口，一条提交路径。** 完整快照可能改动 `program_config`、增删 unit，无法无损转成对现有 unit 的 replace，所以不硬塞进 `merge_changes`：

```
DeltaOutput    → target_adapter.merge_changes(snapshot, changes) + unit / operation 强校验 ┐
SnapshotOutput → target_adapter.validate_snapshot(output, snapshot)                       ├→ 规范化 → 计算 id →
                                                                                          │  持久化内容 → 一次性提交
                                                                                          ┘  outputs（stage="accept"）
```

两条分支共享的是：可写范围检查、身份计算、持久化与发布。**「能解析成 JSON」不等于「允许改这些状态」**——完整快照同样要过可写范围，范围由目标 adapter 声明。T1 的部分输出政策是**全有或全无**：任一输出不合法，本次记录 `failed`（`stage="accept"`、`failure_class="candidate"`），一个都不提交；原始产物作为诊断 artifact 保留，供下一轮提案参考。

**两个标准实现，T1 先做第一个：**

| 实现 | 执行者 | 做什么 | 典型方法 |
|---|---|---|---|
| `CallProducer(capture="auto", parse_fn=None)` | 任意（可以 ≠ 目标） | 以 `inputs` 调用一次执行者的 `execute`（`ItemContext` 的 key 为合成的 `("__call__", 0)`）。输出的捕获方式二选一：`capture="return"` —— 返回值（经 `parse_fn`）解析为输出列表；`capture="snapshot"` —— 调用后经句柄 `take_snapshot()` 得到改过的自己，作为一个 `SnapshotOutput`，只允许执行者即目标。`"auto"` = 执行者即目标时取 snapshot，否则取 return；解析后的模式写进记录的 `producer.describe` | 改进器 G2 修改目标 A7（return）；DGM 式自修改（snapshot）；训练器（`Backend` 对接训练服务，返回 checkpoint 的 `ArtifactRef` 作为 model unit 的新值） |
| `StreamProducer(data, scorer, *, input_fn=None, output_fn=None)` | **必须就是目标本身** | 按记录的 `eval_spec`（必须非空）确定的顺序、单实例逐条执行：`execute` → 打分 → `learn(feedback)`，严格串行；结束时 `take_snapshot()`，以目标为基底产出一个 `SnapshotOutput` | 在线 memory / skill 学习、prequential 交互 |

凡经 `take_snapshot()` 捕获输出的（`CallProducer` 的 snapshot 模式、`StreamProducer`），执行者都必须就是被更新对象，是为了杜绝一个隐蔽错误：对 G2 的句柄调用 `take_snapshot()`，再把结果登记成 A7 的后代。跨对象的有状态生产（G2 一边跑一边改 A7 的内部状态）留给自定义 `Producer`。

**约束（T0 定语义，T1 实现）：**

- **独占实例。** 生产不复用 `DataEvaluator`「N 条观测共享同一实例并发执行」的路径；执行者内部怎么并行是它自己的事。`isolation="item"` 下的生产在 T1 明确拒绝——多实例的状态怎么合并、合并失败怎么办都没有定义，留到 T2。
- **捕获发生在句柄关闭之前**，在实例所属的作用域里。`shutdown` 之后状态已不存在。
- **`repeat` 仍只表示独立的重复测量。** 它不被解释成训练轮数或连续学习的遍数；`StreamProducer` 收到 `repeat > 1` 的 spec 时拒绝。要比较改进器的稳定性，就从同一起点独立地派多次生产。
- **顺序要留痕。** `StreamProducer` 把实际消费顺序写进 report（逐步一行）与 `stats`；T1 只保证「整次生产完成后的快照」，不支持流的中途恢复。
- **产出 ≠ 激活。** B8 被保存，不代表后续会用它。激活是下一次提交时**选哪个快照当执行者 / 当基底**，在提交那一刻固定、记进新记录的 `executor_snapshot_id` / `source_snapshot_id`；在途请求保持它们提交时的版本。不需要命名绑定服务——「新改进机制被后续调用实际使用」直接是一条可查询的记录事实。
- **元评价是测量，不是生产。** 「改进器 G 有多好」的独立评价，是把 G 当程序、其 `execute` 跑一次内层优化、外层 `Evaluator` 测它交付的结果——一条普通的 measure 记录（往往需要 `coordinator=True`，§3「嵌套」）。生产记录自带的过程 metrics 不冒充改进能力。

**职责边界不变**：Evaluator 只测量；Producer 只驱动生产；Adapter 解释状态与变更、声明可写范围；Backend 决定在哪跑、管句柄与恢复；引擎负责记录、校验、接纳与提交。**Producer 永不直接写快照存储，也不推进策略状态**——它交回输出，接不接纳、怎么用由引擎与策略决定。

### 算法侧

```python
class Optimizer(ABC):
    supported_unit_types: ClassVar[frozenset]  # 本算法能改哪几类 unit。元素是字符串，
                                               #   第三方 unit 类型因此同样受这道校验，
                                               #   不必伪装成 field 再靠 metadata 二次过滤
    required_specs: ClassVar[frozenset[str]] = frozenset({"val"})
                                               # 本算法需要哪几个**角色**的数据。
                                               #   与 supported_unit_types 同层：声明式 + 引擎前置校验。
                                               #   MIPRO: {"train", "val"}；带回归集的: {"train", "val", "regression"}
    required_producers: ClassVar[frozenset[str]] = frozenset()
                                               # v4.8 · 本算法需要哪几个**生产角色**（如 {"improve"}）。
                                               #   与 required_specs 同一模式：角色由算法声明，实现由用户在
                                               #   optimize(producers=...) 绑定，引擎在第一个请求前校验

    def __init__(self, adapter, **algo_config): ...   # 算法参数一律在这里

    # 核心三钩子。所有钩子一个名字，def 或 async def 皆可——框架检测并 await，
    # 不再有成对的 async_* 变体。三者都收 RunContext。
    # v4.8 · 对同一个策略状态，三个钩子**串行**调用（单写者）；昂贵的执行并发，钩子不并发。
    #   何时调用 observe、一次交付几条，由调度预设决定（§3「调度」），钩子签名不变。
    propose(state, objective, ctx: RunContext) -> Proposal | list[Proposal]
    observe(state, outcomes: list[TrialOutcome], objective, ctx: RunContext)
    should_stop(state, objective, ctx: RunContext) -> bool

    # 引擎入口（async；同步脚本里写 asyncio.run(opt.optimize(...))）。返回 ProgramAdapter —— 不是
    #   ProgramHandle：句柄是 trial 作用域资源，optimize 返回时对应的 backend.shutdown 早已执行。
    #   返回的 adapter 结构上满足 ProgramHandle，故可直接喂给 evaluator.evaluate。
    async def optimize(self, evaluator, objective, *, specs | eval_spec,
                       budget | max_trials, backend=LocalBackend(),
                       producers: dict[str, Producer] = {},     # v4.8 · 生产角色 → 实现
                       schedule: Schedule = Barrier(),          # v4.8 · Barrier() | Window(max_in_flight=k)；
                                                                #   停止语义也在这里：on_stop="drain" | "cancel"
                       select_on: str = "val",                  # v4.8 · 选择协议锚定的数据角色（§2 比较侧）
                       visibility: dict[str, str] = {},         # v4.8 · 角色 → "full" | "summary" | "none"
                       save_dir=None, resume_from=None) -> ProgramAdapter: ...

    # 状态与产出（有默认实现）：
    serialize_state() / load_state()           # 活对象（GP、study、RNG）⇄ 纯 JSON
    retained_snapshot_ids(state)               # 活跃集合与内存缓存的保留策略。
                                               #   v4.8 · 它只决定「还在不在考虑范围、要不要常驻内存」，
                                               #   永不删除已持久化的快照与证据——退出 archive ≠ 删除事实
    replay_projection(outcome) -> TrialOutcome | None
                                               # v4.8 · 恢复后重放 observe 要保留什么：输入是证据视图下的 outcome，
                                               #   返回**同类型**的裁剪版（默认 = 原样，即视图允许的全部）；
                                               #   None = 声明本算法不可重放（§3「恢复」）。编码归引擎：带版本的
                                               #   outcome 编码按结果类型往返（EvalResult / ProduceResult，含 outputs），
                                               #   原文字段按 report 同一套 JSON 规则，不可序列化值降级为 repr 并置位。
                                               #   重放时 observe 收到的就是解码后的对象——依赖活对象的算法在这里
                                               #   把它换成可序列化的形式
    finalize(state, objective, best) -> Any    # 产出不是「单个最优」时覆写（archive/front/库）；
                                               #   覆写后 optimize 的返回类型由算法定

class Proposal(BaseModel):
    source_snapshot_id: str                    # 目标基底
    changes: list[UnitChange]
    eval_spec: EvalSpec | None = None          # 本 trial 用什么评，必须派生自 ctx.specs（role 随之而来）。
                                               #   None：measure 取 select_on 角色的基准 spec；produce 表示不读数据。
                                               #   minibatch / 全量复评 / bootstrap 由此表达
    kind: str = "measure"                      # v4.8 · "measure" | "produce"
    executor_snapshot_id: str | None = None    # v4.8 · produce：谁来跑；None = 目标自己跑（自修改 / 在线学习）
    producer: str | None = None                # v4.8 · produce：生产角色名，提交时解析为固定实现 + describe
    inputs: Any = None                         # v4.8 · produce：交给 Producer 的 JSON 载荷（大值走 ArtifactRef）
    metadata: dict = {}                        # 声明性来由：parent ids、reference_snapshot_ids、理由……
    # v4.8 · 校验：kind="measure" 时后三项必须为空（测量的执行者就是目标）；kind="produce" 时 producer 必填

TrialOutcome = (record: RecordView, result: ResultView | None)  # 未产生结果的记录为 None；
                                               #   produce 记录的 result 是 ProduceResult（EvalResult 的子类）。
                                               #   v4.8 · 标注是只读视图协议，TrialRecord / EvalResult 本身就满足它：
                                               #   宿主侧（Budget.settle、Objective）拿到原对象，observe 拿到按可见性
                                               #   构造的视图（§3「证据视图」）——同一个类型，字段名不变

# 算法配置一律走 __init__。optimize() 不再有 **kwargs 扇出：
# 引擎参数是显式签名，跨层信息走三层上下文。
# 预算与终止：optimize(budget=Budget(...))；max_trials=N 是 Budget(trials=N) 的糖。
# 数据角色：optimize(specs={"train": ..., "val": ...})；eval_spec=X 是 {"val": X} 的糖。
# v4.8 · 新增的四个参数全有默认值（Barrier + drain、select_on="val"、算法声明的角色全部 full），
#   §1 最短路径一个字不用改。行为上有三处有意的差别：best 只在选择协议内产生、预算先预留后派发、
#   恢复按 §3 的语义处理在途执行。
```

**一个改进器自修改的最小例子（v4.8）**——改进器与任务程序同在一个快照里，`propose` 只决定「让谁去改谁」：

```python
class SelfImprover(Optimizer):
    required_producers = frozenset({"improve"})

    def propose(self, state, objective, ctx):
        if state.to_measure:                                 # 先测上一轮产出的新版本
            return [Proposal(source_snapshot_id=s) for s in state.pop_all_to_measure()]
        parent = self.pick_parent(state)                     # archive / 父代选择：纯策略
        return Proposal(source_snapshot_id=parent, kind="produce",
                        executor_snapshot_id=None,           # 自修改：目标自己跑自己的改进入口
                        producer="improve",
                        inputs={"task": "improve yourself", "evidence": self.digest(state)})

    def observe(self, state, outcomes, objective, ctx):
        for o in outcomes:
            if o.record.kind == "produce":                   # 产出的新版本 → 再派一次 measure
                state.to_measure += o.record.outputs
            else:
                state.archive.update(o.record)               # 入档规则：纯策略

# 用户侧：
await SelfImprover(adapter=AgentRepo()).optimize(
    evaluator=evaluator, objective=ScalarObjective("solve_rate"),
    producers={"improve": CallProducer()},                   # 执行者即目标 → capture 自动取 snapshot：
                                                             #   以 inputs 调一次 execute，再捕获改过的自己
    backend=SubprocessBackend(), budget=CostBudget(usd=50))
```

谱系不靠算法自报：每个新版本的 `producers_of` 指向那条 produce 记录，记录里写着目标、实际执行者和请求载荷。改成 Hyperagents 式「元 agent 改任务 agent」只需把 `executor_snapshot_id` 指向元 agent 所在的快照；改成 Promptbreeder 式「变异 prompt 本身也被演化」不需要 produce——变异 prompt 是一个 unit，宿主侧 `propose` 读它、用它，引擎什么都不用知道。

**数据角色是一张表，不是一个 spec。** `optimize(eval_spec=...)` 只能表达一份数据，可算法要的份数各不相同：多数 prompt 优化器只需 val；MIPRO 要 train（bootstrap demo、data-aware proposer）+ val（minibatch 筛选与全量复评）；带回归防护的算法还要第三份 regression。一个参数装不下三个角色，`Proposal.eval_spec` 也补不了——它表达的是「本 trial 的偏离」，偏离得有个基准。

```python
class RunContext:                              # （承前，本节新增一个字段）
    specs: dict[str, EvalSpec]                 # 角色 → 基准 spec。propose 从这里取，
                                               #   model_copy(update=...) 出本 trial 的 Proposal.eval_spec

await opt.optimize(
    evaluator=evaluator,
    specs={"train": EvalSpec(split="train", sample_k=32, seed=0),
           "val":   EvalSpec(split="dev")},
    objective=..., max_trials=10)

# propose 内部：
spec = ctx.specs["train"].model_copy(update={"example_ids": failed_ids})
return Proposal(source_snapshot_id=..., changes=[...], eval_spec=spec)
```

引擎在 trial 0 之前校验 `required_specs ⊆ specs.keys()`，缺了就报「MIPRO 需要角色 `train`，`specs` 里只有 `val`」。

被否掉的写法是**「删掉 `eval_spec`，让 optimizer 内部查 evaluator 有没有它要的集合、评估时自己造 spec」**。诊断（一个 spec 是错误的基数）对，药开重了，三处会坏：

| 坏在哪 | 说明 |
|---|---|
| 降级一个用户概念 | `EvalSpec` 是 §1 五个概念之一（锚点「batch / 验证集选择」）。从入口删掉，它就成了内部细节 |
| 扇出从一处挪到 N 处 | `sample_k / seed / repeat` 是**用户的**旋钮不是算法常量。用户要调 minibatch 大小只能走算法的 `__init__`，于是每个 optimizer 各自长出一套同名参数 |
| split 名字硬编码进算法 | 「查 evaluator 有没有它要的集合」查的是哪个名字？`"train"`？数据源叫 `"validation"` / `"heldout"` 的多的是。而 `FunctionEvaluator` 根本没有 split 可查，这条校验在那儿直接失效 |

角色→spec 的映射把这三条一起解掉：**角色由算法声明，名字由用户绑定**；`sample_k` 之类留在用户手里；`FunctionEvaluator` 场景下 `specs` 照样是一张普通的表，校验依然成立（它只查键，不查数据源）。而校验发生在 run 之前，不是 MIPRO 跑到第 7 个 trial 进 bootstrap 阶段才炸——最贵的那种失败被挪到了最便宜的时刻。

`optimize(eval_spec=X)` 保留为 `specs={"val": X}` 的糖，§1 最短路径一个字不用改。

**为什么三个钩子都要收 `ctx`。** `propose(state, objective)` 隐含一个假设：优化器是纯算法，不消耗资源、不产生可观测行为。对 BO 与演化算法成立；但本框架瞄准的主线场景里，`propose` 内部就是一次或多次 LLM 调用——诊断失败样本、写提案、合成代码。这样的优化器若拿不到 ctx，就**发不了事件、报不了自己的 token 开销、写不了中间产物、拿不到并发配额、也接不到取消信号**：§0 判据四的「花了多少钱」在提案侧完全落空，而提案侧的开销常常与评估侧同量级。这不是子类能补的——子类拿不到引擎从没递给它的东西。

**预算不是一个 int。** `max_trials: int` 把计量单位钉死成「trial 计数」，并把「失败或无效的 trial 算不算数」这个策略问题变成引擎的沉默默认。真实预算是 token、金额、墙钟；anytime 算法要「跑到没提升为止」；成本感知的算法要按剩余预算决定策略；而「基础设施故障导致的失败 trial 该不该消耗轮次」是任何带 agent 评估的场景都会立刻撞上的需求。`should_stop` 补不了这个洞——它是算法侧钩子，管不了引擎的硬上限。

```python
class Budget(ABC):                             # 计量什么 · 失败算不算账 · 什么时候停
    # ── v4.8 · 派发前预留，完成后结算 ─────────────────────────────────
    reserve(self, pending: PendingSummary) -> Reservation | Refusal
                                               # 以 record_id 为幂等键：同一记录重复预留返回原预留。
                                               #   拒绝带结构化原因（额度不足 / 估计缺失 / 组合中某一项拒绝）。
                                               #   拒绝 = 「现在不准入」，不是「停止」：在途执行结算或释放后额度可能
                                               #   回来（§3「调度 · 停止」）。v4.7 只事后计量——剩一次额度时并发派出
                                               #   4 个请求，最终会计 4 次；预留把「不再超发」变成机制
    settle(self, outcome: TrialOutcome, note: ProgressNote) -> None
                                               # 原 charge_trial：记录到达终态时结算。note 见下方 AnytimeBudget
    release(self, record_id: str) -> None      # 取消**确认后**释放未消耗的部分；已发生的费用不撤销。
                                               #   unknown 状态的记录不释放预留，直到核对完成
    charge_cost(self, kind: str, amount: float, *, unit: str, event_id: str) -> None
                                               # **引擎面**：只由 EventEmitter.cost 转发进来。
                                               #   用户与算法作者永不直接调用（也拿不到 Budget 本体）。
                                               #   按 event_id 去重；已处理 id 进 budget_state
    exhausted(self) -> bool                    # 「永久耗尽」：已结算的消耗达到上限，无论在途怎样结算都不会再有额度。
                                               #   与 reserve 被拒是两回事——后者只阻止准入。耗尽触发停止，
                                               #   但**不等于**「在途执行必须结束」——那由调度预设的 on_stop 决定
    view(self) -> BudgetView                   # 只读投影 → RunContext.budget
    serialize_state() -> dict / load_state(d)  # 与 Optimizer 同层的持久化对，纯 JSON。
                                               #   → state.json 的 budget_state（含预留表与已处理 event_id）。
                                               #   不可从 TrialRecord 重放：propose 侧开销不在其中，
                                               #   AnytimeBudget 的 patience 计数也不在

ProgressNote = {"eligible": bool,              # v4.8 · 这条记录是否属于选择协议那一组（§2 比较侧）
                "progress": "improved" | "unchanged" | "neutral"}
                                               #   由选择策略（Objective.eligible / progress）判定，引擎转交；
                                               #   Budget 不自己比分数

# TrialBudget(trials=N)：默认实现。准入：「已预留未结算 + 已结算计账」达到 N 即拒绝预留，因此不会超发；
#   耗尽：「已结算计账」达到 N。两条判据分开——预留占满只是暂不准入，不会把已获准的在途执行取消掉。
#   v4.8 · 计账判据改为 failure_class：status=="ok"、或 failure_class ∈ {candidate, unknown} 计账；
#   failure_class=="infra" 不计（基础设施故障不该由候选买单）。v4.7 的「有 stage 即候选之过」退役——
#   scorer 背后的 judge 服务超时同样落在 stage="scorer"，stage 只说明坏在哪一步，不说明是谁的错。
#   unknown 计账是保守默认：归因不明时宁可多计，换一个不需要回头修正的账本。
#   item 级混合故障（部分观测 infra、部分 candidate）的计账由子类覆写 settle 决定。
#   optimize(max_trials=N) 即 TrialBudget(trials=N)。
# CostBudget(usd=..) / TokenBudget(..) / WallClockBudget(..)：换计量单位。
#   预留用请求的估计值；只有估计值、没有执行端的硬约束时，它是**软预算**，文档如实写明可能的超额。
#   WallClockBudget 不把并行子任务的时长相加。
# AnytimeBudget(patience=..)：按「多少轮没提升」终止，需要读 state，因此也是 Budget。
#   v4.8 · 只消费 ProgressNote：eligible 的记录里 improved 清零、unchanged 加一，neutral（组内
#   首条、无可比对象）不动；非 eligible 一律不动。v4.7 的实现自行按 (split, sample_k, repeat)
#   分组比较，却让所有组共用一个 _stale——train 组第二条记录起的任何提升都会清零 val 攒下的
#   patience，与它自己的承诺相反；而且它够不到 Objective，Pareto / 约束目标下的「提升」它判断不了。
#   现在比较归选择策略、计数归 Budget，各有唯一 owner。
# AllOf([...])：组合，任一耗尽即停。v4.8 · 预留是**原子的**：任一子预算拒绝，已成功的子预留全部回滚。
#
# v4.8 · 嵌套：子 run 的 Budget 是父 run 预留出来的一块额度（以「协调者的 record_id + 子 run 序号」幂等预留）。
#   同一个成本事件沿作用域链到达每一层 Budget，各层按 event_id 只计一次；父级显示的汇总不再上报。
#   「一个子 run 折合父 run 几个 trial」这类逻辑额度由预算策略决定，不由引擎假设。
```

**`Budget` 与 `should_stop` 不互相替代，它们回答两个不同的问题**：前者说「资源用完了」，后者说「算法认为没必要继续了」。引擎在二者任一成立时停止（另一个触发条件是「预留被拒且没有任何在途执行」，见 §3「调度 · 停止」），判定点在 §3 流程的 `Budget` 计账之后。这也解释了为什么 `should_stop` 收 `ctx`：它需要 `ctx.budget` 才能做出「剩余预算不够再跑一批全量复评了，收手」这类判断。

### 比较侧

```python
class Objective(ABC):
    # 入参是 TrialRecord，不是裸 metrics dict。
    def is_better(self, a: TrialRecord, b: TrialRecord) -> bool: ...

    # v4.8 · 资格：哪些记录参与比较。引擎只做一层硬过滤（kind=="measure" 且 status=="ok"——pending /
    #   cancelled / unknown / 生产过程分数永不进入比较），其余由它决定；best 与进展用**同一条**规则。
    #   默认：metrics 非空，且 eval_spec.role 与 measurement_key 都等于选择协议（见下）；协议未绑定时
    #   一律不 eligible。覆写它 = 自定义跨组比较，best 与 ProgressNote 随之一起变。
    def eligible(self, record: TrialRecord, protocol: SelectionProtocol | None) -> bool: ...

    # 有默认实现：在引擎交来的 eligible 记录里归约。其余记录都是诊断证据，不争 best。
    def select_best_trial_record(self, records: list[TrialRecord]) -> TrialRecord | None: ...
    #   覆写它只有一种正当理由：产出根本不是「单个最优」—— 而那通常该写 finalize()。

    # v4.8 · 进展判定，只对 eligible 的记录调用。默认：无 incumbent → neutral；
    #   is_better(new, incumbent) → improved；否则 unchanged。与 eligible 合成 ProgressNote 交给 Budget。
    def progress(self, new: TrialRecord, incumbent: TrialRecord | None) -> str: ...

SelectionProtocol = {"role": str, "measurement_key": str, "anchor": str}   # v4.8 · anchor = 锚定它的 record_id

# ScalarObjective(metric, direction) / ParetoObjective(keys) /
# PenalizedObjective(base, penalties={"reg/traj_div": 0.1})


# ── v4.8 · 测量 manifest 与分组 ──────────────────────────────────────────
# 引擎在**测量条件解析之后**（子集已定、模型版本已返回、backend 已就位）为每条 measure 记录
#   生成 manifest，作为 artifact 落盘，记录里存 measurement_ref。三段如实分开：
#   observed  引擎观测到的：evaluator / scorer 类名、解析后的 eval_spec（含 role）、采样总体身份（见下）、
#             实际样本清单摘要与条数、所用 reference 的规范化摘要（同一批 id 的答案被改也看得出）、
#             backend 类型与配置、包版本；各次打分 ScoreResult.observed 汇总出的实际条件（judge 返回的模型版本等）
#             ——汇总保留差异：同一键出现多个值时全部记下（值 → 次数），不取最后一条
#   declared  组件自述：evaluator.describe()（DataEvaluator 默认含聚合与 on_error 策略、input_fn / output_fn
#             的声明名）、scorer.describe()
#   unknown   拿不到描述的部分（闭包、外部服务）——如实列出，不伪造 hash
#   不叫 fingerprint（§2 程序侧：三个标识各回答一个问题）。
#
# measurement_key = GroupingPolicy(manifest)。分组策略**带名字和版本**，写进 key 本身。
#   默认策略 "default/1" 取 evaluator 与 scorer 的身份（observed + declared）、split、repeat、backend 类型，
#   以及**子集协议 = 采样总体身份 + 抽样规则**。总体是 split，给了 example_ids 时就是这批 id（同时给了
#   sample_k 即「先限定总体、再随机抽」）；总体身份 = 总体 id 清单与其 reference 的规范化摘要，由 DataEvaluator 计算。
#   只有数据源保证不可变（如 MutableDataSource.freeze() 的视图），或给出覆盖总体 id 与 reference 变化的可靠
#   版本标识时才允许缓存，否则每次测量重新计算——摘要对应的数据版本必须与本次实际取样、打分用的一致；
#   数据源给不出时记 unknown（此时只承诺 id 清单变化可检出）。
#   抽样规则 = sample_k（None 即全量）。seed 与实际抽到的清单只进 manifest（取了就每个 minibatch 自成一组）。
#   于是「显式挑几个简单样本」与「全量」永不同组；题池换了、答案改了，同样的 sample_k 也会换 key。
#   不取 role（那是数据扮演的角色，不是测量条件），也不取任何候选侧的东西。
#   注意「与候选无关」只是必要条件：候选可能经 input_fn 或 adapter 描述间接出现在 manifest 里，
#   分组策略要显式排除它们，而不是假定它们不会出现。
#
# 选择协议 = (角色, measurement_key)，连同锚定它的 record_id 写进 run 元数据。optimize(select_on="val")
#   指定角色。judge 实际返回的版本等条件要测过才知道，所以 key 不凭配置算，而在收尾提交时绑定：
#   按**持久化的完成顺序**，第一条满足下列条件的记录把自己的 key 写成协议——kind=="measure"、
#   status=="ok"、eval_spec.role == select_on、eval_spec 等于该角色的基准 spec（不是派生的）、metrics 非空，
#   且测量真的成立：stats.errors < stats.total（DataEvaluator 即至少一条观测打分成功——全部失败时 judge
#   一次都没返回，条件没被观测到；FunctionEvaluator 即 fn 成功返回）。manifest 里有 unknown 段不妨碍绑定——
#   unknown 本身也是 key 的输入。通常就是 baseline；baseline 失败则保持未绑定，等下一条合格记录。
#   绑定与该记录的终态在同一次 checkpoint 里提交，恢复后不重新选锚点；未绑定期间没有 best、进展一律不计。
#   之后同一角色基准 spec 下的测量若得出不同的 key（provider 悄悄换了 judge 版本、数据被改），
#   引擎发 protocol_drift 告警事件——这类测量在默认 Objective 下不 eligible、不会静默抢 best，也不会无声地永远出局。
#   换协议（换 judge、换验证集）= 开新 run；同一 run 内的显式协议切换需要状态转换入口，属 T2。
```

**测量条件记录、不裁决。** v3.2 删 Fidelity、v3.6 删 `DataSource.revision` 时的判断仍然成立：「两个分数能不能比」随算法而变，框架不替谁下判断。v4.8 补的是另一半——**事实必须在场**。当 judge、数据、课程本身也在演化（评估器共演化、自生成课程），事后要区分「变强了」和「评分放松了 / 任务变容易了」，只能靠当时记下的条件；当时没记，以后永远补不回来。所以 manifest 是 T0，而用它做什么（分组、拦截、跨组估计）全是策略。

**比较入口收整条 `TrialRecord`**，因为裸 dict 会截断四样早就存在、比较入口却看不见的信息——这四行在 v4.7 里都不需要新增字段（v4.8 的 `measurement_ref` 只是让第一行不再依赖算法自己往 metadata 里记）：

| 看不到 | 谁会想要 |
|---|---|
| `metadata` / `measurement_ref`（v4.8） | 拦截跨 judge 版本 / 跨模型的比较——v4.8 起条件由引擎写进 manifest，不再依赖算法自己记进 metadata |
| `eval_spec` | **MIPRO 的主路径**：minibatch 分数不该与全量复评分数直接争 best |
| `stats` | 同分时选更便宜的（`cost`）；错误率过半的 trial 不进 best（`errors`） |
| `trial_id` / `snapshot_id` | 同分时偏好更早（更稳）或更晚（更新）的候选 |

这是 §6 那条既有规则的兑现——**跨层信息走类型化对象**。给比较入口传裸 `dict`，与 kwargs 扇出是同一类错误：信息在类型边界上被截断，而截断的位置恰好是唯一一个「策略归算法作者」的地方。两个看起来更小的改法被否掉：*声明「要看 record 就覆写 `select_best_trial_record`」*——自然的覆写点信息不足、够用的覆写点逼人重造轮子并重复 tie-breaking 逻辑；*加第三、第四个参数 `meta_a, meta_b`*——两个平行 dict，正是 §6 禁掉的扇出写法。

> **语义陷阱（v4.8 改写）**：「禁止跨版本比较」用 bool 表达会退化——v1 的 0.8 是 best，v2 的 0.9 返回 `False` ⇒ best 留在 v1；此后每个 v2 候选都拿去和 v1 比，**best 从此永远卡在 v1**。v4.7 给的解法是「按 `(条件组, 分数)` 字典序比，新条件组整体优先」——这也不对：新 judge 更宽松、新子集更容易，都不能凭「新」晋升。v4.8 的默认是**只有一个选择协议**：best 只在它的角色与 `measurement_key` 下产生，其他条件下的测量都是诊断证据。要切换协议（换 judge、换验证集），开一个新 run，把旧 run 的候选当起点在新协议下重测即可；同一 run 内的显式切换要定义协议的状态转换，属 T2。跨组估计、多组各留一个 best，是自定义 `Objective`（覆写 `eligible`）/ `finalize()` 的事，框架不强行用 bool 比较伪造全序。

---

## §3 运行时：单一循环

引擎内部只有一条 async 实现，`optimize()` 本身就是 async 入口（同步脚本里写 `asyncio.run(opt.optimize(...))`）；嵌套不调 `optimize()` 而经 `ctx.run.runs.spawn`，所以永远不会出现嵌套的事件循环。baseline 就是 `trial_id=0` 的普通 measure 记录（以 `select_on` 角色的基准 spec 测量，通常也由它定下选择协议），没有第二条代码路径。

### 一轮优化在做什么

从一个初始快照出发，反复做四件事：**算法提交请求 → 引擎把请求固定成记录、预留额度、派发一次执行 → 执行结果连同版本关系记进历史 → 按调度预设交还算法**。算法从*训练子集*上的测量（分数、逐条反馈、轨迹）判断该改什么，从*验证子集*上的测量判断谁更好；两者的差别只是 `EvalSpec` 不同，不是两条代码路径，也不是两种记录。

**一条记录 = 一个请求的一次执行**，这是全框架唯一的计量单位。它有两种 `kind`：**measure** = 一个候选 × 一份 `EvalSpec` 的一次测量（与 v4.7 的 trial 完全同义）；**produce** = 一次产出新版本的运行（v4.8，§2 生产侧）。二者共用提交、派发、记录、交付、恢复的全部机制，只在「中间那一步交给 `Evaluator` 还是 `Producer`」上分叉。

| 组件 | 只做这件事 | 明确不做 |
|---|---|---|
| `Optimizer` | 决定下一步提交什么请求：改什么（`changes`）、测还是产（`kind`）、谁来跑（`executor_snapshot_id`）、用哪份 `eval_spec`、什么时候收手 | 不执行程序、不打分、不决定谁是 best |
| `TrialExecutor` | 把请求固定成记录、`apply`、备好目录与句柄、驱动一次评估或生产、接纳输出、生成 manifest、记账落盘、维护交付日志 | 不产生 `changes`、不解释 `metrics` |
| `Backend` / `ProgramHandle` | 程序在哪个进程 / 沙箱里跑，副作用关在哪；在途执行的关联、探测与终止 | 不参与状态语义，不决定接纳哪个输出 |
| `ProgramAdapter` | 声明哪些部分可优化与可写范围、快照怎么存取与合并、程序怎么跑一条输入 | 不搜索、不进程管理；measure 时 `execute` 不改自身状态 |
| `Evaluator` | 在给定子集上测量，产出 `metrics` / `feedback` / `items` | 不修改被测程序、不比较候选 |
| `Producer` | 驱动一次生产，交回输出与过程指标 | 不写快照存储、不推进策略状态、不打分 |
| `Scorer` | 单次观测 → 分数 + 评语 + 明细 | 不聚合、不落盘 |
| `Objective` | 哪些记录参与比较；两条 `TrialRecord` 谁更好；一条新记录算不算进展 | 不读 `items`、不产生候选 |

### 版本的两条产生路径

v4.7 的「状态变更只有一个生产者（propose）」在 v4.8 里改写为：**快照只经两条路径产生，两条都留下产生记录。**

1. **启动前的 `apply`**：`propose` 的 `changes` 经 `merge_changes` + 强校验落到目标基底上，得到这条记录的 `snapshot_id`。一条记录只有一次**生效的** `apply`，在派发时（预留之后、启动之前）——measure 与 produce 都一样；恢复时重做的只是尚未提交的纯计算，结果相同。
2. **生产后的接纳**：produce 记录的输出在句柄关闭前捕获，经 §2 生产侧的输出接纳（delta / full 两个入口，一条提交路径）成为新快照，写进 `outputs`。

**测量不改变被测对象**：measure 时 `execute` 只读 `self`，一次评估里的每条观测面对的是同一个候选，`TrialRecord.metrics` 因此严格对应 `snapshot_id`——这一条与 v4.7 完全相同。程序在**测量中**的自我改写（累积 memory、重写源码）仍不进入任何版本；要让运行中学到的东西进入下一版，有两条路：沿用 v4.7 的做法，由 `propose` 从 `items` / `workspace_dir` 读出来转成 `changes`；或者把这次运行声明成 produce（推荐——生成者、请求载荷、过程指标都由引擎记录，而不是由算法自报）。

### 流程

| 谁 | 做什么 |
|---|---|
| **Optimizer** | `propose(state, objective, ctx)`：返回单个或一组 `Proposal`。批内请求从各自的 source 快照分支。提案自身的 LLM 开销经 `ctx.events.cost` 上报，与执行侧同一条账。 |
| **引擎 · 提交** | 对每个 proposal：校验形状（按 `kind`；`eval_spec` 的 `role` 必须是 `ctx.specs` 里的角色、`split` 与该角色基准 spec 一致——不合格即在提交处抛 `ProposalError`：编程错误，fail-fast，不生成记录）；分配 `record_id`；**解析并固定**请求（measure 缺省 `eval_spec` 取 `ctx.specs[select_on]`、`producer` 角色解析为实现 + `describe()`、执行者缺省 = 目标）；写 `status="pending"` 的记录，并在交付日志里**登记它所属的交付组**（`Barrier`：本次 propose 的全部请求一组；`Window`：每条一组）。随后一次 checkpoint 同时提交 {新的 pending 记录, 交付组登记, `optimizer_state`, `budget_state`}——RNG 已推进的提案不会在恢复时丢失，也不会在恢复后被重复提出。 |
| **引擎 · 派发** | 调度预设允许（窗口未满）且 `Budget.reserve` 成功 → `apply` 得到目标输入版本、写入 `snapshot_id`（apply 在预留之后，一批非法请求因此也不会超额计数）→ `limiter.acquire("trial")`（**先于 `backend.launch`**，否则先起 N 个子进程再去排队等 item 令牌）→ 组装 `TrialContext` → 准备 trial_dir / workspace（`prepare_workspace(执行者快照, ctx)`，只写文件）→ 写 `status="running"` 并 checkpoint → `backend.launch(adapter, 执行者快照, ctx)`（启动握手，§2 执行侧）→ `handle.validate_trial`（在边界内侧执行）。apply / prepare 失败则记 `failed`、直接进收尾；launch 抛异常不等于执行已结束（IPC 超时时 worker 可能已登记并在加载候选），同样先按登记 probe / terminate，确认没有存活执行才记 `failed`（`stage="launch"`），确认不了记 `unknown`。trial 令牌持有到 `backend.shutdown` 为止。预留被拒的请求留在队列里（「调度 · 停止」）。**协调者记录例外**（`coordinator=True` 的 Evaluator / Producer）：不取任何池令牌，固定经 `LocalBackend` 在宿主进程启动（§3「嵌套」）。 |
| **Evaluator** | （measure）`spec = record.eval_spec` → 确定性子集 × `repeat` → 并发执行（每次观测带 `ItemContext`）→ 逐条打分 → 流式写 `report.jsonl` → 两级聚合 + 可选 feedback 聚合 → `EvalResult`。 |
| **Producer** | （produce）`run_produce(handle, record.inputs, record.eval_spec, ctx)` → `ProduceResult`；需要捕获执行者状态的，在此调用 `handle.take_snapshot()`（句柄关闭之前）。 |
| **引擎 · 收尾** | `backend.shutdown` → （produce）输出接纳：校验 → 规范化 → 计算 id → 持久化内容 → 一次性提交 `outputs` → （measure）生成测量 manifest、算出 `measurement_key`；选择协议未绑定且本记录合格时绑定之（§2 比较侧）→ 按 `eval_spec.role` 的可见性构造证据视图，对视图求 `replay_projection`、按 outcome 编码持久化 → 剥离 items，落终态 `TrialRecord` → `Objective.eligible` / `progress` 生成 `ProgressNote` → `Budget.settle` → best 交给 `Objective`（只在 eligible 记录内）→ 发 `trial_end` 事件 → **一次** checkpoint 同时提交 {终态记录, 它在交付组里的完成序号（组内全部到达终态即关闭）, 协议绑定, `budget_state`}——完成顺序与终态是同一个事实，不存在「已完成、未入日志」的中间态。 |
| **引擎 · 交付** | 已关闭的交付组按关闭顺序交给 `observe`，这个顺序就是**交付日志**：`Barrier` 的组在本批全部到达终态时关闭；`Window` 的组每条一个、完成即关闭，因此按完成顺序。 |
| **Optimizer** | `observe(state, outcomes, objective, ctx)`：一次消费一个交付组。返回后序列化出新的已提交策略状态，与交付日志的消费游标、`budget_state` 同一次 checkpoint 提交。是否继续提交见「调度 · 停止」。 |

### 调度

**骨架只有一副，预设决定交付时机。** 引擎内核是 submit / complete：请求提交即持久化，执行完成即记录；`propose` / `observe` 什么时候被调用、一次拿到几条结果，由 `optimize(schedule=...)` 的预设决定，钩子签名不变。

| 预设 | 行为 | 适合 |
|---|---|---|
| `Barrier()`（默认） | propose 一批 → 全部到达终态 → observe 整批 → 再 propose。**交付时机与 v4.7 的批同步一致** | 代际进化、群体协同评价、批量 BO、绝大多数 prompt 优化器 |
| `Window(max_in_flight=k, max_queued=None)` | 在途不超过 k；每完成一条就单独交付给 observe；有空位就再 propose | 稳态异步进化（AlphaEvolve / OpenEvolve 一类）、异步后代扩展、长时间训练与短测量混跑、迟到的外部反馈 |

- **单写者**：对同一个策略状态，`propose` / `observe` / `should_stop` 串行调用；昂贵的执行并发。否则两个完成回调会同时改种群和 RNG。**策略状态只在钩子返回时提交**：引擎在钩子返回后 `serialize_state()` 得到一份已提交版本；任何 checkpoint（包括钩子执行期间由别的完成触发的那些）只写最近一份已提交版本，从不序列化钩子正在修改的活对象——否则恢复会拿到「改了一半的状态 + 旧游标」，把同一组再 observe 一次。
- **在途可见**：`ctx.pending` 给出已派发未完成、已提交未派发的请求摘要——异步 BO、提案去重、按剩余资源分配都要看得见它们。
- **回压**：一次 propose 超出窗口的请求以 pending 形式排队（已持久化），队列满则暂停调用 `propose`；批量 propose 不能绕过容量。
- **准入不是停止**：`reserve` 被拒只阻止新的派发，请求留在队列里，等在途执行结算或释放额度；它**不触发** `on_stop`——否则剩两次额度、两条在途时，第一条一完成，「已预留 + 已结算」恰好占满，第二条已获准的执行就会被取消。
- **停止**：由 `should_stop`、`Budget.exhausted()`（永久耗尽）、或「预留被拒且没有任何在途执行可以释放额度」三者之一触发。停止时，未派发的 pending 一律记 `cancelled`（`error="stop"`）并照常进交付组——`Barrier` 那一批因此能关闭、交付，循环不会卡在永远派发不出去的请求上；在途执行按两个预设都带的 `on_stop` 处理：`"drain"`（默认）等它们完成并交付；`"cancel"` 请求取消，取消**被确认**后记 `cancelled` 并释放未消耗额度。停止原因写进 run 元数据与 `run_end` 事件（`should_stop` / `exhausted` / `refused`，后者带 `Refusal` 原因）——「被拒且无在途」不等于预算耗尽，可能只是这批请求太贵。迟到的结果永远归属原请求、原版本，不会被重新解释成新请求的结果。
- **外部驱动（T2）**：把同一套 submit / complete 语义包成 `submit(proposal) -> record_id` / `result(record_id)`（CLI / MCP），让 coding agent 直接当最外层的优化器。它复用这里的记录身份与状态契约，不是两个便利方法——这也是 T0 必须先把记录身份与状态枚举定死的原因。外部提交方需要的幂等键（同一请求重复提交返回原记录）到那时作为 `Proposal` 的可选字段加入，旧记录没有它也无需迁移。

### 恢复

**原子提交的是引用与状态；被引用的内容先落盘。** 每次 checkpoint 原子地发布 {记录表, 交付日志与游标, `optimizer_state`, `budget_state`}；它们引用的请求载荷、输出快照、outcome 编码、测量 manifest 必须在这次发布**之前**已经持久化。于是任何一个已提交的 checkpoint 都是自洽的：不会出现「记录说已完成，但它的输入找不到」。

resume 时按每条记录的状态处理：

| 记录状态 | 恢复动作 |
|---|---|
| `pending`（未派发） | 重新进入派发队列；apply 在派发时才做、是纯函数，重做无副作用；`reserve` 以 `record_id` 幂等，不会重复预留 |
| `running`，进程内执行（`LocalBackend` 的普通记录） | 宿主已死，执行必然随之终止 → `cancelled`（`error="lost"`），释放未消耗额度 |
| `running`，后端可存活于宿主之外 | `backend.probe(record)`：`dead`（含「没有所有权登记」——候选从未加载）→ `cancelled(lost)`；`alive` → `terminate`，确认后 `cancelled(lost)`；无法确认 → `unknown`（保留预留，等待核对；远端对账属 T2） |
| `running`，协调者记录 | 它在宿主内执行，自身必已终止；但它发起的子 run 里可能还有存活的 worker。先对它已登记的每个子 run 做**清理式恢复**：pending 直接记 `cancelled(lost)`；running 按本表探测与终止（子 run 里的协调者记录递归同样处理）；unknown 保留；已完成的记录保留为事实——不派发任何子请求、不调用子 run 的 `observe`，子 run 不续跑。子树内全部在途执行都已确认终止或到达终态 → 协调者记 `cancelled(lost)` 并释放；子树里有 `unknown` → 协调者也记 `unknown`、保留预留 |
| 交付组已关闭、位于消费游标之后 | 用持久化的 outcome 编码还原 outcomes（同类型），**按日志记录的分组与关闭顺序**重新交付——`observe([R1, R2])` 与分两次 `observe([R1])`、`observe([R2])` 对一般算法并不等价 |
| 同上，但算法的 `replay_projection` 返回 None | 明确报错「此算法声明不可重放，无法从该点自动续跑」，不猜、不静默跳过 |

恢复中转为终态的记录（lost、核对完成）与正常完成走同一次收尾提交：终态与它在交付组里的完成序号一起落盘。

两条不变的原则：**引擎不重试**——`cancelled(lost)` 的执行不会被自动重跑，策略在 observe 里看到它、自己决定要不要重新提交（那是一个新请求、新记录）；**不重复计账**——预留按 `record_id` 幂等，成本按 `event_id` 去重。

v4.7 承诺的「可从任意历史点续跑」在 v4.8 里精确地写成：**可从任意已提交的 checkpoint 续跑；策略状态、预算、交付游标三者一致；在途执行要么作为事实被恢复，要么被显式标为 lost / unknown，永不被静默重跑或重复计费。** 「不重复」指**已提交的消费**不重复：崩溃时正在执行的那次 `observe` 会被再调用一次（它的修改从未提交），而钩子自己做的外部副作用不会因为有消费游标就变成 exactly-once。残留窗口与 v4.7 同性质：最后一次 checkpoint 之后、崩溃之前上报的成本会丢失，上界是这段时间里全部未提交的开销——`Window` 下含所有在途执行及其子 run，再加一次 propose。

### 嵌套

元评估（「改进器 G 在固定预算下能把任务程序改到多好」）、harness 组合的预算分配、多层元优化，都要在外层的一次执行里跑一整个内层 `optimize()`。v4.7 下这条路会撞墙：共用 Limiter 时，内层要在持有外层 item 令牌时申请 trial 令牌，debug 模式断言失败、普通模式死锁；各用各的 Limiter，又失去了全局并发上限。v4.8 的规则：

1. **唯一入口**：子 run 只能经 `ctx.run.runs.spawn(...)` 发起，且只能从**协调者记录**发起——声明 `coordinator=True` 的 `Evaluator` / `Producer`。从持有 item 令牌的 `execute` 里调用，立即抛 `NestingRefused`。T1 也不允许 Optimizer 钩子直接发起：钩子没有所属记录，预留键与子目录无处安放，恢复重放 `observe` 时还会再启动一整次子优化；策略要跑子优化，就提交一条协调者记录（钩子直接发起属 T2）。
2. **协调者等待时什么都不持有**：协调者记录固定经 `LocalBackend` 在宿主进程里运行，不占沙箱，**不申请任何池令牌**；它只占自己所在 run 的一个窗口位。T1 中协调者只能是可信代码（固定的元评估逻辑，或数据型改进器）；「沙箱里的不可信改进器自己再发起嵌套」需要子树资源预留，属 T2。
3. **窗口与策略锁按 run 隔离**：每个 run 有自己的在途窗口和单写者锁；子 run 的完成永远不需要父 run 的窗口位或锁。
4. **真实资源池全树共享**：整棵 run 树共用一个 Limiter 实例（§2 上下文侧），trial / item / llm 池只被叶子执行持有。
5. **额度先预留后运行**：`spawn` 以「协调者的 `record_id` + 子 run 序号」为幂等键从父 Budget 预留子额度，并在子 run 开始前把 {子 run id, 序号, 预留} 随父 run 的一次 checkpoint 落盘——恢复时据此找到整棵子树；拿不到额度就立即抛 `BudgetRefused`，由协调者处理（通常让本记录失败，策略在 observe 里看到），**永不排队等待祖先释放**。
6. **身份与传播**：子 run 的目录在父记录的 `trial_dir/runs/<child_run_id>/` 下；子记录带 `parent_scope`；事件作用域沿祖先链延伸；取消向下传播。

**为什么没有环。** 等待只有三种：叶子执行等池令牌、协调者等子 run、策略钩子等交付。持有池令牌的只有叶子执行，而叶子执行从不等待任何别的执行，所以池令牌上没有环；协调者等子 run 时不持有子 run 需要的任何东西（令牌、窗口位、锁、额度），所以父子之间没有环；子 run 的协调者又服从同样的规则，所以任意深度都成立。「子 run 的最小需求不超过总容量」这种静态检查是不够的——需求为 1、容量为 1，也可能正被一个等待它的祖先占着——上面的规则让祖先根本不占。

### 证据视图

held-out 不能靠 split 的名字成立。v4.7 让原始样本、参照答案、预测、反馈全部进入 `observe`，也全部可经 `HistoryView` 读回——这对训练反馈正是需要的，但也意味着「这份数据从未参与选择」无从保证。v4.8 给每个数据角色一个**反馈策略**：

| 策略 | 策略侧（`observe` / `HistoryView` / `PendingView`）能看到什么 |
|---|---|
| `full` | 完整 outcome：metrics、feedback、items（含原文字段）、`report_path`、artifacts |
| `summary` | 只给记录级摘要：`status`、`stage`、`failure_class`、metrics、`stats.total/errors`、`measurement_key`。**不给**任何文本：feedback、items、trace、details、error、traceback、`report_path`、artifacts——这些都可能含有完整的题目或答案 |
| `none` | 只给「这条记录存在、处于什么状态」，连 metrics 都不给；结果只由宿主侧的 `Objective`（门控）、`finalize`、最终报告与 sinks 消费 |

- **默认值不看名字**：按请求的 `eval_spec.role` 施加。算法 `required_specs` 里声明的角色默认 `full`；用户额外提供、算法没有声明的角色默认 `summary`；`optimize(visibility=...)` 显式覆盖。不读数据的生产（spec 为 None）不含任何角色的样本，按 `full` 交付。策略在 run 开始时由宿主固定、写进 run 元数据，算法改不了。
- **允许列表，不是删除列表**：视图按上表从记录中挑出允许的字段构造新对象，而不是从完整对象里删掉几个已知的原文字段——v4.8 草案曾漏掉 `raw_prediction`、trace、traceback，这正是删除列表的通病。
- **视图是运行时只读对象**（`RecordView` / `ResultView`，也是 `TrialOutcome` 的标注类型；`TrialRecord` / `EvalResult` 本身就满足这两个协议）：字段名与 `TrialRecord` / `EvalResult` 相同，允许列表之外的字段访问即抛 `HiddenEvidence`——隐藏是拒绝，不是缺省，所以不拿 None 表达。它们不落盘、不是第二种持久节点；`kind × status` 约束只管持久记录（summary 视图里没有 `measurement_ref`，并不违反它）。
- **统一施加**：`observe` 拿到的 outcomes、`HistoryView` 的 `record / records / result / export`、`PendingView`，以及经它们可达的 artifact 引用，全部经同一个视图构造器。sinks 是宿主侧组件，不受此约束。
- **改进器的证据经请求交付**：改进器该看什么，由策略经 `Proposal.inputs` 显式交给它——证据因此写在请求里、可复现，也同样受策略自身可见性的约束。执行者即使经 `ctx.run.history` 去读，读到的至多也是同一个视图构造器给出的那一份；`SubprocessBackend` 的边界内侧干脆没有 history（允许抛 `NotImplementedError`，§2 执行侧）。
- **它是接口边界，不是安全边界**：同进程的 `LocalBackend` 代码可以直接读文件。需要对抗不可信代码的强隔离时，由声明了 `strong_isolation` 能力的 `Backend` 提供（T2）；T1 只承诺可信扩展之间的接口纪律。

### 规则

- **失败语义统一**：任何记录（含 baseline）失败 → 记录 `error + stage + failure_class + traceback`，run 继续；用户可从 stage 一眼分辨坏在哪一步，从 failure_class 分辨是谁的问题。**失败是否消耗预算由 `Budget` 判定**——「程序答错」与「基础设施故障」在计量上不该同价，而这是策略不是不变量。
- **失败即测量结果，限于真正发生过的测量**：一次已经执行、协议定义了失败含义的观测，失败就是它的测量结果，引擎不替它重试。但 `pending`、`cancelled`、`unknown`、「生产成功但尚未测量」都**不是**零分测量，永不进入比较。
- **产物只增不删**：`save_dir` 下的 `trial_dir` / `workspace` / `item_dir` 由引擎拥有，run 结束后依然完整——引擎与 Backend 都不删除其中任何东西，清理归用户或外部工具。`backend.shutdown` 回收沙箱前先把沙箱内的 workspace 子树同步回来，于是「产物从 `workspace_dir` 读出、由 `propose` 转成 `changes`」这条路在 `SubprocessBackend(isolation="item")` 下同样成立。**这不违反「版本的两条产生路径」**：同步只把字节搬到持久位置，不动任何 `Snapshot`；运行中的 workspace 本身是可变的，它不是版本，要进下一版仍须经 `apply` 或输出接纳。代价是磁盘随 run 单调增长（`isolation="item"` 下按观测数增长），换来的是任何一次观测的现场都可事后复盘。跨机器 / 跨 backend 需要稳定引用的产物走 `ctx.artifacts.put()`——它**摄入内容**，而路径引用出了本机就是悬垂指针。
- **成本单入口、按身份只计一次、记账 checkpoint 粒度**：`ctx.events.cost` 是唯一入口，emitter 扇出到 sink / `Budget` / 作用域链上每个 ledger；ledger 与 Budget 都按 `event_id` 去重，已处理的 id 随 checkpoint 落盘。残留窗口见上文「恢复」。
- **主循环只有一副骨架**：submit → dispatch → execute（measure 或 produce）→ record → deliver → observe，没有任何「大多数 run 里为空」的可选步骤；`Barrier` / `Window` 只改变交付时机，不改变骨架。需要额外 trial 级度量（正则、成本、静态分析、非回归）的场景一律通过装饰 `Evaluator` 表达，主循环一行都不会因此变化。
- **训练子集与验证子集由 `EvalSpec` 表达，不由两条路径表达**：算法要一份诊断信号，就发一个 `changes=[]`、`eval_spec=ctx.specs["train"]` 的 measure——它照常花钱、照常计账、照常进 history，`observe` 从 `result.items` 拿轨迹与逐条反馈；要选优，就发 `eval_spec=ctx.specs["val"]`（或它的派生）的 measure——角色随 spec 走，选择协议与可见性都按它认。多级筛选（minibatch 粗筛 → 全量复评）是算法自己排的请求序列，引擎不认识「阶段」这个概念。
- **best 与进展用同一条资格规则**：引擎只做一层硬过滤（`kind=="measure"`、`status=="ok"`——pending / cancelled / unknown / 生产过程分数永不进入比较），其余由 `Objective.eligible` 决定。默认实现只认选择协议（角色与 key 都匹配）——否则一条训练子集上的高分、一次换了 judge 的测量，都会直接抢走 best 指针。这是*默认策略*不是不变量：要跨组比较就覆写 `eligible`（它拿得到整条 `TrialRecord` 与 manifest 引用），best 与 `ProgressNote` 随之一起变，不会出现「best 跨组选、patience 只按一组算」的错位。
- **并发边界**：执行级并发由引擎管（`acquire("trial")`，覆盖 apply 之后到 shutdown 全程），item 级并发由 Evaluator 管（`acquire("item")`）；两级各有唯一 owner，都走 `ctx.run.limiter`，但**取的是不同的池**。外层持有令牌跨越内层申请，同池即成环，必然死锁。统一要统一在*最下游*——`4 × 8 = 32` 打爆模型端这个老问题，是靠 item 池全局唯一解决的，不是靠跨层共享一个池解决的。嵌套 run 不改变这条规则，只在它上面加一条：协调者不持有任何池令牌（上文「嵌套」）。
- **框架保证的是 item 并发，不是 API 并发**：一个观测内程序自己可能打 k 次模型调用，框架数不到。要真限流，用户程序与 `propose` 自行从 `ctx.run.limiter` 取 `kind="llm"`（`ItemContext.trial.run.limiter` 已经递到手上），或者交给自己的 client 限流。
- **隔离**：apply 是纯函数式契约 + `DictProgramAdapter` 默认实现天然无共享状态；文件型程序每条记录独立 workspace；produce 使用独占实例，永不与测量共享；进程级隔离与候选代码的加载位置归 `Backend`。

---

## §4 记录与观测：三通道各司其职

| 通道 | owner | 内容 | 回答的问题 |
|---|---|---|---|
| `state.json` | 引擎 | `schema_version`；快照（以内容 id 为键的规范化内容，大值走 ArtifactRef）；全部 TrialRecord（含 pending / running 的在途记录与它们的 `execution_ref`）；**交付日志（交付组与完成序号）与消费游标**（v4.8）；run 元数据（选择协议的角色、`measurement_key` 与锚定记录，各角色的可见性策略，`parent_scope`，已登记的子 run）；optimizer_state、budget_state（含预留表与已处理的 `event_id`）。纯 JSON 强校验，原子发布。 | 从任意已提交 checkpoint 续跑（含预算、patience、在途请求）；哪个 candidate 最好、好多少；每个版本由谁、基于什么产生 |
| `trials/<id>/report.jsonl` | Evaluator / Producer | 逐次观测（或逐步生产）流式追加，主键 `(example_id, repeat)`：prediction、metrics、trace、**feedback**、**details**、traceback。`EvalResult.load` 可整体重载复盘。**给人看的**明细，可被 `keep_items` 截断。 | 每条样本发生了什么；**上一版轨迹长什么样** |
| events | Tracker | `run_start / propose_end / trial_start / trial_end / batch_end / checkpoint / run_end`，v4.8 增 `submit / dispatch / deliver / spawn`；载荷含 cost——**由任何持有 `RunContext` 的组件上报，含 `propose` 自身**，替代全局 cost 单例。**尽力交付**：坏 sink 只告警、事件可丢。 | 过程观测；wandb 的接入点；**回归集累积的接入点** |

**v4.8 · 权威记录与观测事件分开。** `state.json`、它引用的 artifact（请求载荷、输出快照、outcome 编码、测量 manifest），以及后端写在 `trial_dir` 里的所有权登记（`execution.json`）是**权威记录**：先持久化内容、再原子发布引用，恢复只依赖它们。events 是**观测流**：给进度条、wandb、CaseHarvester 用，sink 出错时丢弃事件而不拖垮 run——正因为它可丢，任何「恢复需要的事实」（作业提交、预算结算、策略消费到哪）都不能只存在于事件里。

**v4.8 · 目录布局冻结。** `save_dir/` 下 `state.json`、`artifacts/`（内容寻址）、`trials/<trial_id>/{report.jsonl, workspace/, items/, execution.json, outcome.json, manifest.json, runs/<child_run_id>/}` 的布局写进规格、随 `schema_version` 演进（`execution.json` 是后端的所有权登记，`outcome.json` 是重放用的 outcome 编码）。coding agent 型提案者（把全部历史源码、分数、轨迹放进文件系统让它自己查）浏览的是 `ctx.run.history.export(dir)` 物化出的**视图目录**：沿用 `trials/<id>/` 的命名，只含经同一个视图构造器裁剪过的记录、报告与快照，不含 `state.json` 原件（`optimizer_state` / `budget_state`）与未授权的 artifact；它的具体布局 T1 实现时定，不随 `schema_version` 冻结。直接把 `save_dir` 交给它，等于绕过可见性策略。

`report.jsonl` 有第二重身份：它不只是复盘日志，**它是 trajectory reference store**。每一轮的执行轨迹在写下的那一刻就成了下一轮正则化的参照物，代价为零——这是「轨迹对比正则」得以变成便宜操作的全部秘密，不需要任何新的存储机制。它的读取口是通用的 `ctx.run.history`，于是同一个仓库同时服务正则化、archive 采样与跨 trial 统计。

**动态 benchmark 的落位也在这条通道上。** 把某个 agent/工作流的成功或失败输入累积成回归测试集，是 events 的一个消费者，而非引擎逻辑：`CaseHarvester` sink 消费 `trial_end` 事件里的 items，按规则（失败样本 / 首次成功样本 / 高分歧样本）写入 append-only 的 `RegressionSuite(MutableDataSource)`。引擎不知道它存在，纯增量。

默认 Tracker sink 复刻现有终端体验（进度条 + 结束报告），引擎主循环本身不再内嵌任何打印——进度输出也是事件的消费者。wandb / 文件 / TensorBoard / CaseHarvester sink 都是纯增量：实现同一个 sink 接口即可，不触碰引擎。**这是「wandb 那一面」的全部架构承诺：点位现在埋好，昂贵的部分（UI、对比、托管）留给 sink 生态。**

---

## §5 扩展指南：九类算法与 RSI 一族的落位

| 类别 | 判定 | 用到的机制 |
|---|---|---|
| BO 超参 / 模型选择 | ✅ 纯扩展 | MODEL/FIELD units + `metadata["search_space"]` 声明分布；`serialize_state` 存观测历史，活 GP/study 续跑重建。 |
| MIPRO（prompt + demos） | ✅ 纯扩展 | `required_specs={"train","val"}`；minibatch = `ctx.specs["val"]` 派生的 `proposal.eval_spec`；bootstrap = `ctx.specs["train"]` 的 NOOP trial；优胜者全量复评 = 不带 `sample_k` 的 val spec；demo 筛选读 `outcomes[i].result.items` 的 `(inputs, prediction)`；data-aware proposer 读 train trial items 的 `raw_example`；**默认只有与选择协议同 key 的全量复评争 best**（`sample_k` 不同即分组不同，minibatch 分数天然只是诊断证据）；要让二者同台，由自定义 `Objective` 读 `record.eval_spec` 与 manifest 自己判。 |
| Memory（ReasoningBank 等） | ✅ 纯扩展 | MEMORY unit 声明可优化状态；`propose` 从 `outcomes[i].result.items` 蒸馏成功轨迹，产出 `UnitChange(operation="append")`（「只蒸馏成功轨迹」在这里表达）+ `retained_snapshot_ids`；大 bank 走 ArtifactRef。程序在沙箱里写出的 bank 由 `shutdown` 同步回 `workspace` / `item_dir`，`propose` 照常读得到。 |
| Skills（技能库） | ✅ 纯扩展 | CODE/FILE units + workspace 物化 + `validate_trial` 冒烟测试；trial 级并发天然安全；沙箱内新长出的技能经 `shutdown` 同步回 workspace。 |
| Harness 自进化 | ✅ 纯扩展 | FILE units + workspace 物化 + `SubprocessBackend` 隔离执行：harness 即程序，新一版源码由 `propose` 产出，adapter 只声明语义，进程管理零代码；失败 trial 的 stage/traceback 是提案养料。 |
| 工作流结构（AFlow / EvoPrompt / TextGrad） | ✅ 纯扩展 | 图 = 单 unit 整值替换；种群/树 = 执行记录给出的多源谱系 + optimizer_state；**文本反馈从 `ItemResult.feedback` 类型化直取**，题目原文与模型原始输出从同一个 `ItemResult` 的原文四件套直取，`propose` 不持有 `DataSource`。 |
| 正则化 / 抗过拟合研究线 | ✅ 纯扩展 | Evaluator 装饰器 + `ctx.changes` + `ctx.run.history`（report.jsonl 作参照库）；`PenalizedObjective` 组合；回归集由 `CaseHarvester` sink 累积。新度量（正则、成本、静态分析、安全门、逐 case 非回归）= 包一层 Evaluator，**连引擎的扩展点都不占**。 |
| Rubric / LLM-judge 型 benchmark | ✅ 纯扩展 | `get_reference` 返回 rubric；`RubricJudgeScorer` 实现 `score(ScoreRequest)`；逐项进 `details`、总分进 `metrics`、评语进 `feedback`。零框架改动。 |
| 程序形态演化（冷启动探索 / agent 架构搜索 / 评估器协同进化） | ✅ 纯扩展 | `program_config` 分派程序形态 + `fingerprint` 语义放行 resume；探索本身就是 trial 0（轨迹进 items，`propose` 据此合成下一形态）。被评估对象来自另一个 run 的产物时，`evaluate(program, spec, ctx)` 已能从 `ctx.workspace` 构造数据源——无需新概念。v4.8：形态改变（改 `program_config`、增删 unit）由 `SnapshotOutput` + `validate_snapshot` 承接。 |

**RSI 一族（v4.8）。** 下表按「机制形态」而不是按论文组织；方法名只是例子，引擎不认识它们。

| 形态 | 判定 | 用到的机制 |
|---|---|---|
| 数据型改进器演化（变异 prompt / 提案策略配置本身被演化） | ✅ 纯扩展（v4.7 已可） | 改进器配置是一个 unit；宿主侧 `propose` 读当前选中快照里的那个 unit 并据此提案。谁的改进器被用了，由提案时选中的 `source_snapshot_id` 与 `metadata` 声明 |
| 代码型自修改（改进器与任务程序同在一个快照，执行者 = 目标） | ✅ 纯扩展 | `kind="produce"`、`executor_snapshot_id=None`、`CallProducer`；`SubprocessBackend` 隔离；输出经接纳成为新快照，`producers_of` 给出谱系；archive / 父代选择 / 入档规则全在 `optimizer_state`，非最优后代可以继续作为起点 |
| 元改进器修改任务程序（执行者 ≠ 目标） | ✅ 纯扩展 | 同上，`executor_snapshot_id` 指向改进器所在快照；四种关系（目标 / 执行者 / 输出 / 声明参考）各自入账；「改进器的新版本被后续使用」= 后续请求的 `executor_snapshot_id` |
| 在线经验学习（memory / skill / playbook 边跑边长） | ✅ 纯扩展 | `StreamProducer`：单实例按序跑一份数据，结束时 `take_snapshot`；学到的状态与之后的独立 probe 测量分属两条记录；「学到的内容」与「访问策略」是两个 unit |
| 训练 / 适应（SFT、RL、自生成数据训练、测试时训练） | ✅ 纯扩展（T1 本地 / T2 远端） | produce + `CallProducer`，执行者是训练程序，`Backend` 对接训练服务（submit / status / collect / cancel 落在 `execution_ref` / `probe` / `terminate` 上）；输出把 model unit 的值换成新 checkpoint 的 `ArtifactRef`。采样用 checkpoint 与可续训的训练状态是**不同的制品**，各自声明；梯度与损失的数学留在后端 |
| 嵌套元优化（评估「改进器在固定预算下能改到多好」、多层元优化、harness 组合的预算分配） | ✅ 纯扩展（T1 宿主协调；沙箱协调者 T2） | `FunctionEvaluator(coordinator=True)` 在 fn 里 `ctx.run.runs.spawn(...)` 跑内层优化，外层 Evaluator 测内层交付的结果；子 run 预算从父 run 预留；成本沿祖先链只计一次 |
| 评估器 / 课程共演化 | ✅ 纯扩展 | 被演化的 judge / 任务生成器是普通 unit（可以就是 produce 的输出）；**用于选 best 的选择协议固定**，换协议 = 新 run（同一 run 内的显式切换属 T2）；manifest 让「评分放松 / 任务变易」事后可查 |
| 异步稳态搜索（岛屿、MAP-Elites、后代信用、补测） | ✅ 纯扩展 | `Window(max_in_flight=k)`；`ctx.pending` 看在途；补测 = 对已有快照再发 measure（不产生新版本）；后代信用按 `producers_of` 谱系在策略里算 |
| 外部驱动（coding agent 直接当最外层优化器） | T2 | 同一套 submit / complete 语义包成 `submit` / `result`（CLI / MCP）；T0 已定好它依赖的请求身份与状态枚举 |

**边界规则**（比能力清单更重要，v4.8 改写）：框架管的是*执行记录粒度的 submit → execute → record → observe，版本是可快照的程序配置*。**step 级 RL 内循环、梯度与损失的数学不进框架**——它们在训练后端里；框架把一次训练当作一条 produce 记录来调度、记录、计账、恢复，存产物（ArtifactRef）、比结果，不提供 replay buffer 或梯度循环。任何把这条线内移的提案默认拒绝。

同构的三条边界：

- 框架提供*参照物与距离测量的位置*，不提供任何具体的正则语义。KL 取什么形式、轨迹怎么对齐、judge 用什么 prompt、惩罚系数怎么调度，全部在装饰器的实现里。同理，框架提供*反馈的通道*，不规定反馈的格式或语言。
- 框架提供*上下文与计量的位置*，不规定预算语义、隔离粒度、参照策略，**也不规定两个分数能不能放一起比**——花多少算多、失败算不算数、每次观测要不要独立沙箱、参照物对齐到哪一版、换了 judge 或换了模型之后旧分数还算不算数，全部在 `Budget` / `Backend` / `Evaluator` / `Objective` 的实现里。
- 反向的一条：**程序形态可以演化，但演化的粒度仍然是快照**。v4.8 起，在一次执行内部持续在线学习的程序可以作为 produce 记录进入框架——前提是它在执行结束时能被快照定格；细到每个请求都更新、从不形成快照的程序（例如每请求更新权重的在线服务），仍不在承诺范围内，只能由 Backend 托管成有状态服务、按周期产出快照来接入。
- v4.8 补一条：**固定的是实验边界，不是任何一种搜索策略。** 所有 RSI 系统都有一个固定的最外层，但那个最外层在本框架里是「身份、执行、资源、记录」这些契约，而不是 archive、父代选择或接受规则——那些都是策略，可以被替换，甚至可以被放进快照里演化（有工作就让元 agent 改写了父代选择机制）。引擎只保证：无论策略怎么变，记录与计账照旧成立，正在执行的版本不会被悄悄替换。

---

## §6 工程规则

- **三信号分流**：可比较的走 `metrics`、可解读的走 `feedback`、可追溯的走 `items/trace`；每类一个去向（§0.5）。测量结果上的新增字段必须能在那张表里定位；版本来源、执行状态、激活不是评价信号，归下一条。
- **三种身份分立**（v4.8）：版本归 `Snapshot`（内容寻址、不可变、不带父指针），执行归 `TrialRecord`（目标 / 执行者 / 输出 / 声明参考四种关系分开记），测量条件归 manifest（§0.6）。三者互不代替——用父指针冒充生成者、用一次测量冒充一次生产、用 metadata 冒充引擎观测到的条件，都是分层违规。
- **零重复路径**：引擎与 Evaluator 内部均为 async 单实现；用户钩子一个名字、同步异步皆可（框架检测并 await）。禁止再出现成对的 `async_*` 公开方法。
- **零签名嗅探、零 kwargs 扇出**：所有跨层信息走类型化对象——**三层作用域上下文**（`RunContext / TrialContext / ItemContext`）加载荷对象（`EvalSpec / TrialOutcome / ScoreRequest`）；算法配置只在 `__init__`。**算法对数据的需求也走这条路**：`required_specs` 声明角色、`ctx.specs` 供给基准 spec、样本原文随 `ItemResult` 到手——没有一处需要 optimizer 反向去摸 evaluator 的具体类型。
- **上下文即扩展面**：任何新扩展点先回答两个问题——它属于哪一层作用域、谁消费它。上下文里的字段必须有明确消费者，扩展点需要的信息必须在上下文里找得到；两边任一不成立，设计就还没定好。**推论（必须一起执行，否则这条规则只会让框架变胖）**：一个组件如果*只是因为拿不到信息*才独立存在，那么信息补齐之后它就该退役。这条替代了「以后需要什么再加参数」。
- **上下文永不为 `None`**：扩展面不能是可空的——一个「有时候没有」的上下文，等于要求每个实现者各自发明一套缺省行为，是隐式约定的另一种形状。缺省一律表达在*数据*里（空 `changes`、空 `history`、无限 `budget`、无 sink 的 `events`），不表达在*类型*里，更不表达在*路径*里。归一化全库只有一处（`Evaluator.evaluate` 里的 `ephemeral_trial_context`，`Backend.session` 复用同一个）。
- **评估层三档分工**：`Evaluator` ABC 是*引擎面*契约（`TrialExecutor` 只认这个类型），`DataEvaluator` 是*用户面*标准实现（装配槽 + 四个具名钩子），`FunctionEvaluator` 是逃生舱，装饰器是组合层。自定义评估按代价从「换装配槽」往下走；继承 ABC 意味着写一条新执行管线，是框架 / contrib 作者的动作。**一个抽象类如果它的全部价值都在某个具体子类的方法体里，它就不是用户的扩展点。**
- **主路径只放所有 run 都要走的东西**：一个「大多数 run 里为空」的固定步骤，成本是让每个用户都多认一个概念，收益只归少数场景。这类能力一律用*组合*表达（装饰 `Evaluator`、组合 `Objective`、挂 events sink），不进主循环、不占引擎扩展点。
- **版本只有两条产生路径**（v4.8 改写自「状态变更只有一个生产者」）：`propose` 的 `changes` 经 `apply`（派发时、启动前，一条记录只有一次生效的 apply），或 produce 记录的输出经接纳（句柄关闭前捕获，结束后一次性提交）；两条都留下产生记录。measure 时 `execute` 只读 `self`，`Evaluator` 只测量、不修改被测对象——`TrialRecord.metrics` 因此严格对应 `TrialRecord.snapshot_id`。测量中的自我改写不进入任何版本：要它进入下一版，由 `propose` 显式转成 `changes`，或把那次运行声明成 produce。
- **零宿主依赖**：数据模型基于纯 pydantic `BaseModel`（保留 JSON 往返守卫）；日志可注入；cost 走事件；数据源是 `DataSource` Protocol，evoagentx 的 Benchmark 只是一个满足者。
- **持久化即契约**：能进 `state.json` 的只有纯 JSON + ArtifactRef；写入前强校验并报出违规路径。逐条明细一律走 Evaluator 通道。**测量字段的唯一例外**：trial 级 `feedback` 进 state.json 但不参与比较，且带硬长度上限。v4.8 补三条：所有持久对象带 `schema_version`，不认识的版本拒绝续跑而不是猜；**内容先落盘、引用后发布**——checkpoint 原子发布的只是引用与状态；已提交的内容不可原地修改，权威是规范化字节而不是内存里的可变对象。
- **可比性归算法作者，格式层只管能力**：框架*不*表示、不校验、也不拦截「两个分数能不能比」。它只提供一样算法作者事后补不了的东西：**重复观测可表达**（`repeat` 进 `EvalSpec`、观测主键进 `ItemResult`）。数据、judge、执行运行时确实都会漂移，但什么算「同一次测量」随算法而变（successive halving 跨档比、population 跨代比）。要拦截就写自己的 `Objective`——`is_better` 收整条 `TrialRecord`，这条路径是真的存在的。v4.8 补另一半：**判断归算法作者，事实归引擎**——测量条件由引擎写进 manifest（§2 比较侧），不再靠算法记得往 `metadata` 里写；条件当时没记，事后就永远无法区分「变强了」和「评分放松了」。
- **策略不写死成不变量**：预算与终止（`Budget`）、best 的比较规则（`Objective`）、隔离粒度（`Backend`）、聚合与错误处理、参照物解析（均归 `Evaluator`）各有唯一 owner 且可覆写。引擎只保留没有合理替代的不变量——*一条规则如果能想出一个正当的反例，它就该是策略而不是不变量*。
- **语义与执行分离**：`ProgramAdapter` 只声明程序是什么，程序在哪跑归 `Backend`，程序好不好归 `Evaluator`（含正则一类的 trial 级度量），怎么产出新版本归 `Producer`（v4.8），怎么比归 `Objective`。任何在 adapter 里内嵌进程管理、或在 `propose` 里实现度量计算的写法，视为分层违规。
- **失败就是测量结果**（v4.8 限定：只对真正发生过、协议定义了失败含义的测量成立；`pending` / `cancelled` / `unknown` / 尚未测量的生产都不是零分测量）：**引擎不重试观测。** 超时、崩溃、格式错本身就是候选质量的一部分，替它重试等于系统性地给不稳定的候选补分，污染的是比较本身——而比较是引擎唯一的产出；何况引擎自己无从区分「基础设施抖了」与「这一版真的写坏了」——能区分的只有抛错的那一层（见下条 `failure_class`）。要重试的人在自己那一层重（LLM client 重一次调用，比重跑整条轨迹便宜两个数量级）。因此 `report.jsonl` 每个 `ObservationKey` 恰好一行。注意这是**存储**上的要求，不是 exactly-once **执行**保证：观测执行完、行还没写入时崩溃，重启后它会被当作未执行——这正是 §3「恢复」把在途执行显式标为 lost / unknown、而不是假装它们没发生的原因。
- **错误必须可归因**：每个失败带 stage（§2 结果对象的 STAGES）、`failure_class`（candidate / infra / unknown）与完整 traceback。stage 说明坏在哪一步，failure_class 说明是谁的问题，二者正交；`Budget` 按 failure_class 决定计不计账，归因不明（unknown）默认计账。
- **单写者与提交先于派发**（v4.8）：对同一个策略状态，`propose` / `observe` / `should_stop` 串行，策略状态只在钩子返回时提交、checkpoint 只写已提交版本；请求在派发前已经固定并持久化（含解析后的 eval_spec、producer 绑定、执行者），执行时不再解析「当前版本」。在途执行永远绑定它提交时的版本——激活只影响之后提交的请求。
- **等待者不持有**（v4.8）：任何等待其他执行完成的位置（协调者、策略钩子）都不持有那些执行所需的池令牌、窗口位、锁或额度；池令牌只由从不等待的叶子执行持有。新增任何会等待的组件，先证明它满足这一条。
- **证据按允许列表暴露**（v4.8）：策略侧能看到什么由宿主在 run 开始时按角色固定（full / summary / none），视图按允许列表构造；新增任何可能携带样本原文的字段，默认不在 summary 里。
- **命名**：`Snapshot`、`RunContext / TrialContext / ItemContext`、`EvalSpec / EvalResult / ItemResult / TrialRecord / TrialOutcome`、`ObservationKey`、`ScoreRequest / ScoreResult`、`Backend / Budget`；v4.8 增 `Producer / ProduceResult / DeltaOutput / SnapshotOutput / CallProducer / StreamProducer`、`Barrier / Window`、`ScopeRef / PendingView / RunSpawner`、`ProgressNote / SelectionProtocol`、`RecordView / ResultView`；一次执行的身份只叫 `record_id`；不引入第六个用户概念。

---

## §7 实施顺序

1. **冻结契约**：`RunContext / TrialContext / ItemContext`（先于其余，它决定其余的上限）、`ObservationKey / Budget（含 BudgetView 与 serialize_state/load_state）`、`RunContext 的运行时设施协议`、`UnitChange / TrialRecord / TrialFeedback`、`Objective（is_better 收 TrialRecord）`、`EvalSpec（含 repeat）/ Evaluator（evaluate 归一化 + run_eval 抽象）/ ephemeral_trial_context（含全部退化设施）/ EvalResult / ItemResult / DataSource（含 get_reference）/ ScoreRequest / ScoreResult / ArtifactRef / Backend（含 session；`shutdown(handle, ctx)` 的同步语义与 workspace / 沙箱所有权划分）/ ProgramHandle（存根化，execute + validate_trial 两方法——v4.8 扩为四方法，加 take_snapshot / learn——与 adapter 逐字同签名；边界内侧 ctx 为真 ItemContext）/ ProgramAdapter.execute 签名 / MutableDataSource / FeedbackAggregator` + `Proposal.eval_spec` + `RunContext.specs / Optimizer.required_specs` + `ItemResult 的原文五字段与 keep_items 落盘策略`。这一步决定序列化格式与执行边界，最先定，**协议全定；实现除 `EventEmitter` / `Limiter` / `HistoryView` 外可后补**。
2. **DataEvaluator**：现 pipeline 的 async 内核平移（并发窗口、分阶段错误捕获、流式落盘、resume 约 80% 复用），加 spec 子集解析、`repeat` 展开与两级聚合；scorer 迁到 `ScoreRequest/ScoreResult`（现有 10 个 benchmark × 5 类 scorer 跟随，裸函数走 `as_scorer` 兼容）；删除 sync/async 双路径；**实现方法落到 `run_eval`，并把四个具名可覆写钩子与「不可覆写的管线骨架」的边界一次定死**。**本步依赖 `ephemeral_trial_context` 与全部退化设施先落地**——没有它们，独立评估这条最常用的路径连冒烟都跑不了，而它正是本步唯一不经过引擎主循环的验收入口。
3. **重写引擎主循环**：`TrialExecutor`（baseline 归一、信号量外移、**三层上下文装配**、经 `ProgramHandle` 驱动执行、`Budget` 计账点）+ `LocalBackend`、async-first、钩子单名化、observe 改收 outcomes、best 归 `Objective`、事件点位一次埋齐。
4. **SubprocessBackend**：worker 进程 + stdio JSON-RPC + 崩溃归因（stage="program"）；边界内侧的设施代理（`events` / `cancel` 随本步落地，`limiter` 次之，`artifacts` 先由 worker 写 workspace 后收编）；adapter 定义在 `__main__` 时在 `launch` 处 fail-fast；配一个带副作用的 harness 最小示例做验收。
5. **验收**：迁移 sew 示例与 example_optimizer；新增 BO 超参示例（验证 `FunctionEvaluator` 与 search_space 约定）；新增一个 `WithRegularization` 装饰器 + `PenalizedObjective` 的最小示例（验证正则通路端到端，且证明主循环无需任何改动）；再加一个 `repeat>1` + 自定义 `Budget` 的最小示例（验证两级聚合与「故障不计账」）。
6. **清场**：删除全部退役类，不留兼容层——拆包前是唯一可以不留的窗口。

### v4.8 · 契约三档

v4.8 把第 1 步「冻结契约」重新打开一次，在 §9 第 5 步动工之前完成。判断一项属于哪一档只问一个问题：**晚定会不会要求迁移已存的 run，或丢失事后补不回来的事实？**

| 档 | 内容 | 晚定的代价 |
|---|---|---|
| **T0 现在冻结** | ① 目标 / 执行者 / 已固定请求的含义与字段（`record_id`、`executor_snapshot_id`、`producer`、`inputs`、`outputs`、`execution_ref`、`TrialContext.executor`）；② 输出接纳的两个入口与内容身份规则（`snapshot_id` 规范化与 hash 输入、`ArtifactRef.$ref` 即内容地址、`$ref` 为保留键）；③ 完成 / 交付 / 消费 / 重放的边界（交付组提交时登记、完成时与终态原子关闭，消费游标，带版本的 outcome 编码先于「可恢复的完成」落盘，checkpoint 只写已提交策略状态）；④ 状态枚举与转换、`unknown` 的含义、启动握手（running 先于 launch 落盘、加载候选前登记所有权）、`Backend.execution_ref / probe / terminate` 的签名；⑤ 协调者等待规则（只由协调者记录发起、子 run 身份与预留先于启动落盘）、预留 / 结算 / 释放与 `event_id` 去重、准入与耗尽分开、作用域祖先链；⑥ 测量 manifest（含 `ScoreResult.observed`）、分组策略、选择协议（角色 + key + 锚定记录）、`Objective.eligible`、`ProgressNote`、可见性策略与 `EvalSpec.role`；以及 `Backend.launch(adapter, snapshot, ctx)` 签名与 `schema_version` | 历史无法区分「谁在跑」与「谁被改」；角色名恢复后绑到别的实现；所有快照引用随 hash 规则迁移；恢复改变 observe 的顺序或批次；本地孤儿进程被误判消失、额度提前释放；嵌套死锁与重复计费；旧 best 的含义说不清；测量的数据角色与协议锚点事后补不回来；已经泄露给策略的证据收不回来 |
| **T1 第 5–11 步实现（只做本地）** | `LocalBackend` / `SubprocessBackend` 下的全部 T0 语义：measure 路径用新记录格式 + `Barrier`；`CallProducer`；`Window` + 本地恢复（进程组所有权确认）；宿主协调者嵌套；证据视图；manifest 与选择协议；两个现存缺陷的修复 | — |
| **T2 预留，按需实现** | 同一 run 内的选择协议切换；策略钩子直接发起子 run；远端作业对账（`unknown` 的核对）与远端幂等适配器；外部提交的幂等键；沙箱里的不可信协调者（子树资源预留）；item 隔离下的生产结果合并；`StreamProducer` 的流中途恢复；模型网关（自动计费、OTel span、token 级轨迹、角色 → 模型绑定）；CLI / MCP 外部驱动；人工审批作为外部事件 | T0 已规定「不支持时怎么拒绝、已发生但不确定时怎么记录」，所以晚做只是晚有能力，不丢事实 |

T1 遇到 T2 能力时一律**明确拒绝**（`NotSupported` / `NestingRefused` / 能力缺失报错），不静默降级。

---

## §8 目标达成度评估

对照 §0 的五项判据逐一自检，含仍存的风险——达成度评估只有诚实才有用。

**易维护 · 达成。** 同一段 trial 逻辑归一到 `TrialExecutor`，sync/async 归零（async-first + 钩子单名化），评估结果归一类。每类信息一个 owner：决策数据归 state.json、明细归 report.jsonl、过程归 events——修 bug 时不再需要在三处同步改。*剩余风险*：钩子「同步异步皆可」的自适应 await 要在基类里实现一次且只实现一次；三层上下文有膨胀倾向——它一旦变成什么都往里塞的口袋，就退化成换了名字的 kwargs 扇出，所以 §6 给它配了「必须说得出消费者」与「永不为 None」两道门槛；`ephemeral_trial_context` 必须实现成「一个真实上下文取了退化值」，一旦它长出自己的执行分支，它自己就是「零重复路径」的反例。

**可扩展 · 达成。** 九类算法谱系全部纯扩展（§5），扩展面收敛为四个稳定表面：算法（propose/observe/should_stop + 状态序列化）、程序（四方法 + 生命周期钩子）、评估（Evaluator / Scorer 接口，装饰器亦在此层）、生产（Producer，v4.8），四者共用同一条上下文通道。扩展面本身是可检查的对象：「未来能不能支持 X」不靠推演，只需问「X 需要的信息在三层上下文里吗」。负向可扩展性也有明文边界（执行记录粒度铁律 + 快照粒度铁律 + 「只给位置不给语义」），防止框架被 RL 训练需求或某一种范式拖散架。*剩余风险*：`trace["steps"]` 是约定而非强制，若生态里没人产出这个视图，内建轨迹正则就成摆设——第 5 步的示例必须同时演示 `trace_fn` 怎么写。

**易用、心智负担小 · 达成。** 用户面仍是五个概念、每个有 PyTorch 锚点（§1 表）、最短路径约 15 行；所有新增物要么是 Evaluator 的装配槽或装饰器、要么是 sink、要么是有默认值的 `optimize()` 参数（`Budget`、`Backend`，以及 v4.8 的 `producers` / `schedule` / `select_on` / `visibility`）、要么是框架自己装配的上下文。「评估」在优化内外只有一种做法，字面成立。「我想自定义评估」有按代价排序的四条路，而不是「重写整条管线」与「依赖实现细节」两个坏选择。*剩余风险*：写新算法的作者除了谱系与 optimizer_state 的纯 JSON 约束，还多认一个 ctx 参数；这是三行文档的代价，换来提案侧的可观测性，划算但要在范例里写清楚（PromptVariant、BO、正则装饰器三个官方范例各演示一面）。

**可观测、可追溯 · 架构层面达成。** 四个判据逐条对上：每个 candidate 好在哪 → `TrialRecord`（metrics + reg/\* + feedback 摘要）；每条样本发生了什么 → report.jsonl 完整明细含文本反馈，主键含重复观测，可重载；花了多少 → cost 由任何持有 `RunContext` 的组件上报，**含 `propose` 自身**；任意点续跑 → 原子 checkpoint + 交付日志 + 在途执行的恢复语义（§3「恢复」）；每个版本由谁产生 → 执行记录的谱系。*显式交换*：「这个分数在什么条件下测出来」v4.8 起由引擎记进 manifest，默认 best 只在选择协议内产生，协议漂移发告警——换了 judge 或数据后的测量不会静默抢走 best；但「两个条件组能不能比、怎么比」框架仍不裁决，`is_better` 看得见整条 `TrialRecord` 与 manifest 引用，想跨组比较的算法自己写。

**平台化余地 · 接入点而非平台。** 「PyTorch 那一面」的结构已齐：稳定的核心抽象 + 收敛的扩展面 + 三层上下文契约 + 执行平面协议 + contrib 分层。「wandb 那一面」交付的是**接入点**：事件通道、per-trial cost、逐条明细都已在正确位置，但对比 UI、run 托管、多实验检索属于 sink 生态，是拆包后的独立产品工作。已知的规模化债务留在 P2 且各有预留位：共享并发预算（位置已上收到 `RunContext.limiter`）、大产物存储（ArtifactRef 格式已冻结、v4.8 起 `$ref` 即内容地址，本地内容寻址已实现，远端存储后补）、`Budget` 家族与 item 级隔离（协议已冻结，实现后补）。新能力挂在哪一层、由谁消费，都有唯一答案，因此 sink 生态与执行生态可以各自独立生长而不互相牵扯。结论：架构不封死任何一条路，但平台本身要靠拆包后的持续投入长出来。

**RSI 承载（v4.8）· 契约层面达成，待 T1 验收。** v4.7 是一个「可扩展的候选测量引擎」：它能**包装**不少 RSI 方法（例如跑一个 `changes=[]` 的测量让程序写 workspace，下一轮再捞出来），但谁产生了哪个版本、哪个改进器被实际使用、测量条件是什么，都只能靠算法自报。v4.8 把这三件事变成引擎观测到的事实（§0.6），把执行形态从「只测量」扩到「测量或生产」，把调度从「只批同步」扩到「批同步或完成驱动」，并把嵌套、恢复、预算预留、证据可见性定成契约。判据是 §9 第 11 步的 D1–D6：任何一条没过，就不声称支持对应的 RSI 形态。

*代价要认*：
- **引擎内部复杂度明显上升**——持久化的请求、有序交付日志、预留与结算、内容 hash、按策略构造的证据视图，都是 v4.7 没有的实现量。用户面不变（五个概念、15 行最短路径不改一行；行为上只有 §2 算法侧列出的三处有意差别），`Producer` 只面向写改进器 / 在线学习 / 训练类算法的作者。对策是 T1 分批落地（§9），每批独立验收。
- **剩余风险一**：细到每个请求都更新、从不形成快照的在线学习，只能由 Backend 托管成有状态服务、按周期出快照；那时要区分的不只是快照频率，还有推理版本、学习状态、流游标与反馈归属——这些在 T2 再细化。
- **剩余风险二**：多个程序独立更新、互相修改、没有全局屏障的群体系统。需要整体提交的组合可以放进一个快照；完全独立演化的组件需要「组件版本引用 + 实际调用关系」，单靠一个不透明的 `program_config` 不能宣称支持——未验证，留作已知缺口。
- **剩余风险三**：若「coding agent 直接当最外层优化器」成为主流，用户写 `Optimizer` 子类的场景会变少，框架价值向评估门、隔离、记录与预算集中。T0 已把外部驱动所需的请求身份与状态定好，但 CLI / MCP 本身是 T2。

---

## §9 实施计划（基线 `refactor_optimizers` @ f4bd4cf；v4.8 修订基线 @ cb3a335 + 工作区第 4 步）

§7 给的是契约冻结顺序；本节把它细化成**自底向上、一步一个完整 component**的落地序列，每步有独立验收方式，验收通过才进下一步。

### 原则

- **新代码进新模块，旧引擎在第 9 步接线前保持可跑**。`adapter.py` 里已有旧 `TrialContext` / `TrialWorkspace`，与新三层上下文重名——新契约一律放独立文件（见下），两代共存到清场，不做中途改名。
- **协议与实现同步交付但验收只验协议**：每步的单测断言的是契约行为（JSON 往返、退化值表、偏序、幂等），不是实现细节。
- **不留兼容层**（§7 第 6 步）：所有退役物集中在最后一步删除，中途只标注 deprecated，不写桥接代码。

### 现状盘点：复用 / 重写 / 退役

| 现有物 | 处置 |
|---|---|
| `evaluators/pipeline.py` async 内核（worker 池、三阶段错误捕获、流式 JSONL、resume） | **平移**进 `DataEvaluator.run_eval`（~80% 复用），sync 路径丢弃 |
| `benchmark/scorers/`（base/qa/math/code，metric_keys 契约） | **原地保留**在 `benchmark/scorers/`，逻辑不动，仅**签名迁移**到 `ScoreRequest/ScoreResult`；`Scorer` / `ScoreRequest` / `ScoreResult` / `as_scorer` / `CompositeScorer` 这套**协议与通用组合子上收**到 `optimizers/core/scoring.py`，`benchmark/scorers/*` 反向 import 它 |
| `engine/base.py`：`SnapShot` / `UnitChange` / `OptimizationUnit` / `assert_pure_json` / 持久化守卫 | **保留**，`unit_type` 放宽为开放字符串，`TrialRecord` 换新字段表 |
| `engine/adapter.py`：`ProgramAdapter.apply`（merge+强校验）、units 校验、fingerprint | **保留骨架**，`execute`/`validate_trial` 换签名，删 `capture_after_eval` 与全部 `async_*` 双胞胎 |
| `engine/optimizer.py`（evaluate_fn 闭包、签名嗅探、sync/async 双路径） | **重写**为 `TrialExecutor` + 三钩子收 ctx |
| `engine/objective.py`（`is_better` 收裸 metrics dict） | **重写**签名收 `TrialRecord` |
| `evaluators/`：`EvaluationPipeline` / `EvaluationReport` / `EvaluationResult` / `DataLoader`、`Benchmark.evaluate/async_evaluate`、旧 `TrialContext` / `capture_after_eval` | **退役**（第 12 步删除） |

### 目标模块布局

`evoagentx/optimizers/` 就是未来拆包的边界：engine（框架）+ algorithms（具体算法）+ 未来的 contrib，整个目录可原样搬出成独立包。切分线不是「评估相关的都随包走」，而是**协议随包走、实例留宿主**：

- **协议与通用组合子进 `optimizers/core/`**——`DataSource` / `MutableDataSource`、`Scorer` / `ScoreRequest` / `ScoreResult` / `as_scorer` / `CompositeScorer`。它们出现在 `DataEvaluator.__init__` 与 `Evaluator.metric_keys` 的签名里，是引擎自己的对外接口；若定义在 `benchmark/`，拆出去的包就 import 不到自己的类型，「零宿主依赖」当场破功。这几个东西零业务逻辑、零数据集知识，是纯结构。
- **具体数据集与具体打分器留在 `evoagentx/benchmark/`**——`Benchmark` 各子类（`get_split` / `get_reference` 使其成为 `DataSource` 的满足者）与 `scorers/{qa,math,code}.py`。它们承载的是「GSM8K 的答案怎么抽」「HumanEval 怎么跑测例」这类**领域知识**，与优化引擎无关，且是用户最常自己写、自己扩的一层：用户新增一个 benchmark 就顺手在旁边新增它的 scorer，两者同一目录、同一心智，不必为了加一个正则匹配去动引擎包。

依赖方向因此仍是 **`benchmark → optimizers` 单向**：benchmark 侧 import `optimizers.core` 的协议来声明自己；`optimizers/` 内部永不 import `benchmark` / 宿主其余部分——「零宿主依赖」由此落到目录结构上可 grep 检查（CI 一条 `grep -r "from evoagentx.benchmark" evoagentx/optimizers/` 必须为空）。`optimizers/evaluation/` 于是只剩与优化引擎强相关的部分：`Evaluator` / `DataEvaluator` / `FunctionEvaluator` / 正则装饰器。

```
evoagentx/optimizers/
    core/                      # 冻结契约：纯数据对象 + 三层上下文（序列化格式所在，最先定）
        spec.py                EvalSpec
        results.py             ObservationKey / ItemResult / EvalResult / TrialFeedback
        records.py             Snapshot / UnitChange / Proposal / TrialRecord / STAGES / assert_pure_json
        canonical.py           v4.8 · 规范化 JSON + snapshot_id 计算（hash 输入规则的唯一实现处）
        production.py          v4.8 · ProduceResult（EvalResult 子类）/ DeltaOutput / SnapshotOutput（纯数据）
        outcome.py             TrialOutcome（运行时对，标注为 RecordView / ResultView 协议；永不落盘）
        context.py             RunContext / TrialContext / ItemContext / 设施 Protocol（含 PendingView /
                               RunSpawner）/ ScopeRef / ArtifactRef
        data.py                DataSource / MutableDataSource（Protocol，无实现）
        scoring.py             Scorer / ScoreRequest / ScoreResult / as_scorer / CompositeScorer
    runtime/                   # 框架代管设施（用户不接触）
        limiter.py             Limiter（三池 + 偏序断言）
        events.py              EventEmitter + 默认 sink（进度条 / 结束报告，自 utils.py 改造）
        budget.py              Budget 家族 + BudgetView
        artifacts.py           ArtifactStore（本地目录 / 带上限内存两实现）
        history.py             HistoryView / EmptyHistoryView
        cancel.py              CancelToken
        ephemeral.py           ephemeral_trial_context（缺省上下文的唯一归一化点）
        scheduler.py           v4.8 · Barrier / Window + 交付日志与消费游标
        manifest.py            v4.8 · 测量 manifest 构造 + GroupingPolicy
        views.py               v4.8 · 证据视图（full / summary / none 的允许列表构造器）
        spawner.py             v4.8 · RunSpawner（协调者嵌套、子预算预留、拒绝规则）
        executor.py            TrialExecutor（主循环：提交 / 派发 / 收尾 / 恢复）
    backend/
        base.py                Backend ABC（含 capabilities / execution_ref / probe / terminate）
                               + ProgramHandle Protocol + session
        local.py               LocalBackend
        subprocess.py          SubprocessBackend
        worker.py              子进程入口（stdio JSON-RPC）
    program/
        adapter.py             ProgramAdapter（apply / fingerprint / units 校验）
        units.py               OptimizationUnit / ChangeOperation / Unit 便捷构造器
        dict_adapter.py        DictProgramAdapter
    evaluation/                # 只放与优化引擎强相关的评估装配件
        evaluator.py           Evaluator ABC（evaluate 归一化 + run_eval 抽象）
        data_evaluator.py      DataEvaluator
        function_evaluator.py  FunctionEvaluator
        decorators.py          WithRegularization 及正则器族
    production/                # v4.8 · 生产装配件，与 evaluation/ 对称
        producer.py            Producer ABC
        call.py                CallProducer
        stream.py              StreamProducer
    objective.py               Objective / Scalar / Pareto / Penalized
    optimizer.py               Optimizer ABC（重写）
    algorithms/                # 具体算法：PromptVariant / BO / MIPRO / sew 迁移目的地
    engine/                    # 现状旧引擎，接线前保持可跑，第 12 步整目录删除
    legacy/                    # 现状保留，清场时处置

evoagentx/benchmark/           # 宿主侧：领域知识与用户扩展点，不随包走
    benchmark.py               Benchmark（新增 get_split / get_reference → 满足 DataSource）
    gsm8k.py / humaneval.py / ...
    scorers/                   ← 原地保留，仅换签名
        qa.py / math.py / code.py   （import optimizers.core.scoring 的协议）
```

包内依赖方向自上而下单向：`core` ← `runtime` / `backend` / `program` / `evaluation` / `production` ← `objective` / `optimizer` ← `algorithms`，宿主的 `benchmark/` 挂在最外圈只 import `core`。`core` 不 import 包内任何其它模块，是拆包时序列化兼容性的最小锚点。扁平结构的附带收益：新代码与旧 `engine/` 零路径冲突，共存期互不干扰，清场就是删掉整个 `engine/` 目录。

### 第 1 步 · `DataSource` 协议 + Benchmark 兼容 ✅

- 新 `core/data.py`：`DataSource` Protocol（`get_split(split, *, example_ids=None, sample_k=None, seed=None)` / `get_id` / `get_reference(example_id)`）与 `MutableDataSource`（`append` / `freeze`），均 `runtime_checkable`，零实现零依赖。
- `Benchmark` 增两个方法使其成为满足者：`get_split` 按 **id**（不是位置下标）过滤并叠加带种子的随机抽样，抽样用局部 `random.Random(seed)`，不污染全局随机状态；`get_reference` 默认 `= get_label(get_example_by_id(id))`，rubric benchmark 覆写它返回 rubric 列表。未知 id / 重复 id 一律 fail-fast（重复观测归 `EvalSpec.repeat`，不靠重复 id 表达）。
- **`get_example_by_id` 走索引**：构造期一次性建 `{split: {id: example}}`（与 id 校验同一趟遍历），查找从「拼三个 split 再线性扫」变成 dict 命中；`mode` 语义与重复 id 的「先到先得」顺序保持不变，跨 split 撞 id 时告警。MATH 全量 5000 次查找 3.4s → 0.003s。
- **example id 契约收紧**：id 是 `ObservationKey` 的主键、要落 report.jsonl，因此必须是**非空字符串**，在 `Benchmark._validate_ids`（构造期一次）强制；重复 id 仍只告警。随之修正的 benchmark：MBPP / AFlowMBPP（int `task_id` → `str`）、RealMMRAG（int id → `str`）、HotPotQA（AFlow 导出里混入 19 条 int `_id` → `str`）、BIGBenchHard（原本拿整段 input 文本当 id → 按任务文件内位置派生 `"0"`、`"1"`…，在 dev/test 切分前分配）、AFlowMATH（AFlow 的 math 文件根本没有 id 字段，原本构造即 `KeyError`，现按 split 内位置派生）、WorfBench（`example.get("id","")` 掩盖缺失 → `example["id"]`）、LiveCodeBench（`question_id` 只在 code_generation 下唯一：test_output_prediction 每题多条 test，code_execution 每题多条执行实例且 `question_id` 是 int，改为按 scenario 取 `id` / `question_id-test_id`）。
- **验收**：`tests/src/benchmark/test_datasource.py`——协议一致性扫全部 benchmark 类（结构性 `issubclass` 检查）；id 契约四测（非字符串 / 空串报错、重复只告警）；HumanEval 子集语义十测（同 seed 两次逐 id 相同、全局 random 状态不受扰、`example_ids` 顺序与未知/重复报错、`example_ids` × `sample_k` 组合、`get_reference` 默认取 label）；MBPP 的 int→str id 与 scorer prompt 反查；BIGBenchHard 派生 id 跨 dev/test 切分保持指向同一样本。另在真实数据上逐个跑过一遍：GSM8K / MATH / NQ / BIGBenchHard / WorfBench / AFlow 五件套 / HumanEval / MBPP / LiveCodeBench 三 scenario，全部 id 唯一、非空、字符串（**含跨 split 唯一**：每个 benchmark 的 train / dev / test 两两无交集），`get_reference` 与 `get_label` 一致。

### 第 2 步 · 打分侧协议上收：`ScoreRequest` / `ScoreResult` ✅

- 新 `optimizers/core/scoring.py`：`ScoreRequest`（key/inputs/prediction/reference/trace）与 `ScoreResult`（metrics/feedback/details）；`Scorer` ABC，`score(req) -> ScoreResult` 改 **async 单名**（删 `ascore` 双路径，同步实现由 `as_scorer` 或基类自适应包装）；`metric_keys` 契约保留（全失败仍出 `{key: 0.0}`）；`as_scorer(fn)` 继续包 `(prediction, reference) -> dict` 裸函数；`CompositeScorer(scorers, prefixes=[...])` 跟随（合并 metrics、拼接 feedback、并列 details）。以上皆自现 `benchmark/scorers/base.py` 迁入。
- `benchmark/scorers/` **保留原地**：`qa.py` / `math.py` / `code.py` 改为 `from evoagentx.optimizers.core.scoring import Scorer, ScoreRequest, ScoreResult`，签名从 `req` 取 `prediction` / `reference`，打分逻辑零改动；`benchmark/scorers/base.py` 降为一层 re-export（`__init__.py` 同时导出协议与内建 scorer，用户 `from evoagentx.benchmark.scorers import ...` 的写法不变），拆包后该 re-export 是宿主侧的适配点，不进包。
- **验收**：scorer 单测（含 `metric_keys` 全失败契约、Composite 前缀合并、裸函数包装、async judge 型 scorer 一个假实现）；外加一条依赖方向断言测试：`optimizers/` 全目录不出现 `from evoagentx.benchmark` / `import evoagentx.benchmark`。

### 第 3 步 · 冻结纯数据契约 ✅

- `core/spec.py`：`EvalSpec`（`repeat` / `example_ids` / `sample_k` / `seed` 派生写回规则 / `extras`）。`extra="forbid"`（拼错的旋钮必须炸，不能静默评估默认子集）；`example_ids` 沿用第 1 步的 id 契约（非空字符串）；`derive_seed(rng)` 是纯函数——无 `sample_k` 或已有 seed 时原样返回，因此幂等。
- `core/results.py`：`ObservationKey` / `Metrics` 的**唯一定义处**（`scoring.py` 反向 re-export，三个消费者 `ItemContext` / `ItemResult` / `ScoreRequest` 共用一份）；`ItemResult`（主键 + 原文五字段 + `feedback` / `details` / `stage` / `traceback`）与 `item_to_json_dict` 的 `keep_items` 三档剥离；`TrialFeedback.from_text` 硬上限截断；`aggregate_items` 两级聚合纯函数（先按样本折 `repeat`，再折样本——一次拍平会让被多次观测的样本按观测数加权）；`EvalResult`（`save` / `load` / `aggregate`）。report.jsonl 定为「逐条 item 行 + 一条 summary 行」：`save` 与 `DataEvaluator` 的流式追加落成同一种文件，缺 summary 行（跑到一半被打断）仍可载入，末行被拦腰截断（写到一半掉电）只跳过该行并告警。**metrics 逐键渲染而非整体序列化**：numpy 标量按 `float()` 收编（否则一次昂贵评估会在最后一步 `TypeError` 全废），NaN / Inf 写成 `null` 并在载入时丢弃（`json.dumps` 默认会写出裸 `NaN` 这种非法 JSON；不可表示的指标是「缺测量」，不是 0）。可追溯字段仍走 repr 降级——落盘失败不该拖垮一次评估。
- `core/records.py`（自 `engine/base.py` 迁入并改为**纯 pydantic `BaseModel`**，去掉 `BaseModule` 宿主依赖）：`Snapshot`（改名）/ `UnitChange` / `OptimizationUnit` / `assert_pure_json` / `assert_payloads_pure_json`；`TrialRecord` 迁到 v4.5 字段表（`status` ok/failed、`eval_spec`、`feedback`、`stats`、`report_path`、`stage`、`metadata`）；`OptimizationUnit.unit_type` 放宽为开放字符串（内建六类降为 `UnitType` 常量集合）；`Proposal`（自 `OptimizationProposal` 改名）增 `eval_spec`。`ValidationResult` 不随迁——`validate_trial` 失败即 `stage="validate"` 的失败 trial。
- **验收**：`tests/src/optimizers/test_core_contracts.py`（41 测）——全部对象 JSON 往返（含 `assert_payloads_pure_json` 对活对象 / NaN / `class_name` 的拦截路径）；`EvalResult` 存取往返、report 行型与主键唯一、无 summary 行的中断报告仍可载入、`keep_items` 只作用于落盘（内存 items 不变）；`keep_items` 三档 + `raw_prediction` repr 摘要（含不可序列化对象）；两级聚合对 `repeat>1` / 三档 `on_error` / 全失败出 `{key: 0.0}` / 键集基数与数据规模无关；`unit_type` 开放字符串与自定义 operation。旧 `engine/` 与新 `core/` 并存不冲突，依赖方向断言仍为空。

### 第 4 步 · 运行时设施 + 三层上下文 + `ephemeral_trial_context` ✅

- `runtime/` 各一文件：`limiter.py`（默认三池 trial/item/llm + debug 模式 contextvar 偏序断言，`capacity(kind)` 供 evaluator 算 `min(self.concurrency, item 池)`；n 令牌原子获取走 Condition，防两个多令牌请求互相卡死）；`events.py`（`EventEmitter` 不可变代理链，`bind(ledger=..., **scope)` 绑作用域 id；`cost` 三扇出：sink · `charge_cost` 裸回调 · 链上全部 `CostLedger`——ledger 按 `(kind, unit)` 记账，不同 unit 永不相加；坏 sink 只告警不拖垮 run）；`budget.py`（`TrialBudget` / `CostBudget` / `TokenBudget` / `WallClockBudget`（跨 resume 累计墙钟）/ `AnytimeBudget`（patience 计数即 budget_state 的存在理由；改进按 `(split, sample_k, repeat)` 分组判定，组内首条测量中性）/ `AllOf` / `UnlimitedBudget` + `BudgetView` 活投影 + `serialize_state` / `load_state` 纯 JSON；默认计账策略：status ok 或 stage ∈ 五 STAGES 计账，无 stage 的基础设施故障不计）；`artifacts.py`（`LocalArtifactStore` 内容寻址 + 原子发布，`MemoryArtifactStore` 容量上限、超限报错提示传 save_dir；两者都**摄入内容**，Path 入参读字节不留路径引用）；`history.py`（`FileHistoryView` 按 mtime 懒读 state.json、`result` 顺 `report_path` 走 `EvalResult.load` + `EmptyHistoryView` 查无即抛 KeyError）；`cancel.py`（`CancelToken`，`raise_if_cancelled` 抛 `asyncio.CancelledError`）。
- `core/context.py`：六个设施 Protocol 与上下文同文件冻结 + `ArtifactRef`（`{"$ref", "sha256", "kind"}`，`$ref` 走 pydantic alias）+ `RunContext` / `TrialContext` / `ItemContext`（events 为绑定作用域 id 的代理）。**`ephemeral_trial_context` 实际落在 `runtime/ephemeral.py`**（§2 退化值表逐行落地，实现为「真实上下文取退化值」，无独立分支；接受可选 `spec`（→ `specs={"val": spec}`，`Backend.session` 不给则为空表）与可选 `cancel`；无 `save_dir` 时 `trial_dir=None`）——它要**装配真实设施**（建目录、开临时目录、实例化六个实现），放 `core/` 就得靠函数体内的延迟 import 才能维持依赖方向，放 `runtime/` 则 `core` 对 `runtime` 的依赖**彻底为零**，代价只是 `core/context.py` 的文档里指一下它在哪。`TrialOutcome` 落 **`core/outcome.py`**（NamedTuple，`Budget.charge_trial` / `observe` 共用）——不放 `records.py`，是为了让那个「字段表即序列化格式」的模块不必 import `EvalResult`。模块级依赖方向是 runtime → core 单向；`snapshot` 的 `isinstance(program, ProgramAdapter)` 分支延迟 import `program/adapter`，第 5 步落地前以 `take_snapshot` 能力探测兜底（handle 协议无此方法，行为一致）。
- **验收**：`tests/src/optimizers/test_runtime_context.py`（39 测）——limiter 同池重入即抛、偏序违规断言、容量真限并发、异常路径令牌不泄漏、超容量请求 fail-fast；budget 计账策略（ok / 可归因失败计账、无 stage 故障不计）/ 耗尽 / 全家族经真 JSON 的序列化往返（含 AllOf 子结构数不符拒绝恢复）；emitter cost 三扇出、作用域 id 靠 bind 不靠参数、ledger 沿链累计且 unit 不混加、坏 sink 不影响其余 sink、组件拿不到 Budget 本体（emitter 只持裸回调、view 无 charge 方法）；ephemeral ctx 对照 §2 那张表逐字段断言（standalone run_id/mode、trial_id=-1、空 changes、真 budget/history/limiter/cancel、specs 真表、cost 汇进 ledger、无 save_dir 时临时目录 finally 清理 + 内存 artifacts + `trial_dir=None`、有 save_dir 时落地不删、adapter 取快照而 handle 得空快照）。既有 170 测全绿，`optimizers/` 新模块零宿主 import。

### 第 4.5 步 · v4.8 契约修订（T0，代码层）

第 3、4 步已冻结的契约按 v4.8 重新打开一次。只改 `core/` 与 `runtime/` 的数据契约和设施协议，不碰主循环；改完再进第 5 步。

- `core/records.py`：`TrialRecord` 换 v4.8 字段表（`schema_version` / `record_id` / `kind` / 六态 `status` / `parent_scope` / `execution_ref` / `producer` / `inputs` / `executor_snapshot_id` / `outputs` / `measurement_ref` / `measurement_key` / `failure_class`），`kind × status` 组合约束写成模型校验器；`STAGES` 扩充；`snapshot_id` 字段说明改为「派发时 apply 成功即写入、后续失败保留」；`Proposal` 增 `kind` / `executor_snapshot_id` / `producer` / `inputs` 及按 `kind` 的校验。
- `core/spec.py`：`EvalSpec.role`；派生只许改子集与重复次数（`example_ids` / `sample_k` / `seed` / `repeat`）的校验函数，供提交时调用。
- `core/canonical.py`（新）：规范化 JSON 与 `snapshot_id` 计算——键排序、float 最短往返、不做结构改写；含 `$ref` 键的对象必须是合法 `ArtifactRef`；`Snapshot` 以已提交的规范化字节为权威，读取返回独立对象，提交与加载时重算校验。
- `core/scoring.py`：`Scorer.score(req, ctx: ItemContext)`、`Scorer.describe()`、`ScoreResult.observed`；`as_scorer` / `CompositeScorer` 与 `benchmark/scorers/*` 跟随（签名机械迁移，打分逻辑不动）。
- `core/results.py`：`ItemResult.failure_class`；`EvalResult.spec` 可空；`EvalResult.save/load` 带结果类型标记；带版本的 outcome 编码（重放用，按结果类型往返）。`core/production.py`（新）：`ProduceResult(EvalResult)` / `DeltaOutput` / `SnapshotOutput`。`core/outcome.py` 的标注改为视图协议（`ProduceResult` 是 `EvalResult` 的子类，无需另开类型）。
- `core/context.py`：`TrialContext` 增 `record_id` / `kind` / `executor`；`RunContext` 增 `parent` / `pending` / `runs`；`ScopeRef`（`record_id` 必填）、`PendingView`、`RunSpawner` 协议；`HistoryView` 协议换成 `record / records / result / snapshot / producers_of / measurements_of / export`；只读视图类型 `RecordView` / `ResultView`（越权访问抛 `HiddenEvidence`）与 `SelectionProtocol`；`ArtifactRef.$ref` 改为内容地址 `sha256:<hex>`。
- `runtime/events.py`：作用域改祖先链（`bind` 追加一帧）；`cost(..., event_id=None)`，ledger 与 Budget 回调都按 `event_id` 去重。
- `runtime/budget.py`：`reserve` / `settle` / `release`；`exhausted` 改为永久耗尽、与 `reserve` 拒绝分开；默认计账判据改为 `failure_class`；`AnytimeBudget` 改为只消费 `ProgressNote`（**修复现存缺陷一**：v4.7 实现里所有组共用 `_stale`，train 组第二条记录起的提升会清零 val 的 patience）；`AllOf` 原子预留与回滚；`budget_state` 含预留表与已处理 `event_id`。
- `runtime/history.py`、`runtime/artifacts.py`（store 按 hash 定位 `sha256:<hex>`；`open` 默认校验 sha256）、`runtime/ephemeral.py`（新字段的退化值，§2 表格逐行落地）跟随。
- **现存缺陷二**：`TrialRecord.snapshot_id` 的契约在本步改正；第 9a 步的执行器按它实现，覆盖 validate / program / scorer / shutdown 各阶段失败。
- **验收**：在 `test_core_contracts.py` / `test_runtime_context.py` 上扩充——`snapshot_id` 稳定性（键序无关、float 表示无关、同一内容在两个 store 里的引用与快照 id 相同、换 sha256 id 改变）；ArtifactRef 与形似它的普通 JSON 不碰撞（含 `$ref` 而非合法引用的对象被拒、`$ref` 与 sha256 不一致被拒）；深层不可变（修改读出的副本不影响已提交内容）；派生 spec 保留 `role`、改 `split` 被拒；`ProduceResult` 存取往返仍是 `ProduceResult` 且 outputs 不丢；预留占满时 `exhausted()` 仍为 False；`kind × status` 合法与非法组合矩阵；`Proposal` 按 `kind` 的校验（measure 带 executor / producer / inputs 即拒绝）；`event_id` 重复上报 ledger 与 Budget 都只计一次；`bind` 祖先链不覆盖父 `run_id`；预留幂等与 `AllOf` 回滚；`AnytimeBudget` 回归测试（val 0.8 → val 0.7 → train 0.4 → train 0.5 之后 patience 计数仍为 1）。既有测试全绿或随契约更新。

### 第 5 步 · `ProgramAdapter` 契约收敛 + `DictProgramAdapter`

- `program/adapter.py`（自现 `engine/adapter.py` 迁入并收敛，units 相关拆到 `program/units.py`）：`execute(inputs, ctx: ItemContext)` 单名（同步异步皆可，框架自适应 await），只读契约按 v4.8 限定到 measure 实例；`validate_trial(ctx: TrialContext)` 换签名；`prepare_workspace(snapshot, ctx)`（只写文件）；新增可选钩子 `validate_snapshot(snapshot, base)`（默认：unit 集合与 `program_config` 不变）与 `learn(feedback, ctx)`（默认空操作）；删 `capture_after_eval` / `async_capture_after_eval` / `async_validate_trial` / `async_execute`；`apply` / units 校验 / `fingerprint`（「能否解释快照」语义）保留。
- 新增 `program/dict_adapter.py`：`DictProgramAdapter`，`units` 类属性声明 + `self.values`，`register_units` / `take_snapshot` / `merge_changes` / `from_snapshot` 全代劳，用户只写 `execute`；`Unit.prompt(...)` 等便捷构造器。
- **验收**：`DictProgramAdapter` 单测（apply 纯函数性——输入快照不被修改；快照往返重建等值且 `snapshot_id` 不变；`execute` 只读契约的并发冒烟：N 并发观测跑同一实例）；`prepare_workspace` 对一个「import 即抛错」的候选模块只写文件、不触发 import；`validate_snapshot` 默认实现拒绝改 `program_config` 的完整快照。

### 第 6 步 · `Backend` / `ProgramHandle` / `LocalBackend`

- `backend/base.py`：`Backend` ABC（`launch(adapter, snapshot, ctx)` / `shutdown(handle, ctx)` 先同步 workspace 子树再回收 / `session` / `capabilities` / `execution_ref` / `probe` / `terminate`）与 `ProgramHandle` Protocol（`execute` + `validate_trial` + `take_snapshot` + `learn`，与 adapter 逐字同签名）；`backend/local.py`：`LocalBackend`（`launch` = `adapter.from_snapshot(snapshot)`、shutdown 为空、`execution_ref` 恒为 None）。
- `session(program, ctx=None)` = `launch(program, await program.take_snapshot(), ctx)`，ctx 缺省时调用与 Evaluator 同一个 `ephemeral_trial_context`。
- **验收**：`ProgramAdapter` 结构满足 `ProgramHandle` 的静态断言（typing `runtime_checkable` / mypy）；`LocalBackend.session` 往返；一个假 Backend 验证 shutdown 的 workspace 同步语义与 `probe` / `terminate` 的调用约定。

### 第 7 步 · `Evaluator` ABC + `DataEvaluator` + `FunctionEvaluator` + 测量 manifest（第一条端到端路径）

- `evaluation/evaluator.py`：`Evaluator` ABC（`evaluate` 基类归一化 + `run_eval` 抽象 + `metric_keys` + `describe` + `coordinator`）；`evaluation/data_evaluator.py`：`DataEvaluator` = pipeline.py async 内核平移（worker 池 → `min(self.concurrency, item 池)`、program/output_fn/scorer 分阶段错误捕获并记 `failure_class`、流式 report.jsonl、resume 锚点改 `ObservationKey`）+ spec 子集解析 + `repeat` 展开 + 两级聚合 + 原文五字段挂载 + `score(req, item_ctx)` 与 `ScoreResult.observed` 收集 + 取 reference 时顺带算摘要 + 四个具名钩子（`resolve_subset` / `aggregate` / `make_feedback` / `on_item_error`）；`evaluation/function_evaluator.py`：`FunctionEvaluator`（`describe` / `coordinator` 参数；fn 返回 dict 时 stats 归一化为 total=1、errors=0）。
- `runtime/manifest.py`：manifest 构造（observed / declared / unknown 三段）与 `GroupingPolicy("default/1")`；独立评估同样产出 manifest。
- **验收**（依赖第 1–6 步全部）：独立评估 `await evaluator.evaluate(program, EvalSpec(...))` 在一个现有 benchmark（gsm8k 或 humaneval）+ 一个假 program 上跑通——ephemeral ctx 生效、report.jsonl 落盘且 `EvalResult.load` 复盘、`on_error` 三策略、`repeat>1` 主键与聚合、`keep_items` 三档、四钩子各覆写一测、给 `save_dir` 与不给的两种清理行为；manifest 三段正确（闭包型 `FunctionEvaluator` 未给 `describe` 时进 unknown）；同一协议下重采样 minibatch 的 `measurement_key` 相同；换 scorer、judge 在 `observed` 里报出新版本、显式 `example_ids` 子集对全量、确定子集的 reference 被改、同样 `sample_k` 但总体换成另一题池、同一 run 内 id 不变而 reference 被改（未声明不可变的数据源），这六种情况 key 都不同。

### 第 8 步 · `Objective` 收 `TrialRecord` + 选择协议

- `objective.py`：`is_better(a: TrialRecord, b: TrialRecord)`；`eligible(record, protocol)`（引擎先硬过滤 `kind=="measure"` 且 `status=="ok"`；默认再要求角色与 key 都匹配选择协议）；`select_best_trial_record` 在 eligible 记录内归约；`progress(new, incumbent)`；`ScalarObjective` / `ParetoObjective` 迁移，新增 `PenalizedObjective`。
- **验收**：单测（诊断测量不抢 best、produce 记录不进比较、status/metrics 空记录跳过、Penalized 与 reg/* 键组合、换 judge 后的新协议记录不抢旧 best、同 spec 不同角色的记录不 eligible、自定义 Objective 覆写 `eligible` 跨组比较时 best 与 ProgressNote 一起变、`progress` 的 neutral / improved / unchanged 三态）。

### 第 9 步 · 引擎主循环重写（`TrialExecutor` + `Optimizer` 接线），分四批

**9a · measure 路径 + `Barrier` + 本地恢复。** `runtime/executor.py`（`TrialExecutor`）+ 顶层 `optimizer.py` 重写 + `runtime/scheduler.py`：三钩子收 `RunContext`、钩子单名自适应 await、单写者；`specs` / `required_specs` / `required_producers` 在第一个请求前校验（`eval_spec=X` ≡ `{"val": X}` 糖）；提交（分配 `record_id`、校验 `role`、固定请求、写 pending、登记交付组，与已提交的 optimizer_state 同一次 checkpoint）→ 派发（`reserve` → `apply` → `acquire("trial")` → 备目录 → 写 running 并 checkpoint → `launch` → `validate_trial`）→ evaluate → shutdown → manifest 与协议绑定 → outcome 编码 → 终态记录 → `eligible` / `progress` / `settle` → best 归 `Objective` → 与交付组完成序号同一次提交；准入与停止分开（停止时 pending 记 `cancelled(stop)`）；事件点位一次埋齐（run / submit / dispatch / trial / deliver / checkpoint）；resume：pending 重新派发、进程内 running 记 `cancelled(lost)`、游标之后按日志重放并还原同类型 outcome（拒绝续跑 standalone、fingerprint 校验沿用）；baseline = trial 0 无第二条路径。`utils.py` 的进度条 / 结束报告改造成默认 events sink。
- **验收**：玩具 `PromptVariantOptimizer` 在 LocalBackend 下端到端——`max_trials` 糖且并发派发不超发、中断后 resume 续跑、失败记录（stage + failure_class 归因）不中断 run 且按 Budget 策略计账、propose 侧 `ctx.events.cost` 进 stats 与 budget 且只计一次、`required_specs` 缺角色的前置报错；baseline 失败后由下一条合格记录绑定选择协议；§1 最短路径不改一行即可运行，与 v4.7 的差别只在 §2 算法侧列出的三处。迁移 sew 示例与 example_optimizer 作为回归。

**9b · produce 路径 + `CallProducer` + 证据视图。** `production/producer.py` / `production/call.py`（`capture` 的 return / snapshot 两种模式）；输出接纳（delta / full 两入口、可写范围检查、全有或全无提交）；`runtime/views.py` 的允许列表视图（`RecordView` / `ResultView`）施加于 observe / history（含 `export`）/ pending。
- **验收**：D1、D2（见第 11 步）；`summary` 角色的 observe 输入里不出现任何原文、feedback、traceback 文本，访问 `items` 抛 `HiddenEvidence`。

**9c · `Window` 预设。** 完成驱动交付、`ctx.pending`、回压（`max_queued`）、`on_stop="drain" | "cancel"`、钩子执行期间的 checkpoint 只写已提交策略状态、迟到结果归属。
- **验收**：一个异步稳态进化玩具（岛屿 + 每完成一个就补一个）跑通；D5、D6。

**9d · 协调者嵌套。** `runtime/spawner.py`：`spawn` 只许协调者记录发起、子 run id / 序号 / 预留先于子 run 启动落盘、子 run 目录与祖先链、取消下传、恢复时对子树做清理式恢复（终结子 pending、探测回收子 running，不派发、不 observe）；协调者记录不申请池令牌。
- **验收**：D3。

### 第 10 步 · `SubprocessBackend`

- `backend/subprocess.py` + `backend/worker.py`：worker 进程（独立进程组 + 父死信号）+ stdio JSON-RPC；worker 内 `from_snapshot` 重建 adapter；边界内侧真 `ItemContext`（`events` / `cancel` 代理先行，`limiter` 次之，`artifacts` 先由 worker 写 workspace 后收编）；`take_snapshot` / `learn` 镜像；`isolation="item"` 每观测新沙箱，produce 下拒绝；崩溃归因 stage="program"；adapter 在 `__main__` 定义时 launch 处 fail-fast；shutdown 先同步 workspace 子树；启动握手（worker 先把 `{pid, pgid, start_time}` 落盘到 `trial_dir/execution.json`、等宿主确认才加载候选），`probe` / `terminate` 按登记的进程组实现。
- **验收**：带副作用 harness 最小示例——候选往 workspace 写文件（shutdown 后父进程读得到）、一个 `sys.exit()` 候选只废该观测不带崩 run、两个 trial 的同名模块互不污染；kill 宿主后 resume：存活的 worker 被探测到、终止确认后记 `cancelled(lost)`，确认不了记 `unknown` 且预留不释放；在「running 已落盘、launch 未返回」处 kill：没有登记的记为 lost、有登记的被探测与回收，同一记录不会被启动两次；launch 握手超时而 worker 已登记时，不直接记 failed，探测回收后才终结；登记文件损坏时记 unknown。

### 第 10.5 步 · `StreamProducer`

- `production/stream.py`：执行者必须是目标；单实例按记录的 `eval_spec` 逐条执行 `execute` → 打分 → `learn`，严格串行，实际顺序写进 report 与 stats；结束时经句柄 `take_snapshot`；spec 为空、`repeat > 1` 与 `isolation="item"` 拒绝。
- **验收**：一个在线 memory 玩具——`learn` 收到的反馈顺序与观测顺序一致；学习后的快照以输入为基底入账，随后的独立 probe 测量是另一条记录；对执行者 ≠ 目标的请求明确拒绝。

### 第 11 步 · 验收示例矩阵（§7 第 5 步）

- BO 超参示例（`FunctionEvaluator` + `metadata["search_space"]` 约定）；`WithRegularization` + `PenalizedObjective` 端到端（证明主循环零改动，且独立评估下正则天然产出 0.0）；`repeat>1` + 自定义 `Budget`（两级聚合 + infra 故障不计账）；示例中同时演示 `trace_fn` 的写法（§8 提到的 `trace["steps"]` 约定风险）。
- **v4.8 · RSI 契约验收 D1–D6**（任何一条没过，就不声称支持对应的 RSI 形态）：
  - **D1 改进器被实际使用**：G1 产出 G2；下一个请求以 G2 为执行者、A7 为目标；记录能证明实际启动的是 G2（`executor_snapshot_id`、launch 收到的快照），G2 经 `ctx.trial.snapshot` 读到的是 A7，二者没有混用；对 G2 请求 `capture="snapshot"` 被拒绝。
  - **D2 多输出与失败保留**：一次 produce 返回两个候选，分别 measure；其中一次 scorer 失败，两个候选的快照都仍在、都可被 `producers_of` 查到；一个改 `program_config` 的完整快照在 adapter 允许时被接纳、不允许时被拒绝；多输出中一个非法时一个都不提交。
  - **D3 嵌套不死锁**：外层协调者发起子优化，`trial=1, item=1`，再叠一层子协调者，父 run 用 `Window(max_in_flight=1)`；结果要么完成、要么 `spawn` 立即 `BudgetRefused`，不死锁、不超全局上限，每个成本事件在 ledger 与各层 Budget 中只计一次；父协调者所在宿主被 kill 而孙 worker 存活时，恢复对子树做清理式恢复——子 run 里的 pending 被终结而不是被派发，孙 worker 终止确认后协调者才记 lost、释放预留。
  - **D4 选择协议**：baseline 失败时协议保持未绑定、由下一条合格记录绑定，恢复后不重选；只返回 dict 的 `FunctionEvaluator` baseline 同样能绑定；`train` 与 `val` 绑同一份 spec 时，train 角色的测量不争 best、按 train 的可见性交付；显式挑出的简单子集与全量 key 不同、不抢 best；中途换 judge（`ScoreResult.observed` 报出新版本）→ `protocol_drift` 告警，旧 best 不被静默替换。
  - **D5 中断与恢复**：`Window` 预设下分别在「running 已落盘、launch 前」「两条乱序完成后、observe 前」「observe 中」「策略状态更新后、checkpoint 前」四处 kill；resume 后同一记录不被启动两次，observe 的消费顺序与分组和日志一致、已提交的消费不重复，重放拿到的 `ProduceResult` 仍带 outputs，费用不重复，SubprocessBackend 的子进程被探测与回收。
  - **D6 准入与停止**：`Window(max_in_flight=2, on_stop="cancel")`、剩 2 次额度、两条在途——第一条完成后第二条不被取消；`Barrier` 一批 10 条只剩 3 次额度——3 条执行完后循环不卡死，其余 7 条以 `cancelled(stop)` 交付；一批 apply 必然失败的请求不超额计数。

### 第 12 步 · 清场

- 删除：`EvaluationPipeline` / `EvaluationReport` / `EvaluationResult` / `DataLoader`（`evaluators/` 目录整体退役）、`benchmark/scorers/base.py` 中被 `core/scoring.py` 取代的协议定义（仅留 re-export）、`Benchmark.evaluate` / `async_evaluate`、旧 `engine/adapter.py` / `engine/base.py` / `engine/optimizer.py` 中被新模块取代的残留（旧 `TrialContext` / `capture_after_eval` / `Objective` 旧签名 / 全部 `async_*` 成对方法 / `evaluate_fn` 闭包路径与签名嗅探工具函数）；v4.8 退役物：`HistoryView.trial_of`、按 stage 计账的判据、`AnytimeBudget.group_key` 自比较、8 位随机 `snapshot_id` 生成。
- 对照 §「取代」清单逐项 grep 确认零引用后删除，不留兼容层——拆包前是唯一可以不留的窗口。

### 依赖图（可并行的机会）

第 1、2 步互相独立可并行（两者都只往 `core/` 加协议文件，互不触碰）；第 3 步只依赖第 2 步的 `ScoreRequest`；第 4.5 步依赖第 3、4 步，是 v4.8 的唯一前置；第 5 步依赖第 4.5 步的 ctx 类型但不依赖设施实现；第 7 步是第一处汇合点（依赖 1–6）；第 8 步依赖第 4.5 步与第 7 步的 manifest，因此排在第 7 步之后；9a → 9b / 9c / 9d 三者互相独立可并行；第 10 步依赖 9a，10.5 依赖 9b 与 10。关键路径：3 → 4 → 4.5 → 7 → 9a → 9b → 11。
