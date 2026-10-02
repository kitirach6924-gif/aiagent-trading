"""What happened to the 5 signals that passed after the threshold change?

These are BUY/SELL decisions with risk PASS recorded after 01:43. For each one,
answer: did _execute run, and did the broker answer? The journal is the record;
the bridge log holds the broker's own words.
"""
import datetime as dt
import sqlite3

CUT = "2026-10-01T01:43:00"
c = sqlite3.connect("/app/data/trading.db")
c.row_factory = sqlite3.Row

print("=" * 70)
print(f"SIGNALS SINCE {CUT} (every non-WAIT decision, in order)")
print("=" * 70)
rows = c.execute("""SELECT id, ts, symbol, decision, risk_result, reason
                     FROM decisions WHERE ts > ? AND decision != 'WAIT'
                     ORDER BY id""", (CUT,)).fetchall()
if not rows:
    print("  none")
for r in rows:
    print(f"  #{r['id']:<5} {str(r['ts'])[11:19]} {r['symbol']:7s} "
          f"{r['decision']:5s} risk={r['risk_result']}")
    print(f"          {str(r['reason'])[:76]}")

print()
print("=" * 70)
print("THE DECISIVE QUESTION: did _execute ever run?")
print("=" * 70)
att = c.execute("""SELECT COUNT(*) FROM decisions
                   WHERE ts > ? AND risk_result = 'ORDER_ATTEMPT'""", (CUT,)).fetchone()[0]
snd = c.execute("""SELECT COUNT(*) FROM decisions
                   WHERE ts > ? AND risk_result = 'ORDER_SENT'""", (CUT,)).fetchone()[0]
print(f"  ORDER_ATTEMPT journaled : {att}")
print(f"  ORDER_SENT journaled    : {snd}")
print(f"  trades                  : {c.execute('SELECT COUNT(*) FROM trades').fetchone()[0]}")

if rows and att == 0:
    print()
    print("  >>> A decision reached risk=PASS but ORDER_ATTEMPT was never")
    print("      journaled. _execute is the only place that publishes it, so")
    print("      either _act_on_decision returned before calling _execute, or")
    print("      the running container predates the tracing code.")
elif rows and att > snd:
    print()
    print("  >>> orders were attempted but none were accepted — the broker's")
    print("      reason is in the bridge log and in Telegram TRADE_OPEN_FAILED.")
