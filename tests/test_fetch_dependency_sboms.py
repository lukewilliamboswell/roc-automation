import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fetch_dependency_sboms", ROOT / "actions/fetch-dependency-sboms/fetch_dependency_sboms.py")
fetcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetcher)

DEP_URL = "https://example.org/releases/1.2.0/dep-abc.tar.zst"
DEP_SHA = hashlib.sha256(b"dep bundle bytes").hexdigest()
TOOL_URL = "https://example.org/releases/tool/2.0.0/tool-x86_64-linux"
TOOL_SHA = hashlib.sha256(b"tool bytes").hexdigest()
NAMESPACE = "https://example.org/sbom/dep/1.2.0/revision-1"


def spdx3(name="dep-abc.tar.zst", version="1.2.0", sha256=DEP_SHA, namespace=NAMESPACE, target="source"):
    return {
        "@context": "https://spdx.org/rdf/3.0.1/spdx-context.jsonld",
        "@graph": [
            {"type": "CreationInfo", "@id": "_:creationinfo", "specVersion": "3.0.1", "created": "2026-10-21T00:00:00Z"},
            {"type": "SpdxDocument", "spdxId": f"{namespace}#spdx/document", "rootElement": [f"{namespace}#spdx/sbom"]},
            {"type": "software_Sbom", "spdxId": f"{namespace}#spdx/sbom", "rootElement": [f"{namespace}#Subject"]},
            {"type": "software_Package", "spdxId": f"{namespace}#Subject", "name": name, "software_packageVersion": version, "comment": json.dumps({"digest_kind": "final-deliverable-sha256", "target": target}), "verifiedUsing": [{"type": "Hash", "algorithm": "sha256", "hashValue": sha256}]},
        ],
    }


def spdx2(name="dep-abc.tar.zst", version="1.2.0", sha256=DEP_SHA, target="source"):
    return {
        "spdxVersion": "SPDX-2.3", "SPDXID": "SPDXRef-DOCUMENT", "name": name, "documentNamespace": NAMESPACE,
        "packages": [{"SPDXID": "SPDXRef-Subject", "name": name, "versionInfo": version, "comment": json.dumps({"target": target}), "checksums": [{"algorithm": "SHA256", "checksumValue": sha256}]}],
        "relationships": [{"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES", "relatedSpdxElement": "SPDXRef-Subject"}],
    }


def cyclonedx(name="dep-abc.tar.zst", version="1.2.0", sha256=DEP_SHA, target="source"):
    return {
        "bomFormat": "CycloneDX", "specVersion": "1.7", "serialNumber": "urn:uuid:3e671687-395b-41f5-a30f-a58921a69b79", "version": 1,
        "metadata": {"component": {"type": "file", "name": name, "version": version, "hashes": [{"alg": "SHA-256", "content": sha256}], "properties": [{"name": "blueprint:target", "value": target}]}},
        "components": [],
    }


def encode(document):
    return json.dumps(document, sort_keys=True).encode("utf-8")


def composition(**overrides):
    base = {
        "profile": "blueprint-spdx-3.0-v1",
        "components": [
            {"id": "dep", "name": "dep-abc.tar.zst", "version": "1.2.0", "origin": "source:dep"},
            {"id": "tool", "name": "tool", "version": "", "origin": "program:tool:x86_64-linux"},
            {"id": "tree", "name": "tree", "version": "", "origin": "source:tree"},
            {"id": "own", "name": "own", "version": "1", "origin": "reviewed"},
        ],
        "relationships": [], "coverage": [], "inputs": [], "imported_documents": [], "unavailable_sboms": [],
    }
    base.update(overrides)
    return base


LOCK = {
    "format": 2,
    "sources": {
        "dep": {"name": "dep", "provider": "file", "ref": DEP_URL, "rev": "", "digest": "sha256:" + DEP_SHA, "content_identity": "sha256:" + DEP_SHA, "digestKind": "file", "flatSha256": DEP_SHA, "metadata": {}},
        "tree": {"name": "tree", "provider": "nix", "ref": "github:example/tree", "rev": "a" * 40, "digest": "sha256-abc=", "content_identity": "nix:sha256-abc=", "digestKind": "tree", "flatSha256": None, "metadata": {}},
    },
    "fetched": [
        {"name": "tool", "system": "x86_64-linux", "urls": [TOOL_URL], "sha256": TOOL_SHA, "content_identity": "sha256:" + TOOL_SHA, "size": 10, "obtained": "downloaded", "served": ["example.org"], "witness": ""},
        {"name": "tool", "system": "aarch64-darwin", "urls": ["https://example.org/releases/tool/2.0.0/tool-aarch64-darwin"], "sha256": "b" * 64, "content_identity": "sha256:" + "b" * 64, "size": 10, "obtained": "downloaded", "served": [], "witness": ""},
    ],
}


class Published:
    """A pretend network: documents by URL, every request recorded; None is a 404."""

    def __init__(self, documents):
        self.documents = documents
        self.requests = []

    def __call__(self, url):
        self.requests.append(url)
        return self.documents.get(url)


class IdentityTests(unittest.TestCase):
    def test_each_format_yields_the_identity_blueprint_compares(self):
        self.assertEqual(fetcher.document_identity(spdx3()), {"format": "SPDX-3.0.1", "namespace": NAMESPACE, "name": "dep-abc.tar.zst", "version": "1.2.0", "sha256": DEP_SHA, "target": "source"})
        self.assertEqual(fetcher.document_identity(spdx2()), {"format": "SPDX-2.3", "namespace": NAMESPACE, "name": "dep-abc.tar.zst", "version": "1.2.0", "sha256": DEP_SHA, "target": "source"})
        self.assertEqual(fetcher.document_identity(cyclonedx()), {"format": "CycloneDX-1.7", "namespace": "urn:uuid:3e671687-395b-41f5-a30f-a58921a69b79", "name": "dep-abc.tar.zst", "version": "1.2.0", "sha256": DEP_SHA, "target": "source"})
        self.assertEqual(fetcher.document_identity(cyclonedx(target="x86_64-linux"))["target"], "x86_64-linux")

    def test_documents_with_no_described_subject_still_identify_themselves(self):
        scanner = {"spdxVersion": "SPDX-2.3", "SPDXID": "SPDXRef-DOCUMENT", "documentNamespace": "https://anchore.com/syft/dir/src-1", "packages": [{"SPDXID": "SPDXRef-Package-x", "name": "x", "versionInfo": "1"}], "relationships": [{"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES", "relatedSpdxElement": "SPDXRef-DocumentRoot-Directory-src"}]}
        self.assertEqual(fetcher.document_identity(scanner), {"format": "SPDX-2.3", "namespace": "https://anchore.com/syft/dir/src-1", "name": "", "version": "", "sha256": "", "target": ""})
        bom = {"bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": "urn:uuid:1", "metadata": {"component": {"type": "application", "name": "src"}}}
        self.assertEqual(fetcher.document_identity(bom), {"format": "CycloneDX-1.5", "namespace": "urn:uuid:1", "name": "src", "version": "", "sha256": "", "target": ""})
        unparsable = spdx2()
        unparsable["packages"][0]["comment"] = "not json"
        self.assertEqual(fetcher.document_identity(unparsable)["target"], "")
        described_by = spdx2()
        described_by["relationships"] = [{"spdxElementId": "SPDXRef-Subject", "relationshipType": "DESCRIBED_BY", "relatedSpdxElement": "SPDXRef-DOCUMENT"}]
        self.assertEqual(fetcher.document_identity(described_by)["sha256"], DEP_SHA)
        listed = spdx2()
        listed["relationships"], listed["documentDescribes"] = [], ["SPDXRef-Subject"]
        self.assertEqual(fetcher.document_identity(listed)["name"], "dep-abc.tar.zst")

    def test_unidentifiable_documents_are_refused(self):
        for document, reason in (
            ({"hello": 1}, "neither SPDX"),
            ({"@graph": []}, "exactly one SpdxDocument"),
            ({"@graph": [{"type": "SpdxDocument", "spdxId": "no-fragment"}]}, "NAMESPACE#name"),
            ({"spdxVersion": "SPDX-2.3"}, "documentNamespace"),
            ({"bomFormat": "CycloneDX", "specVersion": "1.7"}, "serialNumber"),
        ):
            with self.subTest(document=document), self.assertRaisesRegex(ValueError, reason):
                fetcher.document_identity(document)

    def test_only_https_without_userinfo_or_query_is_fetched(self):
        self.assertTrue(fetcher.https_url(DEP_URL))
        for url in ("http://example.org/x", "https://user@example.org/x", "https://example.org/x?y", "https://example.org/x y", "https://localhost/x", "https://127.0.0.1/x", "", "https://", "ftp://example.org/x"):
            with self.subTest(url=url):
                self.assertFalse(fetcher.https_url(url))


class RecordTests(unittest.TestCase):
    def test_import_entry_binds_the_document_to_the_locked_bytes(self):
        component = {"id": "dep", "version": "1.2.0", "origin": "source:dep"}
        pinned = {"state": "pinned", "urls": [DEP_URL], "sha256": DEP_SHA, "target": "x86_64-linux", "detail": ""}
        content = encode(spdx3())
        entry = fetcher.import_entry(component, pinned, DEP_URL + ".spdx.json", content, "https://example.org/security")
        self.assertEqual(entry["id"], "dep-sbom")
        self.assertEqual(entry["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(entry["namespace"], NAMESPACE)
        self.assertEqual(entry["document"], content.decode())
        self.assertEqual(entry["subject"], {"component": "dep", "filename": "dep-abc.tar.zst", "version": "1.2.0", "sha256": DEP_SHA})
        self.assertEqual(entry["relation"], "dependsOn")
        # The supplier's bundle is architecture-independent and says so; the
        # entry keeps that, so a native consumer can bind it once Blueprint admits it.
        self.assertEqual(entry["target"], "source")
        silent = spdx3()
        silent["@graph"][3].pop("comment")
        self.assertEqual(fetcher.import_entry(component, pinned, DEP_URL + ".spdx.json", encode(silent), "https://example.org/security")["target"], "x86_64-linux")
        self.assertEqual(entry["contact"], "https://example.org/security")

    def test_a_program_without_a_reviewed_version_takes_the_suppliers_or_its_digest(self):
        component = {"id": "tool", "version": "", "origin": "program:tool:x86_64-linux"}
        pinned = {"state": "pinned", "urls": [TOOL_URL], "sha256": TOOL_SHA, "target": "x86_64-linux", "detail": ""}
        entry = fetcher.import_entry(component, pinned, TOOL_URL + ".cdx.json", encode(cyclonedx("tool-x86_64-linux", "2.0.0", TOOL_SHA)), "https://e/s")
        self.assertEqual(entry["subject"]["version"], "2.0.0")
        bare = cyclonedx("tool-x86_64-linux", "", TOOL_SHA)
        del bare["metadata"]["component"]["version"]
        entry = fetcher.import_entry(component, pinned, TOOL_URL + ".cdx.json", encode(bare), "https://e/s")
        self.assertEqual(entry["subject"]["version"], "sha256-" + TOOL_SHA)

    def test_a_document_about_other_bytes_a_different_file_or_version_is_refused(self):
        component = {"id": "dep", "version": "1.2.0", "origin": "source:dep"}
        pinned = {"state": "pinned", "urls": [DEP_URL], "sha256": DEP_SHA, "target": "x86_64-linux", "detail": ""}
        for document, reason in (
            (spdx3(sha256="0" * 64), "not the locked bytes"),
            (spdx2(name="other.tar.zst"), "not 'dep-abc.tar.zst'"),
            (cyclonedx(version="1.3.0"), "reviews dep as '1.2.0'"),
        ):
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                fetcher.import_entry(component, pinned, DEP_URL + ".spdx.json", encode(document), "https://e/s")
        with self.assertRaisesRegex(ValueError, "not valid UTF-8 JSON"):
            fetcher.import_entry(component, pinned, DEP_URL + ".spdx.json", b"\xff{", "https://e/s")

    def test_naming_convention_tries_spdx_then_cyclonedx_beside_each_locked_url(self):
        network = Published({TOOL_URL + ".cdx.json": b"{}"})
        self.assertEqual(fetcher.find_published(["https://example.org/mirror/tool", TOOL_URL], network), (TOOL_URL + ".cdx.json", b"{}"))
        self.assertEqual(network.requests, ["https://example.org/mirror/tool.spdx.json", "https://example.org/mirror/tool.cdx.json", TOOL_URL + ".spdx.json", TOOL_URL + ".cdx.json"])
        self.assertIsNone(fetcher.find_published([DEP_URL], network))
        with self.assertRaisesRegex(ValueError, "not an HTTPS address"):
            fetcher.find_published(["http://example.org/x"], network)

    def test_tree_pinned_sources_cannot_be_bound_and_are_not_fetched(self):
        pinned = fetcher.locked_file({"id": "tree", "origin": "source:tree"}, LOCK, "x86_64-linux")
        self.assertEqual(pinned["state"], "unbindable")
        self.assertIn("tree digest", pinned["detail"])
        self.assertEqual(fetcher.locked_file({"id": "dep", "origin": "source:dep"}, LOCK, "x86_64-linux"), {"state": "pinned", "urls": [DEP_URL], "sha256": DEP_SHA, "target": "x86_64-linux", "detail": ""})
        self.assertEqual(fetcher.locked_file({"id": "tool", "origin": "program:tool:x86_64-linux"}, LOCK, "source")["target"], "x86_64-linux")
        for component, reason in (({"id": "x", "origin": "source:absent"}, "does not pin"), ({"id": "x", "origin": "program:tool:x86_64-windows"}, "does not pin once")):
            with self.subTest(component=component), self.assertRaisesRegex(ValueError, reason):
                fetcher.locked_file(component, LOCK, "x86_64-linux")

    def test_recorded_documents_must_be_reproduced_by_the_fetch(self):
        output = Path("sbom/imports.json")
        recorded = {"profile": fetcher.IMPORTS_PROFILE, "imported_documents": [{"id": "dep-sbom", "url": DEP_URL + ".spdx.json", "sha256": "a" * 64}]}
        fetcher.check_recorded(None, [], output)
        fetcher.check_recorded(recorded, [{"id": "dep-sbom", "url": DEP_URL + ".spdx.json", "sha256": "a" * 64}, {"id": "new-sbom", "url": "https://e/n.spdx.json", "sha256": "c" * 64}], output)
        with self.assertRaisesRegex(ValueError, "changed: .*records a{64} at .*the fetch returned b{64}"):
            fetcher.check_recorded(recorded, [{"id": "dep-sbom", "url": DEP_URL + ".spdx.json", "sha256": "b" * 64}], output)
        with self.assertRaisesRegex(ValueError, "no longer publishes"):
            fetcher.check_recorded(recorded, [], output)
        with self.assertRaisesRegex(ValueError, "not a roc-automation-sbom-imports-v1"):
            fetcher.check_recorded({"imported_documents": []}, [], output)


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.workspace = self.root / "repo"
        (self.workspace / "sbom").mkdir(parents=True)
        self.blueprint = self.root / "blueprint"
        self.blueprint.write_text("#!/bin/sh\n[ \"$1 $2 $3\" = 'lock show --json' ] || exit 3\ncat \"$LOCK_REPORT\"\n")
        self.blueprint.chmod(0o755)
        self.lock = self.root / "lock.json"
        self.lock.write_text(json.dumps(LOCK))
        self.env = {
            "GITHUB_WORKSPACE": str(self.workspace), "GITHUB_OUTPUT": str(self.root / "outputs"), "GITHUB_STEP_SUMMARY": str(self.root / "summary.md"),
            "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "owner/repo", "INPUT_TARGET": "x86_64-linux", "INPUT_BLUEPRINT_BINARY": str(self.blueprint), "LOCK_REPORT": str(self.lock),
        }

    def run_action(self, network, comp=None, **extra):
        (self.workspace / "sbom/composition.json").write_text(json.dumps(comp or composition()))
        with patch.dict(os.environ, {**self.env, **extra}, clear=False):
            fetcher.main(download=network)
        return dict(line.split("=", 1) for line in (self.root / "outputs").read_text().splitlines())

    def imports(self):
        return json.loads((self.workspace / "sbom/imports.json").read_text())

    def test_published_sidecars_are_recorded_and_missing_ones_become_a_stub_that_fails(self):
        network = Published({DEP_URL + ".spdx.json": encode(spdx3())})
        with self.assertRaisesRegex(ValueError, r"1 locked dependency has no published SBOM: tool\. Complete the unavailable_sboms stub"):
            self.run_action(network)
        record = self.imports()
        self.assertEqual(record["profile"], "roc-automation-sbom-imports-v1")
        self.assertEqual(record["target"], "x86_64-linux")
        self.assertEqual([entry["id"] for entry in record["imported_documents"]], ["dep-sbom"])
        self.assertEqual(record["imported_documents"][0]["contact"], "https://github.com/owner/repo/security/policy")
        self.assertEqual(record["unavailable_sboms_stub"], [{"component": "tool", "reason": f"REPLACE: state why no supplier SBOM is published beside {TOOL_URL}", "contact": "REPLACE: who can resolve this gap, such as the supplier's security contact"}])
        self.assertEqual(record["unbindable"], [{"component": "tree", "detail": "source tree is pinned by a tree digest (nix:sha256-abc=), not a file SHA-256"}])
        self.assertEqual(network.requests, [DEP_URL + ".spdx.json", TOOL_URL + ".spdx.json", TOOL_URL + ".cdx.json"])
        outputs = dict(line.split("=", 1) for line in (self.root / "outputs").read_text().splitlines())
        self.assertEqual((outputs["fetched"], outputs["missing"], outputs["unbindable"], outputs["stale"]), ("1", "1", "1", "0"))
        summary = (self.root / "summary.md").read_text()
        self.assertIn("| `dep` | `source:dep` | fetched |", summary)
        self.assertIn("| `tool` | `program:tool:x86_64-linux` | missing |", summary)
        self.assertIn("| `tree` | `source:tree` | unbindable |", summary)

    def test_reviewed_opt_outs_pass_and_tree_pins_are_reported_as_unavailable_when_reviewed(self):
        comp = composition(unavailable_sboms=[{"component": "tool", "reason": "No SBOM for 2.0.0", "contact": "https://example.org/s"}, {"component": "tree", "reason": "Tree pin", "contact": "https://example.org/s"}])
        outputs = self.run_action(Published({DEP_URL + ".cdx.json": encode(cyclonedx())}), comp)
        self.assertEqual((outputs["fetched"], outputs["missing"], outputs["unbindable"], outputs["stale"]), ("1", "0", "1", "0"))
        summary = (self.root / "summary.md").read_text()
        self.assertIn("| `tool` | `program:tool:x86_64-linux` | unavailable | No SBOM for 2.0.0 (contact https://example.org/s) |", summary)
        self.assertIn("| `tree` | `source:tree` | unavailable |", summary)

    def test_a_stale_opt_out_fails_by_default_and_warns_on_request(self):
        comp = composition(components=composition()["components"][:1], unavailable_sboms=[{"component": "dep", "reason": "none", "contact": "https://example.org/s"}])
        network = Published({DEP_URL + ".spdx.json": encode(spdx3())})
        with self.assertRaisesRegex(ValueError, "unavailable_sboms lists dep, whose supplier now publishes"):
            self.run_action(network, comp)
        self.assertEqual(self.imports()["imported_documents"], [])
        outputs = self.run_action(network, comp, INPUT_STALE_OPT_OUTS="warn")
        self.assertEqual(outputs["stale"], "1")

    def test_a_dependency_already_imported_in_the_composition_is_not_fetched(self):
        comp = composition(components=composition()["components"][:1], imported_documents=[{"id": "dep-sbom", "subject": {"component": "dep"}}])
        network = Published({})
        outputs = self.run_action(network, comp)
        self.assertEqual(network.requests, [])
        self.assertEqual(outputs["fetched"], "0")

    def test_a_changed_supplier_document_is_never_adopted_silently(self):
        comp = composition(components=composition()["components"][:1])
        self.run_action(Published({DEP_URL + ".spdx.json": encode(spdx3())}), comp)
        first = self.imports()
        changed = spdx3()
        changed["@graph"].append({"type": "software_Package", "spdxId": NAMESPACE + "#extra", "name": "extra"})
        with self.assertRaisesRegex(ValueError, "the supplier's SBOM for dep-sbom changed"):
            self.run_action(Published({DEP_URL + ".spdx.json": encode(changed)}), comp)
        self.assertEqual(self.imports(), first)
        with self.assertRaisesRegex(ValueError, "no longer publishes"):
            self.run_action(Published({}), comp)
        self.assertEqual(self.imports(), first)

    def test_network_failures_other_than_absence_fail_rather_than_opting_out(self):
        def failing(url):
            raise fetcher.Refusal(f"fetching {url} failed with HTTP 503")
        comp = composition(components=composition()["components"][:1])
        with self.assertRaisesRegex(ValueError, "HTTP 503"):
            self.run_action(failing, comp)
        self.assertFalse((self.workspace / "sbom/imports.json").exists())

    def test_the_lock_report_must_be_format_2(self):
        self.lock.write_text(json.dumps({**LOCK, "format": 3}))
        with self.assertRaisesRegex(ValueError, "format 2"):
            self.run_action(Published({}))
        self.lock.write_text("not json")
        with self.assertRaisesRegex(ValueError, "not valid"):
            self.run_action(Published({}))

    def test_malformed_inputs_are_refused_before_the_lock_is_read(self):
        for name, value, reason in (
            ("INPUT_TARGET", "linux", "target must be"),
            ("INPUT_MISSING", "ignore", "missing must be"),
            ("INPUT_STALE_OPT_OUTS", "yes", "stale-opt-outs must be"),
            ("INPUT_CONTACT", "mailto:x", "contact must be"),
            ("INPUT_OUTPUT", "../imports.json", "output must be"),
            ("INPUT_BLUEPRINT_BINARY", str(self.root / "missing"), "blueprint-binary is not"),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, reason):
                self.run_action(Published({}), **{name: value})
        self.assertFalse((self.root / "outputs").exists())

    def test_a_composition_without_locked_dependencies_needs_no_lock_and_no_network(self):
        network = Published({})
        outputs = self.run_action(network, composition(components=[{"id": "own", "origin": "reviewed"}]), INPUT_BLUEPRINT_BINARY=str(self.blueprint), LOCK_REPORT=str(self.root / "absent"))
        self.assertEqual(outputs["fetched"], "0")
        self.assertEqual(network.requests, [])
        self.assertEqual(self.imports()["imported_documents"], [])


if __name__ == "__main__":
    unittest.main()
