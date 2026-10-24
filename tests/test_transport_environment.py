"""The real transport must give the inert archive parser no job credentials."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile


BINARY = os.environ.get("ROC_LIFECYCLE_TRANSPORT_BINARY")
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(BINARY, "set ROC_LIFECYCLE_TRANSPORT_BINARY to the built utility")
class TransportEnvironmentTests(unittest.TestCase):
    def test_actual_extraction_discards_credentials_oidc_and_python_hooks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "raw"
            raw.mkdir()
            archive = raw / "artifact.zip"
            with zipfile.ZipFile(archive, "w") as output:
                entry = zipfile.ZipInfo("retained.txt")
                entry.create_system = 3
                entry.external_attr = (stat.S_IFREG | 0o644) << 16
                output.writestr(entry, b"exact inert bytes")
            digest = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"artifacts": [{"id": 1, "name": "selected", "digest": digest}]}))
            helper = root / "reviewed-helper.py"
            forbidden = ["GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_URL",
                         "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "PYTHONPATH", "PYTHONSTARTUP", "RA_ZSTD",
                         "UNNEEDED_CONTROLLER_CANARY"]
            helper.write_text(
                "import os, runpy\n"
                f"for name in {forbidden!r}:\n"
                "    assert name not in os.environ, 'unneeded inherited capability: ' + name\n"
                "assert os.environ.get('PATH'), 'missing tool resolution path'\n"
                "if os.name == 'nt':\n"
                "    assert os.environ.get('SystemRoot'), 'missing Windows Python runtime root'\n"
                f"runpy.run_path({str(ROOT / 'actions/release-compatibility/archive.py')!r}, run_name='__main__')\n"
            )
            # Empty PYTHONPATH avoids changing the controller launcher itself;
            # absence (rather than an empty credential) is asserted in the child.
            environment = {**os.environ, **{name: "canary" for name in forbidden}}
            environment["PYTHONPATH"] = ""
            ran = subprocess.run(
                [BINARY, "extract", str(manifest), str(raw), str(root / "extracted"), sys.executable, str(helper)],
                env=environment, capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertEqual((root / "extracted/selected/retained.txt").read_bytes(), b"exact inert bytes")


if __name__ == "__main__":
    unittest.main()
