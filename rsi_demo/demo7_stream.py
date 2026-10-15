"""Demo 7 ·（可选）produce 型在线学习：StreamProducer 按「预测→打分→learn」产出新快照；
train 角色为 summary 可见性时，observe / history 拿不到原文。"""
from kernel import *
from toy import *
from common import check, main


async def demo():
    data = make_data()
    a = task_snapshot(("add",))
    peek = {}

    class Learner(Script):
        def observe(self, st, outcomes, objective, ctx):
            super().observe(st, outcomes, objective, ctx)
            for rec, res in outcomes:
                if rec.kind == "produce" and rec.status == "ok":
                    st["learned"] = rec.outputs
                    peek["metrics"] = res.metrics
                    for f in ("items", "feedback"):
                        try:
                            getattr(res if f == "items" else rec, f); peek[f] = "visible"
                        except HiddenEvidence:
                            peek[f] = "hidden"

    steps = [lambda c, s: [Proposal(a.id, kind="produce", producer="learn", eval_spec=dict(c.specs["train"])),
                           Proposal(a.id, kind="produce", producer="learn", executor_snapshot_id=improver_snapshot().id,
                                    eval_spec=dict(c.specs["train"]))],
             lambda c, s: [Proposal(x) for x in s["learned"]]]
    opt = Learner(Toy(a), steps=steps)
    g = improver_snapshot()
    await opt.optimize(DataEvaluator(data, ExactScorer()), ScalarObjective("acc"),
                       specs={"val": spec("dev"), "train": spec("train")}, visibility={"train": "summary"},
                       producers={"learn": StreamProducer(data, ExactScorer())}, max_trials=10, store={g.id: g})
    run = opt.last_run
    base, prod, bad, meas = (run.records[i] for i in run.order)
    learned = run.store[prod["outputs"][0]]
    check("StreamProducer 产出一个新快照（学会全部算子），谱系可查",
          all(learned.unit_values["ops"].values()) and run.ctx.history.producers_of(learned.id)[0].record_id == prod["record_id"])
    check("实际消费顺序进 stats；过程指标只描述生产过程", prod["stats"]["order"] == data.ids("train") and "stream_acc" in prod["metrics"])
    check("执行者≠目标的 StreamProducer 请求被拒绝", bad["status"] == "failed" and "NotSupported" in bad["error"])
    check("summary 可见性：observe 拿得到 metrics，拿不到 items / feedback",
          peek == {"metrics": prod["metrics"], "items": "hidden", "feedback": "hidden"}, str(peek))
    try:
        run.ctx.history.result(prod["record_id"]).items; hid = False
    except HiddenEvidence:
        hid = True
    check("经 HistoryView 读同一条记录也同样被拦（统一视图构造器）", hid)
    check("学到的状态与之后独立的 val 测量分属两条记录，且成为 best",
          meas["snapshot_id"] == learned.id and run.best_record()["record_id"] == meas["record_id"]
          and meas["metrics"]["acc"] > base["metrics"]["acc"])


main("demo7 StreamProducer 在线学习 + 证据可见性", demo)
