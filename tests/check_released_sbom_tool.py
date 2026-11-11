"""Check admitted native and source SBOM tools against released fixtures.

Native mode runs on all five systems without Roc or Blueprint configuration.
Source mode uses the record's exact compiler and invocation protocol. This is a
read-only acceptance test, never a privileged publisher operation.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile
import shutil
import sys
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sbom_sidecars", ROOT / "actions/sbom-sidecars/sbom_sidecars.py")
sidecars = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sidecars)
sys.path.insert(0, str(ROOT / "actions/sbom-sidecars"))
import verify_transferred
VERSION = os.environ.get("BLUEPRINT_VERSION", "0.11.0")
FIXTURE_DIGESTS = {
    "0.11.0": "ab138ce9fcc20736ca014d81e27d865cadb653d6dedfaff149cd23fb8d57a42a",
    "0.12.0": "d6c2582aab61b45c9df4feefa883df834643b304e9c3588c7731f467207bfada",
    "0.13.0": "2098dc20816af426f65447614f1f210ad08a31abcf731d9ac05f5ab613a4589d",
}
CYCLONEDX_PROFILES = {
    "0.11.0": "standard-containment-v1",
    "0.12.0": "standard-containment-v1",
    "0.13.0": "standard-evidence-v2",
}


class Tool(NamedTuple):
    argv: list[str]
    timeout: int


def check(tool, directory, *arguments, refusal=None):
    result = subprocess.run([*tool.argv, *map(str, arguments)], cwd=directory, capture_output=True, text=True, timeout=tool.timeout)
    if refusal is None:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode == 1 and refusal in result.stderr, result.stderr


def bind_complete_set(tool, directory, inventory, subject, context, artifact, complete_manifest):
    check(tool, directory, "bind", inventory, subject, context, artifact)
    if not complete_manifest:
        check(tool, directory, "bind", inventory, subject, context, artifact,
              Path(str(artifact) + ".spdx-2.3.json"), "--spdx-2.3")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", action="store_true", help="Exercise the released source app with its exact recorded compiler.")
    parser.add_argument("--roc-binary", default="roc")
    args = parser.parse_args()
    system = sidecars.setup_blueprint.SYSTEMS[(os.environ["RUNNER_OS"], os.environ["RUNNER_ARCH"])]
    recorded = sidecars.sbom_tool(VERSION)
    complete_manifest = "complete-manifest" in recorded["features"]
    with tempfile.TemporaryDirectory(prefix="released-sbom-") as temporary:
        root = Path(temporary)
        if args.source:
            # This explicit test mode exercises source invocation even where a
            # native asset exists; the production action still prefers native.
            compiler = subprocess.run([args.roc_binary, "version"], capture_output=True, text=True, check=True, timeout=30)
            assert sidecars.compiler_matches(compiler.stdout, recorded["compiler"]), "source app requires the reviewed compiler"
            tool = Tool(sidecars.source_tool_command(args.roc_binary, recorded), 180)
            tool_identity = {"url": recorded["url"], "sha256": ""}
        else:
            tool = Tool([str(sidecars.install_binary(VERSION, system, recorded["binaries"][system], root / "tool"))], 60)
            tool_identity = {"url": sidecars.binary_url(VERSION, system), "sha256": recorded["binaries"][system]}
        archive = root / "fixtures.zip"
        sidecars.setup_blueprint.fetch(f"{sidecars.setup_blueprint.RELEASES}/{VERSION}/roc-blueprint-sbom-fixtures-{VERSION}.zip", archive)
        assert sidecars.sha256_of(archive) == FIXTURE_DIGESTS[VERSION], "released fixture bytes differ from their reviewed digest"
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
        bind_complete_set(tool, fixtures, inventory, inputs / "subject.json", inputs / "context.json",
                          artifact, complete_manifest)
        for suffix in ("spdx.json", "cdx.json", "spdx-2.3.json"):
            assert (inputs / f"sample-artifact.{suffix}").read_bytes() == (fixtures / f"native-linux.{suffix}").read_bytes(), suffix
        assert json.loads((inputs / "sample-artifact.sbom-manifest.json").read_text())["profile"] == recorded["profile"]
        extra = [] if complete_manifest else [inputs / "sample-artifact.spdx-2.3.json"]
        check(tool, fixtures, "check", "--manifest", inputs / "sample-artifact.sbom-manifest.json",
              "--inventory", inventory, "--artifacts", inputs, *extra)

        # Read the standard v2 representation independently: containment is
        # nested, the compiler remains build-only, and coverage stays visible.
        bom = json.loads((inputs / "sample-artifact.cdx.json").read_text())
        subject = bom["metadata"]["component"]
        platform = next(item for item in subject["components"] if item["bom-ref"] == "platform")
        assert platform["components"][0]["bom-ref"] == "native"
        compiler = next(item for item in bom["components"] if item["bom-ref"] == "compiler")
        assert compiler["scope"] == "excluded"
        assert {item["value"] for item in bom["metadata"]["properties"] if item["name"] == "blueprint:cyclonedx-profile"} == {CYCLONEDX_PROFILES[VERSION]}
        assert "native-closure" in json.dumps(bom)
        check(tool, fixtures, "check", fixtures / "legacy/blueprint-0.8.cdx.json")

        # Generate another real record set with the action's source/run
        # context, transfer it as inert bytes, and exercise the privileged
        # recheck independently of any Roc compiler or package cache.
        candidate = root / "candidate"
        (candidate / "dist").mkdir(parents=True)
        delivered = candidate / "dist/sample-artifact"
        shutil.copyfile(artifact, delivered)
        subject = json.loads((inputs / "subject.json").read_text())
        source = {"server": "https://github.com", "repository": "owner/repo", "commit": "a" * 40}
        producer = {"workflow": "owner/repo/.github/workflows/release.yml@refs/heads/main", "run_id": "77", "run_attempt": "2"}
        subject["url"] = f"https://github.com/owner/repo/releases/download/{subject['version']}/{subject['filename']}"
        (root / "subject.json").write_text(json.dumps(subject))
        context = json.loads((inputs / "context.json").read_text())
        context["namespace"] = f"https://github.com/owner/repo/sbom/{source['commit']}/{subject['filename']}/revision-1"
        context["generator"] = recorded["generator"]
        (root / "context.json").write_text(json.dumps(context))
        bind_complete_set(tool, root, inventory, root / "subject.json", root / "context.json",
                          delivered, complete_manifest)
        written = {delivered: [(Path(str(delivered) + suffix), sidecars.sha256_of(Path(str(delivered) + suffix))) for suffix in (*sidecars.SIDECAR_SUFFIXES, sidecars.MANIFEST_SUFFIX)]}
        env = {"GITHUB_SERVER_URL": source["server"], "GITHUB_REPOSITORY": source["repository"], "GITHUB_WORKFLOW_REF": producer["workflow"], "GITHUB_RUN_ID": producer["run_id"], "GITHUB_RUN_ATTEMPT": producer["run_attempt"]}
        envelope = sidecars.evidence_envelope(env, candidate, [delivered], written,
            {**recorded, **tool_identity}, VERSION,
            subject["target"], subject["version"], "https://github.com/owner/repo/releases/download/" + subject["version"], source["commit"], context["created"], [], {delivered: inventory})
        assert envelope["generator"]["tool"] == tool_identity["url"]
        assert envelope["generator"]["tool_sha256"] == tool_identity["sha256"]
        (candidate / verify_transferred.EVIDENCE_PATH).write_text(json.dumps(envelope))
        assets = "\n".join(str(file.relative_to(candidate)) for file in [delivered, *(path for path, _ in written[delivered])])
        transferred = root / "transferred"
        shutil.copytree(candidate, transferred)
        if os.name != "nt":  # the privileged checker runs on Ubuntu, with O_NOFOLLOW
            verify_transferred.verify(transferred, "dist/sample-artifact", assets, source, producer, subject["version"], target=subject["target"])

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
    print(f"Released Blueprint {VERSION} {'source-app' if args.source else 'native'} SBOM contract passed on {system}")


if __name__ == "__main__":
    main()
