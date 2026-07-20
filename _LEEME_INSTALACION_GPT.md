# Instalación del módulo GPT create-only

Este ZIP es un overlay preparado para el commit `e5ae3e5` del repositorio
`HuberGiron/fablabcdmx-inventario-api`. Sus rutas comienzan en la raíz del
repositorio, por lo que debe descomprimirse dentro de esa carpeta.

No contiene `.env`, cuentas de servicio de Firebase ni secretos reales. El
archivo `.env.example` es únicamente una referencia y no sustituye el archivo
activo `/etc/fablab-inventario-api/api.env` del servidor.

## 1. Aplicar en una rama local

```bash
cd fablabcdmx-inventario-api
git switch -c feature/gpt-inventory-create-only
unzip -o /ruta/fablabcdmx-inventario-api-gpt-overlay.zip -d .
git status --short
git diff --check
python -m unittest discover -s tests -v
```

Revisa el `git diff` antes de confirmar y subir los cambios. El ZIP modifica
`app/main.py`, `README.md` y `.env.example`; agrega dos módulos, documentación
y pruebas. No reemplaza `deploy/`, `requirements.txt`, archivos subidos ni la
configuración de Nginx/systemd.

## 2. Conservar Firebase y agregar los secretos de la Action

No cambies estas variables existentes:

```env
GOOGLE_APPLICATION_CREDENTIALS="/etc/fablab-inventario-api/firebase-service-account.json"
UPLOAD_ROOT="/var/lib/fablab-inventario-api/uploads"
CORS_ORIGINS="https://inventario.mecatronica-ibero.mx,http://localhost:5500,http://127.0.0.1:5500"
```

Genera dos valores independientes:

```bash
openssl rand -hex 32
openssl rand -hex 32
```

Edita el archivo real del servidor:

```bash
sudo nano /etc/fablab-inventario-api/api.env
```

Agrega, sin borrar las variables actuales:

```env
GPT_ACTION_API_KEY="PRIMER_VALOR_GENERADO"
GPT_ACTION_SIGNING_SECRET="SEGUNDO_VALOR_GENERADO"
GPT_ACTION_TOKEN_TTL_SECONDS=600
```

- `GPT_ACTION_API_KEY` autentica las llamadas del GPT al backend. Ese primer
  valor también se captura en la Action como API Key de tipo Bearer.
- `GPT_ACTION_SIGNING_SECRET` firma las confirmaciones de alta. Nunca se pega
  en el GPT ni sale del servidor.
- `GPT_ACTION_TOKEN_TTL_SECONDS` define la vigencia de la confirmación. Se
  limita automáticamente al intervalo de 60 a 1800 segundos.

La credencial existente de Firebase autentica al backend frente a Firestore;
no autentica al GPT frente al backend. Por eso ambos grupos de credenciales
son necesarios y cumplen funciones diferentes.

## 3. Desplegar

Después de subir la rama y actualizar el servidor:

```bash
cd /opt/fablab-inventario-api
git pull
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
sudo systemctl restart inventario-api
sudo systemctl status inventario-api --no-pager
```

No se requieren dependencias nuevas de producción.

## 4. Probar sin escribir

```bash
curl https://inventario-api.mecatronica-ibero.mx/api/health
curl -H "Authorization: Bearer PRIMER_VALOR_GENERADO" \
  "https://inventario-api.mecatronica-ibero.mx/api/gpt/context?includeLocations=false"
```

La segunda petición sólo consulta. No pruebes `/api/gpt/create` manualmente
hasta haber validado el flujo completo de preparación y confirmación.

## 5. Configurar la Action

1. Pega `docs/gpt-action-openapi.yaml` en el esquema de la Action.
2. Selecciona autenticación `API Key` y tipo `Bearer`.
3. Captura únicamente el valor de `GPT_ACTION_API_KEY`.
4. Conserva el servidor definido en el esquema:
   `https://inventario-api.mecatronica-ibero.mx`.

Nginx ya envía todas las rutas del dominio a FastAPI, por lo que no requiere
una ubicación adicional. Tampoco debes añadir ChatGPT a `CORS_ORIGINS`: las
Actions hacen peticiones servidor a servidor y no dependen del CORS del
navegador.

## Garantía funcional

Las rutas existentes permanecen disponibles. El nuevo router sólo ocupa
`/api/gpt/*`. Sus consultas y preparación no escriben. La operación final usa
exclusivamente `batch.create(...)`, vuelve a consultar Firestore y rechaza el
lote completo si ya existe algún SKU, ID, zona, subzona o ubicación. No se
agregan rutas `PUT`, `PATCH` o `DELETE` para el GPT.
