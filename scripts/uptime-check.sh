#!/usr/bin/env bash
# Проверка живости прода. Падает — значит врач сейчас видит сломанный продукт.
#
# Каждая точка проверяется дважды с паузой: одиночный сбой сети между
# GitHub и Cloudflare — не авария, а шум, и будить по нему никого не нужно.
set -uo pipefail

BASE="${BASE_URL:-https://theavris.ai}"
FAILED=()

# один заход: имя, адрес, ожидаемый код, доп. заголовок
try_once() {
  local url="$1" want="$2" hdr="${3:-}"
  local code
  if [ -n "$hdr" ]; then
    code=$(curl -s -o /tmp/body.txt -w '%{http_code}' --max-time 20 -H "$hdr" "$url" || echo 000)
  else
    code=$(curl -s -o /tmp/body.txt -w '%{http_code}' --max-time 20 "$url" || echo 000)
  fi
  [ "$code" = "$want" ]
}

check() {
  local name="$1" path="$2" want="$3" hdr="${4:-}"
  if try_once "$BASE$path" "$want" "$hdr"; then
    echo "  ok      $name"
    return 0
  fi
  sleep 15
  if try_once "$BASE$path" "$want" "$hdr"; then
    echo "  ok      $name (со второй попытки)"
    return 0
  fi
  echo "  ПАДЕНИЕ $name — $(head -c 160 /tmp/body.txt)"
  FAILED+=("$name")
}

echo "Проверяю $BASE"
check "лендинг"        "/"            200
check "приложение"     "/app"         200
check "тарифы"         "/pricing"     200
check "API живо"       "/api/health"  200
check "авторизация"    "/api/patients/" 401

if [ -n "${HEALTH_TOKEN:-}" ]; then
  q=""
  [ "${DEEP:-}" = "live" ] && q="?probe=live"
  check "конфигурация${q:+ и ключи}" "/api/health/deep$q" 200 "X-Health-Token: $HEALTH_TOKEN"
else
  echo "  пропуск конфигурации: HEALTH_TOKEN не задан в секретах репозитория"
fi

if [ ${#FAILED[@]} -gt 0 ]; then
  printf 'FAILED=%s\n' "$(IFS=', '; echo "${FAILED[*]}")" >> "${GITHUB_ENV:-/dev/null}"
  echo
  echo "Упало: ${FAILED[*]}"
  exit 1
fi
echo
echo "Всё живо"
