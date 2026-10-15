# RSI 方法调研与 EvoAgentX 架构适配评审

调研日期：2026-10-13。评审对象：`debug/self_evolving_optimizer_architecture.md`，尤其是第 3、7–16、19 节。本文是研究与设计评审，不表示相关方法已在仓库实现或复现。

配套设计：[RSI 扩展 RFC](/Users/jinyuan/Desktop/workspace/remote/EvoAgentX/debug/self_evolving_optimizer_rsi_extension.md)。

## 1. 结论

**可以沿用现有 Adapter-first 设计，扩展成覆盖当前主要 RSI 范式的框架；但仅增加 `CodeSpec`、`GraphSpec` 或几个 optimizer 子类还不够。** 需要把当前的“修改一组值并评分”扩展为“执行有明确版本与资源作用域的操作，保存产物和反馈，再决定后续更新与调用”。一次操作可以只补充证据，不必创建候选或产生分数。

现有三个用户概念仍然成立：

- `ProgramAdapter`：把任意程序，包括 optimizer、训练器、任务生成器、judge，接入框架。
- `OptimizationUnit`：声明可变表面。它可以是 prompt，也可以是整个代码包、权重引用或一个策略配置。
- `ValueSpec`：约束候选表示。它不应负责训练、启动进程或运行自修改代码。

缺口主要集中在三层：

1. **制品与运行实例**：多文件代码、动态结构、权重、依赖、持久状态需要版本化；默认静态候选隔离，显式共享或在线状态按交互协议执行。
2. **改进策略**：产生改进的程序本身要能被修改，并实际参与下一代生成；还要支持分支、群体经验、延迟收益。
3. **实验协议**：丰富反馈、在线适应、嵌套优化、变化中的评价器、训练任务和预算都需要明确语义。

建议先完成前两层和实验协议的基础部分，支持 harness evolution 与 optimizer self-improvement；权重训练和 evaluator/curriculum co-evolution 作为可选后端逐步接入。**框架统一的是实验生命周期和版本关系，不必统一所有方法的搜索算法。**

### 1.1 本报告的设计用途

**目标是提炼通用范式，不是为下文每篇论文添加专用核心模块。** 方法地图是需求证据和覆盖测试集；论文中的角色、动作和阶段不应直接变成框架的封闭枚举。判断扩展是否合理，要看未知方法能否通过新的表示、策略、操作契约或后端接入，而不是看内置多少方法名称。

配套 [通用扩展 RFC](/Users/jinyuan/Desktop/workspace/remote/EvoAgentX/debug/self_evolving_optimizer_rsi_extension.md) 是扩展方案的规范入口：稳定内核约束身份、执行、证据、资源与激活；操作和角色开放；archive、单 incumbent、固定阶段和分 epoch 评价均为可选预设。本文保留研究依据及对原始基线的缺口判断；接口以 RFC 为准。RFC §14 说明与后续引擎的兼容边界，§17 用假想新组合测试扩展性。

## 2. 范围、来源与证据强度

本次采用面向架构的范围综述：检索 recursive self-improvement、self-modifying agents、meta-evolution、harness evolution、self-training、self-play、evaluator co-evolution 等关键词，并沿核心论文的相关工作补查。纳入经典前驱、2025 年代表方法，以及截至调研日可以查到的 2026 年工作；特别核对了仓库 `seed_rsi.md` 中的四个种子题目。

事实依据使用论文原文、作者项目页或官方仓库；搜索中的第三方总结只用于发现线索。表中：

- **M**：读取了原论文的方法、算法或实验协议相关段落；不表示逐页精读全文或复现实验。
- **A**：核对论文摘要，适合判断范式；不据此断言实现细节。
- **O**：官方项目说明。

这是对**主要方法类别的全面覆盖**，不是穷尽所有包含 self-evolution 字样论文的系统综述。没有运行各论文实验，也没有把不同模型、预算、数据划分下的成绩做横向排名。近期预印本的机制可用于设计参考，性能声明仍需独立复现。日期主要采用初次公开时间；方法细节链接尽量固定到所读取的版本。

补充综述交叉检查：[RSI: From Bounded Self-Refinement to Autonomous Research Loops](https://arxiv.org/abs/2607.07663)、[Self-Improving Agents in the Era of Experience](https://frontisai.github.io/Awesome-Self-Improving-Agents/)。它们帮助检查分类遗漏，下面的具体机制判断尽量回到方法原文。

## 3. 首先区分：反复改进、递归改进、元改进

文献没有完全统一的 RSI 定义。例如 STOP 明确说它不修改 LM 权重，因此不属于其采用的“full RSI”；DGM、Hyperagents 等工作则讨论冻结模型下的 agent-system RSI。不能用“是否改权重”或“是否改 Python”单独判断递归性。[STOP 原文](https://arxiv.org/abs/2310.02304)、[Hyperagents 方法](https://arxiv.org/html/2603.19461v1#S3)。

本文采用以下**分析维度**，不是声称这是领域统一分级：

| 类别 | 核心问题 | 例子 | 是否必然改变改进机制 |
|---|---|---|---|
| 单次输出修正 | 这道题的答案能否更好？ | Self-Refine | 否 |
| 持久经验适应 | 下次执行能否利用过去经验？ | Reflexion、Voyager、ACE、ReasoningBank | 否 |
| 系统设计搜索 | 哪个 prompt、workflow、harness 更好？ | GEPA、ADAS、AFlow、Meta-Harness | 否 |
| 自指改进 | 被改进的 agent 是否也是执行后续改进的 agent？ | SICA、DGM | 可以，但部分外层机制仍固定 |
| 元改进 | 产生、选择或学习改进的机制能否改变？ | Promptbreeder、STOP、Hyperagents | 是，范围因方法而异 |
| 学习闭环共同演化 | 数据、课程、权重、评价器是否互相推动更新？ | SEAL、AZR、RQGM、Evo² | 不一定重写整个学习算法 |

因此，prompt 方法也可能具有元改进，例如 Promptbreeder 的 mutation prompt；修改整个 repo 也可能只是固定搜索器下的普通优化。反复调用 `optimize()` 不自动变成 RSI。

还有一个独立问题：**某种方法能自修改，不等于它已经证明改进速度会持续加速。** 工程上能运行闭环、有限预算内提升任务分数、提升后代生成能力、跨域迁移、长期无界加速，是不同强度的结论。

理论上的 Gödel Machine 通过证明自重写有益来触发修改；经验 benchmark 的提升不能代替这种证明。本报告建议的框架以可执行实验为核心，可以接入证明器作为专用验证后端，但不声称实现了理论 Gödel Machine 的最优性保证，也不覆盖任意不受约束的内核自重写。[Gödel Machines 原始论文](https://arxiv.org/abs/cs/0309048)。

## 4. 方法地图与当前设计的覆盖程度

以下“适配判断”都是针对用户指定设计文档的工程推断，不是论文作者对 EvoAgentX 的评价。“小扩展”仍需要实现算法本身，不表示已有现成支持。

### 4.1 输出、prompt、memory 与 skill

| 方法 / 时间 | 实际更新对象与反馈 | 递归边界 | 对当前设计的要求 | 来源 / 阅读 |
|---|---|---|---|---|
| Self-Refine / 2023-03 | 当前输出；同模型产生反馈并迭代修订 | 单任务内循环，不要求跨任务持久状态 | 可以放在 `execute()` 内；通常不必新增 optimizer 概念 | [论文](https://arxiv.org/abs/2303.17651) A |
| Reflexion / 2023-03 | 把失败反馈写成 episodic reflection | 反思策略固定，记忆随 trial 变化 | memory 内容 unit、episode 状态范围；不能只有 retrieval policy | [论文](https://arxiv.org/abs/2303.11366) A |
| Voyager / 2023-05 | 可执行 skill 库与自动课程；环境反馈、错误、自验证 | 获得新技能，不等于重写所有改进规则 | skill 集合生命周期、环境任务源、显式学习阶段 | [论文](https://arxiv.org/abs/2305.16291) A |
| Promptbreeder / 2023-09 | task prompt 与 mutation prompt 共同进化 | 改进提示本身可变，外部进化框架固定 | 小扩展；mutation prompt 必须绑定到 proposer 的真实调用路径 | [论文](https://arxiv.org/abs/2309.16797) A |
| GEPA / 2025-07 | 由轨迹反思改 prompt，并组合互补候选 | 搜索方法本身通常固定 | per-example 结果、轨迹、候选池与合并；只返回均值不足 | [论文](https://arxiv.org/abs/2507.19457) A |
| ReasoningBank / 2025-09 | 从自判成功和失败经验提炼策略记忆；MaTTS 扩大经验采样 | 经验与推理互相增强，模型权重不必更新 | 学习/评估分离、记忆版本、交互预算与样本顺序 | [论文](https://arxiv.org/abs/2509.25140) A |
| ACE / 2025-10 | 用生成、反思、整理维护结构化 playbook | 外部上下文进化，更新流程固定 | 增量 patch、条目身份与删除/合并；不宜只做全文替换 | [论文](https://arxiv.org/abs/2510.04618) A |
| Living-Harness / 2026-07 | episode 反馈更新 episodic memory 和 state graph | domain Evolution-SOP 固定；tools/base context 冻结 | 在线阶段协议、双存储的原子版本、跨任务复用 | [论文](https://arxiv.org/abs/2607.26598) A |
| Procedural Graphs / 2026-09 | 成败轨迹指导图拓扑与属性修改；验证门控、保留拒绝记录 | 推理时图冻结，离线 refiner 更新图 | `GraphSpec` 可表示；需要诊断 rollout、验证分离和 rejection memory | [原文 §3](https://arxiv.org/html/2609.09153v1#S3) M |

**重要区分**：memory policy 是“怎样存取”，memory contents 是“已经学到了什么”；skill enable 是开关，skill implementation 是能力本体。当前第 5.4/17.3 节主要解决前者，不能据此声称完整覆盖经验学习。

### 4.2 程序、workflow 与 harness 搜索

| 方法 / 时间 | 实际更新对象与反馈 | 递归边界 | 对当前设计的要求 | 来源 / 阅读 |
|---|---|---|---|---|
| FunSearch / 2023-12 | 程序 skeleton 中关键函数；自动 evaluator；岛屿种群 | 固定 LLM 与搜索框架，通常不是自改优化器 | 代码制品、候选池、异步测量 | [Nature 原文](https://www.nature.com/articles/s41586-023-06924-6) M |
| ADAS / 2024-08 | meta agent 根据 archive 编写新 agent 代码 | task agent 可变，meta search 通常固定 | `RepositoryAdapter` / code package、archive、执行隔离 | [论文](https://arxiv.org/abs/2408.08435) A |
| AFlow / 2024-10 | 代码表示的 workflow；MCTS 与执行反馈 | 搜索器固定 | 图可稳定表达时用 `GraphSpec`，否则保留 code 原生表示；树节点需绑定版本 | [论文](https://arxiv.org/abs/2410.10762) A |
| AlphaEvolve / 2025-05 | 程序/代码库；LLM 提案、自动验证、进化数据库 | 优化训练基础设施也不自动等于 proposer 自递归 | 完整代码包、评价阶段、种群与资源预算 | [官方说明](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/) O |
| Meta-Harness / 2026-03 | agentic proposer 查询历史代码、得分、完整轨迹，再编辑 harness | 名称中的 meta 不表示其搜索 harness 自身持续被改写 | 可查询证据库、agentic proposal、代码工作区，避免把历史压成短 feedback | [原文 §3](https://arxiv.org/html/2603.28052v1#S3) M |
| Self-Harness / 2026-06 | weakness mining → 多个最小修改 → regression validation | 固定模型和 evaluator 下的 harness 改进 | proposal 批次、失败归因、父版对照、合并后重测 | [原文 §3](https://arxiv.org/html/2606.09498v1#S3) M |
| RRSI / 2026-09 | harness 任意组件；约束提案复杂度与选择过程 | regularized harness search，不要求优化器代码可变 | 历史假设账本、退火编辑预算、噪声/成本门控、pruning | [原文 §3](https://arxiv.org/html/2609.24972v1#S3) M |
| AgentEvolver: System-Wide… / 2026-10 | 多类可复用实体的版本生命周期，执行中积累能力 | 固定 foundation model；任务成功与能力积累分开 | 动态实体、制品谱系、运行/学习耦合协议 | [论文](https://arxiv.org/abs/2610.11613) A |

仓库种子中的 **LongHorizon-Harness** 属于相邻基础设施：Manage–Execute–Audit 把任务状态与执行上下文分离，不能仅凭这篇论文把它列成“自动演化 harness 的方法”。它可以作为被优化程序或长程执行后端。[论文](https://arxiv.org/abs/2608.01964) A。

### 4.3 自指代码改进与真正的 optimizer / meta-agent 改进

| 方法 / 时间 | 关键机制 | 与其他方法的差别 | 对当前设计的要求 | 来源 / 阅读 |
|---|---|---|---|---|
| STOP / 2023-10 | seed improver 接受程序与 utility，再把自身作为待优化程序 | 明确优化“程序改进器”的下游效用 | optimizer-as-program、嵌套运行、meta evaluation | [论文](https://arxiv.org/abs/2310.02304) M |
| Gödel Agent / 2024-10 | agent 读取并改写自身逻辑，包括决策过程 | 不限于预定义 prompt/固定组件 | 自代码制品、可替换策略、明确 activation 边界 | [原文 Algorithm 1](https://arxiv.org/html/2410.04444v1) M |
| SICA / 2025-04 | 历史最佳 coding agent 读取归档并产生后续版本 | best-so-far agent 实际参与下一次改进 | 实际提案者版本与被改状态的父版分别记录和加载 | [原文 §3](https://arxiv.org/html/2504.15228v1#S3) M |
| DGM / 2025-05 | 从 archive 选择 agent，由其生成自修改版本并验证 | 保留可用但非当前最佳的 stepping stones | archive admission 不能等于 best 更新；父版 fork；编辑能力验证 | [原文 Algorithm 1](https://arxiv.org/html/2505.22954v1#A1.SS4) M |
| HGM / 2025-10 | 用后代群体表现估计 clade metaproductivity | 当前任务分数与未来产出潜力并不等价；扩展/补测分开 | descendant credit、局部评估、再评估动作、异步调度 | [原文 §2–3](https://arxiv.org/html/2510.21614v1#S3) M |
| GEA / 2026-02 | 选择父代组，共享不同分支的修改与失败经验 | evolution unit 是 group；不只是独立树分支 | 分清代码父版本与经验供体；batch proposal、跨分支证据 | [原文 §3](https://arxiv.org/html/2602.04837v1#S3) M |
| Hyperagents / 2026-03 | task agent 与 meta agent 同属可编辑程序 | meta agent 能改自身；主实验的外部 parent selection 仍固定 | 版本化 improver、下一代实际使用新 meta 入口、元能力测量 | [原文 §3、§5.2、附录 D.3](https://arxiv.org/html/2603.19461v1) M |
| AIDE² / 2026-09 | 外层改研究 agent，内层在固定预算下优化一组研究任务 | 直接评估研究效率；主实验外层是固定强 agent | 子 run 协议、public/private reward、统一预算与任务族 | [原文 §2、§3.6](https://arxiv.org/html/2609.26457v1) M |

这类工作最直接说明：**待优化程序可以是 optimizer，但框架必须能把 optimizer 当作一个有输入、输出、运行预算和版本的程序来执行。** 把它的源码塞进 `TextSpec`，而 `BaseOptimizer.propose()` 仍调用原先 Python 对象，不构成闭环。

### 4.4 权重、数据、课程与学习规则

| 方法 / 时间 | 可变部分与反馈 | 边界 | 对当前设计的要求 | 来源 / 阅读 |
|---|---|---|---|---|
| POET / 2019 | 环境和 solver 配对共同演化、跨环境迁移 | 开放课程前驱，不是 LLM 自代码方法 | task/environment provider、环境版本与迁移评价 | [论文](https://arxiv.org/abs/1901.01753) A |
| STaR / 2022 | 生成/修正 reasoning，筛选并训练下一轮模型 | self-training，训练规则固定 | 数据制品、训练作业、checkpoint 与 held-out eval | [论文](https://arxiv.org/abs/2203.14465) A |
| Absolute Zero / 2025-05 | 同一模型提任务、解任务；执行器验证；RL 更新 | 自生成课程仍依赖外部可执行环境的 grounding | task generator + trainer；任务合法性和难度/学习收益 | [论文](https://arxiv.org/abs/2505.03335) A |
| SEAL / 2025-06 | 模型生成 self-edit 数据/指令；内层 SFT；外层 RL 学编辑策略 | 优化学习策略，不是让模型随意改全部权重更新算法 | 训练/适应操作、临时权重隔离、下游收益作为 meta reward | [原文 §3](https://arxiv.org/html/2506.10943v1#S3) M |
| R-Zero / 2025-08 | 从同一 base 初始化独立 Challenger/Solver，分别训练并交互生成课程 | 双角色共同演化，不应与 AZR 的单模型角色共享混同 | 多策略 checkpoint、任务生成与求解的交替训练、独立外部评估 | [论文](https://arxiv.org/abs/2508.05004) A |
| EvolveR / 2025-10 | 离线经验蒸馏、在线策略检索和 policy reinforcement | 同时涉及外部经验与策略更新 | 经验管线 + 训练后端；不能只实现 memory setter | [论文](https://arxiv.org/abs/2510.16079) A |
| DiscoRL / 2025 | meta-network 学习 RL 更新规则，通过 agent 学习表现优化 | 学习规则可变，但 meta-training 算法不必自重写 | learner-as-program、训练/环境状态、可微后端可选 | [Nature 原文](https://www.nature.com/articles/s41586-025-09761-x) M |
| AgentEvolver: Towards Efficient… / 2025-11 | self-questioning、self-navigating、self-attributing | 此篇与 2026 年同名系统是不同论文 | 数据生成、经验重用、trajectory credit、RL 后端 | [论文](https://arxiv.org/abs/2511.10395) A |
| Search over Self-Edit Strategies / 2026-01 | 搜索自身训练数据模板与超参数，考察 archive | 只研究单轮适应，不能当长期递归成功证据 | 适应策略搜索 + checkpoint 隔离；需防探索同质化 | [论文](https://arxiv.org/abs/2601.14532) A |
| TTT-Discover / 2026-01 | 测试时在当前问题上训练 proposer | 目标是找到一个优秀解，不要求跨任务泛化 | 训练与搜索交错、best artifact 和 policy checkpoint 分开 | [论文](https://arxiv.org/abs/2601.16175) A |
| OPD-Evolver / 2026-06 | 快环管理记忆，慢环把经验与管理能力蒸馏进策略 | 双时间尺度共同演化 | online adaptation + distillation jobs + 两类状态谱系 | [论文](https://arxiv.org/abs/2606.17628) A |
| Evo² / 2026-10-09 | harness 搜索训练共享 Proposer/Reflector；SFT + interleaved GRPO | 更新的是 evolver 的权重；task executor 在共同演化时固定 | 分离 proposer / executor 模型角色，记录编辑轨迹与训练版本 | [原文 §3](https://arxiv.org/html/2610.13025v1#S3) M |

这组方法要求新增训练能力，但不要求把 GPU trainer 塞进 optimizer core。核心可以调度外部训练作业、绑定不可变 checkpoint；RL/SFT 的数学细节仍留在专用后端。

### 4.5 评价器与目标共同演化

| 方法 / 时间 | 变化对象与反馈 | 架构意义 | 来源 / 阅读 |
|---|---|---|---|
| Self-Rewarding LM / 2024-01 | 模型生成答案并自评分，迭代 DPO | 训练 reward 与最终度量要区分，judge 版本须记录 | [论文](https://arxiv.org/abs/2401.10020) A |
| Meta-Rewarding / 2024-07 | 对 judgment 再评价，改进判断能力 | task 能力和 evaluator 能力应有不同实验协议 | [论文](https://arxiv.org/abs/2407.19594) A |
| Red Queen Gödel Machine / 2026-06 | epoch 内 utility 固定，epoch 间更新 utility | 不能把不同 evaluator/utility 下的历史分数直接比较 | [论文](https://arxiv.org/abs/2606.26294) A；HTML 未取得，仅采用摘要层机制 |
| SBCO / 2026-08 | verifier bank 与 harness policy 分块交替优化 | co-evolution 不一定需要可自改的 meta agent | [论文](https://arxiv.org/abs/2608.10157) A |
| Who Verifies the Verifier? / 2026-10-08 | 组合可检查 detector，用 anchored agreement 选 verifier，与 skill loop 交替 | grader 自身需要独立有效性测试；任务得分无法单独证明 grader 正确 | [原文 §3、§5](https://arxiv.org/html/2610.11464v1) M |

这里需要版本化 evaluator 和明确的比较协议。冻结比较窗口、重测参考候选、独立外层锚点是一种保守方案；连续变化或跨条件估计也可使用其他有依据的协议。不能仅改 `scorer` 后把同名 `accuracy` 当作原尺度的能力提升。

## 5. 可以抽取出的通用范式

以下是依据上述方法形成的**架构归纳**，不是某篇论文已有的统一算法。

首先将状态分成开放的制品及绑定，而不是固定列举 task/meta/judge 等组件：

```text
S_t = (versioned artifacts, executable bindings, persistent state refs)
H_t = observed evidence and execution history
actions_t = policy_version(S_t, H_t, incoming_events, resource_context)

K = (artifact and invocation identity, capability boundaries,
     resource accounting, evidence provenance, explicit activation)
```

`K` 是建议保留稳定的实验基础设施；`policy_version` 自身也可属于 `S_t` 的可执行绑定。制品可以表达任意角色和表示，动作由版本化契约解释；反馈可以延迟到来，动作也可以返回零个或多个作业。evaluator、curriculum、search policy 都可演化，而记录与比较规则有明确版本。这是架构描述，不是声称所有学习算法都能无损转为离散、无梯度的黑盒搜索。

一种常见的候选搜索预设可以表示为：

```text
1. 从 archive / incumbent / experience pool 选择参考版本与证据
2. 用某个明确版本的 improver 决定下一步实验
3. 构造候选：改值、改文件、学习记忆、训练权重，或组合这些动作
4. materialize 独立的候选实例
5. 按固定 EvaluationProtocol 运行，得到逐样本结果、轨迹和成本
6. 记录整个实验，包括失败和拒绝的候选
7. 分别决定：归档、继续探索、更新 incumbent、输出最终制品
8. 若修改了 improver，在未来生成阶段显式绑定其新版本
```

这比 `propose → apply → evaluate → update` 多出的本质信息是：**谁提案、从哪个版本构建、运行什么过程、评价的到底是什么、结果在哪个未来环节生效。**

通用的是这些问题及对应契约，不是上面的八步顺序。持续训练可以没有候选接受环节，异步系统可以先补充证据再决定更新，群体系统可以没有唯一 incumbent。新方法不应被迫伪造 candidate、score 或 epoch 才能接入。

### 5.1 表示形式和系统角色是两个正交维度

| 表示形式 | task 角色 | improver 角色 | evaluator / curriculum 角色 |
|---|---|---|---|
| text | task prompt | mutation prompt | judge rubric、任务生成指令 |
| structured data | memory / skill index | search config、经验摘要 | 任务分布、检测器组合 |
| graph | workflow / procedural graph | 搜索决策图 | curriculum graph |
| code package | agent harness | optimizer / meta agent | verifier / task generator |
| checkpoint | task model | proposer / reflector model | learned reward model |

因此不建议继续增加 `PromptOptimizer`、`MemoryOptimizer`、`SkillOptimizer` 这样的核心类型来追赶论文。`kind` 描述表示，role/binding 描述实际用途，algorithm 描述更新方法。

表中行列都只是例子，不能变成封闭类型。扩展应沿六个正交问题组织：更新对象、表示与恢复、更新算子、反馈契约、交互与调度、递归绑定。组合这些维度比不断添加“某某论文模式”更能容纳未来方法。

### 5.2 三种收益不能混用

1. **Task utility**：当前 agent 在任务上得分多少。
2. **Improvement utility**：让某个 improver 从相同初始条件出发，在给定预算内能产生多好结果。
3. **Exploration value**：候选是否提供互补能力、新行为或更有潜力的后代。

普通 `Objective` 可以比较数值，但不负责自动产生这三种实验。尤其是 HGM 的后代信用和 Hyperagents 的元能力测量，需要先有 lineage 和子实验支持，再定义相应指标。[HGM](https://arxiv.org/html/2510.21614v1#S3)、[Hyperagents 附录 D.3](https://arxiv.org/html/2603.19461v1#A4.SS3)。

## 6. 对现有设计逐项评审

### 6.1 已经正确、应当保留的部分

- **对象无关的 Adapter 边界**：比只针对 EvoAgentX Agent/Workflow 的方案更适合 RSI。
- **局部 unit 与整体 apply 分离**：能表达跨组件事务和 client/graph 重建。
- **`ValueSpec` 与 algorithm 分离**：把候选表示与搜索策略解耦。
- **自定义 `evaluate_fn`**：为嵌套实验、外部 benchmark 留出了逃生口。
- **明确评估副作用**：第 15 节已注意 memory/cache 污染；这正是在线方法需要的出发点。
- **Objective、Group/Relation、SearchPrimitive**：继续保持可选，不把某个算法的图语义写进 core。

因此，不需要推翻架构。主要问题是当前协议省略的信息还太多，导致“什么都能包装进去”和“能够可靠、可复用地实现方法”之间有距离。

### 6.2 缺口清单

| 缺口 | 原文位置 | 具体后果 | 建议 |
|---|---|---|---|
| 候选缺少父版本 | §7；§12.2 选择 parent，却只返回局部值 | 在当前 runtime 上应用来自旧父代的 delta，会形成未被提案的混合版本 | 复用后续已有 `Proposal.source_snapshot_id`；donor 另记 |
| 单个可变 runtime | §3.2、§8 | 群体分支/并发候选互相污染；不能可靠恢复任意旧代码版本 | 按 snapshot 重建；简单 setter 兼容路径串行执行；显式共享单独声明 |
| unit 索引只在构造时建立 | §8 `UnitIndex(adapter.units())` | 新增 skill/tool/node 后索引、约束和快照可能失配 | 稳定 container unit 优先；必要时按版本 rediscover/reindex |
| 快照只默认来自 unit 值 | §4、§15 | 文件、依赖、向量库、权重、优化器状态未必能重建 | 明确 snapshot coverage 与 immutable artifact manifest |
| `evaluate_fn() -> dict` 信息不足 | §9 | 反思无法查轨迹；GEPA/GEA 丢逐题互补性；HGM 难补测 | 兼容 dict，同时支持结构化结果和可查询证据引用 |
| `accepted` 语义合并 | §3.2 的 `record(trial, objective)` | 非最佳但有探索价值的候选可能被永久排除 | record / archive admission / incumbent promotion 分离 |
| `propose()` 默认是固定宿主方法 | §11–13 | 改了 optimizer 源码却没改变后续实际生成器 | 版本化策略入口；保留既有算法 wrapper，下一次调用明确绑定版本 |
| 一次 trial 总是假定立即得到 value | §3.2、§6 | SFT、RL、构建、环境采集无法有清晰成本/取消/恢复语义 | 开放 `ExperimentPlan` 操作契约，允许异步作业和非分数结果 |
| 没有子运行身份与预算 | §9、§11 | meta optimizer 的候选得分和内层试验混在一起，费用可能重复统计 | `parent_run_id`、子预算、结果引用、独立状态 |
| `preserve_if_accepted` 太粗 | §15 | online memory 是学习还是评估污染依赖场景，不能只按一次分数决定 | 明确 adapt/probe、episode/stream 边界与状态输出 |
| 评价契约没有完整版本 | §9–10 | 数据、judge、预算变化后历史 score 不再默认可比 | 版本化契约与证据，比较由策略解释；默认重测而非强制 epoch |
| `max_steps` 替代资源约束 | §11 | 1 个昂贵长程候选和 1 个简单 prompt trial 被当作等价 | 实际 token、费用、时间、GPU 与嵌套预算 |

上述位置对应指定文档；例如 [父代选择示例](/Users/jinyuan/Desktop/workspace/remote/EvoAgentX/debug/self_evolving_optimizer_architecture.md:867)、[固定 unit 索引](/Users/jinyuan/Desktop/workspace/remote/EvoAgentX/debug/self_evolving_optimizer_architecture.md:625)、[评估边界](/Users/jinyuan/Desktop/workspace/remote/EvoAgentX/debug/self_evolving_optimizer_architecture.md:681)、[副作用策略](/Users/jinyuan/Desktop/workspace/remote/EvoAgentX/debug/self_evolving_optimizer_architecture.md:1062)。

### 6.3 现有伪代码还有三处语义需要收紧

**失败路径**：§3.2 在 `normalize/apply/evaluate` 周围没有完整异常处理，但后面读取 `trial.error`。如果前面抛异常，trial 可能根本不会产生。规范应要求失败也生成 record，并在 `finally` 中释放实例；恢复失败应标记实例不可用。

**`preserve_if_accepted` 对应哪个 best snapshot**：默认保存 `snapshot_after_apply` 对静态评估正确；若在线学习状态被有意保留，best/version 就应明确指向学习后的状态，且带上训练数据/任务流来源。不能运行时保留了记忆，`restore_best` 又丢失它。

**normalize 和语义修复**：去 fence 是格式处理；补模板变量、截断代码、调用 LLM repair 是候选内容变化。需要分别保存 raw candidate、normalized candidate、repair lineage，并统计 repair 成本。特别是 code package，不能用文本截断保证“合法”。

### 6.4 不要把旧文档的缺口误判成当前代码完全缺失

工作区已经有更丰富的实现结构：

- [context.py](/Users/jinyuan/Desktop/workspace/remote/EvoAgentX/evoagentx/optimizers/core/context.py:52)：`ArtifactRef`、`ArtifactStore`、history、budget、events、trial/item context。
- [results.py](/Users/jinyuan/Desktop/workspace/remote/EvoAgentX/evoagentx/optimizers/core/results.py:150)：逐 observation 的输入、输出、reference、trace、artifact、feedback。
- [records.py](/Users/jinyuan/Desktop/workspace/remote/EvoAgentX/evoagentx/optimizers/core/records.py:48)：多种 change operation、纯 JSON 持久化约束。
- [outcome.py](/Users/jinyuan/Desktop/workspace/remote/EvoAgentX/evoagentx/optimizers/core/outcome.py:19)：record 与运行期完整结果配对。

后续 `records.py` 已有带 `source_snapshot_id` 的 Proposal，`Snapshot.program_config` 可表达重建配置。因此上表的父版和基本快照缺口针对原始设计，不应再新增第二套 Proposal / ProgramVersion。`TrialOutcome` 目前仍是测量结果；训练作业及延迟反馈需要内部调用记录与作用域扩展。

后续引擎明确规定“一条 trial = 候选的一次测量”“状态变更来自 propose”“execute 只读”。RFC 保留这些规则在静态测量中的意义，同时明确一般学习操作超出了它们的作用范围。这是需要设计及迁移的扩展，不能称“现有接口已无缝支持”。这里核对了相关设计章节和数据类型，没有完成整个仓库实现审计，也未修改工作区代码。

## 7. 几个最容易被论文名称和成绩误导的地方

### 7.1 DGM 不等于全系统自改，Hyperagents 也有固定外层

DGM 的 coding agent 参与自修改，但自改指令生成存在固定机制；Hyperagents 把 task/meta 两部分放入可编辑程序，主实验仍固定外部父代选择。这支持“稳定内核 + 可演化策略”的实现方式，而不是要求把事务、评价记录和资源管理全部交给被测程序改写。[Hyperagents §1、§3](https://arxiv.org/html/2603.19461v1#S3)。

### 7.2 archive 不是 history 的别名

history 回答“发生过什么”；archive 回答“哪些版本还能作为未来起点”。失败候选也要有 history，但不一定允许执行；低分但有效候选可以留在 archive，而不应成为对外使用的 incumbent。DGM 的有效子代入档规则就是一个具体例子。[DGM Algorithm 1](https://arxiv.org/html/2505.22954v1#A1.SS4)。

### 7.3 优化能力不能只靠一次 task score 衡量

HGM 关注后代贡献；Hyperagents 另测固定 meta agent 产生后续改进的能力；AIDE² 以固定预算下的研究成果评价研究 agent。但 AIDE² 主运行使用固定外层强 agent，其替换外层 agent 的补充实验仍受噪声和种子成本限制，不能把主结果写成“已证实持续自加速”。[HGM](https://arxiv.org/html/2510.21614v1)、[AIDE² §1、§2](https://arxiv.org/html/2609.26457v1)。

### 7.4 held-out 要看谁能看、谁用于决策

- Meta-Harness 的 TerminalBench-2 实验明确把同一 89-task benchmark 用于搜索和最终评价；其他设置另有泛化测试，不能互相替代。
- Self-Harness Algorithm 1 用 held-out split 的变化参与候选接受，因此它相对 weakness mining 被留出，但不是完全不参与选择的最终 test。
- Who Verifies the Verifier 的 test 对 verifier evolution 锁定，但 skill loop 用它做评分/rollback；原文也指出 skill-side 绝对成绩比第三个从未触碰的 split 更乐观。

这些是原协议边界，不是笼统否定方法。框架应记录**数据用途与信息可见性**，不要仅靠名为 `test` 或 `held_out` 的字符串判断独立性。[Meta-Harness §4.3](https://arxiv.org/html/2603.28052v1#S4.SS3)、[Self-Harness Algorithm 1](https://arxiv.org/html/2606.09498v1#S3)、[Verifier 原文 §3](https://arxiv.org/html/2610.11464v1#S3)。

### 7.5 权重共同演化必须分清模型角色

Evo² 在主要共同演化设置中训练的是 proposer/reflector 的共享策略，task executor 固定。这和“每次同时把 solver 与 optimizer 的权重都训练一遍”不同。统一框架应记录实际调用位置的模型绑定，允许共享 checkpoint 与任意角色名称；task/improver/judge 只是常见实例。[Evo² §3.1–3.3](https://arxiv.org/html/2610.13025v1#S3)。

### 7.6 evaluator 自进化不能靠自己的分数认证

Who Verifies the Verifier 报告了一个很有针对性的反例：退化的 always-pass grader 仍可能伴随 task score 提升。因此 verifier 的校准/一致性实验与 solver 的下游成绩需要分别记录。类似地，生成更容易的任务并得到更高分，也不能当作原任务能力提升。[原文 §5](https://arxiv.org/html/2610.11464v1#S5)。

### 7.7 通用框架不等于通用最优算法

一项预算匹配研究把 archive、parent selection、exploration、budget allocation 拆开比较，发现固定 harness 在其测试范围内没有普遍优势。这支持把算法留在策略层，而不是将 DGM、MCTS 或 greedy acceptance 写死到 runtime。[Automated Discovery Has No Universally Superior Harness](https://arxiv.org/abs/2607.18235)。

## 8. 建议的扩展规模与实施顺序

| 阶段 | 核心交付 | 代表性验收方法 | 明确不作的能力声明 |
|---|---|---|---|
| P0：版本与证据 | 复用 snapshot/Proposal、代码重建、动态 units、来源与测量；记录和选择解耦 | GEPA 简化版、Procedural Graphs、固定 proposer 的 Meta-Harness 风格实验 | 不声称已有递归 meta improvement |
| P1：开放调用与递归 | 操作派发、调用记录/上下文、candidate/measure/subrun、版本化策略和绑定 | Promptbreeder → STOP → DGM / Hyperagents；HGM/GEA 是可插拔策略 | 不声称任务提升必然对应改进速度提升 |
| P2：长作业与学习 | train/adapt 后端、状态协议、恢复去重及持久成本 | ReasoningBank/ACE 在线协议、SEAL、TTT-Discover、Evo² | 仅接 checkpoint 不等于复现完整训练算法 |
| P3：高级交互 | 持续反馈、共同演化与按需分布式能力；开放比较协议 | AZR/POET 范式、RQGM、verifier co-evolution | 不承诺无界进化或形式化全局最优 |

普通 prompt 用户不应被迫配置这些对象。保留原来的 `optimizer(adapter, dataset, scorer, objective)`；高级能力由 adapter capability、策略插件和实验协议逐步启用。

## 9. 怎样验证“扩展真的支持 RSI”

下面是建议的研究验收协议，不是本次已经完成的实验。

**A. 先验证版本与执行语义。** 单父版修改必须继承指定父版；多源操作声明各输入作用；按承诺范围恢复代码、依赖和 memory；新 meta 版本被后续提案实际调用；失败和取消有记录；并发候选不得共享未声明的可写状态。

**B. 再验证元能力。** 用相同 task agent 初始版本、相同任务族和预算，分别运行旧/new improver；在外层独立数据上评价它们返回的结果。除最终分数外，记录预算—收益曲线、有效候选比例、达到目标所需成本、重复种子的方差。可以加用 Hyperagents 的 improvement@k，但不能把固定候选数等同于固定 token/费用预算。

**C. 隔离收益来源。** 对具有 task/improver 两角色的方法，可设置固定 improver、只变 task、只变 improver、两者都变等消融。研究 improver 的训练收益时控制 task executor；若研究联合训练，则明确耦合并设置相应对照。其他角色结构按其机制设计消融，不强制套用两角色实验。

**D. 验证持久与迁移。** 清空 episode context 后保留已学习制品重测；换任务、换 seed、必要时换 executor model。记录 best 与 final，不只报告被挑选的最好一次。

**E. 明确测试范围。** 对泛化声明，区分搜索、选择与最终评价的信息来源；在线方法可采用 adaptation/probe、prequential 或其他声明的交互协议。分数进入下一轮决策后，那部分数据就是选择信号。流式成绩关联整个学习过程，不能当作最终 snapshot 的静态分数。

**F. 验证共同演化。** evaluator 更换后重测参考候选，或验证声明的跨版本比较依据；后端按承诺恢复 job/checkpoint，不盲目重复提交；按研究目标测量课程可解性、覆盖和旧任务保持。需要数据隔离的实验由执行能力边界落实，不能只靠 prompt 约定。

## 10. 最终判断

你的设计已经是一个合理的**通用优化接入层**。它可以成为 RSI 框架的基础，但目前文档给出的默认闭环主要是“固定 algorithm 优化外部 program”。

要进一步覆盖现有主要 RSI 方法，应当让下面这句话成立：

> 改进机制也可以是被适配的程序；其更新产物能在明确的版本、资源和反馈契约下运行，并被真实绑定到后续改进路径。

真正需要提升为核心契约的是版本、实验和激活语义。反思、MCTS、群体共享、训练损失、课程生成和 judge 组合仍然放在策略/后端中。配套 RFC 给出稳定内核与开放协议、可选执行预设、代表方法映射和未知方法的扩展验收；方法映射用于验证抽象，不决定内核的结构。
