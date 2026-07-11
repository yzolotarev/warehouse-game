#!/usr/bin/env bash
# Склад одним жестом (Super+W) - тумблер:
#   окна нет         → запустить Chrome --app
#   окно не в фокусе → сфокусировать
#   окно в фокусе    → свернуть
URL="http://127.0.0.1:8091/"

# окно Chrome --app получает WM_CLASS вида "127.0.0.1.Google-chrome"
WIN=$(wmctrl -x -l | awk '/ 127\.0\.0\.1\./{print $1; exit}')
if [ -n "$WIN" ]; then
    ACTIVE=$(xprop -root _NET_ACTIVE_WINDOW 2>/dev/null | awk '{print $NF}')
    if [ -n "$ACTIVE" ] && [ $((ACTIVE)) -eq $((WIN)) ]; then
        exec xdotool windowminimize "$((WIN))"
    fi
    exec wmctrl -i -a "$WIN"
fi
exec google-chrome --app="$URL"
