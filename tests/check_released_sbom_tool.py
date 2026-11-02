"""Check the admitted Blueprint 0.11 tool against its immutable public fixtures.

Runs on each of the five released native systems without a Roc compiler or
Blueprint configuration. Downloads are admitted by reviewed SHA-256 values;
this is a build-job acceptance test, never a privileged publisher operation.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sbom_sidecars", ROOT / "actions/sbom-sidecars/sbom_sidecars.py")
sidecars = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sidecars)
VERSION = "0.11.0"
FIXTURES_SHA256 = "ab138ce9fcc20736ca014d81e27d865cadb653d6dedfaff149cd23fb8d57a42a"


def check(tool, directory, *arguments, refusal=None):
    result = subprocess.run([str(tool), *map(str, arguments)], cwd=directory, capture_output=True, text=True, timeout=60)
    if refusal is None:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode == 1 and refusal in result.stderr, result.stderr


def main():
    system = sidecars.setup_blueprint.SYSTEMS[(os.environ["RUNNER_OS"], os.environ["RUNNER_ARCH"])]
    recorded = sidecars.sbom_tool(VERSION)
    with tempfile.TemporaryDirectory(prefix="released-sbom-") as temporary:
        root = Path(temporary)
        tool = sidecars.install_binary(VERSION, system, recorded["binaries"][system], root / "tool")
        archive = root / "fixtures.zip"
        sidecars.setup_blueprint.fetch(f"{sidecars.setup_blueprint.RELEASES}/{VERSION}/roc-blueprint-sbom-fixtures-{VERSION}.zip", archive)
        assert sidecars.sha256_of(archive) == FIXTURES_SHA256, "released fixture bytes differ from their reviewed digest"
        with zipfile.ZipFile(archive) as source:
            for item in source.infolist():
                name = Path(item.filename)
                assert not name.is_absolute() and ".." not in name.parts and "\\" not in item.filename
                assert item.file_size <= sidecars.MAX_JSON_BYTES and not ((item.external_attr >> 16) & 0o170000 == 0o120000)
            source.extractall(root)
        fixtures = root / f"roc-blueprint-sbom-fixtures-{VERSION}"
        inputs = fixtures / "input"
        inventory = root / "inventory.json"
        check(tool, fixtures, "inspect", inputs / "Spec", inputs / "Lock", inputs / "composition.json", "x86_64-linux", inventory)
        assert inventory.read_bytes() == (inputs / "inventory.json").read_bytes()
        artifact = inputs / "sample-artifact"
        check(tool, fixtures, "bind", inventory, inputs / "subject.json", inputs / "context.json", artifact)
        check(tool, fixtures, "bind", inventory, inputs / "subject.json", inputs / "context.json", artifact, inputs / "sample-artifact.spdx-2.3.json", "--spdx-2.3")
        for suffix in ("spdx.json", "cdx.json", "spdx-2.3.json"):
            assert (inputs / f"sample-artifact.{suffix}").read_bytes() == (fixtures / f"native-linux.{suffix}").read_bytes(), suffix
        assert json.loads((inputs / "sample-artifact.sbom-manifest.json").read_text())["profile"] == recorded["profile"]
        check(tool, fixtures, "check", "--manifest", inputs / "sample-artifact.sbom-manifest.json", "--inventory", inventory, "--artifacts", inputs, inputs / "sample-artifact.spdx-2.3.json")

        # Read the standard v2 representation independently: containment is
        # nested, the compiler remains build-only, and coverage stays visible.
        bom = json.loads((inputs / "sample-artifact.cdx.json").read_text())
        subject = bom["metadata"]["component"]
        platform = next(item for item in subject["components"] if item["bom-ref"] == "platform")
        assert platform["components"][0]["bom-ref"] == "native"
        compiler = next(item for item in bom["components"] if item["bom-ref"] == "compiler")
        assert compiler["scope"] == "excluded"
        assert {item["value"] for item in bom["metadata"]["properties"] if item["name"] == "blueprint:cyclonedx-profile"} == {"standard-containment-v1"}
        assert "native-closure" in json.dumps(bom)
        check(tool, fixtures, "check", fixtures / "legacy/blueprint-0.8.cdx.json")

        # A real source supplier binds into a native target without relabeling
        # its source identity; each supplied mismatch must still be refused.
        imported = fixtures / "source-import"
        imported_inventory = root / "imported-inventory.json"
        check(tool, fixtures, "inspect", imported / "Spec", imported / "Lock", imported / "composition.json", "x86_64-linux", imported_inventory)
        assert imported_inventory.read_bytes() == (imported / "inventory-x86_64-linux.json").read_bytes()
        check(tool, fixtures, "bind", imported_inventory, imported / "subject.json", imported / "context.json", imported / "consumer-artifact")
        for suffix in ("spdx.json", "cdx.json"):
            assert (imported / f"consumer-artifact.{suffix}").read_bytes() == (fixtures / f"native-source-import.{suffix}").read_bytes()
        check(tool, fixtures, "check", "--inventory", imported_inventory, "--artifacts", imported, imported / "consumer-artifact.spdx.json", imported / "consumer-artifact.cdx.json")
        for name, refusal in (("tamper-document-digest", "imported SBOM content digest mismatch"),
                              ("tamper-subject-digest", "import subject filename/version/SHA256 mismatch"),
                              ("tamper-import-native", "imported document subject target mismatch")):
            output = root / f"{name}.json"
            check(tool, fixtures, "inspect", imported / "Spec", imported / "Lock", imported / f"{name}.json", "x86_64-linux", output, refusal=refusal)
            assert not output.exists()
    print(f"Released Blueprint {VERSION} SBOM contract passed on {system}")


if __name__ == "__main__":
    main()
