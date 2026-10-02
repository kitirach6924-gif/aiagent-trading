"""Watch the order pipeline after the threshold change.

Reports, from inside the container:
  - how many decisions since the restart
  - the score-gate split (WAIT vs a real signal)
  - the decisive number: risk_result breakdown
  - whether any order was attempted, and whether any reached the broker
"""
import sqlite3
import sys

DB = "/app/data/trading.db"
c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row


def one(q, *a):
    return c.execute(q, a).fetchone()[0]


print("=" * 68)
print("PIPELINE STATE")
print("=" * 68)
print("  decisions      :", one("SELECT COUNT(*) FROM decisions"))
print("  risk_blocks    :", one("SELECT COUNT(*) FROM risk_blocks"))
print("  trades         :", one("SELECT COUNT(*) FROM trades"))
print("  last decision  :", one("SELECT MAX(ts) FROM decisions"))

print()
print("=" * 68)
print("DECISIONS BY RESULT (the whole history)")
print("=" * 68)
for r in c.execute("""SELECT decision, COALESCE(risk_result,'(null)') rr, COUNT(*) n
                      FROM decisions GROUP BY decision, rr ORDER BY n DESC"""):
    print("  %-8s %-10s %d" % (r["decision"], r["rr"], r["n"]))

print()
print("=" * 68)
print("ORDER TRACING — the new evidence")
print("=" * 68)
attempts = one("SELECT COUNT(*) FROM decisions WHERE risk_result='ORDER_ATTEMPT'")
sent = one("SELECT COUNT(*) FROM decisions WHERE risk_result='ORDER_SENT'")
print("  ORDER_ATTEMPT journaled :", attempts)
print("  ORDER_SENT journaled    :", sent)
print("  trades recorded         :", one("SELECT COUNT(*) FROM trades"))
if sent == 0 and one("SELECT COUNT(*) FROM decisions WHERE risk_result='PASS'") > 0:
    print()
    print("  >>> risk PASS but no ORDER_SENT — the order died inside the")
    print("      connector call. Check the Telegram TRADE_OPEN_FAILED message")
    print("      for the broker's own error text.")

print()
print("=" * 68)
print("LAST 12 DECISIONS")
print("=" * 68)
for r in c.execute("""SELECT ts, decision, COALESCE(risk_result,'') rr, reason
                      FROM decisions ORDER BY id DESC LIMIT 12"""):
    print("  %s %-6s %-10s %s" % (str(r["ts"])[:19], r["decision"], r["rr"][:10],
                                  str(r["reason"])[:48]))
