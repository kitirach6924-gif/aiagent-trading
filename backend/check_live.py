import sqlite3
c = sqlite3.connect("/app/data/trading.db")
cols = [r[1] for r in c.execute("PRAGMA table_info(decisions)")]
print("  decision cols:", cols)
n = 0
for row in c.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT 800"):
    ts = str(row[1])
    if ts < "2026-10-01T09:30":
        continue
    n += 1
    if n <= 4:
        print("  ", ts[11:19], "|", " | ".join(str(x)[:60] for x in row[2:8]))
print("   post-deploy decisions:", n)