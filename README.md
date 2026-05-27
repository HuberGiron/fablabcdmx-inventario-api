# FabLab CDMX Inventario API

Backend independiente para subir, consultar y descargar imágenes y documentación asociada a los items del inventario.

Este repositorio debe vivir separado del frontend. El código puede actualizarse con `git pull` sin borrar imágenes ni documentos, porque los archivos subidos se guardan fuera del repositorio, en una carpeta persistente definida por `UPLOAD_ROOT`.

## Estructura

```text
fablabcdmx-inventario-api/
├─ app/
│  ├─ __init__.py
│  └─ main.py
├─ deploy/
│  ├─ inventario-api.service
│  └─ nginx-inventario-api.conf
├─ docs/
│  └─ firestore-fields.md
├─ scripts/
│  ├─ install_ubuntu.sh
│  └─ update_backend.sh
├─ .env.example
├─ .gitignore
├─ requirements.txt
└─ README.md
```

## Endpoints

- `GET /api/health`: prueba del servicio.
- `POST /api/items/{item_id}/assets/image`: sube imagen del item. Requiere token Firebase y usuario con `role: admin`.
- `POST /api/items/{item_id}/assets/documentation`: sube documentación del item. Requiere token Firebase y usuario con `role: admin`.
- `GET /api/files/{file_id}/view`: muestra el archivo si está activo.
- `GET /api/files/{file_id}/download`: descarga el archivo si está activo.

## Variables de entorno

Copia `.env.example` a `/etc/fablab-inventario-api/api.env` y ajusta:

```env
PROJECT_NAME="FabLab Inventario API"
GOOGLE_APPLICATION_CREDENTIALS="/etc/fablab-inventario-api/firebase-service-account.json"
UPLOAD_ROOT="/var/lib/fablab-inventario-api/uploads"
CORS_ORIGINS="https://inventario.mecatronica-ibero.mx,http://localhost:5500,http://127.0.0.1:5500"
MAX_IMAGE_BYTES=8388608
MAX_DOCUMENT_BYTES=26214400
```

## Instalación recomendada en Droplet

### 1. Crear repositorio en GitHub

En tu computadora:

```bash
git init
git add .
git commit -m "Backend inicial para inventario FabLab"
git branch -M main
git remote add origin git@github.com:TU_USUARIO/fablabcdmx-inventario-api.git
git push -u origin main
```

### 2. Clonar en el Droplet

```bash
sudo mkdir -p /opt/fablab-inventario-api
sudo chown -R $USER:$USER /opt/fablab-inventario-api
git clone git@github.com:TU_USUARIO/fablabcdmx-inventario-api.git /opt/fablab-inventario-api
```

### 3. Crear carpetas persistentes

```bash
sudo mkdir -p /etc/fablab-inventario-api
sudo mkdir -p /var/lib/fablab-inventario-api/uploads
sudo chown -R www-data:www-data /var/lib/fablab-inventario-api/uploads
sudo chmod 750 /var/lib/fablab-inventario-api/uploads
```

La carpeta `/var/lib/fablab-inventario-api/uploads` no pertenece al repositorio. No se borra con `git pull`, `rsync` ni actualizaciones del frontend.

### 4. Crear entorno Python

```bash
cd /opt/fablab-inventario-api
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
sudo chown -R www-data:www-data /opt/fablab-inventario-api
```

### 5. Configurar Firebase Admin

Descarga desde Firebase Console el JSON de Service Account y súbelo al servidor. Luego:

```bash
sudo mv firebase-service-account.json /etc/fablab-inventario-api/firebase-service-account.json
sudo chown root:www-data /etc/fablab-inventario-api/firebase-service-account.json
sudo chmod 640 /etc/fablab-inventario-api/firebase-service-account.json
```

### 6. Crear archivo de entorno

```bash
sudo nano /etc/fablab-inventario-api/api.env
```

Pega:

```env
PROJECT_NAME="FabLab Inventario API"
GOOGLE_APPLICATION_CREDENTIALS="/etc/fablab-inventario-api/firebase-service-account.json"
UPLOAD_ROOT="/var/lib/fablab-inventario-api/uploads"
CORS_ORIGINS="https://inventario.mecatronica-ibero.mx,http://localhost:5500,http://127.0.0.1:5500"
MAX_IMAGE_BYTES=8388608
MAX_DOCUMENT_BYTES=26214400
```

### 7. Activar systemd

```bash
sudo cp /opt/fablab-inventario-api/deploy/inventario-api.service /etc/systemd/system/inventario-api.service
sudo systemctl daemon-reload
sudo systemctl enable inventario-api
sudo systemctl start inventario-api
sudo systemctl status inventario-api
```

Prueba local:

```bash
curl http://127.0.0.1:8001/api/health
```

### 8. Configurar Nginx

```bash
sudo cp /opt/fablab-inventario-api/deploy/nginx-inventario-api.conf /etc/nginx/sites-available/inventario-api
sudo ln -sfn /etc/nginx/sites-available/inventario-api /etc/nginx/sites-enabled/inventario-api
sudo nginx -t
sudo systemctl reload nginx
```

### 9. Activar HTTPS

Asegúrate de tener el DNS `inventario-api.mecatronica-ibero.mx` apuntando al Droplet.

```bash
sudo certbot --nginx -d inventario-api.mecatronica-ibero.mx
```

Prueba pública:

```bash
curl https://inventario-api.mecatronica-ibero.mx/api/health
```

## Actualización del backend

```bash
cd /opt/fablab-inventario-api
git pull
.venv/bin/pip install -r requirements.txt
sudo systemctl restart inventario-api
```

O usa:

```bash
sudo bash scripts/update_backend.sh
```

## Respaldos recomendados

El código se respalda en GitHub. Los archivos subidos no están en GitHub; respáldalos aparte.

```bash
sudo tar -czf /root/fablab-inventario-uploads-$(date +%Y%m%d).tar.gz /var/lib/fablab-inventario-api/uploads
```

También conviene respaldar la metadata de Firestore, porque ahí se guarda la relación entre `items` y `files`.
