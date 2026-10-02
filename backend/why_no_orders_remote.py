import json
import sqlite3
import sys

db = sys.argv[1] if len(sys.argv) > 1 else "/app/data/trading.db"
c = sqlite3.connect(db)
c.row_factory = sqlite3.Row


def show(title):
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


show("RISK BLOCKS (last 12)")
for r in c.execute("SELECT * FROM risk_blocks ORDER BY id DESC LIMIT 12"):
    d = dict(r)
    print("  %-20s %-8s %s" % (str(d.get("ts"))[:19], d.get("symbol"),
                               str(d.get("reason"))[:66]))

show("DISTINCT BLOCK REASONS")
for r in c.execute("SELECT reason, COUNT(*) n FROM risk_blocks GROUP BY reason ORDER BY n DESC LIMIT 10"):
    print("  %4d  %s" % (r["n"], str(r["reason"])[:80]))

show("DECISION BREAKDOWN")
for r in c.execute("SELECT decision, COUNT(*) n FROM decisions GROUP BY decision ORDER BY n DESC"):
    print("  %-8s %d" % (str(r["decision"]), r["n"]))

show("TOP NON-WAIT REASONS")
for r in c.execute("""SELECT reason, COUNT(*) n FROM decisions
                      WHERE decision='WAIT' GROUP BY reason ORDER BY n DESC LIMIT 8"""):
    print("  %4d  %s" % (r["n"], str(r["reason"])[:80]))

show("KEY STATE")
try:
    for r in c.execute("SELECT * FROM key_value"):
        d = dict(r)
        k = list(d)[0]
        v = list(d.values())[1]
        if k in ("paused", "emergency_stop", "active_version", "trading_mode",
                 "last_decision", "loop_state"):
            print("  %-16s = %s" % (k, str(v)[:70]))
except Exception as e:
    print("  (cannot read: %s)" % e)
