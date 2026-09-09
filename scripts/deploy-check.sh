#!/usr/bin/env bash
# Доехал ли выкат до прода.
#
# Дважды за 9 сентября выкат объявлялся состоявшимся, когда его не было:
# приложение падало на старте, Railway оставлял работать прежний контейнер,
# а версия файлов приходила из кэша Cloudflare — снаружи всё выглядело живым.
# Проверять надо не файл, а процесс: какой коммит он несёт и когда стартовал.
#
#   scripts/deploy-check.sh            — ждёт коммит из HEAD
#   scripts/deploy-check.sh <sha>      — ждёт конкретный коммит
set -uo pipefail

BASE="${BASE_URL:-https://theavris.ai}"
WANT="${1:-$(git rev-parse --short=7 HEAD 2>/dev/null)}"
TRIES="${DEPLOY_CHECK_TRIES:-40}"      # 40 × 15 с ≈ 10 минут

if [ -z "$WANT" ]; then
  echo "не знаю, какой коммит ждать: укажите аргументом" >&2
  exit 2
fi

echo "жду коммит $WANT на $BASE"
for i in $(seq 1 "$TRIES"); do
  body=$(curl -s --max-time 15 "$BASE/api/health" || echo '{}')
  got=$(printf '%s' "$body" | sed -n 's/.*"commit"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
  started=$(printf '%s' "$body" | sed -n 's/.*"started_at"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
  if [ "$got" = "$WANT" ]; then
    echo "выкат доехал: коммит $got, процесс стартовал $started"
    exit 0
  fi
  if [ "$got" = "unknown" ]; then
    # Образ собран без метаданных о коммите — проверять нечего, и молчать
    # об этом нельзя: иначе скрипт создаёт видимость проверки.
    echo "прод не сообщает коммит (unknown) — проверить выкат этим способом нельзя" >&2
    exit 3
  fi
  [ $((i % 4)) -eq 0 ] && echo "  ещё старый коммит: ${got:-нет ответа}"
  sleep 15
done

echo "за отведённое время прод так и не поднялся на $WANT (сейчас ${got:-нет ответа})" >&2
echo "смотрите статус контейнера: railway status" >&2
exit 1
