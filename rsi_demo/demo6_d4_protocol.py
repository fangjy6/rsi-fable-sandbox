"""Demo 6 · D4 选择协议与 evaluator 演化：子集 key ≠ 全量 key 不抢 best；数据 reference 改了 key 跟着变；
中途换 judge（ScoreResult.observed 报出新版本）→ protocol_drift，旧 best 不被静默替换。"""
from kernel import *
from toy import *
from common import check, main


async def demo():
    data = make_data()                                     # dev 8 题：add/sub/mul/mod 各 2
    scorer = ExactScorer(switch_after=26)                  # 第 27 次打分起 provider 悄悄换成宽松的 judge-v2
    a = task_snapshot(("add",))
    two = [{"uid": "ops", "value": {"add": True, "sub": True, "mul": False, "mod": False}}]
    allops = [{"uid": "ops", "value": {o: True for o in OPS}}]
    add_ids = [i for i in data.ids("dev") if data.get(i)["op"] == "add"]

    def s_subset(ctx, st):
        return [Proposal(a.id, eval_spec=dict(ctx.specs["val"], example_ids=add_ids))]   # 显式挑简单子集

    def s_refchange(ctx, st):
        data.set_reference("dev-1", 999)                   # 同一批 id 的答案被改
        return [Proposal(a.id, two)]

    def s_judge(ctx, st):
        data.set_reference("dev-1", make_data().reference("dev-1"))
        return [Proposal(a.id, allops)]

    opt = Script(Toy(a), steps=[lambda c, s: [Proposal(a.id, two)], s_subset, s_refchange, s_judge])
    await opt.optimize(DataEvaluator(data, scorer), ScalarObjective("acc"), eval_spec=spec("dev"), max_trials=10)
    run = opt.last_run
    b, r1, r_sub, r_ref, r_judge = (run.records[i] for i in run.order)
    drift = [e["record_id"] for e in run.bus.events if e["kind"] == "protocol_drift"]
    key = run.protocol["measurement_key"]
    check("协议锚定在 baseline", run.protocol["anchor"] == b["record_id"])
    check("同条件下的提升照常成为 best", r1["measurement_key"] == key and r1["progress"] == "improved")
    check("显式简单子集：分数 1.0，但 key 与全量不同、不抢 best、也不算漂移",
          r_sub["metrics"]["acc"] == 1.0 and r_sub["measurement_key"] != key and r_sub["record_id"] not in drift)
    check("reference 被改 → 总体身份变 → key 变，发 protocol_drift，不 eligible",
          r_ref["measurement_key"] != key and r_ref["record_id"] in drift and not run.objective.eligible(r_ref, run.protocol))
    man = run.artifacts.open(r_judge["measurement_ref"])
    check("manifest observed 段记下 judge 实际版本（值→次数）", man["observed"]["score_observed"]["judge"] == {'"judge-v2"': 8})
    check("换 judge → protocol_drift；宽松 judge 的 1.0 不静默替换旧 best",
          r_judge["record_id"] in drift and r_judge["metrics"]["acc"] == 1.0 and run.best_record()["record_id"] == r1["record_id"])
    check("漂移测量的 ProgressNote 不 eligible，不推进 patience", r_judge["progress"] == "neutral" and r_ref["progress"] == "neutral")
    check("manifest 如实保留 declared / unknown 三段", set(man) == {"observed", "declared", "unknown"})


main("demo6 D4 选择协议与 evaluator 演化", demo)
