"""demo 共用：断言收集 + PASS/FAIL 打印。"""
import sys, asyncio

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail and not cond else ""))


def main(title, coro_fn):
    print(f"== {title}")
    try:
        asyncio.run(asyncio.wait_for(coro_fn(), timeout=25))
    except Exception as e:
        import traceback; traceback.print_exc()
        check(f"demo 运行无异常（{type(e).__name__}: {e}）", False)
    bad = [n for n, ok in RESULTS if not ok]
    print(f"== {title}: {'PASS' if not bad else 'FAIL'} ({len(RESULTS) - len(bad)}/{len(RESULTS)})")
    sys.exit(1 if bad else 0)
