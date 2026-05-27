#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/fablab-inventario-api"

cd "$APP_DIR"
git pull
.venv/bin/pip install -r requirements.txt
sudo systemctl restart inventario-api
sudo systemctl status inventario-api --no-pager
