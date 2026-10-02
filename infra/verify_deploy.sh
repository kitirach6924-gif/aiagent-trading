#!/bin/sh
# Confirm each deployed fix is present in the RUNNING container.
C=infra-trading-core-1

check() {
  desc="$1"; file="$2"; pattern="$3"
  if docker exec "$C" grep -q "$pattern" "$file" 2>/dev/null; then
    echo "  OK    $desc"
  else
    echo "  FAIL  $desc  ($file !~ $pattern)"
  fi
}

check "session derived from clock, not hardcoded LONDON" /app/app/core/risk.py 'str | None = None'
check "telegram dedup window active"                    /app/app/integrations/telegram.py 'dedup_window_seconds'
check "max_risk_percent documented as a no-op"          /app/app/config.py 'DEAD SETTING'
check "bridge auth dead branch removed"                 /app/app/mt5/http_bridge.py 'compare_digest'
check "engine uses the shared session table"            /app/app/core/strategy_engine.py 'from app.core import sessions'
check "risk gate uses the shared session table"         /app/app/core/risk.py 'from app.core import sessions'

if docker exec "$C" ls /app/app/core/sessions.py >/dev/null 2>&1; then
  echo "  OK    sessions.py present (single source of truth)"
else
  echo "  FAIL  sessions.py missing"
fi

echo
echo "  no hardcoded LONDON default remains:"
docker exec "$C" grep -n 'session_name: str = "LONDON"' /app/app/core/risk.py 2>/dev/null \
  && echo "  FAIL  old default still present" || echo "  OK    confirmed absent"
