#!/usr/bin/env bash
# Склад одним жестом (Super+W): фокус уже открытого окна или новое Chrome --app.
URL="http://127.0.0.1:8091/"

# окно Chrome --app получает WM_CLASS вида "127.0.0.1.Google-chrome"
if wmctrl -x -l | grep -q "^0x[0-9a-f]*  *[0-9-]*  *127\.0\.0\.1\."; then
    exec wmctrl -x -a "127.0.0.1."
fi
exec google-chrome --app="$URL"
