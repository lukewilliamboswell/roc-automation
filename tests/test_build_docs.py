import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ACTION = Path(__file__).resolve().parents[1] / "actions" / "build-docs" / "build_docs.py"
SPEC = importlib.util.spec_from_file_location("build_docs", ACTION)
build_docs = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(build_docs)


class BuildDocsTests(unittest.TestCase):
    def test_paths_must_be_relative_and_contained(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "docs").mkdir()
            self.assertEqual(build_docs.contained_path(root, "docs", "docs"), root / "docs")
            for value in ("", "/tmp", "../outside"):
                with self.subTest(value=value), self.assertRaises(SystemExit):
                    build_docs.contained_path(root, value, "docs")

    def test_api_generation_uses_an_argument_vector_and_requires_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary).resolve()
            source = workspace / "platform" / "main.roc"
            source.parent.mkdir()
            source.write_text("platform \"\"\n", encoding="utf-8")
            site = workspace / "site"
            site.mkdir()
            args = type("Args", (), {
                "api_entrypoint": "platform/main.roc",
                "roc_command": "roc",
            })()

            def generate(*command, cwd):
                output = Path(next(part.split("=", 1)[1] for part in command if part.startswith("--output=")))
                output.mkdir()
                (output / "index.html").write_text("api", encoding="utf-8")

            with patch.object(build_docs.shutil, "which", return_value="/bin/roc"), \
                 patch.object(build_docs, "run", side_effect=generate) as invoked:
                build_docs.build_api(args, workspace, site)
            self.assertEqual(invoked.call_args.args[:3], ("/bin/roc", "docs", str(source)))

    def test_github_outputs_are_absolute_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            output = root / "github-output"
            site, pdf = root / "site", root / "manual.pdf"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
                build_docs.write_outputs(site, pdf)
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                f"site-directory={site}\npdf-path={pdf}\n",
            )


if __name__ == "__main__":
    unittest.main()
