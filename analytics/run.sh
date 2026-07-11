#!/bin/bash
# Дашборд активности склада (datasette + datasette-dashboards).
# Читает ту же живую БД, что и сам склад — без ETL, без копий.
cd "$(dirname "$0")"
exec datasette ~/.local/share/warehouse/warehouse.db \
    --metadata metadata.yml \
    --port 8500 \
    -h 127.0.0.1
