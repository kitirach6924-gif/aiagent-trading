"""Why did 16 risk=PASS signals not become orders?

The score gate is not the only blocker: 16 decisions cleared the risk gate and
still produced no trade. This traces what happens between
`_journal_decision(..., "PASS", ...)` and `connector.open_position`.

Read-only, no config change. It answers one question: which call in that window
can return without opening a position, and does it log anything when it does?
"""
import ast
import pathlib
import sys

ROOT = pathlib.Path(r"E:\forex\aiagent\backend")
sys.path.insert(0, str(ROOT))

print("=" * 70)
print("1. THE PASS -> ORDER WINDOW in _act_on_decision")
print("=" * 70)

src = (ROOT / "app/core/loop.py").read_text(encoding="utf-8")
tree = ast.parse(src)
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TradingLoop")
fn = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef)
          and n.name == "_act_on_decision")

lines = src.splitlines()
for i, n in enumerate(fn.body):
    seg = ast.get_source_segment(src, n)
    first = (seg or "").strip().splitlines()[0] if seg else ""
    print(f"  {first[:90]}")

print()
print("=" * 70)
print("2. EVERY early `return` between the PASS journal and _execute")
print("=" * 70)
pass_journaled = False
for n in fn.body:
    seg = ast.get_source_segment(src, n) or ""
    if '"PASS" if risk.allowed else "BLOCK"' in seg or "risk_result" in seg:
        pass_journaled = True
        continue
    if not pass_journaled:
        continue
    if isinstance(n, ast.Return):
        print("  >>> RETURN:", (seg or "").strip().replace("\n", " ")[:110])

print()
print("=" * 70)
print("3. Inside _execute: every way it exits WITHOUT opening")
print("=" * 70)
exe = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_execute")
for n in ast.walk(exe):
    if isinstance(n, ast.Return):
        seg = (ast.get_source_segment(src, n) or "").strip().replace("\n", " ")
        print("  RETURN:", seg[:110])

print()
print("  The order is only attempted inside:")
for n in ast.walk(exe):
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
        if n.func.attr in ("open_position", "record_open", "_verify_open"):
            print(f"    - {n.func.attr}() at line {n.lineno}")

print()
print("=" * 70)
print("4. Does anything publish an event when open_position is not reached?")
print("=" * 70)
guards = [n for n in ast.walk(exe)
          if isinstance(n, ast.If)]
for g in guards:
    seg = (ast.get_source_segment(src, g.test) or "").strip().replace("\n", " ")
    body = ast.get_source_segment(src, g) or ""
    has_pub = "bus.publish" in body
    has_return = any(isinstance(x, ast.Return) for x in ast.walk(g))
    if has_return:
        print(f"  guard: {seg[:60]:60s} publishes={has_pub}")

print()
print("=" * 70)
print("5. journal.record_open is the ONLY writer of the trades table")
print("=" * 70)
j = (ROOT / "app/core/journal.py").read_text(encoding="utf-8")
print("  record_open exists:", "def record_open" in j)
print("  writes to 'trades':", "INSERT INTO trades" in j)
print()
print("  => trades=0 means record_open never ran, which means open_position")
print("     either raised, or returned not-ok (which publishes")
print("     TRADE_OPEN_FAILED — and no such event was logged).")
