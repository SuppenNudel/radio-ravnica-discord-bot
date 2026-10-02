import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from admin_web.app import app


class BotLogsAccessTestCase(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_logs_page_requires_admin_login(self):
        response = self.client.get("/rr-tournaments-web/bot-logs/")

        self.assertEqual(response.status_code, 302)
        redirect = urlsplit(response.headers["Location"])
        self.assertEqual(redirect.path, "/rr-tournaments-web/login")
        self.assertEqual(parse_qs(redirect.query)["next"], ["/rr-tournaments-web/bot-logs/"])

    def test_logs_api_requires_admin_login(self):
        response = self.client.get("/rr-tournaments-web/api/bot-logs?lines=20")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json(), {"error": "Authentication required."})

    def test_authenticated_admin_can_load_logs(self):
        with self.client.session_transaction() as session:
            session["admin_authenticated"] = True

        with patch("admin_web.app.get_bot_logs", return_value=[]):
            response = self.client.get("/rr-tournaments-web/api/bot-logs?lines=20")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["logs"], [])