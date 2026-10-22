"""Exercise the SBOM sidecar and supplier-chain actions with the released tools.

The workflow copies `examples/sbom-chain/` to the workspace root and
installs the real Blueprint and the Roc release its SBOM tool needs. This runs
the two controllers against that fixture: the supplier's published SBOM is
fetched and bound, sidecars are written and checked, and then the refusals a
maintainer must see are provoked: a stale opt-out, a missing supplier SBOM, a
changed supplier document, and a sidecar left from an earlier build.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SIDECARS = ROOT / "actions/sbom-sidecars/sbom_sidecars.py"
FETCH = ROOT / "actions/fetch-dependency-sboms/fetch_dependency_sboms.py"
CORE_SBOM = "https://github.com/lukewilliamboswell/roc-blueprint/releases/download/0.7.0/roc-blueprint-core-9Z7WuswxTGgKd4i3cM5JDf5Kq9JqBpWoMxtWHALnjMcC.tar.zst.spdx.json"


def run(script, env, expect=0):
    outputs = Path(tempfile.mkdtemp(prefix="sbom-chain-", dir=os.environ["RUNNER_TEMP"])) / "outputs"
    result = subprocess.run([sys.executable, str(script)], env={**os.environ, "GITHUB_OUTPUT": str(outputs), "GITHUB_STEP_SUMMARY": "", **env}, cwd=ROOT, capture_output=True, text=True)
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    assert result.returncode == expect, f"{script.name} exited {result.returncode}, expected {expect}"
    values, lines, index = {}, outputs.read_text().splitlines() if outputs.exists() else [], 0
    while index < len(lines):
        if "<<" in lines[index] and "=" not in lines[index].split("<<", 1)[0]:
            key, delimiter = lines[index].split("<<", 1)
            index, collected = index + 1, []
            while lines[index] != delimiter:
                collected.append(lines[index])
                index += 1
            values[key] = "\n".join(collected)
        else:
            key, _, value = lines[index].partition("=")
            values[key] = value
        index += 1
    return result, values


def bundle(directory):
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir()
    subprocess.run(["roc", "bundle", "package/main.roc", "--output-dir", str(directory)], cwd=ROOT, check=True, capture_output=True, text=True)
    archives = sorted(directory.glob("*.tar.zst"))
    assert len(archives) == 1, "roc bundle writes one archive"
    return archives[0].relative_to(ROOT)


def main():
    workspace = ROOT
    assert (workspace / "sbom/composition.json").is_file() and (workspace / "package/main.roc").is_file(), "copy examples/sbom-chain to the workspace root first"
    composition = json.loads((workspace / "sbom/composition.json").read_text())
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    common = {"GITHUB_WORKSPACE": str(workspace), "GITHUB_SHA": commit, "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": os.environ.get("GITHUB_REPOSITORY", "lukewilliamboswell/roc-automation")}
    fetch_env = {**common, "INPUT_TARGET": "source", "INPUT_OUTPUT": "sbom/chain-imports.json"}

    # The supplier publishes an SBOM beside the locked file: it is fetched and bound.
    _, fetched = run(FETCH, fetch_env)
    assert (fetched["fetched"], fetched["missing"], fetched["unbindable"], fetched["stale"]) == ("1", "0", "0", "0"), fetched
    record = json.loads((workspace / "sbom/chain-imports.json").read_text())
    entry = record["imported_documents"][0]
    assert entry["url"] == CORE_SBOM and entry["subject"]["component"] == "blueprint-core" and entry["subject"]["version"] == "0.7.0"
    assert hashlib.sha256(entry["document"].encode()).hexdigest() == entry["sha256"]
    assert record["unavailable_sboms_stub"] == [] and record["unbindable"] == []

    # Re-fetching reproduces the recorded bytes, so the record is stable to commit.
    _, again = run(FETCH, fetch_env)
    assert again["fetched"] == "1" and json.loads((workspace / "sbom/chain-imports.json").read_text()) == record

    # A recorded document the supplier changed is never adopted silently.
    tampered = dict(record)
    tampered["imported_documents"] = [{**entry, "sha256": "0" * 64}]
    (workspace / "sbom/chain-imports-tampered.json").write_text(json.dumps(tampered))
    result, _ = run(FETCH, {**fetch_env, "INPUT_OUTPUT": "sbom/chain-imports-tampered.json"}, expect=1)
    assert "changed" in result.stderr and json.loads((workspace / "sbom/chain-imports-tampered.json").read_text()) == tampered

    # An opt-out for a dependency whose supplier publishes a sidecar is stale.
    stale = dict(composition)
    stale["unavailable_sboms"] = [{"component": "blueprint-core", "reason": "pretend none is published", "contact": "https://example.org/security"}]
    (workspace / "sbom/composition-stale.json").write_text(json.dumps(stale))
    result, values = run(FETCH, {**fetch_env, "INPUT_COMPOSITION": "sbom/composition-stale.json", "INPUT_OUTPUT": "sbom/chain-imports-stale.json"}, expect=1)
    assert "whose supplier now publishes a sidecar" in result.stderr and values["stale"] == "1"
    result, values = run(FETCH, {**fetch_env, "INPUT_COMPOSITION": "sbom/composition-stale.json", "INPUT_OUTPUT": "sbom/chain-imports-stale.json", "INPUT_STALE_OPT_OUTS": "warn"})
    assert values["stale"] == "1" and "::warning::" in result.stdout

    # Sidecars bound to the bundle's final bytes carry the supplier's graph under the import prefix.
    archive = bundle(workspace / "dist-chain")
    sidecar_env = {**common, "INPUT_FILES": str(archive), "INPUT_VERSION": "0.1.0", "INPUT_TARGET": "source", "INPUT_AUTHOR": "Organization: roc-automation contributors", "INPUT_IMPORTS": "sbom/chain-imports.json", "INPUT_SPDX_2_3": "true"}
    _, values = run(SIDECARS, sidecar_env)
    expected = [str(archive)] + [f"{archive}{suffix}" for suffix in (".cdx.json", ".spdx-2.3.json", ".spdx.json")]
    assert values["assets"].splitlines() == expected, values["assets"]
    assert values["compiler"] == "nightly-2026-10-21-5349569" and values["commit"] == commit
    digest = hashlib.sha256((workspace / archive).read_bytes()).hexdigest()
    spdx = json.loads((workspace / f"{archive}.spdx.json").read_text())
    subjects = [element for element in spdx["@graph"] if element.get("type") == "software_Package" and element["spdxId"].endswith("#Subject")]
    assert subjects and subjects[0]["verifiedUsing"][0]["hashValue"] == digest and subjects[0]["name"] == archive.name
    imported = [element["spdxId"].split("#", 1)[1] for element in spdx["@graph"] if element.get("type") == "software_Package" and "#blueprint-core-sbom-" in element["spdxId"]]
    assert "blueprint-core-sbom-Subject" in imported and len(imported) > 1, imported
    copied = [element for element in spdx["@graph"] if element.get("relationshipType") == "copiedTo"]
    assert copied and copied[0]["from"].endswith("#blueprint-core-sbom-Subject") and copied[0]["to"][0].endswith("#blueprint-core")
    cyclonedx = json.loads((workspace / f"{archive}.cdx.json").read_text())
    assert cyclonedx["metadata"]["component"]["hashes"][0]["content"] == digest
    listing = Path(values["digests"]).read_text().splitlines()
    assert len(listing) == 3 and all(line.split("  ", 1)[1] in expected[1:] for line in listing)
    for line in listing:
        recorded, name = line.split("  ", 1)
        assert hashlib.sha256((workspace / name).read_bytes()).hexdigest() == recorded

    # A sidecar left behind is stale: the second run refuses before the tool starts.
    result, _ = run(SIDECARS, sidecar_env, expect=1)
    assert "would be stale" in result.stderr

    # Without the supplier's SBOM and without a reviewed reason, inspect refuses and the action points at the fetch step.
    archive = bundle(workspace / "dist-chain")
    result, _ = run(SIDECARS, {**sidecar_env, "INPUT_IMPORTS": ""}, expect=1)
    assert "has no supplier SBOM" in result.stderr and "actions/fetch-dependency-sboms" in result.stderr
    assert sorted(path.name for path in (workspace / "dist-chain").iterdir()) == [archive.name]

    # A reviewed opt-out is accepted and becomes a visible incomplete scope.
    exempt = dict(composition)
    exempt["unavailable_sboms"] = [{"component": "blueprint-core", "reason": "Exercise the reviewed opt-out path", "contact": "https://example.org/security"}]
    (workspace / "sbom/composition-exempt.json").write_text(json.dumps(exempt))
    result, values = run(SIDECARS, {**sidecar_env, "INPUT_IMPORTS": "", "INPUT_COMPOSITION": "sbom/composition-exempt.json", "INPUT_SPDX_2_3": "false"})
    assert "unavailable: blueprint-core" in result.stdout and "incomplete: sbom:blueprint-core" in result.stdout
    assert values["assets"].splitlines() == [str(archive), f"{archive}.cdx.json", f"{archive}.spdx.json"]
    # The package-to-platform chain: the native executable's composition imports the
    # supplier's source bundle SBOM bound. The fetch action records the bundle's own
    # `source` target, and Blueprint 0.7.0 refuses that import into a native inventory
    # with exactly one message; the release carrying roc-blueprint#62 admits it, and
    # this check then flips to require success.
    system = {"Linux": {"X64": "x86_64-linux", "ARM64": "aarch64-linux"}, "macOS": {"ARM64": "aarch64-darwin"}}[os.environ["RUNNER_OS"]][os.environ["RUNNER_ARCH"]]
    bound = f"sbom/composition-{system}-bound.json"
    _, values = run(FETCH, {**common, "INPUT_TARGET": system, "INPUT_COMPOSITION": bound, "INPUT_OUTPUT": f"sbom/chain-imports-{system}-bound.json"})
    assert values["fetched"] == "1" and values["missing"] == "0"
    recorded = json.loads((workspace / f"sbom/chain-imports-{system}-bound.json").read_text())["imported_documents"][0]
    assert recorded["target"] == "source" and recorded["subject"]["component"] == "blueprint-core", recorded["target"]
    native = Path("dist-chain-native")
    if native.exists():
        shutil.rmtree(native)
    native.mkdir()
    subprocess.run(["roc", "build", "app/main.roc", f"--output={native}/hello"], cwd=ROOT, check=True, capture_output=True, text=True)
    result, _ = run(SIDECARS, {**common, "INPUT_FILES": f"{native}/hello", "INPUT_VERSION": "0.1.0", "INPUT_TARGET": system, "INPUT_AUTHOR": "Organization: roc-automation contributors", "INPUT_COMPOSITION": bound, "INPUT_IMPORTS": f"sbom/chain-imports-{system}-bound.json"}, expect=1)
    assert "import target differs from subject" in result.stderr, result.stderr
    assert sorted(path.name for path in native.iterdir()) == ["hello"]
    print("SBOM chain checks passed")


if __name__ == "__main__":
    main()
