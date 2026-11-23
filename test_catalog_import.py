import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from server import create_app
from store import Store


CSV_HEADER = "sku,name,category,price,stock,description,art\n"


class CatalogImportTest(unittest.TestCase):
    def test_preview_validation_and_import(self):
        with TemporaryDirectory() as directory, patch.dict("os.environ", {
            "SHOPBOT_ADMIN_TOKEN": "test-admin", "DATABASE_URL": "", "SHOPBOT_REQUIRE_POSTGRES": "0",
        }):
            with TestClient(create_app(Store(sqlite_path=Path(directory) / "shop.sqlite3"))) as client:
                headers = {"Authorization": "Bearer test-admin", "Content-Type": "text/csv"}
                csv = CSV_HEADER + "coast-mug-2,Coast Mug,Drinkware,12.25,3,A ceramic mug.,mug\n"
                preview = client.post("/admin/catalog/preview", headers=headers, content=csv)
                self.assertEqual(preview.json()["products"][0]["cents"], 1225)
                self.assertEqual(len(client.get("/catalog").json()), 4)
                self.assertEqual(client.post("/admin/catalog/import", content=csv).status_code, 401)
                invalid = csv + "bad,Bad,Drinkware,wrong,1,Description,mug"
                self.assertEqual(client.post("/admin/catalog/import", headers=headers, content=invalid).status_code, 422)
                self.assertEqual(len(client.get("/catalog").json()), 4)
                self.assertEqual(client.post("/admin/catalog/import", headers=headers, content=csv).json(), {"saved": 1})
                self.assertEqual(len(client.get("/catalog").json()), 5)

                def chat(message):
                    return client.post("/chat", json={"user_id": "seller-test", "thread_id": "order", "message": message}).json()

                proposal = chat("/buy coast-mug-2 1")
                confirmation = re.search(r"CONFIRM [A-F0-9]{8}", proposal["reply"]).group(0)
                self.assertEqual(client.post("/admin/catalog/import", headers=headers, content=csv.replace("12.25", "13.25")).status_code, 200)
                self.assertIn("price or stock has changed", chat(confirmation)["reply"])
                product = next(p for p in client.get("/catalog").json() if p["sku"] == "coast-mug-2")
                self.assertEqual((product["cents"], product["stock"]), (1325, 3))
