import json
import sqlite3
from collections import defaultdict

c = sqlite3.connect("/app/data/trading.db")
c.row_factory = sqlite3.Row
deals = list(c.execute(
    "SELECT * FROM deals ORDER BY ticket")) if any(
    r[0] == "deals" for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")) else []
print("  deals table rows:", len(deals))
if deals:
    print("  cols:", [d[0] for d in deals[0].keys()])
    for d in deals[:6]:
        print("   ", {k: d[k] for k in list(d.keys())[:9]})