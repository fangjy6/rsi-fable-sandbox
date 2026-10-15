"""rsi_demo 最小内核：只实现验证 spec v4.8 RSI 契约（D1/D3/D4/D5/D6 + 基础闭环）所需的子集。

纯标准库、单进程 asyncio。命名尽量与规格一致；规格没写清楚、只能自己拍板的地方用
「摩擦 Fn」注释标出，编号与 REPORT.md 的规格摩擦清单对应。
"""
from __future__ import annotations

import asyncio, contextlib, contextvars, copy, hashlib, inspect, json, os, random, signal, time, traceback, uuid

SNAPSHOT_FORMAT = "toy-snapshot/1"
SCHEMA_VERSION = 1
TERMINAL = {"ok", "failed", "cancelled"}


class NestingRefused(Exception): pass
class BudgetRefused(Exception): pass
class ProposalError(Exception): pass
class NotSupported(Exception): pass
class CandidateError(Exception): pass
class InfraError(Exception): pass
class HiddenEvidence(Exception): pass   # 刻意不继承 AttributeError：getattr(v, k, default) 不能把「隐藏」静默变成缺省


async def maybe_await(x):
    return await x if inspect.isawaitable(x) else x


# ───────────────────────────── 版本身份 ─────────────────────────────
def canonical_json(obj) -> str:
    # 键排序、无多余空白、float 走 Python repr（最短往返）；NaN/Inf 拒绝。摩擦 F1：1 与 1.0 产出不同字节
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_hex(s) -> str:
    return hashlib.sha256(s.encode() if isinstance(s, str) else s).hexdigest()


def check_json(obj, path="$"):
    """强校验：纯 JSON；含 "$ref" 的对象必须恰好是合法 ArtifactRef（保留键）。"""
    if isinstance(obj, dict):
        if "$ref" in obj:
            if set(obj) != {"$ref", "sha256", "kind"} or obj["$ref"] != f"sha256:{obj['sha256']}":
                raise ValueError(f"非法 ArtifactRef @ {path}")
        for k, v in obj.items():
            if not isinstance(k, str):
                raise TypeError(f"非字符串键 @ {path}")
            check_json(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            check_json(v, f"{path}[{i}]")
    elif not (obj is None or isinstance(obj, (str, int, float, bool))):
        raise TypeError(f"非 JSON 值 @ {path}: {type(obj).__name__}")


class Snapshot:
    """内容寻址快照。权威内容是规范化字节；读取永远返回独立新对象。不带父指针。"""
    __slots__ = ("id", "_bytes")

    def __init__(self, unit_values: dict, program_config: dict | None = None):
        body = {"format": SNAPSHOT_FORMAT, "unit_values": unit_values, "program_config": program_config or {}}
        check_json(body)
        self._bytes = canonical_json(body)
        self.id = sha256_hex(self._bytes)

    @classmethod
    def load(cls, canonical: str, expect_id: str):
        d = json.loads(canonical)
        s = cls(d["unit_values"], d["program_config"])
        if s.id != expect_id:
            raise ValueError(f"快照 digest 不符：{expect_id[:12]} != {s.id[:12]}")
        return s

    @property
    def unit_values(self): return json.loads(self._bytes)["unit_values"]
    @property
    def program_config(self): return json.loads(self._bytes)["program_config"]
    def canonical(self): return self._bytes


class ArtifactStore:
    """内容寻址；save_dir 为 None 时在内存。open 默认校验 sha256。"""
    def __init__(self, root=None):
        self.root, self.mem = root, {}
        if root: os.makedirs(root, exist_ok=True)

    def put(self, obj, *, kind: str) -> dict:
        data = canonical_json(obj).encode()
        h = sha256_hex(data)
        if self.root:
            p = os.path.join(self.root, h)
            if not os.path.exists(p):
                atomic_write(p, data)
        else:
            self.mem[h] = data
        return {"$ref": f"sha256:{h}", "sha256": h, "kind": kind}

    def open(self, ref, *, verify=True):
        h = ref["sha256"]
        data = self.mem.get(h) if not self.root else open(os.path.join(self.root, h), "rb").read()
        if verify and sha256_hex(data) != h:
            raise ValueError("artifact 被篡改")
        return json.loads(data)


def atomic_write(path, data):
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode())
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


# ───────────────────────────── 事件 / 成本 / 预算 ─────────────────────────────
class EventBus:
    """一棵 run 树一个。ledger 以作用域帧为键，按 event_id 去重。"""
    def __init__(self):
        self.events, self.ledgers, self.budgets = [], {}, {}


class Emitter:
    def __init__(self, bus: EventBus, chain=()):
        self.bus, self.chain = bus, tuple(chain)

    def bind(self, frame):                      # 追加一帧（祖先链），不是覆盖
        return Emitter(self.bus, self.chain + (frame,))

    def emit(self, kind, payload=None):
        self.bus.events.append({"kind": kind, "scope": [list(f) for f in self.chain], **(payload or {})})

    def cost(self, kind, amount, *, unit="usd", event_id=None):
        eid = event_id or uuid.uuid4().hex
        self.emit("cost", {"cost_kind": kind, "amount": amount, "unit": unit, "event_id": eid})
        for i, frame in enumerate(self.chain):
            led = self.bus.ledgers.setdefault(frame, {"seen": set(), "total": 0.0})
            if eid not in led["seen"]:
                led["seen"].add(eid); led["total"] += amount
            if frame[0] == "run" and frame[1] in self.bus.budgets:
                via = next((f[1] for f in self.chain[i + 1:] if f[0] == "run"), None)
                self.bus.budgets[frame[1]].charge_cost(kind, amount, unit=unit, event_id=eid, via_child=via)


class Budget:
    """TrialBudget(trials=N) 与软 CostBudget(usd=X) 合一。准入（reserve）与永久耗尽（exhausted）分开。"""
    def __init__(self, trials=None, usd=None):
        self.trials, self.usd = trials, usd
        self.res = {}            # record_id -> 1：已预留未结算
        self.settled = 0         # 已结算且计账的记录数
        self.spent = 0.0
        self.seen = set()        # 已处理的 cost event_id
        self.children = {}       # 幂等键(协调者 record_id#序号) -> {run_id, usd, charged, open}

    def _outstanding_child(self):
        return sum(max(0.0, c["usd"] - c["charged"]) for c in self.children.values() if c["open"])

    def reserve(self, record_id):
        """成功返回 None，拒绝返回结构化原因（拒绝 ≠ 停止）。"""
        if record_id in self.res:
            return None
        if self.trials is not None and self.settled + len(self.res) >= self.trials:
            return {"reason": "trials", "settled": self.settled, "reserved": len(self.res)}
        if self.usd is not None and self.spent + self._outstanding_child() >= self.usd:
            return {"reason": "usd", "spent": self.spent}
        self.res[record_id] = 1
        return None

    @staticmethod
    def counts(rec):
        return rec["status"] == "ok" or (rec["status"] == "failed" and rec.get("failure_class") in ("candidate", "unknown"))

    def settle(self, rec, note):
        self.res.pop(rec["record_id"], None)
        if self.counts(rec):
            self.settled += 1

    def release(self, record_id):
        self.res.pop(record_id, None)

    def charge_cost(self, kind, amount, *, unit, event_id, via_child=None):
        if unit != "usd" or event_id in self.seen:
            return
        self.seen.add(event_id); self.spent += amount
        for c in self.children.values():
            if c["run_id"] == via_child:
                c["charged"] += amount   # 摩擦 F9：子 run 的花费冲抵父级为它预留的额度，而不是叠加

    def reserve_child(self, key, run_id, usd):
        if key in self.children:
            return self.children[key]
        if self.usd is None:
            raise BudgetRefused("父预算没有 usd 维度，无法切出子额度")
        avail = self.usd - self.spent - self._outstanding_child()
        if usd > avail + 1e-9:
            raise BudgetRefused(f"子 run 需要 {usd}，父 run 只剩 {avail:.3f}")
        self.children[key] = {"run_id": run_id, "usd": usd, "charged": 0.0, "open": True}
        return self.children[key]

    def close_child(self, key):
        if key in self.children:
            self.children[key]["open"] = False

    def exhausted(self):
        return (self.trials is not None and self.settled >= self.trials) or (self.usd is not None and self.spent >= self.usd)

    def view(self):
        return {"spent": {"usd": self.spent, "trials": self.settled}, "reserved": {"trials": len(self.res)},
                "exhausted": self.exhausted()}

    def serialize_state(self):
        return {"trials": self.trials, "usd": self.usd, "res": self.res, "settled": self.settled,
                "spent": self.spent, "seen": sorted(self.seen), "children": self.children}

    def load_state(self, d):
        self.trials, self.usd, self.res, self.settled = d["trials"], d["usd"], dict(d["res"]), d["settled"]
        self.spent, self.seen, self.children = d["spent"], set(d["seen"]), d["children"]


# ───────────────────────────── 并发 ─────────────────────────────
_ORDER = {"trial": 0, "item": 1, "llm": 2}
_held = contextvars.ContextVar("held", default=())
_coord = contextvars.ContextVar("coord", default=None)   # 当前协调者记录的 TrialContext（摩擦 F7）


class Limiter:
    """一棵 run 树共用一个实例；记录峰值以便断言「不超全局上限」。debug 断言偏序。"""
    def __init__(self, trial=4, item=8, llm=None):
        self.caps = {"trial": trial, "item": item, "llm": llm}
        self.sems = {k: asyncio.Semaphore(v) for k, v in self.caps.items() if v}
        self.cur = dict.fromkeys(self.caps, 0)
        self.peak = dict.fromkeys(self.caps, 0)

    @contextlib.asynccontextmanager
    async def acquire(self, kind):
        held = _held.get()
        if any(_ORDER[h] >= _ORDER[kind] for h in held):
            raise RuntimeError(f"Limiter 偏序违规：持有 {held} 时申请 {kind}")
        sem = self.sems.get(kind)
        if sem: await sem.acquire()
        self.cur[kind] += 1; self.peak[kind] = max(self.peak[kind], self.cur[kind])
        tok = _held.set(held + (kind,))
        try:
            yield
        finally:
            _held.reset(tok); self.cur[kind] -= 1
            if sem: sem.release()


class Barrier:
    def __init__(self, on_stop="drain"): self.on_stop = on_stop
class Window:
    def __init__(self, max_in_flight=1, on_stop="drain"): self.max_in_flight, self.on_stop = max_in_flight, on_stop


# ───────────────────────────── 上下文 ─────────────────────────────
class RunContext:
    def __init__(self, **kw): self.__dict__.update(kw)
class TrialContext:
    def __init__(self, **kw): self.__dict__.update(kw)
class ItemContext:
    def __init__(self, trial, key, events, item_dir=None):
        self.trial, self.key, self.events, self.item_dir = trial, key, events, item_dir


def spec(split="dev", *, example_ids=None, sample_k=None, seed=0, repeat=1, role=None) -> dict:
    return {"split": split, "example_ids": example_ids, "sample_k": sample_k, "seed": seed, "repeat": repeat, "role": role}


# ───────────────────────────── 证据视图 ─────────────────────────────
# 摩擦 F12：§3 summary 允许列表没有列身份字段（record_id/kind/snapshot_id/outputs/eval_spec），
# 但 observe 没有它们无法工作。这里把「身份」显式加入允许列表。
_IDENT = {"record_id", "trial_id", "kind", "status", "snapshot_id", "source_snapshot_id",
          "executor_snapshot_id", "outputs", "eval_spec", "parent_scope", "producer"}
_REC_ALLOW = {"full": None, "summary": _IDENT | {"stage", "failure_class", "metrics", "stats", "measurement_key"},
              "none": {"record_id", "trial_id", "kind", "status"}}
_RES_ALLOW = {"full": None, "summary": {"metrics", "stats", "outputs", "type"}, "none": set()}


class View:
    """允许列表构造的只读视图；列表外字段访问抛 HiddenEvidence（拒绝，不是 None）。"""
    def __init__(self, data, allowed, policy):
        object.__setattr__(self, "_d", data); object.__setattr__(self, "_a", allowed)
        object.__setattr__(self, "policy", policy)

    def __getattr__(self, k):
        d, a = object.__getattribute__(self, "_d"), object.__getattribute__(self, "_a")
        if a is not None and k not in a:
            raise HiddenEvidence(f"{k} 在 {self.policy} 可见性下不可见")
        if k not in d:
            raise AttributeError(k)
        v = copy.deepcopy(d[k])
        if k == "stats" and a is not None:
            v = {s: v[s] for s in ("total", "errors") if s in v}
        return v

    def __setattr__(self, k, v): raise TypeError("证据视图只读")


def make_outcome(rec, result, policy):
    rv = View(rec, _REC_ALLOW[policy], policy)
    return (rv, None if result is None else View(result, _RES_ALLOW[policy], policy))


# ───────────────────────────── 评估 / 打分 ─────────────────────────────
class ScoreRequest:
    def __init__(self, key, inputs, prediction, reference, trace=None):
        self.key, self.inputs, self.prediction, self.reference, self.trace = key, inputs, prediction, reference, trace


class ScoreResult:
    def __init__(self, metrics, feedback=None, details=None, observed=None):
        self.metrics, self.feedback, self.details, self.observed = metrics, feedback, details or {}, observed or {}


class Dataset:
    """玩具 DataSource：splits = {split: [{"id","input","ref"}]}；reference 可变（模拟数据被改）。"""
    def __init__(self, splits):
        self.splits = {s: [dict(e) for e in es] for s, es in splits.items()}
        self.by_id = {e["id"]: e for es in self.splits.values() for e in es}
    def ids(self, split): return [e["id"] for e in self.splits[split]]
    def get(self, i): return copy.deepcopy(self.by_id[i]["input"])
    def reference(self, i): return copy.deepcopy(self.by_id[i]["ref"])
    def set_reference(self, i, ref): self.by_id[i]["ref"] = ref


def _result(metrics, spec_, items, stats, observed=None, outputs=None, typ="EvalResult"):
    return {"type": typ, "metrics": metrics, "spec": spec_, "feedback": None, "stats": stats,
            "items": items, "observed": observed or {}, "outputs": outputs or []}


class Evaluator:
    coordinator = False
    def describe(self): return {}

    async def evaluate(self, program, spec_, ctx=None):
        if ctx is None:                                        # 全库唯一的归一化点
            ctx = await ephemeral_trial_context(program, spec_)
        return await self.run_eval(program, spec_, ctx)


class DataEvaluator(Evaluator):
    def __init__(self, data: Dataset, scorer, *, input_fn=None, metric_keys=("acc",)):
        self.data, self.scorer, self.input_fn, self.metric_keys = data, scorer, input_fn or (lambda ex: ex), metric_keys

    def describe(self): return {"aggregation": "mean", "on_error": "zero", "input_fn": getattr(self.input_fn, "__name__", "?")}

    def resolve_subset(self, sp):
        ids = list(sp["example_ids"]) if sp.get("example_ids") else self.data.ids(sp["split"])
        if sp.get("sample_k"):
            ids = sorted(random.Random(sp.get("seed") or 0).sample(ids, sp["sample_k"]))
        return ids

    def population_identity(self, sp):
        pop = list(sp["example_ids"]) if sp.get("example_ids") else self.data.ids(sp["split"])
        return sha256_hex(canonical_json([[i, self.data.reference(i)] for i in pop]))

    async def run_eval(self, program, sp, ctx):
        ids = self.resolve_subset(sp)
        keys = [(i, r) for i in ids for r in range(sp.get("repeat", 1))]

        async def one(key):
            async with ctx.run.limiter.acquire("item"):
                tok = _coord.set(None)                         # 持 item 令牌的 execute 里不允许 spawn
                try:
                    ictx = ItemContext(ctx, key, ctx.events.bind(("item", f"{ctx.record_id}#{key[0]}#{key[1]}")))
                    it = {"key": list(key), "raw_example": self.data.get(key[0]), "reference": self.data.reference(key[0]),
                          "error": None, "stage": None, "failure_class": None, "observed": {}}
                    stage = "program"
                    try:
                        it["inputs"] = self.input_fn(it["raw_example"])
                        it["raw_prediction"] = it["prediction"] = await maybe_await(program.execute(it["inputs"], ictx))
                        stage = "scorer"
                        sr = await self.scorer.score(ScoreRequest(key, it["inputs"], it["prediction"], it["reference"]), ictx)
                        it.update(metrics=sr.metrics, feedback=sr.feedback, details=sr.details, observed=sr.observed)
                    except Exception as e:
                        fc = "candidate" if isinstance(e, CandidateError) else "infra" if isinstance(e, InfraError) else "unknown"
                        it.update(error=repr(e), stage=stage, failure_class=fc, metrics={k: 0.0 for k in self.metric_keys})
                    return it
                finally:
                    _coord.reset(tok)

        items = await asyncio.gather(*(one(k) for k in keys))
        n = max(1, len(items))
        metrics = {k: sum(it["metrics"].get(k, 0.0) for it in items) / n for k in self.metric_keys}
        cond = {}                                              # ScoreResult.observed 汇总：值 → 次数，保留差异
        for it in items:
            for k, v in it["observed"].items():
                cond.setdefault(k, {}).setdefault(canonical_json(v), 0)
                cond[k][canonical_json(v)] += 1
        observed = {"population": self.population_identity(sp), "sample": sha256_hex(canonical_json(ids)),
                    "n": len(keys), "score_observed": cond, "scorer": type(self.scorer).__name__,
                    "scorer_declared": self.scorer.describe()}
        return _result(metrics, sp, items, {"total": len(items), "errors": sum(1 for i in items if i["error"])}, observed)


class FunctionEvaluator(Evaluator):
    def __init__(self, fn, metric_keys=(), *, describe=None, coordinator=False):
        self.fn, self.metric_keys, self._describe, self.coordinator = fn, metric_keys, describe or {}, coordinator
    def describe(self): return dict(self._describe)

    async def run_eval(self, program, sp, ctx):
        out = await maybe_await(self.fn(program, sp, ctx))
        if isinstance(out, dict) and "type" not in out:        # 只返回 dict：归一化为 total=1, errors=0
            return _result({k: float(v) for k, v in out.items()}, sp, [], {"total": 1, "errors": 0})
        return out


def measurement_key(manifest) -> str:
    """GroupingPolicy "default/1"：evaluator/scorer 身份 + split/repeat/backend + 子集协议（总体身份 + sample_k）。
    不取 role、seed、实际样本清单、观测次数。摩擦 F14：observed 里哪些进 key 规格没逐项列出，这里只取条件值的集合。"""
    o, d, sp = manifest["observed"], manifest["declared"], manifest["observed"]["eval_spec"]
    basis = {"evaluator": o["evaluator"], "scorer": o.get("scorer"), "declared": d, "split": sp["split"],
             "repeat": sp.get("repeat", 1), "backend": o["backend"], "population": o.get("population"),
             "sample_k": sp.get("sample_k"),
             "conditions": {k: sorted(v) for k, v in o.get("score_observed", {}).items()}}
    return "default/1:" + sha256_hex(canonical_json(basis))[:16]


class ScalarObjective:
    def __init__(self, metric, maximize=True): self.metric, self.maximize = metric, maximize

    def is_better(self, a, b):
        x, y = a["metrics"].get(self.metric, float("-inf")), b["metrics"].get(self.metric, float("-inf"))
        return x > y if self.maximize else x < y

    def eligible(self, rec, protocol):
        return bool(protocol and rec["metrics"] and rec["eval_spec"] and rec["eval_spec"].get("role") == protocol["role"]
                    and rec["measurement_key"] == protocol["measurement_key"])

    def select_best(self, recs):
        best = None
        for r in recs:
            if best is None or self.is_better(r, best):
                best = r
        return best

    def progress(self, new, incumbent):
        return "neutral" if incumbent is None else "improved" if self.is_better(new, incumbent) else "unchanged"


# ───────────────────────────── 程序 / Backend / 生产 ─────────────────────────────
class Program:
    """DictProgramAdapter 风格：adapter 与实例合一。from_snapshot 纯函数式；measure 时 execute 只读。"""
    def __init__(self, snapshot: Snapshot | None = None, unit_values=None, program_config=None):
        snapshot = snapshot or Snapshot(unit_values or {}, program_config or {})
        self.values, self.config = snapshot.unit_values, snapshot.program_config

    def from_snapshot(self, snap): return type(self)(snap)
    def take_snapshot(self): return Snapshot(self.values, self.config)

    def merge_changes(self, snap, changes):
        vals = snap.unit_values
        for c in changes:
            if c["uid"] not in vals:
                raise CandidateError(f"未声明的 unit：{c['uid']}")
            if c.get("operation", "replace") != "replace":
                raise CandidateError(f"不支持的 operation：{c['operation']}")
            vals[c["uid"]] = c["value"]
        return Snapshot(vals, snap.program_config)

    def validate_snapshot(self, out, base):
        if set(out.unit_values) != set(base.unit_values) or out.program_config != base.program_config:
            raise CandidateError("完整快照越过可写范围（unit 集合或 program_config 变化）")

    def learn(self, feedback, ctx): pass
    def execute(self, inputs, ctx): raise NotImplementedError


class LocalBackend:
    capabilities = frozenset()
    def __init__(self, log_path=None): self.launches, self.log_path = [], log_path

    async def launch(self, adapter, snapshot, ctx):
        self.launches.append((ctx.record_id, snapshot.id))
        if self.log_path:
            with open(self.log_path, "a") as f:
                f.write(json.dumps({"record_id": ctx.record_id, "snapshot_id": snapshot.id}) + "\n")
        return adapter.from_snapshot(snapshot)                 # 不包装：重建出的 adapter 就是句柄

    async def shutdown(self, handle, ctx): pass


def read_registration(trial_dir):
    p = os.path.join(trial_dir or "", "execution.json")
    if not trial_dir or not os.path.exists(p):
        return "missing", None
    try:
        reg = json.load(open(p))
        assert {"pid", "pgid", "start_time"} <= set(reg)
        return "ok", reg
    except Exception:
        return "corrupt", None


class ProcessProbeBackend(LocalBackend):
    """只实现 SubprocessBackend 的恢复边界（probe / terminate + 所有权登记），足以验证启动握手语义。"""
    capabilities = frozenset({"probe", "terminate"})

    @staticmethod
    def register(trial_dir, pid):
        os.makedirs(trial_dir, exist_ok=True)
        reg = {"pid": pid, "pgid": os.getpgid(pid), "start_time": _proc_start(pid)}
        atomic_write(os.path.join(trial_dir, "execution.json"), json.dumps(reg))   # 原子发布

    async def probe(self, rec):
        st, reg = read_registration(rec.get("trial_dir"))
        if st == "missing": return "dead"                     # 无登记 ⇒ 候选从未加载
        if st == "corrupt": return "unknown"                  # 损坏 ≠ 无登记
        try:
            os.kill(reg["pid"], 0)
        except ProcessLookupError:
            return "dead"
        return "alive" if _proc_start(reg["pid"]) == reg["start_time"] else "dead"

    async def terminate(self, rec):
        _, reg = read_registration(rec.get("trial_dir"))
        try:
            os.killpg(reg["pgid"], signal.SIGKILL)
        except ProcessLookupError:
            return True
        for _ in range(100):
            try:
                os.waitpid(reg["pid"], os.WNOHANG)
            except ChildProcessError:
                pass
            if await self.probe(rec) == "dead" or _zombie(reg["pid"]):
                return True
            await asyncio.sleep(0.02)
        return False


def _proc_start(pid):
    try:
        return open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[19]
    except Exception:
        return None


def _zombie(pid):
    try:
        return open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[0] == "Z"
    except Exception:
        return True


class Producer:
    coordinator = False
    def describe(self): return {}


class CallProducer(Producer):
    def __init__(self, capture="auto", parse_fn=None): self.capture, self.parse_fn = capture, parse_fn
    def describe(self): return {"capture": self.capture}

    async def run_produce(self, program, inputs, sp, ctx):
        same = ctx.executor.id == ctx.snapshot.id
        mode = self.capture if self.capture != "auto" else ("snapshot" if same else "return")
        if mode == "snapshot" and not same:
            raise NotSupported("capture='snapshot' 要求执行者即目标（否则会把 G 的状态登记成 A 的后代）")
        ictx = ItemContext(ctx, ("__call__", 0), ctx.events.bind(("item", f"{ctx.record_id}#__call__#0")))
        raw = await maybe_await(program.execute(inputs, ictx))
        if mode == "snapshot":
            outs = [{"snapshot": await maybe_await(program.take_snapshot())}]
        else:
            outs = [{"changes": ch} for ch in (self.parse_fn or (lambda r: r["outputs"]))(raw)]
        items = [{"key": ["__call__", 0], "inputs": inputs, "raw_prediction": raw, "error": None}]
        return _result({}, sp, items, {"total": 1, "errors": 0, "capture": mode}, outputs=outs, typ="ProduceResult")


class StreamProducer(Producer):
    """单实例按序：execute → score → learn，结束时 take_snapshot。执行者必须是目标本身。"""
    def __init__(self, data, scorer, *, input_fn=None): self.data, self.scorer, self.input_fn = data, scorer, input_fn or (lambda e: e)

    async def run_produce(self, program, inputs, sp, ctx):
        if ctx.executor.id != ctx.snapshot.id: raise NotSupported("StreamProducer 的执行者必须是目标")
        if sp is None or sp.get("repeat", 1) != 1: raise NotSupported("StreamProducer 需要非空且 repeat=1 的 spec")
        items, hits = [], 0.0
        for i in (sp["example_ids"] or self.data.ids(sp["split"])):
            ictx = ItemContext(ctx, (i, 0), ctx.events.bind(("item", f"{ctx.record_id}#{i}#0")))
            ex = self.data.get(i); inp = self.input_fn(ex)
            pred = await maybe_await(program.execute(inp, ictx))
            sr = await self.scorer.score(ScoreRequest((i, 0), inp, pred, self.data.reference(i)), ictx)
            await maybe_await(program.learn(sr, ictx))
            hits += sr.metrics.get("acc", 0.0)
            items.append({"key": [i, 0], "raw_example": ex, "inputs": inp, "prediction": pred,
                          "reference": self.data.reference(i), "metrics": sr.metrics, "feedback": sr.feedback})
        outs = [{"snapshot": await maybe_await(program.take_snapshot())}]
        return _result({"stream_acc": hits / max(1, len(items))}, sp, items,
                       {"total": len(items), "errors": 0, "order": [it["key"][0] for it in items]},
                       outputs=outs, typ="ProduceResult")


# ───────────────────────────── 嵌套 / 历史 / 独立评估 ─────────────────────────────
class RunSpawner:
    def __init__(self, run): self.run, self.seq = run, {}

    async def spawn(self, optimizer, *, evaluator, objective, specs, budget, backend=None, producers=None,
                    schedule=None, select_on=None, visibility=None):
        tctx = _coord.get()
        if self.run is None or tctx is None or tctx.run.run_id != self.run.run_id:
            raise NestingRefused("只有协调者记录能 spawn（钩子 / 持 item 令牌的 execute 里禁止）")
        n = self.seq[tctx.record_id] = self.seq.get(tctx.record_id, 0) + 1
        key, child_id = f"{tctx.record_id}#{n}", f"{self.run.run_id}.{tctx.trial_id}.{n}"
        self.run.budget.reserve_child(key, child_id, budget.usd)          # 拿不到立即 BudgetRefused
        self.run.meta["children"].append({"run_id": child_id, "key": key, "usd": budget.usd})
        self.run.checkpoint()                                              # 子 run 身份与预留先于启动落盘
        child = Run(optimizer, evaluator=evaluator, objective=objective, specs=specs, budget=budget,
                    backend=backend or self.run.backend, producers=producers if producers is not None else self.run.producers,
                    schedule=schedule or Barrier(), select_on=select_on or self.run.select_on,
                    visibility=visibility if visibility is not None else self.run.visibility,
                    save_dir=os.path.join(tctx.trial_dir, "runs", child_id) if tctx.trial_dir else None,
                    limiter=self.run.limiter, bus=self.run.bus, store=self.run.store, run_id=child_id,
                    parent={"run_id": self.run.run_id, "record_id": tctx.record_id}, emitter=tctx.events.bind(("run", child_id)),
                    cancel_parent=self.run)
        try:
            await child.run()
            return child
        finally:
            self.run.budget.close_child(key)


class HistoryView:
    def __init__(self, run): self.run = run

    def _o(self, rid):
        return make_outcome(self.run.records[rid], self.run.results.get(rid), self.run.policy_of(self.run.records[rid]))

    def record(self, rid): return self._o(rid)[0]
    def result(self, rid): return self._o(rid)[1]
    def snapshot(self, sid): return self.run.store[sid]

    def records(self, *, kind=None, status=None):
        for rid in self.run.order:
            r = self.run.records[rid]
            if (kind is None or r["kind"] == kind) and (status is None or r["status"] == status):
                yield self._o(rid)[0]

    def producers_of(self, sid): return [self._o(rid)[0] for rid in self.run.order if sid in self.run.records[rid]["outputs"]]
    def measurements_of(self, sid):
        return [self._o(rid)[0] for rid in self.run.order
                if self.run.records[rid]["kind"] == "measure" and self.run.records[rid]["snapshot_id"] == sid]


async def ephemeral_trial_context(program, sp):
    snap = await maybe_await(program.take_snapshot())
    rid = f"standalone-{uuid.uuid4().hex[:8]}"
    rctx = RunContext(run_id=rid, save_dir=None, artifacts=ArtifactStore(), events=Emitter(EventBus(), [("run", rid)]),
                      budget=Budget().view(), history=None, limiter=Limiter(), cancel=None, specs={"val": dict(sp, role="val")},
                      mode="standalone", parent=None, pending=[], runs=RunSpawner(None))
    return TrialContext(run=rctx, trial_id=-1, record_id=f"{rid}/-1", kind="measure", snapshot=snap, executor=snap,
                        source_snapshot_id="", changes=[], trial_dir=None, workspace=None,
                        events=rctx.events.bind(("record", f"{rid}/-1")))


# ───────────────────────────── Optimizer 基类 ─────────────────────────────
class Proposal(dict):
    def __init__(self, source_snapshot_id, changes=(), eval_spec=None, kind="measure", executor_snapshot_id=None,
                 producer=None, inputs=None, metadata=None):
        super().__init__(source_snapshot_id=source_snapshot_id, changes=list(changes), eval_spec=eval_spec, kind=kind,
                         executor_snapshot_id=executor_snapshot_id, producer=producer, inputs=inputs, metadata=metadata or {})


class Optimizer:
    required_specs = frozenset({"val"})
    required_producers = frozenset()

    def __init__(self, adapter, **cfg):
        self.adapter, self.cfg, self.state = adapter, cfg, self.init_state()

    def init_state(self): return {}
    def propose(self, state, objective, ctx): return []
    def observe(self, state, outcomes, objective, ctx): pass
    def should_stop(self, state, objective, ctx): return False
    def serialize_state(self): return copy.deepcopy(self.state)
    def load_state(self, d): self.state = copy.deepcopy(d)

    async def optimize(self, evaluator, objective, *, specs=None, eval_spec=None, budget=None, max_trials=None, **kw):
        specs = specs or {"val": eval_spec}
        budget = budget or Budget(trials=max_trials)
        self.last_run = Run(self, evaluator=evaluator, objective=objective, specs=specs, budget=budget, **kw)
        await self.last_run.run()
        b = self.last_run.best_record()
        return None if b is None else self.adapter.from_snapshot(self.last_run.store[b["snapshot_id"]])


def crash_point(name, cond=True):
    """D5 崩溃注入：环境变量 RSI_CRASH 命中即 os._exit（模拟宿主被 kill）。"""
    if cond and os.environ.get("RSI_CRASH") == name:
        os._exit(17)


class _BudgetView:
    """RunContext.budget：只读投影，够不到 Budget 本体。"""
    def __init__(self, b): object.__setattr__(self, "_b", b)
    def __getattr__(self, k): return object.__getattribute__(self, "_b").view()[k]
    def __setattr__(self, k, v): raise TypeError("BudgetView 只读")


# ───────────────────────────── 引擎 ─────────────────────────────
class Run:
    def __init__(self, optimizer, *, evaluator, objective, specs, budget, backend=None, producers=None,
                 schedule=None, select_on="val", visibility=None, save_dir=None, resume=False, limiter=None,
                 bus=None, store=None, run_id=None, parent=None, emitter=None, baseline=True, cancel_parent=None):
        self.opt, self.adapter, self.evaluator, self.objective = optimizer, optimizer.adapter, evaluator, objective
        self.budget, self.backend = budget, backend or LocalBackend()
        self.producers, self.schedule = producers or {}, schedule or Barrier()
        self.select_on, self.save_dir, self.parent = select_on, save_dir, parent
        self.run_id = run_id or f"run-{uuid.uuid4().hex[:6]}"
        self.limiter, self.bus = limiter or Limiter(), bus or EventBus()
        self.store = store if store is not None else {}
        self.events = emitter or Emitter(self.bus, [("run", self.run_id)])
        self.bus.budgets[self.run_id] = budget
        # 角色 → 基准 spec：逐角色复制并填好 role（两个角色绑同一对象也互不覆盖）
        self.specs = {r: dict(s, role=r) for r, s in specs.items()}
        miss = set(optimizer.required_specs) - set(self.specs)
        if miss: raise ProposalError(f"算法需要数据角色 {sorted(miss)}，specs 里只有 {sorted(self.specs)}")
        if set(optimizer.required_producers) - set(self.producers):
            raise ProposalError(f"算法需要生产角色 {sorted(optimizer.required_producers)}")
        vis = {r: ("full" if r in optimizer.required_specs else "summary") for r in self.specs}
        vis.update(visibility or {})
        self.visibility = vis
        self.artifacts = ArtifactStore(os.path.join(save_dir, "artifacts") if save_dir else None)
        self.records, self.order, self.results = {}, [], {}
        self.groups, self.closed, self.cursor = {}, [], 0
        self.protocol, self.meta = None, {"children": [], "stop_reason": None, "visibility": vis, "select_on": select_on}
        self.queue, self.tasks, self.stopping, self.refusal = [], {}, False, None
        self.next_tid, self.committed = 0, optimizer.serialize_state()
        self.baseline, self.resume, self.cancel_parent = baseline, resume, cancel_parent
        self.ctx = RunContext(run_id=self.run_id, save_dir=save_dir, artifacts=self.artifacts, events=self.events,
                              budget=_BudgetView(budget), history=HistoryView(self), limiter=self.limiter, cancel=None, specs=self.specs,
                              mode="optimize", parent=parent, pending=self, runs=RunSpawner(self))
        initial = optimizer.adapter.take_snapshot()
        self.store[initial.id] = initial
        self.initial_id = initial.id

    # ── 持久化 ──
    def checkpoint(self):
        if not self.save_dir: return
        os.makedirs(self.save_dir, exist_ok=True)
        st = {"schema_version": SCHEMA_VERSION, "run_id": self.run_id, "records": self.records, "order": self.order,
              "snapshots": {k: v.canonical() for k, v in self.store.items()}, "groups": self.groups,
              "closed": self.closed, "cursor": self.cursor, "optimizer_state": self.committed,
              "budget_state": self.budget.serialize_state(), "protocol": self.protocol, "meta": self.meta,
              "next_tid": self.next_tid, "initial_id": self.initial_id}
        check_json(st)
        atomic_write(os.path.join(self.save_dir, "state.json"), canonical_json(st))

    def _load(self):
        st = json.load(open(os.path.join(self.save_dir, "state.json")))
        if st["schema_version"] != SCHEMA_VERSION: raise NotSupported("不认识的 schema_version，拒绝续跑")
        self.run_id = st["run_id"]
        self.bus.budgets[self.run_id] = self.budget
        self.events = Emitter(self.bus, [("run", self.run_id)]); self.ctx.events = self.events; self.ctx.run_id = self.run_id
        self.records, self.order, self.groups, self.closed = st["records"], st["order"], st["groups"], st["closed"]
        self.cursor, self.protocol, self.meta, self.next_tid = st["cursor"], st["protocol"], st["meta"], st["next_tid"]
        self.store.update({k: Snapshot.load(v, k) for k, v in st["snapshots"].items()})
        self.committed = st["optimizer_state"]; self.opt.load_state(self.committed)
        self.budget.load_state(st["budget_state"])
        for rid in self.order:
            p = os.path.join(self._tdir(self.records[rid]), "outcome.json")
            if os.path.exists(p):
                self.results[rid] = json.load(open(p))

    def _tdir(self, rec): return os.path.join(self.save_dir, "trials", str(rec["trial_id"])) if self.save_dir else None

    # ── 视图 / 选择 ──
    def policy_of(self, rec):
        sp = rec.get("eval_spec")
        return "full" if sp is None else self.visibility.get(sp.get("role"), "summary")

    def best_record(self):
        el = [r for r in (self.records[i] for i in self.order)
              if r["kind"] == "measure" and r["status"] == "ok" and self.objective.eligible(r, self.protocol)]
        return self.objective.select_best(el)

    def requests(self):                                       # PendingView
        return [{"record_id": r, "kind": self.records[r]["kind"], "status": self.records[r]["status"]}
                for r in self.order if self.records[r]["status"] in ("pending", "running")]

    # ── 提交 ──
    def _submit(self, props):
        gid = None
        for p in props:
            p = dict(p)
            if p["kind"] not in ("measure", "produce"): raise ProposalError(f"未知 kind {p['kind']}")
            if p["kind"] == "measure" and (p["executor_snapshot_id"] or p["producer"] or p["inputs"] is not None):
                raise ProposalError("measure 不能带 executor / producer / inputs")
            if p["kind"] == "produce" and p["producer"] not in self.producers:
                raise ProposalError(f"未绑定的生产角色 {p['producer']}")
            sp = p["eval_spec"]
            if sp is not None:
                if sp.get("role") not in self.specs or sp["split"] != self.specs[sp["role"]]["split"]:
                    raise ProposalError("eval_spec 必须派生自 ctx.specs（role 存在、split 不变）")
            elif p["kind"] == "measure":
                sp = dict(self.specs[self.select_on])
            tid = self.next_tid; self.next_tid += 1
            rid = f"{self.run_id}/{tid}"
            prod = self.producers.get(p["producer"]) if p["kind"] == "produce" else None
            rec = {"schema_version": SCHEMA_VERSION, "trial_id": tid, "record_id": rid, "kind": p["kind"],
                   "status": "pending", "parent_scope": self.parent, "execution_ref": None,
                   "source_snapshot_id": p["source_snapshot_id"], "changes": p["changes"], "eval_spec": sp,
                   # 摩擦 F4：自修改时执行者 = apply 之后的目标，提交时只能记「None = 目标自身」
                   "executor_snapshot_id": p["executor_snapshot_id"],
                   "producer": prod and {"role": p["producer"], "impl": type(prod).__name__, "describe": prod.describe()},
                   "inputs": p["inputs"], "metadata": p["metadata"], "snapshot_id": None, "outputs": [], "metrics": {},
                   "measurement_ref": None, "measurement_key": None, "feedback": None, "stats": {},
                   "trial_dir": None, "error": None, "stage": None, "failure_class": None, "traceback": None}
            check_json(rec)
            self.records[rid] = rec; self.order.append(rid); self.queue.append(rid)
            if isinstance(self.schedule, Window) or gid is None:
                gid = f"g{len(self.groups)}"; self.groups[gid] = {"members": [], "done": []}
            self.groups[gid]["members"].append(rid); rec["group"] = gid
            self.events.emit("submit", {"record_id": rid})
        self.checkpoint()   # {pending 记录, 交付组登记, optimizer_state, budget_state} 同一次提交

    # ── 派发 ──
    def _dispatch_ready(self):
        cap = self.schedule.max_in_flight if isinstance(self.schedule, Window) else 10 ** 9
        while self.queue and len(self.tasks) < cap:
            rid = self.queue[0]
            ref = self.budget.reserve(rid)
            if ref is not None:
                self.refusal = ref; return
            self.queue.pop(0)
            self.tasks[rid] = asyncio.ensure_future(self._execute(rid))

    async def _execute(self, rid):
        rec, res, err = self.records[rid], None, None
        try:
            try:
                target = self.adapter.merge_changes(self.store[rec["source_snapshot_id"]], rec["changes"])
            except Exception as e:
                return self._finalize(rid, None, ("apply", e))
            self.store[target.id] = target
            rec["snapshot_id"] = target.id
            if rec["executor_snapshot_id"] is None:
                rec["executor_snapshot_id"] = target.id
            executor = self.store[rec["executor_snapshot_id"]]
            comp = self.evaluator if rec["kind"] == "measure" else self.producers[rec["producer"]["role"]]
            rec["trial_dir"] = self._tdir(rec)
            if rec["trial_dir"]: os.makedirs(rec["trial_dir"], exist_ok=True)
            ctx = TrialContext(run=self.ctx, trial_id=rec["trial_id"], record_id=rid, kind=rec["kind"], snapshot=target,
                               executor=executor, source_snapshot_id=rec["source_snapshot_id"], changes=rec["changes"],
                               trial_dir=rec["trial_dir"], workspace=None, events=self.events.bind(("record", rid)))
            # 协调者不取任何池令牌，固定在宿主进程运行
            lock = contextlib.nullcontext() if comp.coordinator else self.limiter.acquire("trial")
            async with lock:
                rec["status"] = "running"; self.checkpoint()
                crash_point("after_running", self.parent is None)
                backend = LocalBackend() if comp.coordinator else self.backend
                handle = await backend.launch(self.adapter, executor, ctx)
                tok = _coord.set(ctx if comp.coordinator else None)
                stage = "program" if rec["kind"] == "measure" else "produce"
                try:
                    if rec["kind"] == "measure":
                        res = await self.evaluator.evaluate(handle, rec["eval_spec"], ctx)
                    else:
                        res = await comp.run_produce(handle, rec["inputs"], rec["eval_spec"], ctx)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    err = (stage, e)
                finally:
                    _coord.reset(tok)
                    await backend.shutdown(handle, ctx)
            self._finalize(rid, res, err)
        except asyncio.CancelledError:
            self._cancel(rid, "stop")                          # 取消被确认后才写 cancelled
        finally:
            self.tasks.pop(rid, None)

    # ── 收尾 ──
    def _finalize(self, rid, res, err):
        rec = self.records[rid]
        if err is None and rec["kind"] == "produce":
            err = self._accept(rec, res)
        if err is not None:
            stage, e = err
            fc = ("candidate" if isinstance(e, (CandidateError, NotSupported)) else
                  "infra" if isinstance(e, (InfraError, BudgetRefused)) else "unknown")   # 摩擦 F10
            rec.update(status="failed", stage=stage, error=repr(e), failure_class=fc,
                       traceback="".join(traceback.format_exception(e))[-2000:])
        else:
            rec.update(status="ok", metrics=res["metrics"], stats={k: v for k, v in res["stats"].items()})
        if res is not None and rec["kind"] == "measure" and rec["status"] == "ok":
            self._manifest(rec, res)
        if res is not None:
            enc = {k: v for k, v in res.items() if k != "outputs"}
            enc["outputs"] = rec["outputs"]
            self.results[rid] = json.loads(json.dumps(enc, default=repr))   # 带类型标记的 outcome 编码
            if rec["trial_dir"]:
                atomic_write(os.path.join(rec["trial_dir"], "outcome.json"), json.dumps(self.results[rid]))
        el = rec["kind"] == "measure" and rec["status"] == "ok" and self.objective.eligible(rec, self.protocol)
        others = [self.records[r] for r in self.order if r != rid and self.records[r]["kind"] == "measure"
                  and self.records[r]["status"] == "ok" and self.objective.eligible(self.records[r], self.protocol)]
        note = {"eligible": el, "progress": self.objective.progress(rec, self.objective.select_best(others)) if el else "neutral"}
        rec["progress"] = note["progress"]
        self.budget.settle(rec, note)
        self._close(rec)
        self.events.emit("trial_end", {"record_id": rid, "status": rec["status"]})
        self.checkpoint()   # 终态 + 完成序号 + 协议绑定 + budget_state 一次提交

    def _accept(self, rec, res):
        target, out_ids = self.store[rec["snapshot_id"]], []
        try:
            snaps = []
            for o in res["outputs"]:
                if "changes" in o:
                    snaps.append(self.adapter.merge_changes(target, o["changes"]))
                else:
                    self.adapter.validate_snapshot(o["snapshot"], target); snaps.append(o["snapshot"])
        except Exception as e:
            return ("accept", e)                               # 全有或全无
        for s in snaps:
            self.store[s.id] = s; out_ids.append(s.id)
        rec["outputs"] = out_ids
        return None

    def _manifest(self, rec, res):
        ob = res.get("observed", {})
        manifest = {"observed": {"evaluator": type(self.evaluator).__name__, "eval_spec": rec["eval_spec"],
                                 "backend": type(self.backend).__name__, **ob},
                    "declared": {"evaluator": self.evaluator.describe(), "scorer": ob.get("scorer_declared", {})},
                    "unknown": [] if ob else ["evaluator internals (FunctionEvaluator 闭包)"]}
        rec["measurement_ref"] = self.artifacts.put(manifest, kind="measurement_manifest")
        rec["measurement_key"] = key = measurement_key(manifest)
        sp, role = rec["eval_spec"], rec["eval_spec"]["role"]
        is_base = sp == self.specs[role]
        if role != self.select_on or not is_base:
            return
        if self.protocol is None:
            if rec["metrics"] and rec["stats"].get("errors", 0) < rec["stats"].get("total", 0):
                self.protocol = {"role": role, "measurement_key": key, "anchor": rec["record_id"]}
                self.events.emit("protocol_bound", self.protocol)
        elif key != self.protocol["measurement_key"]:
            self.events.emit("protocol_drift", {"record_id": rec["record_id"], "key": key, "protocol": self.protocol})

    def _close(self, rec):
        g = self.groups[rec["group"]]
        g["done"].append(rec["record_id"])
        if len(g["done"]) == len(g["members"]):
            self.closed.append(rec["group"])

    def _cancel(self, rid, reason):
        rec = self.records[rid]
        rec.update(status="cancelled", error=reason)
        self.budget.release(rid)
        self._close(rec)
        self.events.emit("trial_end", {"record_id": rid, "status": "cancelled", "reason": reason})
        self.checkpoint()

    # ── 交付 ──
    async def _deliver_ready(self):
        while self.cursor < len(self.closed):
            crash_point("before_observe", self.parent is None and len(self.closed) - self.cursor >= 2)
            g = self.groups[self.closed[self.cursor]]
            outs = [make_outcome(self.records[r], self.results.get(r), self.policy_of(self.records[r])) for r in g["done"]]
            await maybe_await(self.opt.observe(self.opt.state, outs, self.objective, self.ctx))
            self.committed = self.opt.serialize_state()        # 策略状态只在钩子返回时提交
            crash_point("after_observe", self.parent is None)
            self.cursor += 1
            self.events.emit("deliver", {"group": self.closed[self.cursor - 1]})
            self.checkpoint()

    # ── 停止 ──
    async def _stop(self, reason):
        self.stopping = True
        self.meta["stop_reason"] = reason if reason != "refused" else {"refused": self.refusal}
        for rid in list(self.queue):
            self._cancel(rid, "stop")
        self.queue.clear()
        if self.schedule.on_stop == "cancel":
            for t in list(self.tasks.values()):
                t.cancel()

    def _can_propose(self):
        if self.cursor < len(self.closed): return False
        if isinstance(self.schedule, Window):
            return len(self.tasks) + len(self.queue) < self.schedule.max_in_flight
        return not self.tasks and not self.queue

    async def _recover(self):
        self._load()
        for rid in self.order:
            rec = self.records[rid]
            if rec["status"] == "pending":
                self.queue.append(rid)
            elif rec["status"] == "running":
                verdict = await self.backend.probe(rec) if "probe" in self.backend.capabilities else "dead"
                if verdict == "alive":
                    verdict = "dead" if await self.backend.terminate(rec) else "unknown"
                if verdict == "dead":
                    self._cancel(rid, "lost")
                else:
                    rec["status"] = "unknown"; self.checkpoint()   # 保留预留，等待核对

    # ── 主循环 ──
    async def run(self):
        if self.resume:
            await self._recover()
        else:
            self.checkpoint()
            if self.baseline:
                self._submit([Proposal(self.initial_id)])
        while True:
            await self._deliver_ready()
            if not self.stopping:
                if await maybe_await(self.opt.should_stop(self.opt.state, self.objective, self.ctx)):
                    await self._stop("should_stop")
                elif self.budget.exhausted():
                    await self._stop("exhausted")
            if not self.stopping and self._can_propose():
                props = await maybe_await(self.opt.propose(self.opt.state, self.objective, self.ctx))
                props = [props] if isinstance(props, Proposal) else list(props or [])
                self.committed = self.opt.serialize_state()
                if props:
                    self._submit(props)
                    self._dispatch_ready()
                    continue                                    # 有空位就再 propose，不必先等完成
                elif not self.tasks and not self.queue:
                    await self._stop("no_proposals")
            if not self.stopping:
                self.refusal = None
                self._dispatch_ready()
                if self.queue and not self.tasks and self.refusal:
                    await self._stop("refused")                 # 预留被拒且没有任何在途执行
            if self.tasks:
                await asyncio.wait(list(self.tasks.values()), return_when=asyncio.FIRST_COMPLETED)
            elif self.stopping and self.cursor >= len(self.closed):
                break
        self.events.emit("run_end", {"reason": self.meta["stop_reason"]})
        self.checkpoint()
        return self
