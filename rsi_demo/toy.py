"""玩具世界：算术小题 + 被解释执行的改进器 DSL。全部确定性，无任何模型调用。"""
import asyncio
from kernel import Program, Dataset, ScoreResult, Optimizer, Proposal, spec, Snapshot

OPS = {"add": lambda a, b: a + b, "sub": lambda a, b: a - b, "mul": lambda a, b: a * b, "mod": lambda a, b: a % b}


def make_data(n_per_split=8):
    splits = {}
    for si, split in enumerate(("train", "dev", "test", "meta")):
        rows = []
        for i in range(n_per_split):
            op = list(OPS)[i % 4]
            a, b = 3 + i + 7 * si, 2 + (i % 3)
            rows.append({"id": f"{split}-{i}", "input": {"a": a, "b": b, "op": op}, "ref": OPS[op](a, b)})
        splits[split] = rows
    return Dataset(splits)


class Toy(Program):
    """一个 adapter 按 program_config["form"] 分派程序形态（task / improver）。"""
    def from_snapshot(self, snap):
        return {"task": TaskProgram, "improver": ImproverProgram}[snap.program_config.get("form", "task")](snap)


class TaskProgram(Toy):
    async def execute(self, inputs, ctx):
        d = self.values.get("delay", {}).get(inputs["op"], 0) if isinstance(self.values.get("delay"), dict) else self.values.get("delay", 0)
        if d: await asyncio.sleep(d)
        ctx.events.cost("llm", 0.01, event_id=f"{ctx.trial.record_id}#{ctx.key[0]}#{ctx.key[1]}")
        if self.values.get("crash"): raise RuntimeError("候选崩溃")
        mem = self.values.get("memory", {})
        key = f"{inputs['op']}:{inputs['a']}:{inputs['b']}"
        if key in mem: return mem[key]
        return OPS[inputs["op"]](inputs["a"], inputs["b"]) if self.values["ops"].get(inputs["op"]) else 0

    def learn(self, feedback, ctx):          # 在线学习：答错就把这道题的正确答案记进 memory（produce 实例独占状态）
        if feedback.metrics["acc"] < 1:
            inp = feedback.details["inputs"]
            self.values.setdefault("memory", {})[f"{inp['op']}:{inp['a']}:{inp['b']}"] = feedback.details["reference"]


class ImproverProgram(Toy):
    """改进器 = 快照里的一段 DSL：[["enable_failed", k], ...]。解释执行。"""
    async def execute(self, inputs, ctx):
        ctx.events.cost("llm", 0.05, event_id=f"{ctx.trial.record_id}#improve")
        target = ctx.trial.snapshot                       # 目标经 ctx.trial.snapshot 读到
        if inputs.get("task") == "improve_self":          # 自修改：改自己独占的状态，由 take_snapshot 捕获
            self.values["code"] = [[op, arg + 1] if op == "enable_failed" else [op, arg] for op, arg in self.values["code"]]
            return {"seen_target": target.id, "outputs": []}
        ops = target.unit_values["ops"]
        for op, arg in self.values["code"]:
            if op == "enable_failed":
                for name in [o for o in inputs["failed_ops"] if not ops.get(o)][:arg]:
                    ops[name] = True
        return {"seen_target": target.id, "executor_code": self.values["code"],
                "outputs": [[{"uid": "ops", "value": ops}]]}


class ExactScorer:
    """judge 版本可切换：versions 是按调用序号生效的版本表；v2 是「宽松」judge（永远给 1 分）。"""
    metric_keys = ("acc",)
    def __init__(self, switch_after=None): self.calls, self.switch_after = 0, switch_after
    def describe(self): return {"rubric": "exact-match"}

    async def score(self, req, ctx):
        self.calls += 1
        v = "judge-v2" if self.switch_after is not None and self.calls > self.switch_after else "judge-v1"
        ok = 1.0 if (v == "judge-v2" or req.prediction == req.reference) else 0.0
        return ScoreResult({"acc": ok}, details={"inputs": req.inputs, "reference": req.reference}, observed={"judge": v})


def task_snapshot(ops=("add",), **extra):
    return Snapshot({"ops": {o: (o in ops) for o in OPS}, **extra}, {"form": "task"})


def improver_snapshot(k=1):
    return Snapshot({"code": [["enable_failed", k]]}, {"form": "improver"})


class ImproveLoop(Optimizer):
    """内层优化：测 → 用固定改进器产出 → 测产出 → 择优。改进器由 cfg["improver_id"] 指定。"""
    required_producers = frozenset({"improve"})
    def init_state(self): return {"current": None, "cur_score": -1, "failed": [], "to_measure": []}

    def propose(self, st, objective, ctx):
        if st["to_measure"]:
            ms, st["to_measure"] = st["to_measure"], []
            return [Proposal(s) for s in ms]
        if st["current"] is None: return []
        return Proposal(st["current"], kind="produce", executor_snapshot_id=self.cfg["improver_id"], producer="improve",
                        inputs={"failed_ops": st["failed"]})

    def observe(self, st, outcomes, objective, ctx):
        for rec, res in outcomes:
            if rec.status != "ok": continue
            if rec.kind == "produce":
                st["to_measure"] += rec.outputs
            elif rec.metrics["acc"] > st["cur_score"]:
                st.update(current=rec.snapshot_id, cur_score=rec.metrics["acc"],
                          failed=sorted({it["raw_example"]["op"] for it in res.items if it["metrics"]["acc"] < 1}))


class Script(Optimizer):
    """按脚本逐批提交：cfg["steps"] 是 [ctx -> proposals] 的列表；observe 记下交付分组。"""
    required_specs = frozenset({"val"})
    def init_state(self): return {"step": 0, "seen": []}

    def propose(self, st, objective, ctx):
        steps = self.cfg["steps"]
        if st["step"] >= len(steps): return []
        st["step"] += 1
        return steps[st["step"] - 1](ctx, st)

    def observe(self, st, outcomes, objective, ctx):
        st["seen"].append([r.record_id for r, _ in outcomes])
