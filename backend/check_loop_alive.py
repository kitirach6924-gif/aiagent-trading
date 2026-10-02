"""Is the loop alive, and is anything close to a signal?

Distinguishes 'the loop stopped' from 'the loop is waiting for a setup', which
the raw decision count alone cannot tell you.
"""
import datetime as dt
import sqlite3

c = sqlite3.connect("/app/data/trading.db")
c.row_factory = sqlite3.Row

now = dt.datetime.now(dt.timezone.utc)
last = c.execute("SELECT MAX(ts) FROM decisions").fetchone()[0]
if last:
    t = dt.datetime.fromisoformat(last)
    age = (now - t).total_seconds()
    print(f"  loop age          : {age:.0f}s  (cycle is ~30s)")
    print(f"  alive             : {age < 180}")
else:
    print("  NO decisions ever recorded")

# What the stochastic has been doing: a turn needs the slope to cross zero.
rows = c.execute("""SELECT ts, reason FROM decisions
                    WHERE reason LIKE '%slope%' ORDER BY id DESC LIMIT 8""").fetchall()
print()
print("  slope trend (newest first) — a turn = slope crossing 0:")
for r in rows:
    print("   ", str(r["ts"])[11:19], str(r["reason"])[:60])

# Signals since the threshold change, and whether any reached risk PASS.
print()
since = c.execute("""SELECT COUNT(*) FROM decisions
                     WHERE ts > '2026-10-01T01:43:00' AND decision != 'WAIT'""").fetchone()[0]
print("  non-WAIT since threshold change:", since)

# The blocking reasons, so it is clear what is holding entries back.
print()
print("  reasons on non-WAIT decisions (whole history):")
for r in c.execute("""SELECT decision, COALESCE(risk_result,'-') rr, reason, COUNT(*) n
                      FROM decisions WHERE decision != 'WAIT'
                      GROUP BY decision, rr, reason ORDER BY n DESC LIMIT 8"""):
    print(f"    {r['n']:3}x {r['decision']:5s} {r['rr']:6s} {str(r['reason'])[:52]}")
