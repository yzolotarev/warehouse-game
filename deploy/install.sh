#!/usr/bin/env bash
# Установка склада: зависимости + systemd user-юниты + CLI.
# Запускать из корня репозитория: ./deploy/install.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
UNITS="$HOME/.config/systemd/user"

echo "→ python-зависимости"
python3 -m pip install --user -q -r "$REPO/requirements.txt"

echo "→ systemd user-юнит warehouse-server"
mkdir -p "$UNITS"
sed "s|__REPO__|$REPO|" "$REPO/deploy/warehouse-server.service" > "$UNITS/warehouse-server.service"
systemctl --user daemon-reload
systemctl --user enable --now warehouse-server

echo "→ CLI wh → ~/bin"
mkdir -p "$HOME/bin"
ln -sf "$REPO/bin/wh" "$HOME/bin/wh"

# TG-воркер ставится только при наличии токена (создай бота у @BotFather)
if [ -f "$HOME/.config/warehouse/tg_token" ]; then
    echo "→ systemd user-юнит warehouse-tg (токен найден)"
    sed "s|__REPO__|$REPO|" "$REPO/deploy/warehouse-tg.service" > "$UNITS/warehouse-tg.service"
    systemctl --user daemon-reload
    systemctl --user enable --now warehouse-tg
else
    echo "· TG-вход пропущен: нет ~/.config/warehouse/tg_token (опционально)"
fi

echo
echo "Готово. Склад: http://127.0.0.1:8091/"
echo "Хоткеи навесь сам (Настройки → Клавиатура):"
echo "  захват в инбокс  → $REPO/capture/capture.sh"
echo "  открыть склад    → $REPO/capture/open_warehouse.sh"
