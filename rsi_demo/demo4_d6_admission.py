"""Demo 4 · D6 准入与停止分开：Window 剩 2 额度 2 在途；Barrier 10 条只剩 3 额度；apply 必败的一批不超额计数。"""
from kernel import *
from toy import *
from common import check, main


class Endless(Script):
    """每次 propose 一条，delay 依次取 cfg["delays"]，用尽后重复最后一个。"""
    def propose(self, st, objective, ctx):
        d = self.cfg["delays"][min(st["step"], len(self.cfg["delays"]) - 1)]; st["step"] += 1
        return [Proposal(ctx.history.run.initial_id, [{"uid": "delay", "value": d}])]


async def demo():
    data = make_data(2)
    ev = DataEvaluator(data, ExactScorer())
    a = task_snapshot(("add",), delay=0)

    # ── Window(max_in_flight=2, on_stop="cancel")，额度 2 ──
    opt = Endless(Toy(a), delays=[0.01, 0.15, 0.0])
    await opt.optimize(ev, ScalarObjective("acc"), eval_spec=spec("dev"), max_trials=2,
                       schedule=Window(max_in_flight=2, on_stop="cancel"), baseline=False)
    run = opt.last_run
    st = [(run.records[i]["status"], run.records[i]["error"]) for i in run.order]
    check("第一条完成后，第二条已获准的在途执行不被取消（ok）", st[0][0] == "ok" and st[1][0] == "ok", str(st))
    check("第三条被拒准入 ≠ 停止；耗尽后它以 cancelled(stop) 交付", st[2] == ("cancelled", "stop") and len(st) == 3, str(st))
    check("停止原因 = exhausted，结算计数恰为 2（不超发）", run.meta["stop_reason"] == "exhausted" and run.budget.settled == 2)
    check("Window：每条一组、按完成顺序交付", opt.state["seen"] == [[run.order[0]], [run.order[1]], [run.order[2]]])

    # ── Barrier 一批 10 条，只剩 3 次额度 ──
    batch = lambda ctx, s: [Proposal(a.id, [{"uid": "delay", "value": 0.001 * i}]) for i in range(10)]
    opt = Script(Toy(a), steps=[batch, batch])
    await opt.optimize(ev, ScalarObjective("acc"), eval_spec=spec("dev"), max_trials=3, baseline=False)
    run = opt.last_run
    sts = [run.records[i]["status"] for i in run.order]
    check("3 条执行完，其余 7 条 cancelled(stop)，循环不卡死",
          sts.count("ok") == 3 and sts.count("cancelled") == 7 and len(sts) == 10, str(sts))
    check("Barrier 那一批作为一个组整体交付给 observe（10 条）", len(opt.state["seen"]) == 1 and len(opt.state["seen"][0]) == 10)

    # ── 一批 apply 必然失败的请求：apply 在预留之后，不超额计数 ──
    badb = lambda ctx, s: [Proposal(a.id, [{"uid": "no_such_unit", "value": i}]) for i in range(5)]
    opt = Script(Toy(a), steps=[badb])
    await opt.optimize(ev, ScalarObjective("acc"), eval_spec=spec("dev"), max_trials=3, baseline=False)
    run = opt.last_run
    recs = [run.records[i] for i in run.order]
    check("apply 失败记 failed(stage=apply, candidate)，计账但不超 3",
          sum(r["stage"] == "apply" and r["failure_class"] == "candidate" for r in recs) == 3 and run.budget.settled == 3)
    check("其余 2 条从未派发 → cancelled(stop)，未做 apply（snapshot_id 为空）",
          [r["status"] for r in recs].count("cancelled") == 2 and all(r["snapshot_id"] is None for r in recs))


main("demo4 D6 准入与停止分开", demo)
