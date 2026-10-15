"""Demo 1 · 基础闭环与三种身份：内容寻址快照、measure 记录、选择协议在第一条合格记录上绑定、best 只在协议内产生。"""
from kernel import *
from toy import *
from common import check, main


async def demo():
    # ── 版本身份 ──
    a = Snapshot({"ops": {"add": True, "sub": False}, "x": [1, 2.5]}, {"form": "task"})
    b = Snapshot({"x": [1, 2.5], "ops": {"sub": False, "add": True}}, {"form": "task"})
    check("同内容（键序不同）→ 同 id，且为 64 位十六进制", a.id == b.id and len(a.id) == 64)
    check("内容不同 → id 不同", a.id != Snapshot({"ops": {"add": False, "sub": False}, "x": [1, 2.5]}, {"form": "task"}).id)
    v = a.unit_values; v["ops"]["add"] = False
    check("读取返回独立新对象，改它不影响已提交快照", a.unit_values["ops"]["add"] is True)
    store = ArtifactStore()
    ref = store.put({"bank": [1, 2, 3]}, kind="memory")
    check("合法 ArtifactRef 可进快照，$ref 即内容地址", Snapshot({"m": ref}).id == Snapshot({"m": dict(ref)}).id)
    try:
        Snapshot({"m": {"$ref": "sha256:xx", "note": 1}}); bad = False
    except ValueError:
        bad = True
    check("'$ref' 是保留键：非法引用被强校验拒绝", bad)
    try:
        Snapshot.load(a.canonical(), "0" * 64); bad = False
    except ValueError:
        bad = True
    check("加载时重算并校验 digest", bad)

    # ── 选择协议：baseline 全失败 → 不绑定；下一条合格基准测量绑定；train 角色不争 best ──
    data = make_data()
    ev = DataEvaluator(data, ExactScorer())
    a0 = task_snapshot(("add",), crash=True)                       # baseline 必然全失败
    fixed = [{"uid": "crash", "value": False}]
    steps = [
        lambda ctx, st: [Proposal(a0.id, fixed)],                                  # 合格 → 绑定协议
        lambda ctx, st: [Proposal(a0.id, fixed + [{"uid": "ops", "value": {o: True for o in OPS}}],
                                  eval_spec=dict(ctx.specs["train"]))],            # train 角色高分
        lambda ctx, st: [Proposal(a0.id, fixed + [{"uid": "ops", "value": {"add": True, "sub": True, "mul": False, "mod": False}}])],
    ]
    opt = Script(Toy(a0), steps=steps)
    same = spec("dev")
    best = await opt.optimize(ev, ScalarObjective("acc"), specs={"val": same, "train": same}, max_trials=10)
    run = opt.last_run
    r0, r1, r2, r3 = (run.records[i] for i in run.order)
    check("baseline（trial 0）是普通 measure 记录且 errors==total", r0["kind"] == "measure" and r0["stats"]["errors"] == r0["stats"]["total"])
    check("baseline 全失败 → 协议未由它绑定；由下一条合格记录绑定", run.protocol and run.protocol["anchor"] == r1["record_id"])
    check("measure+ok 记录：snapshot_id = executor_snapshot_id，manifest 引用齐全",
          r1["snapshot_id"] == r1["executor_snapshot_id"] and r1["measurement_ref"] and r1["outputs"] == [])
    check("train 与 val 绑同一 spec：train 测量 key 相同但 role 不同 → 不 eligible",
          r2["measurement_key"] == r1["measurement_key"] and not run.objective.eligible(r2, run.protocol))
    check("train 记录分数更高（1.0）也不抢 best", r2["metrics"]["acc"] == 1.0 and run.best_record()["record_id"] == r3["record_id"])
    check("best 只在协议内产生，optimize 返回 best 的程序", best.values["ops"]["sub"] is True and r3["progress"] == "improved")
    rv, res = make_outcome(r2, run.results[r2["record_id"]], run.policy_of(r2))
    try:
        res.items; hidden = False
    except HiddenEvidence:
        hidden = True
    check("未被算法声明的 train 角色默认 summary：observe 拿不到 items", hidden and rv.metrics["acc"] == 1.0)
    check("快照不带父指针；谱系由记录给出（measurements_of）",
          len(run.ctx.history.measurements_of(r3["snapshot_id"])) == 1 and "parent" not in run.store[r3["snapshot_id"]].canonical())

    # ── 只返回 dict 的 FunctionEvaluator baseline 同样能绑定 ──
    fe = FunctionEvaluator(lambda prog, sp, ctx: {"score": 0.5}, ("score",), describe={"harness": "toy"})
    opt2 = Script(Toy(task_snapshot()), steps=[])
    await opt2.optimize(fe, ScalarObjective("score"), eval_spec=spec("meta"), max_trials=3)
    check("FunctionEvaluator 返回 dict 的 baseline 绑定协议", opt2.last_run.protocol is not None)

    # ── 独立评估：ctx 合成，签名与优化内一致 ──
    rep = await ev.evaluate(best, spec("test"))
    check("独立评估（ctx=None）可跑，cost 照常走 emitter", rep["stats"]["total"] == 8 and 0 < rep["metrics"]["acc"] < 1)


main("demo1 基础闭环与三种身份", demo)
