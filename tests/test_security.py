import importlib
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
from unittest.mock import patch

from starlette.testclient import TestClient


class SecurityBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        os.environ.update(
            {
                "OIDC_PUBLIC_BASE": "https://lemmy.example.com",
                "OIDC_CLIENT_ID": "test-client",
                "OIDC_CLIENT_SECRET": "test-secret",
                "OIDC_DATA_DIR": cls.temp_dir.name,
                "LEMMY_OAUTH_PROVIDER_ID": "7",
                "SSO_USERNAME": "owner",
                "LEMMY_HOSTNAME": "lemmy.example.com",
                "LEMMY_ADMIN_PASSWORD": "admin-secret",
                "LEMMY_ADMIN_USERNAME": "openhost-provisioner",
                "OIDC_PROVIDER_ID_FILE": os.path.join(cls.temp_dir.name, "provider-id"),
            }
        )
        for name in ("sso_bounce", "oidc_bridge", "bootstrap"):
            sys.modules.pop(name, None)
        cls.bounce = importlib.import_module("sso_bounce")
        cls.oidc = importlib.import_module("oidc_bridge")
        cls.bootstrap = importlib.import_module("bootstrap")

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def test_bounce_escapes_script_breakout_and_rejects_external_prev(self):
        client = TestClient(self.bounce.app)
        response = client.get(
            "/sso-bounce",
            params={"prev": "/</script><script>alert(1)</script>"},
            headers={
                "X-OpenHost-Is-Owner": "true",
                "X-Forwarded-Host": "lemmy.example.com",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("</script><script>alert(1)</script>", response.text)
        self.assertIn(
            r"\u003c/script>\u003cscript>alert(1)\u003c/script>", response.text
        )
        self.assertIn("default-src 'none'", response.headers["content-security-policy"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.bounce._safe_prev("//evil.example/path"), "/")
        self.assertEqual(self.bounce._safe_prev("/safe?value=1"), "/safe?value=1")

    def test_oidc_authorize_requires_exact_callback(self):
        client = TestClient(self.oidc.app)
        common = {
            "client_id": "test-client",
            "response_type": "code",
            "state": "state",
        }
        rejected = client.get(
            "/_oidc/authorize",
            params={**common, "redirect_uri": "https://evil.example/callback"},
            headers={"X-OpenHost-Is-Owner": "true"},
        )
        accepted = client.get(
            "/_oidc/authorize",
            params={
                **common,
                "redirect_uri": "https://lemmy.example.com/oauth/callback",
            },
            headers={"X-OpenHost-Is-Owner": "true"},
            follow_redirects=False,
        )

        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(accepted.status_code, 302)
        self.assertTrue(
            accepted.headers["location"].startswith(
                "https://lemmy.example.com/oauth/callback?code="
            )
        )

    def test_nginx_logs_neither_queries_nor_referrers_and_blocks_signup(self):
        config = Path("nginx.conf").read_text()

        self.assertNotIn("$request ", config)
        self.assertNotIn("$request_uri", config)
        self.assertNotIn("$http_referer", config)
        self.assertIn(
            "location = /api/v4/account/auth/register { return 403; }", config
        )
        self.assertIn("location = /api/v3/user/register { return 403; }", config)
        self.assertIn("map $http_accept $actor_upstream", config)
        self.assertIn("proxy_pass $actor_upstream;", config)

    def test_password_reset_keeps_credentials_out_of_process_arguments(self):
        completed = CompletedProcess([], 0, stdout="12\nUPDATE 1\n", stderr="")
        with patch.object(
            self.bootstrap.subprocess, "run", return_value=completed
        ) as run:
            updated = self.bootstrap._psql_reset_password("owner", "bcrypt-secret")

        self.assertTrue(updated)
        command = run.call_args.args[0]
        stdin = run.call_args.kwargs["input"]
        self.assertNotIn("bcrypt-secret", " ".join(command))
        self.assertNotIn("postgresql://", " ".join(command))
        self.assertIn("bcrypt-secret", stdin)

    def test_password_reset_timeout_does_not_log_command_or_secret(self):
        error = TimeoutExpired(["psql", "bcrypt-secret"], 30)
        stderr = StringIO()
        with (
            patch.object(self.bootstrap.subprocess, "run", side_effect=error),
            redirect_stderr(stderr),
        ):
            updated = self.bootstrap._psql_reset_password("owner", "bcrypt-secret")

        self.assertIsNone(updated)
        self.assertNotIn("bcrypt-secret", stderr.getvalue())
        self.assertIn("timed out after 30 seconds", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
