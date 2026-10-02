"""Is the order pipeline wired all the way to the bridge, or does it stop short?

Answers, from inside the container:
  1. When were the last BUY/SELL decisions, the last risk block, the last decision?
  2. Is the MT5 bridge actually reachable from this container?
"""
import socket
import sqlite3

DB = "/app/data/trading.db"
c = sqlite3.connect(DB)

print("=" * 68)
print("TIMING — did anything happen after the last signal?")
print("=" * 68)
last_sig = c.execute("SELECT MAX(ts) FROM decisions WHERE decision IN ('BUY','SELL')").fetchone()[0]
last_block = c.execute("SELECT MAX(ts) FROM risk_blocks").fetchone()[0]
last_dec = c.execute("SELECT MAX(ts) FROM decisions").fetchone()[0]
print("  last BUY/SELL decision :", last_sig)
print("  last risk_block        :", last_block)
print("  last decision of any   :", last_dec)

print()
print("=" * 68)
print("RISK RESULT ON THE 42 SIGNALS")
print("=" * 68)
for r in c.execute("""SELECT decision, risk_result, COUNT(*) n FROM decisions
                      WHERE decision IN ('BUY','SELL') GROUP BY decision, risk_result"""):
    print("  %-6s risk_result=%-10s %d" % (r[0], str(r[1]), r[2]))

print()
print("  A risk_result of PASS/BLOCK here means the gate ran and the journal was")
print("  written. A missing/None risk_result means the cycle never got that far.")

print()
print("=" * 68)
print("BRIDGE REACHABILITY from this container")
print("=" * 68)
for host, port in (("100.69.186.8", 8765),):
    try:
        s = socket.create_connection((host, port), timeout=6)
        s.close()
        print("  %s:%d  CONNECTED" % (host, port))
    except Exception as e:
        print("  %s:%d  UNREACHABLE — %s: %s" % (host, port, type(e).__name__, e))

print()
print("=" * 68)
print("ORDERS / TRADES")
print("=" * 68)
for t in ("trades", "orders"):
    try:
        n = c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        print("  %-8s %d rows" % (t, n))
    except Exception as e:
        print("  %-8s (no table: %s)" % (t, e))
