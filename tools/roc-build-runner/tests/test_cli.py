"""Compiler-independent fail-closed tests of the actual native runner.

Set ROC_BUILD_RUNNER_BINARY to the executable under test. Positive sandbox,
raw-filename, output and exit-status gates run in the consumer's Linux build
suite, where actual isolated mount and network namespaces are available.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class NativeBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configured = os.environ.get("ROC_BUILD_RUNNER_BINARY")
        if not configured:
            raise RuntimeError("ROC_BUILD_RUNNER_BINARY must name the native executable")
        cls.binary = Path(configured).resolve(strict=True)
        if not cls.binary.is_file() or not os.access(cls.binary, os.X_OK):
            raise RuntimeError("ROC_BUILD_RUNNER_BINARY is not an executable regular file")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="roc-build-runner-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cwd = self.root / "cwd"
        self.cwd.mkdir()
        self.project = self.root / "project"
        self.project.mkdir()
        self.marker = self.root / "recipe-was-executed"
        self.output = self.root / "output"
        self.spec = self.root / "build.json"
        self.spec.write_text(json.dumps({
            "schema_version": 1,
            "project": str(self.project),
            "argv": [sys.executable, "-c", f"open({str(self.marker)!r}, 'w').write('ran')"],
            "output": "artifact",
            "path": "",
            "inputs": str(self.root / "inputs"),
            "artifacts": str(self.root / "artifacts"),
            "tools": str(self.root / "tools"),
            "readlink": shutil.which("readlink") or "/missing/readlink",
            "chmod": shutil.which("chmod") or "/missing/chmod",
            "env": [],
        }), encoding="utf-8")
        self.env = {
            "PATH": "",
            "out": str(self.output),
            "ROC_BUILD_RUNNER_READLINK": shutil.which("readlink") or "/missing/readlink",
        }

    def invoke(self, *args, env=None):
        result = subprocess.run(
            [str(self.binary), *map(str, args)],
            cwd=self.cwd,
            env=self.env if env is None else env,
            capture_output=True,
            timeout=10,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(b"roc-build-runner:", result.stderr)
        self.assertFalse(self.marker.exists(), "recipe ran without verified isolation")
        self.assertFalse(self.output.exists(), "output appeared without verified isolation")
        self.assertEqual(list(self.cwd.iterdir()), [], "runner wrote state before proof")
        return result

    def test_missing_witness_fails_before_workspace_or_command(self):
        result = self.invoke(self.spec)
        self.assertIn(b"not executed", result.stderr)

    def test_malformed_json_is_not_interpreted_before_proof(self):
        self.spec.write_bytes(b"this is not JSON\x00\xff")
        result = self.invoke(self.spec)
        self.assertIn(b"not executed", result.stderr)
        self.assertNotIn(b"invalid build specification", result.stderr)

    def test_nonexistent_specification_still_checks_isolation_first(self):
        result = self.invoke(self.root / "missing-specification.json")
        self.assertIn(b"not executed", result.stderr)

    def test_environment_specification_path_has_same_boundary(self):
        env = {**self.env, "runnerSpecPath": str(self.spec)}
        result = self.invoke(env=env)
        self.assertIn(b"not executed", result.stderr)

    def test_invalid_witness_fails_closed(self):
        witness = self.root / "witness"
        witness.write_text("mnt:[not-a-number]\nnet:[123]\n", encoding="utf-8")
        result = self.invoke(self.spec, env={**self.env, "ROC_BUILD_RUNNER_WITNESS": str(witness)})
        self.assertIn(b"not executed", result.stderr)

    def test_symlink_witness_is_not_authority(self):
        target = self.root / "caller"
        target.write_text("mnt:[123]\nnet:[456]\n", encoding="utf-8")
        witness = self.root / "witness"
        witness.symlink_to(target)
        result = self.invoke(self.spec, env={**self.env, "ROC_BUILD_RUNNER_WITNESS": str(witness)})
        self.assertIn(b"not executed", result.stderr)

    def test_directory_witness_is_not_authority(self):
        witness = self.root / "witness"
        witness.mkdir()
        result = self.invoke(self.spec, env={**self.env, "ROC_BUILD_RUNNER_WITNESS": str(witness)})
        self.assertIn(b"not executed", result.stderr)

    def test_fifo_witness_is_rejected_without_reading(self):
        witness = self.root / "witness"
        os.mkfifo(witness)
        result = self.invoke(self.spec, env={**self.env, "ROC_BUILD_RUNNER_WITNESS": str(witness)})
        self.assertIn(b"not executed", result.stderr)

    def test_relative_witness_is_not_authority(self):
        result = self.invoke(self.spec, env={**self.env, "ROC_BUILD_RUNNER_WITNESS": "witness"})
        self.assertIn(b"not executed", result.stderr)

    def test_matching_host_namespaces_fail_closed(self):
        witness = self.root / "witness"
        if sys.platform == "linux":
            identities = [os.readlink(f"/proc/self/ns/{kind}") for kind in ("mnt", "net")]
        else:
            identities = ["mnt:[123]", "net:[456]"]
        witness.write_text("\n".join(identities) + "\n", encoding="utf-8")
        result = self.invoke(self.spec, env={**self.env, "ROC_BUILD_RUNNER_WITNESS": str(witness)})
        if sys.platform == "linux":
            self.assertIn(b"shares caller mnt namespace", result.stderr)
        else:
            self.assertIn(b"requires local Linux namespaces", result.stderr)

    def test_missing_specification_argument_has_no_effects(self):
        self.invoke()

    def test_extra_arguments_have_no_effects(self):
        self.invoke(self.spec, "extra")


if __name__ == "__main__":
    unittest.main()
