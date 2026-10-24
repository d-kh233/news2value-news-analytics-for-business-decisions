#!/usr/bin/env bash
# Проверка развёрнутого сервиса целиком: интерфейс, API, запуск агента, трейс.
#   ./deploy/check.sh                      # на самом сервере
#   ./deploy/check.sh http://203.0.113.10  # с любого компьютера
set -euo pipefail
BASE="${1:-http://localhost}"
# API на том же адресе под /api (nginx). Если API на отдельном адресе (Render): API=https://… ./deploy/check.sh <адрес интерфейса>
API="${API:-$BASE/api}"
ROLE="${ROLE:-cfo}"
ok()   { printf '  ✓ %s\n' "$1"; }
fail() { printf '  ✗ %s\n' "$1"; exit 1; }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

echo "Проверяю $BASE"
curl -fsS "$BASE/" | grep -q 'id="root"' && ok "интерфейс открывается" || fail "интерфейс не отдаётся (контейнер web)"
curl -fsS "$BASE/analytics" | grep -q 'id="root"' && ok "страницы интерфейса открываются по прямой ссылке" || fail "маршрут /analytics"
[ "$(curl -fsS "$API/health" | json 'd["status"]')" = "ok" ] && ok "API отвечает" || fail "API не отвечает (контейнер api)"
curl -fsS -o /dev/null "$API/docs" && ok "документация API: $API/docs" || fail "/docs"

CID="$(curl -fsS "$API/companies" | json 'd["items"][0]["id"]')"
ok "компании есть, проверяю на $CID"
JOB="$(curl -fsS -X POST "$API/companies/$CID/insights/refresh?role=$ROLE" | json 'd["job_id"]')"
STATUS=pending
for _ in $(seq 1 90); do
  STATUS="$(curl -fsS "$API/jobs/$JOB" | json 'd["status"]')"
  [ "$STATUS" = done ] || [ "$STATUS" = failed ] && break
  sleep 2
done
[ "$STATUS" = done ] && ok "агент отработал (роль $ROLE)" || fail "задача агента: $STATUS — смотрите docker compose logs api"

curl -fsS "$API/companies/$CID/insights?limit=100" | json '
"  ✓ карточек: %d, из них написала модель: %d, по шаблонам: %d" % (
    len(d["items"]),
    sum(1 for i in d["items"] if i.get("generated_by") == "llm"),
    sum(1 for i in d["items"] if i.get("generated_by") == "rules"))'
curl -fsS "$API/jobs/$JOB/run" | json '
"  ✓ трейс: %d шагов, режим модели %s" % (len(d["trace"]), d["llm_mode"])'
echo "Готово."
