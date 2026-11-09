"""Check actual admitted Core lookup with a reviewed native CLI, before providers.

CI supplies private XDG/IR roots before the setup action. This program uses those
same roots; it never installs a compiler, rewrites a source URL or copies a tree.
"""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("blueprint_core_native_installer", ROOT / "actions/setup-blueprint/setup_blueprint.py")
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)
CORE = INSTALLER.core_sources

CONFIG = '''app [config] { pf: platform "https://github.com/lukewilliamboswell/roc-blueprint/releases/download/0.12.0/GSb71LxYHqLohZz8jMfawWEwQcyev9ZdVC26sfsDwfq8.tar.zst" }
config = [Name("native-core-admission-smoke")]
'''


def main():
    original_environment = dict(os.environ)
    environment = CORE.child_environment(original_environment)
    binary = Path(os.environ["BLUEPRINT"])
    system, version = os.environ["BLUEPRINT_SYSTEM"], os.environ["BLUEPRINT_VERSION"]
    CORE.require(version == CORE.VERSION, "native Core smoke requires the reviewed 0.12 CLI")
    CORE.require(binary.is_absolute(), "native Core smoke requires the installed absolute CLI path")
    expected = INSTALLER.known_releases()[version][system]
    CORE.require(INSTALLER.sha256_of(binary) == expected, "native Core smoke CLI SHA256 mismatch")
    CORE.require(shutil.which("roc", path=environment.get("PATH")) is None,
                 "native Core smoke must precede compiler installation")
    CORE.require(shutil.which("nix", path=environment.get("PATH")) is None,
                 "native Core smoke must precede provider installation")
    CORE.require("XDG_CACHE_HOME" in environment and "ROC_CACHE_DIR" in environment,
                 "native Core smoke needs explicit private cache roots")
    packages = CORE.packages_directory(environment)
    tree, sidecar = packages / CORE.CONTENT_HASH, packages / (CORE.CONTENT_HASH + ".deps")
    CORE.verify_tree(tree)  # The actual setup action must already have admitted all 25 files.
    CORE.require(not os.path.lexists(sidecar), "native Core smoke requires a cold dependency sidecar")
    artifacts = CORE.safe_directory(Path(CORE.cache_home(environment)) / "blueprint/v1/artifacts")
    ir = CORE.safe_directory(environment["ROC_CACHE_DIR"])
    sentinels = []
    record = {"source": "known-blueprint-core", "version": version, "system": system,
              "cli_sha256": expected, "files": len(CORE.MEMBERS), "content_bytes": sum(row[1] for row in CORE.MEMBERS),
              "external_compiler": False, "provider": False, "commands": []}
    try:
        for directory in (packages, artifacts, ir):
            descriptor, name = tempfile.mkstemp(prefix=".unrelated-core-smoke-", dir=directory)
            with os.fdopen(descriptor, "wb") as output:
                output.write(b"preserve this unrelated cache entry\n")
            sentinels.append(Path(name))
        with tempfile.TemporaryDirectory(prefix="blueprint-core-config-", dir=os.environ["RUNNER_TEMP"]) as temporary:
            workspace = Path(temporary)
            (workspace / "Blueprint.roc").write_text(CONFIG, encoding="utf-8")
            for phase in ("cold-config-check", "recomputed-config-check"):
                started = time.monotonic()
                output = CORE.run_bounded([str(binary), "check"], 65536, 90, environment,
                                          "native Blueprint config check", cwd=workspace)
                record["commands"].append({"phase": phase, "exit": 0, "seconds": round(time.monotonic() - started, 3),
                                           "stdout_sha256": hashlib.sha256(output).hexdigest()})
                CORE.verify_tree(tree)
                CORE.require(CORE.checked_sidecar(sidecar) is not None,
                             "actual configuration did not produce the expected regular Core sidecar")
                if phase == "cold-config-check":
                    record["sidecar_after_config_sha256"] = hashlib.sha256(sidecar.read_bytes()).hexdigest()
                    admission = CORE.admit(version, original_environment, compiler=None)
                    CORE.require(admission["method"] == "verified-existing-tree"
                                 and admission["sidecar"]["invalidated"] and not os.path.lexists(sidecar),
                                 "Core revalidation did not invalidate only its derived sidecar")
                    record["revalidation"] = admission
        CORE.require(dict(os.environ) == original_environment, "native Core smoke changed the parent environment")
        CORE.require(all(path.read_bytes() == b"preserve this unrelated cache entry\n" for path in sentinels),
                     "native Core smoke changed an unrelated source, artifact or IR entry")
        record["preserved_source_artifact_ir_sentinels"] = True
        record["sidecar_recomputed"] = True
        record["outcome"] = "pass"
        destination = Path(os.environ["RUNNER_TEMP"]) / "blueprint-core-native-smoke.json"
        destination.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(record, sort_keys=True))
    finally:
        for path in sentinels:
            path.unlink(missing_ok=True)


def compiler_mode():
    original_environment = dict(os.environ)
    selected = shutil.which("roc")
    CORE.require(bool(selected), "explicit compiler smoke requires the separately installed fixed compiler")
    compiler = Path(selected).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="blueprint-core-compiler-mode-", dir=os.environ["RUNNER_TEMP"]) as temporary:
        root = Path(temporary)
        environment = CORE.child_environment(original_environment)
        environment["XDG_CACHE_HOME"], environment["ROC_CACHE_DIR"] = str(root / "source-cache"), str(root / "ir-cache")
        CORE.require(not (root / "source-cache").exists() and not (root / "ir-cache").exists(),
                     "explicit compiler smoke caches must begin fresh")
        started = time.monotonic()
        output = CORE.run_bounded([sys.executable, "-I", str(ROOT / "actions/setup-blueprint/core_sources.py"),
                                   "--version", CORE.VERSION, "--compiler", str(compiler)],
                                  65536, 180, environment, "explicit trusted-compiler Core CLI", cwd=root)
        admission = json.loads(output)
        packages = CORE.packages_directory(environment)
        CORE.verify_tree(packages / CORE.CONTENT_HASH)
        identity = admission["compiler"]
        CORE.require(admission["method"] == "trusted-roc-unbundle" and not admission["cache"]["reused"]
                     and admission["inventory"]["files"] == 25 and identity["path"] == str(compiler)
                     and identity["sha256"] == INSTALLER.sha256_of(compiler)
                     and identity["version"] == "Roc compiler version nightly-2026-11-07-ad3a5d2",
                     "explicit compiler smoke identity or full inventory mismatch")
        CORE.require(not os.path.lexists(packages / (CORE.CONTENT_HASH + ".deps")),
                     "data-only explicit compiler admission created a dependency sidecar")
        ir = root / "ir-cache"
        CORE.require(not ir.exists() or not list(ir.iterdir()), "data-only explicit compiler admission populated IR")
        CORE.require(dict(os.environ) == original_environment, "explicit compiler smoke changed the parent environment")
        record = {"outcome": "pass", "mode": "explicit-trusted-compiler", "source_and_ir_began_fresh": True,
                  "seconds": round(time.monotonic() - started, 3), "admission": admission,
                  "sidecar_created": False, "ir_populated": False, "parent_environment_preserved": True}
        (Path(os.environ["RUNNER_TEMP"]) / "blueprint-core-compiler-mode-smoke.json").write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(record, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler-mode", action="store_true", help="separate fixed-compiler admission gate after native CLI checks")
    options = parser.parse_args()
    compiler_mode() if options.compiler_mode else main()
