"""Why is no order being placed? Read the loop's own decisions and risk blocks.

Read-only. Prints the last decisions, the distinct reasons, and the risk blocks,
so the block point is visible rather than guessed.
"""
import os
import sqlite3
import sys

DB = sys.argv[1] if len(sys.argv) > 1 else r"E:\forex\aiagent\backend\data\trading.db"

if not os.path.exists(DB):
    print(f"DB not found: {DB}")
    raise SystemExit(1)

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row
cur = c.cursor()

print("=" * 70)
print(f"DB: {DB}")
print("=" * 70)

for t in ("trades", "decisions", "risk_blocks"):
    cur.execute(f"SELECT COUNT(*) FROM {t}")
    print(f"  {t:12s} {cur.fetchone()[0]:>6} rows")

print("\n" + "=" * 70)
print("DECISIONS — schema")
print("=" * 70)
cur.execute("PRAGMA table_info(decisions)")
cols = [r[1] for r in cur.fetchall()]
print("  columns:", cols)

print("\n" + "=" * 70)
print("LAST 15 DECISIONS")
print("=" * 70)
cur.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT 15")
for r in cur.fetchall():
    d = dict(r)
    ts = d.get("ts") or d.get("created_at") or ""
    print(f"  {str(ts)[:19]:20s} {d.get('symbol',''):8s} "
          f"{str(d.get('decision','')):8s} | {str(d.get('reason',''))[:70]}")

print("\n" + "=" * 70)
print("DECISION BREAKDOWN (all time)")
print("=" * 70)
cur.execute("SELECT decision, COUNT(*) n FROM decisions GROUP BY decision ORDER BY n DESC")
for r in cur.fetchall():
    print(f"  {str(r['decision']):10s} {r['n']:>6}")

print("\n" + "=" * 70)
print("TOP REASONS (why it is not trading)")
print("=" * 70)
cur.execute("""SELECT reason, COUNT(*) n FROM decisions
               WHERE decision NOT IN ('BUY','SELL')
               GROUP BY reason ORDER BY n DESC LIMIT 12""")
for r in cur.fetchall():
    print(f"  {r['n']:>6}  {str(r['reason'])[:78]}")

print("\n" + "=" * 70)
print("RISK BLOCKS")
print("=" * 70)
cur.execute("SELECT * FROM risk_blocks ORDER BY id DESC LIMIT 10")
for r in cur.fetchall():
    d = dict(r)
    ts = d.get("ts", "")
    print(f"  {str(ts)[:19]:20s} {d.get('symbol',''):8s} {str(d.get('reason',''))[:60]}")

print("\n" + "=" * 70)
print("TIME RANGE — is the loop still cycling?")
print("=" * 70)
tcol = "ts" if "ts" in cols else cols[1]
cur.execute(f"SELECT MIN({tcol}), MAX({tcol}) FROM decisions")
mn, mx = cur.fetchone()
print(f"  first decision: {str(mn)[:19]}")
print(f"  last  decision: {str(mx)[:19]}")

cur.execute(f"""SELECT {tcol} FROM decisions ORDER BY id DESC LIMIT 1""")
last = cur.fetchone()[0]
print(f"\n  LAST CYCLE AT: {last}")
try:
    from datetime import datetime, timezone
    t = datetime.fromisoformat(str(last))
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - t).total_seconds() / 60
    print(f"  age: {age:.1f} minutes")
    if age > 30:
        print("  >>> LOOP LOOKS STALE — no recent decision at all")
    elif age > 10:
        print("  >>> loop is slow or paused")
    else:
        print("  >>> loop is cycling normally")
except Exception as e:
    print(f"  (age not computed: {e})")

print("\n" + "=" * 70)
print("KEY-VALUE STATE (paused / emergency stop / strategy)")
print("=" * 70)
try:
    cur.execute("SELECT * FROM key_value")
    for r in cur.fetchall():
        d = dict(r)
        k = list(d.values())[0]
        v = list(d.values())[1]
        if k in ("paused", "emergency_stop", "active_version", "last_decision",
                 "trading_mode", "model_routes", "loop_state"):
            print(f"  {k:18s} = {str(v)[:70]}")
except Exception as e:
    print(f"  (cannot read: {e})")
