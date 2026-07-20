import sys
import types
import unittest


if "firebase_admin" not in sys.modules:
    firebase_admin = types.ModuleType("firebase_admin")
    firebase_admin.firestore = types.SimpleNamespace(SERVER_TIMESTAMP=object())
    sys.modules["firebase_admin"] = firebase_admin

from app.gpt_inventory_core import (  # noqa: E402
    check_skus,
    inventory_context,
    suggest_sku,
    validate_and_plan,
)


class FakeSnapshot:
    def __init__(self, document_id, data):
        self.id = document_id
        self._data = data

    def to_dict(self):
        return dict(self._data)


class FakeCollection:
    def __init__(self, rows):
        self._rows = rows

    def stream(self):
        return [FakeSnapshot(document_id, data) for document_id, data in self._rows]


class FakeDb:
    def __init__(self, collections):
        self._collections = collections

    def collection(self, name):
        return FakeCollection(self._collections.get(name, []))


def fixture_db():
    return FakeDb(
        {
            "zones": [("8", {"zoneId": 8, "name": "Prototipado Zona-Limpia"})],
            "subzones": [
                ("8.2", {"subzoneId": "8.2", "zoneId": 8, "name": "Impresión 3D"})
            ],
            "locations": [
                (
                    "8.2-GENERAL",
                    {
                        "locationId": "8.2-GENERAL",
                        "areaCode": "8.2.0",
                        "name": "General / sin área asignada",
                        "type": "general",
                        "zoneId": 8,
                        "zoneName": "Prototipado Zona-Limpia",
                        "subzoneId": "8.2",
                        "subzoneName": "Impresión 3D",
                    },
                )
            ],
            "fabacademyWeeks": [
                ("5", {"weekId": 5, "name": "3D Scanning and Printing"})
            ],
            "items": [
                (
                    "auto-id",
                    {
                        "sku": "0802001",
                        "tipo": "Consumible",
                        "nombre": "Filamento PLA",
                        "zoneId": 8,
                        "subzoneId": "8.2",
                        "locationId": "8.2-GENERAL",
                    },
                ),
                (
                    "0802002",
                    {
                        "tipo": "Material",
                        "nombre": "Documento heredado",
                        "zoneId": 8,
                        "subzoneId": "8.2",
                        "locationId": "8.2-GENERAL",
                    },
                ),
            ],
        }
    )


class GptInventoryCoreTests(unittest.TestCase):
    def setUp(self):
        self.db = fixture_db()

    def test_context_reuses_live_catalogs(self):
        result = inventory_context(self.db)
        self.assertEqual("create-only; no update; no delete", result["writePolicy"])
        self.assertEqual("Impresión 3D", result["subzones"][0]["name"])

    def test_sku_check_detects_field_and_document_id(self):
        result = check_skus(self.db, ["0802001", "0802002", "0802003"])
        self.assertEqual([True, True, False], [row["exists"] for row in result["results"]])

    def test_suggestion_reserves_legacy_document_ids(self):
        result = suggest_sku(self.db, 8, "8.2")
        self.assertEqual("0802003", result["firstAvailableSku"])

    def test_valid_item_builds_create_plan(self):
        draft = [
            {
                "sku": "0802003",
                "tipo": "Consumible",
                "nombre": "Filamento PETG azul",
                "zoneId": 8,
                "subzoneId": "8.2",
                "locationId": "8.2-GENERAL",
                "fabacademyWeeks": [5],
                "fabacademyWeekNames": ["3D Scanning and Printing"],
                "inventarioDeseado": 4,
            }
        ]
        plan = validate_and_plan(self.db, draft, "items")
        self.assertTrue(plan.ok, plan.errors)
        self.assertEqual("items", plan.writes[0].collection)
        self.assertEqual("0802003", plan.writes[0].document_id)
        self.assertEqual("General / sin área asignada", plan.normalized["items"][0]["locationName"])

    def test_existing_sku_is_rejected_not_updated(self):
        draft = {
            "sku": "0802001",
            "tipo": "Consumible",
            "nombre": "No debe reemplazar",
            "zoneId": 8,
            "subzoneId": "8.2",
            "locationId": "8.2-GENERAL",
            "inventarioDeseado": 4,
        }
        plan = validate_and_plan(self.db, draft, "items")
        self.assertFalse(plan.ok)
        self.assertIn("existing_sku", {error["code"] for error in plan.errors})

    def test_hierarchical_create_plan_preserves_dependency_order(self):
        draft = {
            "zones": [{"zoneId": 13, "name": "Nueva capacidad"}],
            "subzones": [{"subzoneId": "13.1", "zoneId": 13, "name": "Proceso nuevo"}],
            "locations": [
                {
                    "locationId": "13.1-GENERAL",
                    "areaCode": "13.1.0",
                    "name": "General / sin área asignada",
                    "type": "general",
                    "zoneId": 13,
                    "subzoneId": "13.1",
                }
            ],
            "items": [
                {
                    "sku": "1301001",
                    "tipo": "Herramienta",
                    "nombre": "Herramienta inicial",
                    "zoneId": 13,
                    "subzoneId": "13.1",
                    "locationId": "13.1-GENERAL",
                    "inventarioDeseado": 1,
                }
            ],
        }
        plan = validate_and_plan(self.db, draft)
        self.assertTrue(plan.ok, plan.errors)
        self.assertEqual(["zones", "subzones", "locations", "items"], [write.collection for write in plan.writes])


if __name__ == "__main__":
    unittest.main()
