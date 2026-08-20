#!/usr/bin/env bash
# Инцидент живёт как issue в репозитории — это и хранилище состояния, и
# история аварий. Без него наблюдатель либо молчит после первого сообщения,
# либо будит каждые пять минут, пока прод лежит.
set -uo pipefail

ACTION="${1:-open}"
TITLE="Прод недоступен"
LABEL="outage"

tg() {
  [ -z "${TG_TOKEN:-}" ] && { echo "TELEGRAM_BOT_TOKEN не задан — сообщение не отправлено"; return 0; }
  [ -z "${TG_CHAT:-}" ]  && { echo "TELEGRAM_CHAT_ID не задан — сообщение не отправлено"; return 0; }
  curl -s -o /dev/null -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
    -d "chat_id=${TG_CHAT}" -d "parse_mode=HTML" --data-urlencode "text=$1"
}

open_issue=$(gh issue list --label "$LABEL" --state open --json number --jq '.[0].number' 2>/dev/null || echo "")

if [ "$ACTION" = "open" ]; then
  body="Проверка не прошла: <b>${FAILED:-неизвестно}</b>%0A%0AЛог: ${RUN_URL:-}"
  if [ -z "$open_issue" ]; then
    gh issue create --title "$TITLE" --label "$LABEL" \
      --body "Упало: ${FAILED:-неизвестно}

Лог проверки: ${RUN_URL:-}

Issue закроется само, когда проверка снова пройдёт." >/dev/null 2>&1 || true
    tg "🔴 <b>Avris лёг</b>%0AУпало: ${FAILED:-неизвестно}%0A%0A${RUN_URL:-}"
    echo "инцидент заведён, сообщение отправлено"
  else
    # Уже знаем — молчим, только дописываем в issue, чтобы видеть длительность.
    gh issue comment "$open_issue" --body "Всё ещё лежит: ${FAILED:-неизвестно} · ${RUN_URL:-}" >/dev/null 2>&1 || true
    echo "инцидент #$open_issue уже открыт — повторное сообщение не отправляю"
  fi
else
  if [ -n "$open_issue" ]; then
    gh issue close "$open_issue" --comment "Проверка снова проходит: ${RUN_URL:-}" >/dev/null 2>&1 || true
    tg "🟢 <b>Avris поднялся</b>%0AИнцидент #${open_issue} закрыт."
    echo "инцидент #$open_issue закрыт, сообщение отправлено"
  else
    echo "всё в порядке, открытых инцидентов нет"
  fi
fi
