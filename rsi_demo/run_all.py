"""一条命令跑完全部 demo，汇总 PASS/FAIL。"""
import os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
DEMOS = ["demo1_basics.py", "demo2_d1_improver.py", "demo3_d3_nested.py", "demo4_d6_admission.py",
         "demo5_d5_recovery.py", "demo6_d4_protocol.py", "demo7_stream.py"]

rows, ok_all = [], True
for d in DEMOS:
    t0 = time.monotonic()
    p = subprocess.run([sys.executable, os.path.join(HERE, d)], cwd=HERE, capture_output=True, text=True, timeout=60)
    dt = time.monotonic() - t0
    print(p.stdout, end="")
    if p.returncode: print(p.stderr[-2000:], end="")
    summary = [l for l in p.stdout.splitlines() if l.startswith("== ") and ":" in l]
    rows.append((d, p.returncode == 0, dt, summary[-1].split(": ", 1)[1] if summary else "无输出"))
    ok_all &= p.returncode == 0

print("\n汇总")
for d, ok, dt, s in rows:
    print(f"  {'PASS' if ok else 'FAIL'}  {d:<26} {s:<14} {dt:5.2f}s")
print(f"总计：{sum(ok for _, ok, _, _ in rows)}/{len(rows)} 个 demo 通过")
sys.exit(0 if ok_all else 1)
