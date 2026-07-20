from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from firebase_admin import firestore


ITEM_DEFAULTS = {
    "Máquina": (True, False, True, True),
    "Maquina": (True, False, True, True),
    "Herramienta": (True, True, False, False),
    "Consumible": (True, True, False, False),
    "Cómputo": (True, False, True, False),
    "Material": (True, True, False, False),
    "Equipo de seguridad": (True, True, False, False),
    "Kit": (True, True, False, False),
    "Refacción": (False, False, False, False),
    "Mobiliario": (False, False, False, False),
    "Accesorio": (True, False, False, False),
    "Equipo auxiliar": (True, False, False, False),
    "Otro": (True, False, False, False),
}

LOCATION_TYPES = {
    "machine",
    "workstation",
    "table",
    "cabinet",
    "drawer",
    "shelf",
    "rack",
    "vitrine",
    "storage",
    "cart",
    "wall_panel",
    "safety_station",
    "general",
    "other",
}

ENTITY_ALIASES = {
    "zone": "zones",
    "zones": "zones",
    "zona": "zones",
    "zonas": "zones",
    "subzone": "subzones",
    "subzones": "subzones",
    "subzona": "subzones",
    "subzonas": "subzones",
    "location": "locations",
    "locations": "locations",
    "ubicacion": "locations",
    "ubicaciones": "locations",
    "item": "items",
    "items": "items",
    "elemento": "items",
    "elementos": "items",
}

ENTITY_ORDER = ("zones", "subzones", "locations", "items")

ALLOWED_FIELDS = {
    "zones": {"zoneId", "name", "active", "description", "order"},
    "subzones": {"subzoneId", "zoneId", "name", "active", "description", "order"},
    "locations": {
        "locationId",
        "areaCode",
        "locationCode",
        "name",
        "type",
        "zoneId",
        "zoneName",
        "subzoneId",
        "subzoneName",
        "parentLocationId",
        "parentLocationName",
        "description",
        "active",
        "order",
    },
    "items": {
        "sku",
        "tipo",
        "nombre",
        "descripcion",
        "zoneId",
        "zoneName",
        "subzoneId",
        "subzoneName",
        "locationId",
        "locationName",
        "locationCode",
        "locationType",
        "relatedMachineId",
        "relatedMachineName",
        "relatedMachineCode",
        "fabacademyWeeks",
        "fabacademyWeekNames",
        "inventarioDeseado",
        "precioUnitario",
        "moneda",
        "purchaseUrl",
        "infoUrl",
        "visibleParaAlumno",
        "prestamoHabilitado",
        "reservaHabilitada",
        "requiereAsistencia",
        "imageFileId",
        "pdfFileId",
        "datasheetFileId",
        "stockAlmacen",
        "stockPrestadoTemporal",
        "stockLargoPlazo",
        "stockDanado",
        "stockPerdido",
        "activo",
    },
}


def canonical(value: Any) -> str:
    return str(value if value is not None else "").strip().casefold()


def public_document(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not key.startswith("_")}


def _stream(db: Any, collection: str) -> list[dict[str, Any]]:
    rows = []
    for snapshot in db.collection(collection).stream():
        row = snapshot.to_dict() or {}
        row["_documentId"] = snapshot.id
        rows.append(row)
    return rows


def _sort_int(value: Any, fallback: int = 999999) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _subzone_sort(value: Any) -> tuple[int, int, str]:
    raw = str(value or "")
    parts = raw.split(".")
    if len(parts) == 2:
        return (_sort_int(parts[0]), _sort_int(parts[1]), raw)
    return (999999, 999999, raw)


@dataclass
class InventoryIndex:
    zones: dict[str, dict[str, Any]] = field(default_factory=dict)
    subzones: dict[str, dict[str, Any]] = field(default_factory=dict)
    locations: dict[str, dict[str, Any]] = field(default_factory=dict)
    weeks: dict[int, dict[str, Any]] = field(default_factory=dict)
    items: list[dict[str, Any]] = field(default_factory=list)
    items_by_sku: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    item_document_ids: dict[str, dict[str, Any]] = field(default_factory=dict)
    area_codes: set[tuple[str, str]] = field(default_factory=set)

    @classmethod
    def from_db(cls, db: Any, include_items: bool = True) -> "InventoryIndex":
        result = cls()
        for row in _stream(db, "zones"):
            for key in {canonical(row.get("zoneId")), canonical(row.get("_documentId"))} - {""}:
                result.zones.setdefault(key, row)
        for row in _stream(db, "subzones"):
            for key in {canonical(row.get("subzoneId")), canonical(row.get("_documentId"))} - {""}:
                result.subzones.setdefault(key, row)
        for row in _stream(db, "locations"):
            for key in {canonical(row.get("locationId")), canonical(row.get("_documentId"))} - {""}:
                result.locations.setdefault(key, row)
            area_code = str(row.get("areaCode") or row.get("locationCode") or "").strip()
            subzone_id = str(row.get("subzoneId") or "").strip()
            if area_code and subzone_id:
                result.area_codes.add((canonical(subzone_id), canonical(area_code)))
        for row in _stream(db, "fabacademyWeeks"):
            try:
                week_id = int(row.get("weekId", row.get("_documentId")))
            except (TypeError, ValueError):
                continue
            result.weeks[week_id] = row
        if include_items:
            result.items = _stream(db, "items")
            by_sku: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in result.items:
                result.item_document_ids[canonical(row.get("_documentId"))] = row
                sku_key = canonical(row.get("sku"))
                if sku_key:
                    by_sku[sku_key].append(row)
            result.items_by_sku = dict(by_sku)
        return result


def inventory_context(db: Any, include_locations: bool = True) -> dict[str, Any]:
    index = InventoryIndex.from_db(db, include_items=False)
    zones = sorted(
        {row["_documentId"]: public_document(row) for row in index.zones.values()}.values(),
        key=lambda row: (_sort_int(row.get("zoneId")), str(row.get("name", ""))),
    )
    subzones = sorted(
        {row["_documentId"]: public_document(row) for row in index.subzones.values()}.values(),
        key=lambda row: _subzone_sort(row.get("subzoneId")),
    )
    locations: list[dict[str, Any]] = []
    if include_locations:
        locations = sorted(
            {row["_documentId"]: public_document(row) for row in index.locations.values()}.values(),
            key=lambda row: (
                _sort_int(row.get("zoneId")),
                _subzone_sort(row.get("subzoneId")),
                _sort_int(row.get("order")),
                str(row.get("locationId", "")),
            ),
        )
    return {
        "source": "Firestore live",
        "zones": zones,
        "subzones": subzones,
        "locations": locations,
        "fabacademyWeeks": [public_document(index.weeks[key]) for key in sorted(index.weeks)],
        "itemTypes": sorted(ITEM_DEFAULTS),
        "locationTypes": sorted(LOCATION_TYPES),
        "skuRule": "ZZSSNNN",
        "writePolicy": "create-only; no update; no delete",
    }


def item_summary(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    fields = (
        "sku",
        "nombre",
        "descripcion",
        "tipo",
        "zoneId",
        "zoneName",
        "subzoneId",
        "subzoneName",
        "locationId",
        "locationName",
        "relatedMachineId",
        "relatedMachineName",
        "fabacademyWeeks",
        "fabacademyWeekNames",
        "inventarioDeseado",
        "activo",
    )
    result = {field_name: row.get(field_name) for field_name in fields if field_name in row}
    result["documentId"] = row.get("_documentId")
    return result


def check_skus(db: Any, skus: list[str]) -> dict[str, Any]:
    index = InventoryIndex.from_db(db, include_items=True)
    results = []
    for raw_sku in skus:
        sku = str(raw_sku).strip()
        matches = index.items_by_sku.get(canonical(sku), [])
        document_match = index.item_document_ids.get(canonical(sku))
        results.append(
            {
                "sku": sku,
                "exists": bool(matches or document_match),
                "matches": [item_summary(row) for row in matches],
                "documentIdCollision": item_summary(document_match) if document_match and not matches else None,
            }
        )
    return {"ok": True, "results": results}


def _sku_prefix(zone_id: int, subzone_id: str) -> str:
    parts = str(subzone_id).strip().split(".")
    if len(parts) != 2:
        raise ValueError("subzoneId debe tener formato zona.componente, por ejemplo 8.2.")
    try:
        subzone_zone, component = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError("subzoneId debe tener componentes numéricos.") from exc
    if subzone_zone != zone_id:
        raise ValueError("subzoneId no pertenece a zoneId.")
    if not (1 <= zone_id <= 99 and 0 <= component <= 99):
        raise ValueError("zoneId y el componente de subzona deben caber en dos dígitos.")
    return f"{zone_id:02d}{component:02d}"


def suggest_sku(db: Any, zone_id: int, subzone_id: str) -> dict[str, Any]:
    index = InventoryIndex.from_db(db, include_items=True)
    zone = index.zones.get(canonical(zone_id))
    subzone = index.subzones.get(canonical(subzone_id))
    if not zone:
        raise ValueError(f"zoneId {zone_id} no existe.")
    if not subzone:
        raise ValueError(f"subzoneId {subzone_id} no existe.")
    if str(subzone.get("zoneId")) != str(zone_id):
        raise ValueError("La subzona no pertenece a la zona.")
    prefix = _sku_prefix(zone_id, subzone_id)
    pattern = re.compile(rf"^{re.escape(prefix)}(\d{{3}})$")
    used: set[int] = set()
    for row in index.items:
        for candidate in (row.get("sku"), row.get("_documentId")):
            match = pattern.fullmatch(str(candidate or "").strip())
            if match:
                used.add(int(match.group(1)))
    first_available = next((value for value in range(1, 1000) if value not in used), None)
    highest = max(used, default=0)
    next_sequential = highest + 1 if highest < 999 else None
    return {
        "ok": first_available is not None,
        "zoneId": zone_id,
        "zoneName": zone.get("name", ""),
        "subzoneId": subzone_id,
        "subzoneName": subzone.get("name", ""),
        "prefix": prefix,
        "usedCount": len(used),
        "firstAvailableSku": f"{prefix}{first_available:03d}" if first_available else None,
        "nextSequentialSku": f"{prefix}{next_sequential:03d}" if next_sequential else None,
    }


def search_items(
    db: Any,
    *,
    query: str = "",
    zone_id: int | None = None,
    subzone_id: str = "",
    location_id: str = "",
    item_type: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    index = InventoryIndex.from_db(db, include_items=True)
    needle = canonical(query)
    matches = []
    for row in index.items:
        if zone_id is not None and str(row.get("zoneId")) != str(zone_id):
            continue
        if subzone_id and canonical(row.get("subzoneId")) != canonical(subzone_id):
            continue
        if location_id and canonical(row.get("locationId")) != canonical(location_id):
            continue
        if item_type and canonical(row.get("tipo")) != canonical(item_type):
            continue
        haystack = canonical(
            " ".join(
                str(row.get(field_name, ""))
                for field_name in (
                    "sku",
                    "nombre",
                    "descripcion",
                    "tipo",
                    "zoneName",
                    "subzoneName",
                    "locationName",
                    "relatedMachineName",
                )
            )
        )
        if needle and needle not in haystack:
            continue
        matches.append(item_summary(row))
        if len(matches) >= limit:
            break
    return {"ok": True, "count": len(matches), "items": matches}


@dataclass
class WriteOperation:
    collection: str
    document_id: str
    data: dict[str, Any]


@dataclass
class DraftPlan:
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    sections: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {entity: [] for entity in ENTITY_ORDER}
    )
    normalized: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {entity: [] for entity in ENTITY_ORDER}
    )
    writes: list[WriteOperation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def summary(self) -> dict[str, int]:
        return {entity: len(self.normalized[entity]) for entity in ENTITY_ORDER}

    def public_result(self) -> dict[str, Any]:
        bundle = {entity: rows for entity, rows in self.normalized.items() if rows}
        return {
            "ok": self.ok,
            "summary": self.summary,
            "errors": self.errors,
            "warnings": self.warnings,
            "normalizedDraft": bundle,
            "writePolicy": "create-only; existing records cause rejection",
        }


def _issue(
    target: list[dict[str, Any]],
    entity: str,
    index: int,
    code: str,
    message: str,
    field_name: str = "",
) -> None:
    issue = {"entity": entity, "index": index, "code": code, "message": message}
    if field_name:
        issue["field"] = field_name
    target.append(issue)


def _as_rows(value: Any, entity: str, plan: DraftPlan) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list) and all(isinstance(row, dict) for row in value):
        return value
    _issue(plan.errors, entity, 0, "invalid_section", "La sección debe ser objeto o arreglo de objetos.")
    return []


def _parse_sections(draft: Any, entity_type: str | None, plan: DraftPlan) -> None:
    if entity_type:
        entity = ENTITY_ALIASES.get(canonical(entity_type))
        if not entity:
            _issue(plan.errors, "input", 0, "invalid_entity_type", f"entityType desconocido: {entity_type}")
            return
        plan.sections[entity] = _as_rows(draft, entity, plan)
        return
    if not isinstance(draft, dict):
        _issue(plan.errors, "input", 0, "missing_entity_type", "Sin entityType se requiere un paquete jerárquico.")
        return
    found = False
    for entity in ENTITY_ORDER:
        if entity in draft:
            found = True
            plan.sections[entity] = _as_rows(draft[entity], entity, plan)
    if not found:
        _issue(plan.errors, "input", 0, "empty_bundle", "No hay secciones reconocidas.")
    unknown = set(draft) - set(ENTITY_ORDER) - {"schemaVersion", "description"}
    if unknown:
        _issue(plan.errors, "input", 0, "unknown_bundle_fields", f"Claves superiores desconocidas: {sorted(unknown)}")


def _integer(
    row: dict[str, Any],
    field_name: str,
    entity: str,
    position: int,
    plan: DraftPlan,
    *,
    minimum: int = 0,
    required: bool = True,
    default: int = 0,
) -> int | None:
    if field_name not in row:
        if required:
            _issue(plan.errors, entity, position, "missing_required", f"{field_name} es obligatorio.", field_name)
            return None
        return default
    value = row[field_name]
    if isinstance(value, bool) or not isinstance(value, int):
        _issue(plan.errors, entity, position, "invalid_integer", f"{field_name} debe ser entero JSON.", field_name)
        return None
    if value < minimum:
        _issue(plan.errors, entity, position, "integer_too_small", f"{field_name} debe ser al menos {minimum}.", field_name)
        return None
    return value


def _required_string(
    row: dict[str, Any], field_name: str, entity: str, position: int, plan: DraftPlan
) -> str:
    value = row.get(field_name)
    if not isinstance(value, str) or not value.strip():
        _issue(plan.errors, entity, position, "missing_required", f"{field_name} debe ser texto no vacío.", field_name)
        return ""
    return value.strip()


def _optional_string(
    row: dict[str, Any], field_name: str, entity: str, position: int, plan: DraftPlan, default: str = ""
) -> str:
    if field_name not in row or row[field_name] is None:
        return default
    value = row[field_name]
    if not isinstance(value, str):
        _issue(plan.errors, entity, position, "invalid_string", f"{field_name} debe ser texto.", field_name)
        return default
    return value.strip()


def _boolean(
    row: dict[str, Any], field_name: str, entity: str, position: int, plan: DraftPlan, default: bool
) -> bool:
    if field_name not in row:
        return default
    value = row[field_name]
    if not isinstance(value, bool):
        _issue(plan.errors, entity, position, "invalid_boolean", f"{field_name} debe ser true o false.", field_name)
        return default
    return value


def _validate_declared_name(
    row: dict[str, Any], field_name: str, expected: str, entity: str, position: int, plan: DraftPlan
) -> None:
    if field_name in row and str(row.get(field_name) or "").strip() != expected:
        _issue(plan.errors, entity, position, "name_mismatch", f"{field_name} no coincide con el catálogo.", field_name)


def _base_payload(row: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: row[key] for key in keys if key in row}


def _validate_document_id(
    value: str, entity: str, position: int, field_name: str, plan: DraftPlan
) -> None:
    if value in {"", ".", ".."} or "/" in value or len(value.encode("utf-8")) > 1500:
        _issue(plan.errors, entity, position, "invalid_document_id", f"{field_name} no es un ID válido de Firestore.", field_name)


def validate_and_plan(db: Any, draft: Any, entity_type: str | None = None) -> DraftPlan:
    plan = DraftPlan()
    _parse_sections(draft, entity_type, plan)
    total_rows = sum(len(rows) for rows in plan.sections.values())
    if total_rows == 0 and not plan.errors:
        _issue(plan.errors, "input", 0, "empty_draft", "El borrador no contiene registros.")
    if total_rows > 399:
        _issue(
            plan.errors,
            "input",
            0,
            "batch_too_large",
            "La Action admite máximo 399 registros por alta para conservar atomicidad.",
        )

    index = InventoryIndex.from_db(db, include_items=True)
    zones = dict(index.zones)
    subzones = dict(index.subzones)
    locations = dict(index.locations)
    area_codes = set(index.area_codes)

    for entity, rows in plan.sections.items():
        for position, row in enumerate(rows, start=1):
            unknown = sorted(set(row) - ALLOWED_FIELDS[entity])
            if unknown:
                _issue(plan.errors, entity, position, "unknown_fields", f"Campos no admitidos: {unknown}")

    seen_zones: set[str] = set()
    for position, row in enumerate(plan.sections["zones"], start=1):
        zone_id = _integer(row, "zoneId", "zones", position, plan, minimum=1)
        name = _required_string(row, "name", "zones", position, plan)
        if zone_id is None:
            continue
        if zone_id > 99:
            _issue(plan.errors, "zones", position, "zone_id_out_of_range", "zoneId debe caber en dos dígitos.", "zoneId")
        key = canonical(zone_id)
        if key in seen_zones:
            _issue(plan.errors, "zones", position, "duplicate_input", f"zoneId {zone_id} se repite.")
        seen_zones.add(key)
        if key in index.zones:
            _issue(plan.errors, "zones", position, "existing_zone", f"zoneId {zone_id} ya existe; no se modificará.")
        normalized = {
            "zoneId": zone_id,
            "name": name,
            "active": _boolean(row, "active", "zones", position, plan, True),
        }
        description = _optional_string(row, "description", "zones", position, plan)
        if description:
            normalized["description"] = description
        if "order" in row:
            order = _integer(row, "order", "zones", position, plan, minimum=0)
            if order is not None:
                normalized["order"] = order
        plan.normalized["zones"].append(normalized)
        plan.writes.append(
            WriteOperation(
                "zones",
                str(zone_id),
                {**normalized, "createdAt": firestore.SERVER_TIMESTAMP, "updatedAt": firestore.SERVER_TIMESTAMP},
            )
        )
        if key not in zones and name:
            zones[key] = {**normalized, "_documentId": str(zone_id), "_proposed": True}

    seen_subzones: set[str] = set()
    for position, row in enumerate(plan.sections["subzones"], start=1):
        subzone_id = _required_string(row, "subzoneId", "subzones", position, plan)
        zone_id = _integer(row, "zoneId", "subzones", position, plan, minimum=1)
        name = _required_string(row, "name", "subzones", position, plan)
        if not subzone_id or zone_id is None:
            continue
        _validate_document_id(subzone_id, "subzones", position, "subzoneId", plan)
        if not re.fullmatch(r"\d{1,2}\.\d{1,2}", subzone_id):
            _issue(plan.errors, "subzones", position, "invalid_subzone_id", "subzoneId debe tener formato numérico zona.componente.", "subzoneId")
        if not subzone_id.startswith(f"{zone_id}."):
            _issue(plan.errors, "subzones", position, "prefix_mismatch", f"subzoneId debe iniciar con {zone_id}.", "subzoneId")
        key = canonical(subzone_id)
        if key in seen_subzones:
            _issue(plan.errors, "subzones", position, "duplicate_input", f"subzoneId {subzone_id} se repite.")
        seen_subzones.add(key)
        if key in index.subzones:
            _issue(plan.errors, "subzones", position, "existing_subzone", f"subzoneId {subzone_id} ya existe; no se modificará.")
        if canonical(zone_id) not in zones:
            _issue(plan.errors, "subzones", position, "missing_zone", f"zoneId {zone_id} no existe ni se crea en esta alta.")
        normalized = {
            "subzoneId": subzone_id,
            "zoneId": zone_id,
            "name": name,
            "active": _boolean(row, "active", "subzones", position, plan, True),
        }
        description = _optional_string(row, "description", "subzones", position, plan)
        if description:
            normalized["description"] = description
        if "order" in row:
            order = _integer(row, "order", "subzones", position, plan, minimum=0)
            if order is not None:
                normalized["order"] = order
        plan.normalized["subzones"].append(normalized)
        plan.writes.append(
            WriteOperation(
                "subzones",
                subzone_id,
                {**normalized, "createdAt": firestore.SERVER_TIMESTAMP, "updatedAt": firestore.SERVER_TIMESTAMP},
            )
        )
        if key not in subzones and name:
            subzones[key] = {**normalized, "_documentId": subzone_id, "_proposed": True}

    seen_locations: set[str] = set()
    location_rows: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for position, row in enumerate(plan.sections["locations"], start=1):
        location_id = _required_string(row, "locationId", "locations", position, plan)
        name = _required_string(row, "name", "locations", position, plan)
        location_type = _required_string(row, "type", "locations", position, plan)
        zone_id = _integer(row, "zoneId", "locations", position, plan, minimum=1)
        subzone_id = _required_string(row, "subzoneId", "locations", position, plan)
        if not location_id or zone_id is None or not subzone_id:
            continue
        _validate_document_id(location_id, "locations", position, "locationId", plan)
        if location_type not in LOCATION_TYPES:
            _issue(plan.errors, "locations", position, "invalid_location_type", f"type no permitido: {location_type}", "type")
        key = canonical(location_id)
        if key in seen_locations:
            _issue(plan.errors, "locations", position, "duplicate_input", f"locationId {location_id} se repite.")
        seen_locations.add(key)
        if key in index.locations:
            _issue(plan.errors, "locations", position, "existing_location", f"locationId {location_id} ya existe; no se modificará.")
        zone = zones.get(canonical(zone_id))
        subzone = subzones.get(canonical(subzone_id))
        if not zone:
            _issue(plan.errors, "locations", position, "missing_zone", f"zoneId {zone_id} no existe ni se crea en esta alta.")
        if not subzone:
            _issue(plan.errors, "locations", position, "missing_subzone", f"subzoneId {subzone_id} no existe ni se crea en esta alta.")
        elif str(subzone.get("zoneId")) != str(zone_id):
            _issue(plan.errors, "locations", position, "route_mismatch", "La subzona no pertenece a la zona.")
        if not canonical(location_id).startswith(canonical(f"{subzone_id}-")):
            _issue(plan.errors, "locations", position, "prefix_mismatch", f"locationId debe iniciar con {subzone_id}-.", "locationId")
        area_code = _optional_string(row, "areaCode", "locations", position, plan)
        if not area_code:
            area_code = _optional_string(row, "locationCode", "locations", position, plan)
        if area_code:
            area_key = (canonical(subzone_id), canonical(area_code))
            if not canonical(area_code).startswith(canonical(f"{subzone_id}.")):
                _issue(plan.errors, "locations", position, "area_prefix_mismatch", f"areaCode debe iniciar con {subzone_id}.", "areaCode")
            if area_key in area_codes:
                _issue(plan.errors, "locations", position, "duplicate_area_code", f"areaCode {area_code} ya está utilizado.")
            area_codes.add(area_key)
        if zone:
            _validate_declared_name(row, "zoneName", str(zone.get("name", "")), "locations", position, plan)
        if subzone:
            _validate_declared_name(row, "subzoneName", str(subzone.get("name", "")), "locations", position, plan)
        normalized = {
            "locationId": location_id,
            "areaCode": area_code,
            "name": name,
            "type": location_type,
            "zoneId": zone_id,
            "zoneName": str((zone or {}).get("name", "")),
            "subzoneId": subzone_id,
            "subzoneName": str((subzone or {}).get("name", "")),
            "parentLocationId": _optional_string(row, "parentLocationId", "locations", position, plan),
            "description": _optional_string(row, "description", "locations", position, plan),
            "active": _boolean(row, "active", "locations", position, plan, True),
        }
        if "order" in row:
            order = _integer(row, "order", "locations", position, plan, minimum=0)
            if order is not None:
                normalized["order"] = order
        location_rows.append((position, row, normalized))
        if key not in locations and name:
            locations[key] = {**normalized, "_documentId": location_id, "_proposed": True}

    for position, row, normalized in location_rows:
        parent_id = normalized.get("parentLocationId", "")
        parent = locations.get(canonical(parent_id)) if parent_id else None
        if parent_id and not parent:
            _issue(plan.errors, "locations", position, "missing_parent", f"parentLocationId {parent_id} no existe ni se crea en esta alta.")
        elif parent and (
            str(parent.get("zoneId")) != str(normalized["zoneId"])
            or canonical(parent.get("subzoneId")) != canonical(normalized["subzoneId"])
        ):
            _issue(plan.errors, "locations", position, "parent_route_mismatch", "La ubicación padre debe estar en la misma zona y subzona.")
        if parent:
            _validate_declared_name(row, "parentLocationName", str(parent.get("name", "")), "locations", position, plan)
        normalized["parentLocationName"] = str((parent or {}).get("name", ""))
        plan.normalized["locations"].append(normalized)
        plan.writes.append(
            WriteOperation(
                "locations",
                normalized["locationId"],
                {**normalized, "createdAt": firestore.SERVER_TIMESTAMP, "updatedAt": firestore.SERVER_TIMESTAMP},
            )
        )

    seen_skus: set[str] = set()
    for position, row in enumerate(plan.sections["items"], start=1):
        sku = _required_string(row, "sku", "items", position, plan)
        item_type = _required_string(row, "tipo", "items", position, plan)
        name = _required_string(row, "nombre", "items", position, plan)
        zone_id = _integer(row, "zoneId", "items", position, plan, minimum=1)
        subzone_id = _required_string(row, "subzoneId", "items", position, plan)
        location_id = _required_string(row, "locationId", "items", position, plan)
        desired = _integer(row, "inventarioDeseado", "items", position, plan, minimum=0)
        if not sku or zone_id is None or not subzone_id or not location_id or desired is None:
            continue
        sku_key = canonical(sku)
        if sku_key in seen_skus:
            _issue(plan.errors, "items", position, "duplicate_input_sku", f"SKU {sku} se repite en el borrador.")
        seen_skus.add(sku_key)
        existing_by_sku = index.items_by_sku.get(sku_key, [])
        if existing_by_sku:
            _issue(plan.errors, "items", position, "existing_sku", f"SKU {sku} ya existe; no se modificará.", "sku")
        elif sku_key in index.item_document_ids:
            _issue(plan.errors, "items", position, "document_id_collision", f"items/{sku} ya existe; no se modificará.", "sku")
        if not re.fullmatch(r"\d{7}", sku):
            _issue(plan.errors, "items", position, "invalid_sku_format", "El SKU debe tener siete dígitos.", "sku")
        else:
            try:
                prefix = _sku_prefix(zone_id, subzone_id)
                if not sku.startswith(prefix):
                    _issue(plan.errors, "items", position, "sku_route_mismatch", f"El SKU debe iniciar con {prefix}.", "sku")
            except ValueError as exc:
                _issue(plan.errors, "items", position, "invalid_subzone", str(exc), "subzoneId")
        if item_type not in ITEM_DEFAULTS:
            _issue(plan.errors, "items", position, "invalid_item_type", f"tipo no permitido: {item_type}", "tipo")
        zone = zones.get(canonical(zone_id))
        subzone = subzones.get(canonical(subzone_id))
        location = locations.get(canonical(location_id))
        if not zone:
            _issue(plan.errors, "items", position, "missing_zone", f"zoneId {zone_id} no existe ni se crea en esta alta.")
        if not subzone:
            _issue(plan.errors, "items", position, "missing_subzone", f"subzoneId {subzone_id} no existe ni se crea en esta alta.")
        elif str(subzone.get("zoneId")) != str(zone_id):
            _issue(plan.errors, "items", position, "route_mismatch", "La subzona no pertenece a la zona.")
        if not location:
            _issue(plan.errors, "items", position, "missing_location", f"locationId {location_id} no existe ni se crea en esta alta.")
        elif (
            str(location.get("zoneId")) != str(zone_id)
            or canonical(location.get("subzoneId")) != canonical(subzone_id)
        ):
            _issue(plan.errors, "items", position, "location_route_mismatch", "La ubicación no pertenece a la zona y subzona.")
        related_id = _optional_string(row, "relatedMachineId", "items", position, plan)
        related = locations.get(canonical(related_id)) if related_id else None
        if related_id and not related:
            _issue(plan.errors, "items", position, "missing_related_machine", f"relatedMachineId {related_id} no existe.")
        elif related and related.get("type") != "machine":
            _issue(plan.errors, "items", position, "related_not_machine", "relatedMachineId debe apuntar a type=machine.")
        elif related and (
            str(related.get("zoneId")) != str(zone_id)
            or canonical(related.get("subzoneId")) != canonical(subzone_id)
        ):
            _issue(plan.errors, "items", position, "related_route_mismatch", "La máquina relacionada debe estar en la misma zona y subzona.")
        for field_name, catalog_row in (
            ("zoneName", zone),
            ("subzoneName", subzone),
            ("locationName", location),
            ("relatedMachineName", related),
        ):
            if catalog_row:
                _validate_declared_name(row, field_name, str(catalog_row.get("name", "")), "items", position, plan)

        week_ids = row.get("fabacademyWeeks", [])
        if not isinstance(week_ids, list) or any(isinstance(value, bool) or not isinstance(value, int) for value in week_ids):
            _issue(plan.errors, "items", position, "invalid_weeks", "fabacademyWeeks debe ser arreglo de enteros.")
            week_ids = []
        if len(week_ids) != len(set(week_ids)):
            _issue(plan.errors, "items", position, "duplicate_weeks", "fabacademyWeeks contiene valores repetidos.")
        canonical_week_names = []
        for week_id in week_ids:
            week = index.weeks.get(week_id)
            if not week:
                _issue(plan.errors, "items", position, "unknown_week", f"weekId {week_id} no existe en el catálogo vivo.")
                canonical_week_names.append("")
            else:
                canonical_week_names.append(str(week.get("name", "")))
        declared_week_names = row.get("fabacademyWeekNames", [])
        if declared_week_names:
            if not isinstance(declared_week_names, list) or any(not isinstance(value, str) for value in declared_week_names):
                _issue(plan.errors, "items", position, "invalid_week_names", "fabacademyWeekNames debe ser arreglo de textos.")
            elif declared_week_names != canonical_week_names:
                _issue(plan.errors, "items", position, "week_names_mismatch", "fabacademyWeekNames no coincide con el catálogo vivo.")

        price = row.get("precioUnitario", 0)
        if isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0:
            _issue(plan.errors, "items", position, "invalid_price", "precioUnitario debe ser un número no negativo.", "precioUnitario")
            price = 0
        defaults = ITEM_DEFAULTS.get(item_type, ITEM_DEFAULTS["Otro"])
        visible, loan, reservation, assistance = defaults
        normalized = {
            "sku": sku,
            "tipo": item_type,
            "nombre": name,
            "descripcion": _optional_string(row, "descripcion", "items", position, plan),
            "zoneId": zone_id,
            "zoneName": str((zone or {}).get("name", "")),
            "subzoneId": subzone_id,
            "subzoneName": str((subzone or {}).get("name", "")),
            "locationId": location_id,
            "locationName": str((location or {}).get("name", "")),
            "locationCode": str((location or {}).get("areaCode") or (location or {}).get("locationCode") or ""),
            "locationType": str((location or {}).get("type", "")),
            "relatedMachineId": related_id,
            "relatedMachineName": str((related or {}).get("name", "")),
            "relatedMachineCode": str((related or {}).get("areaCode") or (related or {}).get("locationCode") or ""),
            "fabacademyWeeks": week_ids,
            "fabacademyWeekNames": canonical_week_names,
            "inventarioDeseado": desired,
            "precioUnitario": price,
            "moneda": _optional_string(row, "moneda", "items", position, plan, "MXN").upper() or "MXN",
            "purchaseUrl": _optional_string(row, "purchaseUrl", "items", position, plan),
            "infoUrl": _optional_string(row, "infoUrl", "items", position, plan),
            "visibleParaAlumno": _boolean(row, "visibleParaAlumno", "items", position, plan, visible),
            "prestamoHabilitado": _boolean(row, "prestamoHabilitado", "items", position, plan, loan),
            "reservaHabilitada": _boolean(row, "reservaHabilitada", "items", position, plan, reservation),
            "requiereAsistencia": _boolean(row, "requiereAsistencia", "items", position, plan, assistance),
            "imageFileId": _optional_string(row, "imageFileId", "items", position, plan),
            "pdfFileId": _optional_string(row, "pdfFileId", "items", position, plan),
            "datasheetFileId": _optional_string(row, "datasheetFileId", "items", position, plan),
            "activo": _boolean(row, "activo", "items", position, plan, True),
        }
        for stock_field in (
            "stockAlmacen",
            "stockPrestadoTemporal",
            "stockLargoPlazo",
            "stockDanado",
            "stockPerdido",
        ):
            value = _integer(
                row,
                stock_field,
                "items",
                position,
                plan,
                minimum=0,
                required=False,
                default=0,
            )
            normalized[stock_field] = value if value is not None else 0
        plan.normalized["items"].append(normalized)
        plan.writes.append(
            WriteOperation(
                "items",
                sku,
                {**normalized, "createdAt": firestore.SERVER_TIMESTAMP, "updatedAt": firestore.SERVER_TIMESTAMP},
            )
        )

    return plan
