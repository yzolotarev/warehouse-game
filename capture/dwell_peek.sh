#!/bin/bash
# Dwell-triggered peek: та же ambient-нотификация, что и Super+Shift+W,
# только запускает её не человек, а факт застоя - самый старый нетронутый
# инбокс-объект ждёт дольше THRESHOLD_MIN. Кулдаун, чтобы не спамить,
# пока человек осознанно занят чем-то другим.
URL_BASE="http://127.0.0.1:8091"
THRESHOLD_MIN=140     # ~2x медианного времени до триажа (69 мин по данным анализа)
COOLDOWN_MIN=90
STATE_FILE="$HOME/.local/share/warehouse/last_dwell_notify"

RESP=$(curl -s -m 3 "$URL_BASE/inbox_dwell")
[ -z "$RESP" ] && exit 0   # склад не отвечает - молча выходим, это не человеку сигнал

OLDEST=$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['oldest_minutes'])" "$RESP")
[ "$OLDEST" -lt "$THRESHOLD_MIN" ] && exit 0

if [ -f "$STATE_FILE" ]; then
    LAST=$(cat "$STATE_FILE")
    NOW=$(date +%s)
    [ $(( (NOW - LAST) / 60 )) -lt "$COOLDOWN_MIN" ] && exit 0
fi

date +%s > "$STATE_FILE"
exec "$(dirname "$0")/peek_warehouse.sh"
