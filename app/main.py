from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import firebase_admin
from firebase_admin import auth, credentials, firestore

load_dotenv()

PROJECT_NAME = os.getenv("PROJECT_NAME", "FabLab Inventario API")
UPLOAD_ROOT = Path(os.getenv("UPLOAD_ROOT", "/var/lib/fablab-inventario-api/uploads")).resolve()
MAX_IMAGE_BYTES = int(os.getenv("MAX_IMAGE_BYTES", str(8 * 1024 * 1024)))
MAX_DOCUMENT_BYTES = int(os.getenv("MAX_DOCUMENT_BYTES", str(25 * 1024 * 1024)))
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "https://inventario.mecatronica-ibero.mx,http://localhost:5500,http://127.0.0.1:5500,http://localhost:5173",
    ).split(",")
    if origin.strip()
]

ALLOWED_DOCUMENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".zip"
}
ALLOWED_DOCUMENT_MIME_PREFIXES = (
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument",
    "application/vnd.ms-",
    "text/plain",
    "application/zip",
    "application/x-zip-compressed",
)

security = HTTPBearer(auto_error=False)

if not firebase_admin._apps:
    credential_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if credential_path:
        cred = credentials.Certificate(credential_path)
        firebase_admin.initialize_app(cred)
    else:
        firebase_admin.initialize_app()

db = firestore.client()
app = FastAPI(title=PROJECT_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


def safe_filename(filename: str | None) -> str:
    name = (filename or "archivo").strip() or "archivo"
    name = name.replace("\\", "_").replace("/", "_")
    return re.sub(r"[^A-Za-z0-9._() áéíóúÁÉÍÓÚñÑ-]", "_", name)[:180]


def extension_for(filename: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    return suffix if suffix and len(suffix) <= 12 else ""


def safe_storage_path(relative_path: str) -> Path:
    if not relative_path:
        raise HTTPException(status_code=400, detail="Ruta de archivo vacía.")
    path = (UPLOAD_ROOT / relative_path).resolve()
    if not str(path).startswith(str(UPLOAD_ROOT)):
        raise HTTPException(status_code=400, detail="Ruta de archivo inválida.")
    return path


async def current_user(credentials_data: HTTPAuthorizationCredentials | None = Depends(security)) -> dict[str, Any]:
    if credentials_data is None or credentials_data.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Autenticación requerida.")
    try:
        return auth.verify_id_token(credentials_data.credentials)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="Token Firebase inválido o expirado.") from exc


async def require_admin(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    uid = user.get("uid")
    user_doc = db.collection("users").document(uid).get()
    if not user_doc.exists:
        raise HTTPException(status_code=403, detail="El usuario no tiene perfil en Firestore.")
    profile = user_doc.to_dict() or {}
    if profile.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Solo administración puede subir archivos del inventario.")
    return {"uid": uid, "profile": profile, "token": user}


def validate_upload(asset_type: str, upload: UploadFile, content: bytes) -> None:
    mime = (upload.content_type or "application/octet-stream").lower()
    ext = extension_for(upload.filename)

    if asset_type == "image":
        if not mime.startswith("image/"):
            raise HTTPException(status_code=400, detail="El archivo de imagen debe tener un tipo MIME image/*.")
        if len(content) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail=f"La imagen excede {MAX_IMAGE_BYTES // (1024 * 1024)} MB.")
        return

    if asset_type == "documentation":
        if ext not in ALLOWED_DOCUMENT_EXTENSIONS and not mime.startswith(ALLOWED_DOCUMENT_MIME_PREFIXES):
            raise HTTPException(status_code=400, detail="Formato de documentación no permitido.")
        if len(content) > MAX_DOCUMENT_BYTES:
            raise HTTPException(status_code=413, detail=f"El documento excede {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB.")
        return

    raise HTTPException(status_code=404, detail="Tipo de activo no soportado. Usa image o documentation.")


def item_fields_for_asset(asset_type: str, file_id: str, original_name: str, mime: str, size: int) -> dict[str, Any]:
    if asset_type == "image":
        return {
            "imageFileId": file_id,
            "imageFilename": original_name,
            "imageMimeType": mime,
            "imageSizeBytes": size,
            "imageUpdatedAt": firestore.SERVER_TIMESTAMP,
            "updatedAt": firestore.SERVER_TIMESTAMP,
        }

    return {
        "documentationFileId": file_id,
        "documentationFilename": original_name,
        "documentationMimeType": mime,
        "documentationSizeBytes": size,
        "documentationUpdatedAt": firestore.SERVER_TIMESTAMP,
        # Campos heredados para que vistas previas que lean pdfFileId sigan funcionando.
        "pdfFileId": file_id,
        "pdfFilename": original_name,
        "updatedAt": firestore.SERVER_TIMESTAMP,
    }


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": PROJECT_NAME}


@app.post("/api/items/{item_id}/assets/{asset_type}")
async def upload_item_asset(
    item_id: str,
    asset_type: str,
    file: UploadFile = File(...),
    admin_user: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    asset_type = asset_type.lower().strip()
    if asset_type in {"pdf", "document", "doc"}:
        asset_type = "documentation"

    item_ref = db.collection("items").document(item_id)
    item_snapshot = item_ref.get()
    if not item_snapshot.exists:
        raise HTTPException(status_code=404, detail="Item no encontrado.")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Archivo vacío.")

    validate_upload(asset_type, file, content)

    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    file_id = uuid.uuid4().hex
    original_name = safe_filename(file.filename)
    ext = extension_for(original_name)
    shard = file_id[:2]
    stored_relative_path = f"{shard}/{file_id}{ext}"
    stored_path = safe_storage_path(stored_relative_path)
    stored_path.parent.mkdir(parents=True, exist_ok=True)
    stored_path.write_bytes(content)

    mime = file.content_type or "application/octet-stream"
    file_doc = {
        "fileId": file_id,
        "itemId": item_id,
        "assetType": asset_type,
        "originalFilename": original_name,
        "storedFilename": stored_path.name,
        "storedPath": stored_relative_path,
        "mimeType": mime,
        "sizeBytes": len(content),
        "active": True,
        "createdAt": firestore.SERVER_TIMESTAMP,
        "updatedAt": firestore.SERVER_TIMESTAMP,
        "createdByUid": admin_user["uid"],
    }

    previous_field = "imageFileId" if asset_type == "image" else "documentationFileId"
    previous_file_id = (item_snapshot.to_dict() or {}).get(previous_field)

    batch = db.batch()
    batch.set(db.collection("files").document(file_id), file_doc)
    item_fields = item_fields_for_asset(asset_type, file_id, original_name, mime, len(content))
    batch.update(item_ref, item_fields)
    if previous_file_id and previous_file_id != file_id:
        batch.set(db.collection("files").document(previous_file_id), {
            "active": False,
            "replacedByFileId": file_id,
            "updatedAt": firestore.SERVER_TIMESTAMP,
        }, merge=True)
    batch.commit()

    return {
        "ok": True,
        "fileId": file_id,
        "file": {**file_doc, "createdAt": None, "updatedAt": None},
        "itemFields": {k: (None if k.endswith("At") else v) for k, v in item_fields.items()},
    }


def get_active_file_or_404(file_id: str) -> dict[str, Any]:
    snap = db.collection("files").document(file_id).get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Archivo no encontrado.")
    data = snap.to_dict() or {}
    if data.get("active") is not True:
        raise HTTPException(status_code=404, detail="Archivo no disponible.")
    return data


@app.get("/api/files/{file_id}/view")
def view_file(file_id: str) -> FileResponse:
    data = get_active_file_or_404(file_id)
    path = safe_storage_path(data.get("storedPath", ""))
    if not path.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado en disco.")
    return FileResponse(path, media_type=data.get("mimeType") or "application/octet-stream")


@app.get("/api/files/{file_id}/download")
def download_file(file_id: str) -> FileResponse:
    data = get_active_file_or_404(file_id)
    path = safe_storage_path(data.get("storedPath", ""))
    if not path.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado en disco.")
    return FileResponse(
        path,
        media_type=data.get("mimeType") or "application/octet-stream",
        filename=data.get("originalFilename") or path.name,
    )
