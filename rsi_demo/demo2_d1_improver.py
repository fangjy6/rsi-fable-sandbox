"""Demo 2 · D1 改进器被实际使用 + 元优化（task utility 与 improvement utility 分开记录）。"""
from kernel import *
from toy import *
from common import check, main


async def demo():
    data = make_data()
    ev = DataEvaluator(data, ExactScorer())
    a = task_snapshot(("add",))
    g1 = improver_snapshot(k=1)
    store = {g1.id: g1}
    G2 = {}

    def s_self(ctx, st):          # G1 自修改：执行者 = 目标 = G1，capture auto → snapshot
        return [Proposal(g1.id, kind="produce", producer="self", inputs={"task": "improve_self"})]

    def s_use_g2(ctx, st):        # 激活：之后的请求以 G2 为执行者、A 为目标
        g2 = ctx.history.producers_of(st["g2"])[0]
        return [Proposal(a.id, kind="produce", executor_snapshot_id=st["g2"], producer="improve",
                         inputs={"failed_ops": ["sub", "mul", "mod"]}, metadata={"g2_from": g2.record_id}),
                Proposal(a.id, kind="produce", executor_snapshot_id=st["g2"], producer="snap", inputs={"failed_ops": []})]

    def s_measure(ctx, st):
        return [Proposal(s) for s in st["new_targets"]]

    class D1(Script):
        def observe(self, st, outcomes, objective, ctx):
            super().observe(st, outcomes, objective, ctx)
            for rec, res in outcomes:
                if rec.kind == "produce" and rec.status == "ok":
                    if rec.producer["role"] == "self": st["g2"] = rec.outputs[0]
                    else: st["new_targets"] = rec.outputs

    opt = D1(Toy(a), steps=[s_self, s_use_g2, s_measure])
    backend = LocalBackend()
    await opt.optimize(ev, ScalarObjective("acc"), eval_spec=spec("dev"), max_trials=10, backend=backend, store=store,
                       producers={"self": CallProducer(), "improve": CallProducer(), "snap": CallProducer(capture="snapshot")})
    run = opt.last_run
    recs = [run.records[i] for i in run.order]
    r_self, r_use, r_snap, r_meas = recs[1], recs[2], recs[3], recs[4]
    g2 = r_self["outputs"][0]
    check("G1 自修改产出 G2：执行者=目标=G1，G2 是新内容 id",
          r_self["executor_snapshot_id"] == g1.id == r_self["snapshot_id"] and g2 != g1.id
          and run.store[g2].unit_values["code"] == [["enable_failed", 2]])
    check("producers_of(G2) 给出谱系（那条 produce 记录）", [v.record_id for v in run.ctx.history.producers_of(g2)] == [r_self["record_id"]])
    check("后续请求固定 executor_snapshot_id=G2、snapshot_id=A", r_use["executor_snapshot_id"] == g2 and r_use["snapshot_id"] == a.id)
    check("launch 实际收到的快照是 G2（Backend 侧事实，不是 metadata）", (r_use["record_id"], g2) in backend.launches)
    seen = run.results[r_use["record_id"]]["items"][0]["raw_prediction"]
    check("G2 经 ctx.trial.snapshot 读到的是 A，自身代码是 G2 的（未混用）",
          seen["seen_target"] == a.id and seen["executor_code"] == [["enable_failed", 2]])
    check("对 G2→A 请求 capture='snapshot' 被拒绝（stage=produce）",
          r_snap["status"] == "failed" and r_snap["stage"] == "produce" and "NotSupported" in r_snap["error"])
    check("G2 一次修好 2 个算子，产出被测量且成为 best",
          r_meas["snapshot_id"] == r_use["outputs"][0] and run.best_record()["record_id"] == r_meas["record_id"]
          and r_meas["metrics"]["acc"] > recs[0]["metrics"]["acc"])

    # ── 元优化：外层在固定内层预算下比较 G1 / G2 的 improvement utility，用独立的 test 数据 ──
    test_ev = DataEvaluator(data, ExactScorer())
    inner_ids = {}

    async def meta_fn(program, sp, ctx):
        child = await ctx.run.runs.spawn(
            ImproveLoop(Toy(a), improver_id=ctx.snapshot.id), evaluator=ev, objective=ScalarObjective("acc"),
            specs={"val": spec("train")}, budget=Budget(trials=5, usd=1.0), producers={"improve": CallProducer()})
        inner_ids[ctx.snapshot.id] = child
        best = child.best_record()
        rep = await test_ev.evaluate(Toy().from_snapshot(child.store[best["snapshot_id"]]), spec("test"))
        return {"improvement_utility": rep["metrics"]["acc"]}

    class Meta(Script):
        pass
    outer = Meta(Toy(g1), steps=[lambda ctx, st: [Proposal(g2)]])
    best_g = await outer.optimize(FunctionEvaluator(meta_fn, ("improvement_utility",), describe={"inner": "ImproveLoop/5"}, coordinator=True),
                                  ScalarObjective("improvement_utility"), eval_spec=spec("meta"),
                                  budget=Budget(trials=3, usd=10.0), store=run.store, producers={})
    orun = outer.last_run
    o1, o2 = (orun.records[i] for i in orun.order)
    c1, c2 = inner_ids[g1.id], inner_ids[g2]
    check("外层记录只有 improvement_utility；内层记录只有 task utility(acc)",
          set(o1["metrics"]) == {"improvement_utility"} and all(set(r["metrics"]) <= {"acc"} for c in (c1, c2) for r in c.records.values()))
    check("内层记录 parent_scope 指向外层协调者记录（两层分开记账、可追溯）",
          all(r["parent_scope"] == {"run_id": orun.run_id, "record_id": o2["record_id"]} for r in c2.records.values()))
    print("    improvement_utility:", o1["metrics"], o2["metrics"])
    check("同一内层预算下 G2 的 improvement utility > G1", o2["metrics"]["improvement_utility"] > o1["metrics"]["improvement_utility"],
          f"{o1['metrics']} vs {o2['metrics']}")
    check("外层 best = G2（改进器选择本身是一次普通 measure）", best_g.values["code"] == [["enable_failed", 2]])
    check("内层每一个 produce 都以被评估的改进器为执行者",
          all(r["executor_snapshot_id"] == g for c, g in ((c1, g1.id), (c2, g2)) for r in c.records.values() if r["kind"] == "produce"))
    check("评估数据独立：外层用 test，内层只用 train", not set(data.ids("test")) & set(data.ids("train")))
    check("父 run 为子 run 预留的额度在子 run 结束后关闭", all(not c["open"] for c in orun.budget.children.values()))


main("demo2 D1 改进器被实际使用 + 元优化", demo)
