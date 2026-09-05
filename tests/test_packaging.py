import re
import unittest
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_backend_and_ui_versions_match(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        versions = re.findall(r"FROM dessalines/lemmy(?:-ui)?:(\S+)", dockerfile)

        self.assertEqual(versions, ["1.0.0-beta.1", "1.0.0-beta.1"])
        self.assertIn("FROM asonix/pictrs:0.5.24 AS pictrs-source", dockerfile)
        self.assertIn("IMAGEMAGICK_VERSION=7.1.1-47", dockerfile)
        self.assertIn("IMAGEMAGICK_SHA256=818e21a248986f15", dockerfile)
        self.assertNotIn("COPY magick", dockerfile)

    def test_manifest_uses_current_resource_fields(self):
        with (ROOT / "openhost.toml").open("rb") as manifest_file:
            manifest = tomllib.load(manifest_file)

        self.assertEqual(manifest["routing"]["public_paths"], ["/"])
        self.assertEqual(manifest["routing"]["health_check"], "/_healthz")
        self.assertEqual(manifest["resources"]["cpu_cores"], 1.5)
        self.assertEqual(manifest["resources"]["memory_mb"], 1536)
        self.assertEqual(manifest["resources"]["build_memory_mb"], 2048)
        self.assertNotIn("cpu_millicores", manifest["resources"])
        self.assertEqual(manifest["data"], {"app_data": True})

    def test_startup_exposes_no_persisted_credentials(self):
        start = (ROOT / "start.sh").read_text()
        config = (ROOT / "config.template.hjson").read_text()

        self.assertNotIn("postgres://lemmy:", start)
        self.assertNotIn("__POSTGRES_PASSWORD__", config)
        self.assertIn("postgres://lemmy@127.0.0.1:5432/lemmy", config)
        self.assertIn("--auth-host=trust", start)
        self.assertIn('OIDC_DATA_DIR="$LEMMY_RUNTIME_DIR/oidc"', start)
        self.assertIn('rm -rf "$PERSIST/oidc"', start)
        self.assertLess(
            start.index("BOOTSTRAP_MODE=reconcile"),
            start.index('echo "[start.sh] Starting nginx'),
        )
        self.assertLess(
            start.index('echo "[start.sh] Waiting for UI and SSO services'),
            start.index('echo "[start.sh] Starting nginx'),
        )
        self.assertIn('wait -n "$PG_PID"', start)
        self.assertIn('wait -n "$PG_PID" "$PICTRS_PID"', start)
        self.assertIn("__PICTRS_API_KEY__", config)

    def test_catalog_documentation_sections_and_license_exist(self):
        readme = (ROOT / "README.md").read_text()
        for heading in (
            "## What you get",
            "## Usage",
            "## Deploying",
            "## Data",
            "## Backup",
            "## Upgrades",
            "## Resources",
            "## Troubleshooting",
            "## Caveats",
            "## License",
        ):
            self.assertIn(heading, readme)
        self.assertTrue((ROOT / "LICENSE").is_file())
        self.assertTrue((ROOT / "NOTICE").is_file())

    def test_bouncer_avoids_python_312_only_multiline_fstrings(self):
        source = (ROOT / "sso_bounce.py").read_text()

        self.assertNotIn('f"{PUBLIC_BASE}/_oidc/authorize?{\n', source)


if __name__ == "__main__":
    unittest.main()
