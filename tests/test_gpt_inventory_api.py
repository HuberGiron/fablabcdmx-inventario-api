import os
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.api_core.exceptions import AlreadyExists

from app.gpt_inventory_api import create_gpt_inventory_router


class FakeSnapshot:
    def __init__(self, document_id, data):
        self.id = document_id
        self._data = data

    def to_dict(self):
        return dict(self._data)


class FakeDocument:
    def __init__(self, db, collection_name, document_id):
        self.db = db
        self.collection_name = collection_name
        self.id = document_id


class FakeCollection:
    def __init__(self, db, name):
        self.db = db
        self.name = name

    def stream(self):
        return [
            FakeSnapshot(document_id, data)
            for document_id, data in self.db.data.get(self.name, {}).items()
        ]

    def document(self, document_id):
        return FakeDocument(self.db, self.name, document_id)


class FakeBatch:
    def __init__(self, db):
        self.db = db
        self.creates = []

    def create(self, reference, data):
        self.creates.append((reference, dict(data)))

    def commit(self):
        for reference, _ in self.creates:
            if reference.id in self.db.data.setdefault(reference.collection_name, {}):
                raise AlreadyExists("document exists")
        for reference, data in self.creates:
            self.db.data[reference.collection_name][reference.id] = data
        self.db.committed_batches.append(list(self.creates))
        return []


class FakeDb:
    def __init__(self):
        self.data = {
            "zones": {"8": {"zoneId": 8, "name": "Prototipado Zona-Limpia"}},
            "subzones": {
                "8.2": {"subzoneId": "8.2", "zoneId": 8, "name": "Impresión 3D"}
            },
            "locations": {
                "8.2-GENERAL": {
                    "locationId": "8.2-GENERAL",
                    "areaCode": "8.2.0",
                    "name": "General / sin área asignada",
                    "type": "general",
                    "zoneId": 8,
                    "zoneName": "Prototipado Zona-Limpia",
                    "subzoneId": "8.2",
                    "subzoneName": "Impresión 3D",
                }
            },
            "fabacademyWeeks": {
                "5": {"weekId": 5, "name": "3D Scanning and Printing"}
            },
            "items": {
                "legacy-auto": {
                    "sku": "0802001",
                    "tipo": "Consumible",
                    "nombre": "Filamento PLA",
                    "zoneId": 8,
                    "subzoneId": "8.2",
                    "locationId": "8.2-GENERAL",
                }
            },
            "gptCreateAudits": {},
        }
        self.committed_batches = []

    def collection(self, name):
        return FakeCollection(self, name)

    def batch(self):
        return FakeBatch(self)


def item_draft(sku="0802002", name="Filamento PETG azul"):
    return [
        {
            "sku": sku,
            "tipo": "Consumible",
            "nombre": name,
            "zoneId": 8,
            "subzoneId": "8.2",
            "locationId": "8.2-GENERAL",
            "fabacademyWeeks": [5],
            "inventarioDeseado": 4,
        }
    ]


class GptInventoryApiTests(unittest.TestCase):
    def setUp(self):
        os.environ["GPT_ACTION_API_KEY"] = "test-action-key"
        os.environ["GPT_ACTION_SIGNING_SECRET"] = "test-signing-secret-with-at-least-32-characters"
        os.environ["GPT_ACTION_TOKEN_TTL_SECONDS"] = "600"
        self.db = FakeDb()
        app = FastAPI()
        app.include_router(create_gpt_inventory_router(self.db))
        self.client = TestClient(app)
        self.headers = {"Authorization": "Bearer test-action-key"}

    def tearDown(self):
        for name in (
            "GPT_ACTION_API_KEY",
            "GPT_ACTION_SIGNING_SECRET",
            "GPT_ACTION_TOKEN_TTL_SECONDS",
        ):
            os.environ.pop(name, None)

    def test_all_routes_require_action_key(self):
        response = self.client.get("/api/gpt/context")
        self.assertEqual(401, response.status_code)

    def test_router_exposes_no_update_or_delete_methods(self):
        methods = {
            method
            for route in self.client.app.routes
            if route.path.startswith("/api/gpt")
            for method in route.methods
        }
        self.assertTrue(methods <= {"GET", "POST"})
        paths = {route.path for route in self.client.app.routes if route.path.startswith("/api/gpt")}
        self.assertFalse(any(word in path for path in paths for word in ("update", "delete", "patch")))

    def test_prepare_then_create_is_atomic_and_create_only(self):
        body = {"entityType": "items", "draft": item_draft()}
        prepared = self.client.post(
            "/api/gpt/prepare-create", json=body, headers=self.headers
        )
        self.assertEqual(200, prepared.status_code, prepared.text)
        prepared_data = prepared.json()
        self.assertTrue(prepared_data["ok"])

        created = self.client.post(
            "/api/gpt/create",
            json={
                **body,
                "confirmationToken": prepared_data["confirmationToken"],
                "confirmationText": prepared_data["confirmationText"],
            },
            headers=self.headers,
        )
        self.assertEqual(200, created.status_code, created.text)
        self.assertEqual(1, created.json()["created"])
        self.assertEqual(0, created.json()["updated"])
        self.assertEqual(0, created.json()["deleted"])
        self.assertIn("0802002", self.db.data["items"])
        self.assertEqual(1, len(self.db.committed_batches))
        self.assertEqual(2, len(self.db.committed_batches[0]))  # auditoría + ítem

    def test_existing_sku_never_gets_confirmation_token(self):
        response = self.client.post(
            "/api/gpt/prepare-create",
            json={"entityType": "items", "draft": item_draft("0802001")},
            headers=self.headers,
        )
        data = response.json()
        self.assertFalse(data["ok"])
        self.assertNotIn("confirmationToken", data)
        self.assertIn("existing_sku", {error["code"] for error in data["errors"]})

    def test_token_cannot_authorize_a_changed_draft(self):
        original = {"entityType": "items", "draft": item_draft()}
        prepared = self.client.post(
            "/api/gpt/prepare-create", json=original, headers=self.headers
        ).json()
        changed = {"entityType": "items", "draft": item_draft(name="Contenido alterado")}
        response = self.client.post(
            "/api/gpt/create",
            json={
                **changed,
                "confirmationToken": prepared["confirmationToken"],
                "confirmationText": prepared["confirmationText"],
            },
            headers=self.headers,
        )
        self.assertEqual(409, response.status_code)
        self.assertNotIn("0802002", self.db.data["items"])


if __name__ == "__main__":
    unittest.main()
