#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/fablab-inventario-api"
ENV_DIR="/etc/fablab-inventario-api"
UPLOAD_DIR="/var/lib/fablab-inventario-api/uploads"
SERVICE_FILE="/etc/systemd/system/inventario-api.service"
NGINX_AVAILABLE="/etc/nginx/sites-available/inventario-api"
NGINX_ENABLED="/etc/nginx/sites-enabled/inventario-api"

if [[ "$EUID" -ne 0 ]]; then
  echo "Ejecuta este script con sudo."
  exit 1
fi

apt update
apt install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx

mkdir -p "$APP_DIR" "$ENV_DIR" "$UPLOAD_DIR"
chown -R www-data:www-data "$UPLOAD_DIR"
chmod 750 "$UPLOAD_DIR"

cd "$APP_DIR"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
chown -R www-data:www-data "$APP_DIR"

if [[ ! -f "$ENV_DIR/api.env" ]]; then
  cp .env.example "$ENV_DIR/api.env"
  echo "Edita $ENV_DIR/api.env antes de arrancar el servicio."
fi

cp deploy/inventario-api.service "$SERVICE_FILE"
cp deploy/nginx-inventario-api.conf "$NGINX_AVAILABLE"
ln -sfn "$NGINX_AVAILABLE" "$NGINX_ENABLED"

systemctl daemon-reload
systemctl enable inventario-api
nginx -t
systemctl reload nginx

echo "Instalación base lista. Ahora edita $ENV_DIR/api.env, coloca la credencial Firebase y ejecuta:"
echo "sudo systemctl start inventario-api"
echo "sudo certbot --nginx -d inventario-api.mecatronica-ibero.mx"
