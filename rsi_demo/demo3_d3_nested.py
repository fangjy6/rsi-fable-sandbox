"""Demo 3 · D3 嵌套：三层 run、外层 Window(1)、全局 Limiter(trial=1,item=1)；不死锁、不超上限、
成本沿作用域链每层只计一次；预算不足时 spawn 立即 BudgetRefused；非协调者位置 spawn 被拒。"""
import asyncio, time
from kernel import *
from toy import *
from common import check, main


class DupScorer(ExactScorer):
    """模拟远端重复回报：同一 event_id 报两次。"""
    async def score(self, req, ctx):
        eid = f"{ctx.trial.record_id}#{req.key[0]}#{req.key[1]}#judge"
        ctx.events.cost("judge", 0.02, event_id=eid)
        ctx.events.cost("judge", 0.02, event_id=eid)
        return await super().score(req, ctx)


async def demo():
    data = make_data(4)
    leaf_ev = DataEvaluator(data, DupScorer())
    a = task_snapshot(("add",))
    sub = [{"uid": "ops", "value": {"add": True, "sub": True, "mul": False, "mod": False}}]
    refused, nest_errs = {}, []

    async def middle_fn(program, sp, ctx):          # 第二层协调者：再 spawn 叶子 run
        inner = await ctx.run.runs.spawn(Script(Toy(a), steps=[lambda c, s: [Proposal(a.id, sub), Proposal(a.id)]]),
                                         evaluator=leaf_ev, objective=ScalarObjective("acc"), specs={"val": spec("dev")},
                                         budget=Budget(trials=4, usd=0.5), schedule=Window(max_in_flight=2))
        return {"inner_best": inner.best_record()["metrics"]["acc"]}

    async def outer_fn(program, sp, ctx):           # 第一层协调者
        usd = 100.0 if ctx.trial_id == 2 else 1.0
        t0 = time.monotonic()
        try:
            mid = await ctx.run.runs.spawn(Script(Toy(a), steps=[lambda c, s: [Proposal(a.id)]]),
                                           evaluator=FunctionEvaluator(middle_fn, ("inner_best",), coordinator=True),
                                           objective=ScalarObjective("inner_best"), specs={"val": spec("meta")},
                                           budget=Budget(trials=3, usd=usd), schedule=Window(max_in_flight=1))
        except BudgetRefused:
            refused["dt"] = time.monotonic() - t0
            raise
        return {"score": mid.best_record()["metrics"]["inner_best"]}

    class Outer(Script):
        def propose(self, st, objective, ctx):
            try:
                coro = ctx.runs.spawn(self, evaluator=None, objective=None, specs={}, budget=Budget(usd=1))
                coro.send(None)
            except NestingRefused:
                nest_errs.append("hook")
            return super().propose(st, objective, ctx)

    lim = Limiter(trial=1, item=1)
    opt = Outer(Toy(a), steps=[lambda c, s: [Proposal(a.id)], lambda c, s: [Proposal(a.id)]])
    await asyncio.wait_for(
        opt.optimize(FunctionEvaluator(outer_fn, ("score",), coordinator=True), ScalarObjective("score"),
                     eval_spec=spec("meta"), budget=Budget(trials=5, usd=3.0), schedule=Window(max_in_flight=1), limiter=lim),
        timeout=10)
    run = opt.last_run
    o = [run.records[i] for i in run.order]
    check("三层嵌套在 10s 内完成（不死锁）", [r["status"] for r in o[:2]] == ["ok", "ok"])
    check("全局上限：trial / item 池峰值都 ≤ 1", lim.peak["trial"] <= 1 and lim.peak["item"] <= 1, str(lim.peak))
    check("预算不够时 spawn 立即 BudgetRefused（不排队），记录失败、run 继续",
          o[2]["status"] == "failed" and "BudgetRefused" in o[2]["error"] and refused.get("dt", 9) < 0.05)
    check("Optimizer 钩子里 spawn → NestingRefused", "hook" in nest_errs)
    opt4 = Script(Toy(a), steps=[])
    await opt4.optimize(FunctionEvaluator(lambda p, s, c: c.run.runs.spawn(None, evaluator=None, objective=None, specs={}, budget=Budget(usd=1))),
                        ScalarObjective("x"), eval_spec=spec("meta"), max_trials=1)
    rec = next(iter(opt4.last_run.records.values()))
    check("非协调者记录里 spawn → NestingRefused（记录失败）", "NestingRefused" in (rec["error"] or ""))

    # ── 成本：每个 event_id 在每一层 ledger / Budget 中只计一次 ──
    bus = run.bus
    uniq = {}
    for e in bus.events:
        if e["kind"] == "cost":
            uniq.setdefault(e["event_id"], e)
    dup_sent = sum(1 for e in bus.events if e["kind"] == "cost") - len(uniq)
    run_ids = {f[1] for e in uniq.values() for f in map(tuple, e["scope"]) if f[0] == "run"}
    ok = True
    for rid in run_ids:
        expect = sum(e["amount"] for e in uniq.values() if ["run", rid] in e["scope"])
        led = bus.ledgers[("run", rid)]["total"]; spent = bus.budgets[rid].spent
        ok &= abs(led - expect) < 1e-9 and abs(spent - expect) < 1e-9
    check(f"重复回报 {dup_sent} 次被去重；{len(run_ids)} 个 run（三层） 的 ledger 与 Budget 都等于子树唯一成本之和", ok and dup_sent > 0)
    total_leaf = sum(e["amount"] for e in uniq.values())
    check("顶层 Budget.spent = 全部叶子成本（只计一次，父级汇总不再上报）", abs(run.budget.spent - total_leaf) < 1e-9,
          f"{run.budget.spent} vs {total_leaf}")
    check("子 run 预留随父 run checkpoint 登记（恢复可找到子树）", len(run.meta["children"]) == 2)


main("demo3 D3 嵌套不死锁、预算正确", demo)
