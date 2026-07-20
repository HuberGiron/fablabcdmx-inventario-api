from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import firestore
from google.api_core.exceptions import AlreadyExists, Conflict, FailedPrecondition
from pydantic import BaseModel, Field

from app.gpt_inventory_core import (
    check_skus,
    inventory_context,
    search_items,
    suggest_sku,
    validate_and_plan,
)


gpt_security = HTTPBearer(auto_error=False, scheme_name="GPT Action API key")
EntityType = Literal[
    "items",
    "item",
    "locations",
    "location",
    "subzones",
    "subzone",
    "zones",
    "zone",
]


class SkuCheckRequest(BaseModel):
    skus: list[str] = Field(min_length=1, max_length=100)


class DraftRequest(BaseModel):
    entityType: EntityType | None = None
    draft: Any


class CreateRequest(DraftRequest):
    confirmationToken: str = Field(min_length=20, max_length=2000)
    confirmationText: str = Field(min_length=1, max_length=120)


def require_gpt_action_key(
    credentials_data: HTTPAuthorizationCredentials | None = Depends(gpt_security),
) -> None:
    expected = os.getenv("GPT_ACTION_API_KEY", "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GPT Action no configurada en el servidor.",
        )
    supplied = ""
    if credentials_data and credentials_data.scheme.lower() == "bearer":
        supplied = credentials_data.credentials.strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key de GPT Action inválida.",
        )


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _guard_payload(value: Any) -> None:
    if len(_json_bytes(value)) > 90_000:
        raise HTTPException(
            status_code=413,
            detail="El borrador excede 90,000 bytes; divídelo en altas más pequeñas.",
        )


def _draft_digest(normalized_draft: dict[str, Any]) -> str:
    return hashlib.sha256(_json_bytes(normalized_draft)).hexdigest()


def _urlsafe_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _urlsafe_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _signing_secret() -> bytes:
    secret = os.getenv("GPT_ACTION_SIGNING_SECRET", "").strip()
    if len(secret) < 32:
        raise HTTPException(
            status_code=503,
            detail="GPT_ACTION_SIGNING_SECRET no está configurado o es demasiado corto.",
        )
    return secret.encode("utf-8")


def _issue_confirmation_token(digest: str, total: int, confirmation_text: str) -> tuple[str, int, str]:
    try:
        ttl = int(os.getenv("GPT_ACTION_TOKEN_TTL_SECONDS", "600"))
    except ValueError:
        ttl = 600
    ttl = max(60, min(ttl, 1800))
    expires_at = int(time.time()) + ttl
    audit_id = uuid.uuid4().hex
    payload = {
        "v": 1,
        "digest": digest,
        "total": total,
        "confirmationText": confirmation_text,
        "auditId": audit_id,
        "exp": expires_at,
    }
    encoded = _urlsafe_encode(_json_bytes(payload))
    signature = _urlsafe_encode(hmac.new(_signing_secret(), encoded.encode("ascii"), hashlib.sha256).digest())
    return f"{encoded}.{signature}", expires_at, audit_id


def _verify_confirmation_token(token: str) -> dict[str, Any]:
    try:
        encoded, supplied_signature = token.split(".", 1)
        expected_signature = _urlsafe_encode(
            hmac.new(_signing_secret(), encoded.encode("ascii"), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise ValueError("signature")
        payload = json.loads(_urlsafe_decode(encoded).decode("utf-8"))
        if payload.get("v") != 1 or int(payload.get("exp", 0)) < int(time.time()):
            raise ValueError("expired")
        required = {"digest", "total", "confirmationText", "auditId", "exp"}
        if not required.issubset(payload):
            raise ValueError("fields")
        return payload
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=409,
            detail="La confirmación es inválida o expiró; prepara nuevamente la alta.",
        ) from exc


def create_gpt_inventory_router(db: Any) -> APIRouter:
    router = APIRouter(
        prefix="/api/gpt",
        tags=["gpt-inventory"],
        dependencies=[Depends(require_gpt_action_key)],
    )

    @router.get("/context", operation_id="getInventoryContext")
    def get_inventory_context(includeLocations: bool = Query(default=True)):
        return inventory_context(db, include_locations=includeLocations)

    @router.post("/check-skus", operation_id="checkSkus")
    def check_inventory_skus(request: SkuCheckRequest):
        skus = [sku.strip() for sku in request.skus if sku and sku.strip()]
        if not skus:
            raise HTTPException(status_code=400, detail="Incluye al menos un SKU no vacío.")
        return check_skus(db, skus)

    @router.get("/suggest-sku", operation_id="suggestSku")
    def get_sku_suggestion(zoneId: int, subzoneId: str):
        try:
            return suggest_sku(db, zone_id=zoneId, subzone_id=subzoneId)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/items/search", operation_id="searchInventoryItems")
    def find_inventory_items(
        q: str = Query(default="", max_length=120),
        zoneId: int | None = None,
        subzoneId: str = Query(default="", max_length=20),
        locationId: str = Query(default="", max_length=180),
        tipo: str = Query(default="", max_length=80),
        limit: int = Query(default=20, ge=1, le=50),
    ):
        return search_items(
            db,
            query=q,
            zone_id=zoneId,
            subzone_id=subzoneId,
            location_id=locationId,
            item_type=tipo,
            limit=limit,
        )

    @router.post("/validate-draft", operation_id="validateInventoryDraft")
    def validate_inventory_draft(request: DraftRequest = Body(...)):
        _guard_payload(request.draft)
        return validate_and_plan(db, request.draft, request.entityType).public_result()

    @router.post("/prepare-create", operation_id="prepareInventoryCreate")
    def prepare_inventory_create(request: DraftRequest = Body(...)):
        _guard_payload(request.draft)
        plan = validate_and_plan(db, request.draft, request.entityType)
        result = plan.public_result()
        if not plan.ok:
            return result
        total = sum(plan.summary.values())
        normalized = result["normalizedDraft"]
        digest = _draft_digest(normalized)
        confirmation_text = f"CREAR {total} REGISTRO" + ("" if total == 1 else "S")
        token, expires_at, audit_id = _issue_confirmation_token(digest, total, confirmation_text)
        return {
            **result,
            "digest": digest,
            "confirmationToken": token,
            "confirmationText": confirmation_text,
            "expiresAtUnix": expires_at,
            "auditId": audit_id,
            "nextStep": "Muestra normalizedDraft al usuario y solicita confirmación explícita antes de crear.",
        }

    @router.post("/create", operation_id="createInventoryRecords")
    def create_inventory_records(request: CreateRequest = Body(...)):
        _guard_payload(request.draft)
        token_data = _verify_confirmation_token(request.confirmationToken)
        if not hmac.compare_digest(request.confirmationText, str(token_data["confirmationText"])):
            raise HTTPException(status_code=409, detail="El texto de confirmación no coincide.")

        # Revalidar contra Firestore inmediatamente antes de crear.
        plan = validate_and_plan(db, request.draft, request.entityType)
        if not plan.ok:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "El inventario cambió o el borrador dejó de ser válido. No se creó nada.",
                    **plan.public_result(),
                },
            )
        normalized = plan.public_result()["normalizedDraft"]
        digest = _draft_digest(normalized)
        if not hmac.compare_digest(digest, str(token_data["digest"])):
            raise HTTPException(
                status_code=409,
                detail="El borrador cambió después de prepararse. No se creó nada.",
            )
        if len(plan.writes) != int(token_data["total"]):
            raise HTTPException(status_code=409, detail="La cantidad de registros cambió.")

        audit_id = str(token_data["auditId"])
        batch = db.batch()
        audit_ref = db.collection("gptCreateAudits").document(audit_id)
        batch.create(
            audit_ref,
            {
                "auditId": audit_id,
                "digest": digest,
                "summary": plan.summary,
                "documentPaths": [
                    f"{write.collection}/{write.document_id}" for write in plan.writes
                ],
                "source": "custom-gpt",
                "policy": "create-only",
                "createdAt": firestore.SERVER_TIMESTAMP,
            },
        )
        for write in plan.writes:
            batch.create(
                db.collection(write.collection).document(write.document_id),
                write.data,
            )
        try:
            batch.commit()
        except (AlreadyExists, Conflict, FailedPrecondition) as exc:
            raise HTTPException(
                status_code=409,
                detail="Un ID o SKU ya existe. La operación atómica fue rechazada; no se modificó nada.",
            ) from exc

        return {
            "ok": True,
            "created": len(plan.writes),
            "summary": plan.summary,
            "auditId": audit_id,
            "documentPaths": [
                f"{write.collection}/{write.document_id}" for write in plan.writes
            ],
            "updated": 0,
            "deleted": 0,
        }

    return router
