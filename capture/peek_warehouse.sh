#!/bin/bash
# Ambient peek (Super+Shift+W): подглядеть "что сейчас" БЕЗ открытия окна склада.
# Клик по уведомлению = сразу открывает нужный экран (foot-in-the-door) —
# минуя меню и весь остальной склад.
URL_BASE="http://127.0.0.1:8091"

RESP=$(curl -s -m 3 "$URL_BASE/peek")
if [ -z "$RESP" ]; then
    notify-send -a "Склад" -u critical "⚠ Склад не отвечает" 2>/dev/null
    exit 0
fi

get() { python3 -c "import json,sys;print(json.loads(sys.argv[1])['now']['$1'])" "$RESP"; }
ACT=$(get act); WHY=$(get why); URL=$(get url)

# timeout: notify-send -A блокируется до клика/закрытия - без потолка висящее
# уведомление стопорит systemd-таймер dwell_peek.sh навсегда (не пересчитывает
# следующий запуск, пока сервис не завершится)
ACTION=$(timeout 45 notify-send -a "Склад" -A "open=▶ начать" "$ACT" "$WHY" 2>/dev/null)
if [ "$ACTION" = "open" ]; then
    exec google-chrome --app="$URL_BASE$URL"
fi
