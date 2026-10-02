import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from admin_web.app import app, dashboard_session_authenticated


class DashboardSsoTestCase(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_signed_out_tournament_route_redirects_to_dashboard_login(self):
        with patch("admin_web.app.dashboard_session_authenticated", return_value=False):
            response = self.client.get("/rr-tournaments-web/")

        redirect = urlsplit(response.headers["Location"])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(redirect.path, "/discord-tools/")
        self.assertEqual(parse_qs(redirect.query)["next"], ["/rr-tournaments-web/"])

    def test_dashboard_session_allows_tournament_route(self):
        with patch("admin_web.app.dashboard_session_authenticated", return_value=True), patch.dict(
            app.view_functions,
            {"ui.index": lambda: "tournaments"},
        ):
            response = self.client.get("/rr-tournaments-web/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), "tournaments")

    def test_login_route_does_not_render_tournament_login(self):
        with patch("admin_web.app.dashboard_session_authenticated", return_value=False):
            response = self.client.get(
                "/rr-tournaments-web/login?next=/rr-tournaments-web/"
            )

        redirect = urlsplit(response.headers["Location"])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(redirect.path, "/discord-tools/")
        self.assertEqual(parse_qs(redirect.query)["next"], ["/rr-tournaments-web/"])

    def test_dashboard_cookie_is_forwarded_to_blacky_session_check(self):
        with app.test_request_context(
            "/rr-tournaments-web/",
            headers={"Cookie": "blacky_tools_session=signed-session"},
        ):
            with patch("admin_web.app.HTTPConnection") as connection_type:
                connection_type.return_value.getresponse.return_value.status = 204

                self.assertTrue(dashboard_session_authenticated())

        connection_type.assert_called_once_with("127.0.0.1", 9003, timeout=2)
        connection_type.return_value.request.assert_called_once_with(
            "GET",
            "/discord-tools/api/session",
            headers={"Cookie": "blacky_tools_session=signed-session", "Connection": "close"},
        )


if __name__ == "__main__":
    unittest.main()