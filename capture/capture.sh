#!/bin/bash
# Хоткей-захват в инбокс (Ctrl+Alt+A). Аргумент = текст (для тестов), иначе окно zenity.
TEXT="$*"
if [ -z "$TEXT" ]; then
    RES=$(xdpyinfo 2>/dev/null | awk '/dimensions:/{print $2}')
    SW=${RES%x*}; SW=${SW:-1920}
    X=$(( (SW - 540) / 2 ))
    exec google-chrome --app="http://127.0.0.1:8091/capture" \
        --window-size=520,132 --window-position=$X,220
fi
[ -z "$TEXT" ] && exit 0
RESP=$(curl -s -m 3 -X POST http://127.0.0.1:8091/inbox \
    -H "Content-Type: application/json" \
    -d "$(python3 -c 'import json,sys; print(json.dumps({"text": sys.argv[1], "source": "pc"}))' "$TEXT")")
if echo "$RESP" | grep -q '"id"'; then
    SIMILAR=$(python3 -c "
import json,sys
d=json.loads(sys.argv[1])
s=d.get('similar_trashed')
print(s['text'][:60] if s else '')" "$RESP" 2>/dev/null)
    if [ -n "$SIMILAR" ]; then
        notify-send -t 4000 "📦 В инбоксе (похоже на затрешенное)" "$TEXT"$'\n'"↳ было: $SIMILAR" 2>/dev/null
    else
        notify-send -t 1500 "📦 В инбоксе" "$TEXT" 2>/dev/null
    fi
else
    notify-send -u critical "⚠ Склад не отвечает" "Текст НЕ сохранён: $TEXT" 2>/dev/null
fi
