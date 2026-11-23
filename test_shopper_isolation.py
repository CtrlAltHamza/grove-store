import os
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from server import create_app
from store import Store


class ShopperIsolationTest(unittest.TestCase):
    def test_signed_sessions_separate_shoppers_and_grading(self):
        with TemporaryDirectory() as folder, patch.dict(os.environ, {
            "SHOPBOT_SESSION_SECRET": "test-only-session-secret", "DATABASE_URL": "",
            "SHOPBOT_REQUIRE_POSTGRES": "0",
        }):
            database = Path(folder) / "test.sqlite3"
            with TestClient(create_app(Store(sqlite_path=database))) as client:
                session = client.post("/shopper/session")
                self.assertEqual(session.status_code, 200)
                self.assertEqual(session.headers["Cache-Control"], "no-store")
                token = session.json()["token"]
                other_token = client.post("/shopper/session").json()["token"]
                shopper_id, signature = token.split(".")
                headers = {"Authorization": f"Bearer {token}"}
                other_headers = {"Authorization": f"Bearer {other_token}"}
                message = {"thread_id": "same", "message": "/preference"}

                saved = client.post("/shopper/chat", headers=headers, json={
                    **message, "message": "/remember recycled paper",
                })
                self.assertEqual(saved.status_code, 200)
                self.assertIn("recycled paper", client.post(
                    "/shopper/chat", headers=headers, json=message,
                ).json()["reply"])
                self.assertNotIn("recycled paper", client.post(
                    "/shopper/chat", headers=other_headers, json=message,
                ).json()["reply"])

                combined_token = f"{other_token.split('.')[0]}.{signature}"
                self.assertEqual(client.post("/shopper/chat", headers={
                    "Authorization": f"Bearer {combined_token}",
                }, json=message).status_code, 401)
                self.assertEqual(client.post("/shopper/chat", json=message).status_code, 401)
                self.assertEqual(client.post("/shopper/chat", headers=other_headers, json={
                    **message, "user_id": shopper_id,
                }).status_code, 422)

                grading = client.post("/chat", json={**message, "user_id": shopper_id})
                self.assertEqual(grading.status_code, 200)
                self.assertEqual(set(grading.json()), {"reply", "tool_calls", "tokens"})
                self.assertEqual(grading.json()["tokens"], {"in": 0, "out": 0})
                self.assertNotIn("recycled paper", grading.json()["reply"])
                self.assertEqual(client.post("/chat", json={
                    **message, "user_id": f"shopper/{shopper_id}",
                }).status_code, 422)

                proposal = client.post("/shopper/chat", headers=headers, json={
                    **message, "message": "/buy pocket-journal 1",
                }).json()["reply"]
                code = re.search(r"CONFIRM [A-F0-9]{8}", proposal).group(0)
                confirmation = {**message, "message": code}
                self.assertIn("no matching", client.post(
                    "/shopper/chat", headers=other_headers, json=confirmation,
                ).json()["reply"])
                self.assertIn("no matching", client.post(
                    "/chat", json={**confirmation, "user_id": shopper_id},
                ).json()["reply"])

            with TestClient(create_app(Store(sqlite_path=database))) as restarted:
                response = restarted.post("/shopper/chat", headers=headers, json={
                    **message, "thread_id": "new-thread",
                })
                self.assertIn("recycled paper", response.json()["reply"])
                order = restarted.post("/shopper/chat", headers=headers, json=confirmation)
                self.assertEqual(order.json()["tool_calls"][0]["name"], "place_demo_order")


if __name__ == "__main__":
    unittest.main()
