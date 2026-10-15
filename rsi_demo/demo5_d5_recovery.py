"""Demo 5 · D5 中断与恢复：Window 下在四个点 os._exit 杀宿主，resume 后核对：
同一记录不被启动两次、已提交的消费不重复且与交付日志一致、费用不重复；
启动握手：无登记 ⇒ dead、登记损坏 ⇒ unknown、存活 ⇒ terminate 后 lost。"""
import asyncio, json, os, shutil, subprocess, sys
from kernel import *
from toy import *
from common import check, main

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "_runs", "d5")
POINTS = ["after_running", "before_observe", "in_observe", "after_observe"]


class CrashOpt(Script):
    def init_state(self): return {"step": 0, "seen": [], "consumed": []}

    def propose(self, st, objective, ctx):
        st["step"] += 1
        a = ctx.history.run.initial_id
        if st["step"] == 1:                     # 三条乱序完成：A 慢、B 快、C 中
            return [Proposal(a, [{"uid": "delay", "value": d}]) for d in (0.06, 0.01, 0.03)]
        return [Proposal(a, [{"uid": "delay", "value": 0.001 * st["step"]}])] if st["step"] <= 4 else []

    async def observe(self, st, outcomes, objective, ctx):
        if not st["consumed"]:
            await asyncio.sleep(0.1)            # 第一次 observe 期间另两条完成并关闭（乱序）
        st["consumed"] += [r.record_id for r, _ in outcomes]
        crash_point("in_observe", len(st["consumed"]) >= 2)   # 改了一半的活状态


def make_run(save_dir, resume, backend=None):
    opt = CrashOpt(Toy(task_snapshot(("add",), delay=0)), steps=[])
    return opt, Run(opt, evaluator=DataEvaluator(make_data(2), ExactScorer()), objective=ScalarObjective("acc"),
                    specs={"val": spec("dev")}, budget=Budget(trials=6), schedule=Window(max_in_flight=3),
                    save_dir=save_dir, resume=resume, baseline=False, run_id="d5",
                    backend=backend or LocalBackend(os.path.join(save_dir, "launches.jsonl")))


def child(save_dir):
    asyncio.run(make_run(save_dir, False)[1].run())


async def demo():
    shutil.rmtree(ROOT, ignore_errors=True)
    crash_states = {}
    for p in POINTS:
        d = os.path.join(ROOT, p)
        rc = subprocess.run([sys.executable, __file__, "--child", d], env={**os.environ, "RSI_CRASH": p}).returncode
        st0 = json.load(open(os.path.join(d, "state.json")))
        crash_states[p] = st0
        running0 = [r for r in st0["records"].values() if r["status"] == "running"]
        opt, run = make_run(d, True)
        await run.run()
        launches = [json.loads(l)["record_id"] for l in open(os.path.join(d, "launches.jsonl"))]
        log = [rid for g in run.closed for rid in run.groups[g]["done"]]
        cons = opt.state["consumed"]
        spent_before = run.budget.spent
        run.events.bind(("record", "d5/1")).cost("llm", 0.01, event_id="d5/1#dev-0#0")   # 重放一条旧事件
        ok = (rc == 17 and len(launches) == len(set(launches)) and cons == log and len(cons) == len(set(cons))
              and abs(run.budget.spent - spent_before) < 1e-12 and abs(run.budget.spent - 0.01 * len(run.budget.seen)) < 1e-9
              and all(e.startswith(tuple(f"{x}#" for x in launches)) for e in run.budget.seen))
        extra = ""
        if p == "after_running":
            lost = running0[0]["record_id"]
            ok &= run.records[lost]["status"] == "cancelled" and run.records[lost]["error"] == "lost" and lost not in launches
            extra = "；launch 前的 running 记录 → cancelled(lost)，从未启动、不被重跑"
        if p in ("in_observe", "after_observe"):
            ok &= st0["cursor"] < len(st0["closed"])          # 崩溃时那一组未提交 → 恢复后重新交付一次
            extra = "；未提交的 observe 重新交付，已提交的不重复"
        check(f"崩溃点 {p}：同一记录只启动一次、消费=交付日志且无重复、费用不重复{extra}", ok,
              f"rc={rc} launches={launches} cons={cons} log={log}")
    # 「两条乱序完成后、observe 前」：崩溃时确有 ≥2 个已关闭未消费的组，且关闭顺序≠提交顺序
    st0 = crash_states["before_observe"]
    pend = st0["closed"][st0["cursor"]:]
    check("before_observe 崩溃现场：≥2 组乱序关闭且未被消费", len(pend) >= 2 and pend != sorted(pend, key=lambda g: int(g[1:])), str(pend))

    # ── 启动握手 / 恢复边界 ──
    d = os.path.join(ROOT, "handshake")
    subprocess.run([sys.executable, __file__, "--child", d], env={**os.environ, "RSI_CRASH": "after_running"})
    st = json.load(open(os.path.join(d, "state.json")))
    r0, r1, r2 = (st["records"][f"d5/{i}"] for i in range(3))
    for r in (r1, r2):                                   # 构造另两条「已 running」的现场
        r["status"], r["trial_dir"] = "running", os.path.join(d, "trials", str(r["trial_id"]))
        os.makedirs(r["trial_dir"], exist_ok=True)
    orphan = subprocess.Popen(["sleep", "30"], start_new_session=True)           # 宿主死后存活的 worker
    ProcessProbeBackend.register(r1["trial_dir"], orphan.pid)
    open(os.path.join(r2["trial_dir"], "execution.json"), "w").write('{"pid": 12')  # 半截登记
    atomic_write(os.path.join(d, "state.json"), canonical_json(st))
    opt, run = make_run(d, True, backend=ProcessProbeBackend(os.path.join(d, "launches.jsonl")))
    await run.run()
    R = run.records
    check("无所有权登记 ⇒ probe=dead ⇒ cancelled(lost)", (R["d5/0"]["status"], R["d5/0"]["error"]) == ("cancelled", "lost"))
    check("登记存活 ⇒ terminate 确认后 cancelled(lost)，孤儿进程已被回收",
          (R["d5/1"]["status"], R["d5/1"]["error"]) == ("cancelled", "lost") and orphan.poll() is not None)
    check("登记文件损坏 ⇒ unknown（不按无登记处理），保留预留、不交付",
          R["d5/2"]["status"] == "unknown" and "d5/2" in run.budget.res and "d5/2" not in opt.state["consumed"])


if __name__ == "__main__" and "--child" in sys.argv:
    child(sys.argv[-1])
else:
    main("demo5 D5 中断与恢复", demo)
