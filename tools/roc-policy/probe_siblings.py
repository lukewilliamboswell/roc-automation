"""Exercise the binary against snapshots; never write to sibling repositories."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile


binary = Path(os.environ.get("ROC_POLICY_BINARY", "/tmp/roc-policy-dev")).resolve()
siblings = Path(__file__).resolve().parents[3]
cases = [
    ("roc-ansi", "examples", "ansi"),
    ("roc-parser", "examples", "parser"),
    ("roc-blueprint", "blueprint-cli", None),
]
for repository, directory, alias in cases:
    with tempfile.TemporaryDirectory(prefix="roc-policy-probe-") as temp:
        root = Path(temp)
        sources = sorted((siblings / repository / directory).rglob("*.roc"))
        if not sources:
            raise SystemExit(f"missing sibling source: {repository}/{directory}")
        digest = hashlib.sha256()
        for source in sources:
            relative = source.relative_to(siblings / repository)
            data = source.read_bytes()
            digest.update(str(relative).encode() + b"\0" + data)
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        rules = [f'{{kind: NoCompilerPin, scope: "{directory}"}}']
        if alias:
            rules.append(f'{{kind: Dependency, scope: "{directory}", name: "{alias}", value: "../package/main.roc"}}')
        else:
            rules.append('{kind: ForbiddenImport, scope: "blueprint-cli/main.roc", name: "nix.Locks"}')
        (root / "Blueprint.roc").write_text(
            f'repository_policy = {{schema_version: 1, roots: ["{directory}"], rules: ['
            + ",".join(rules) + "]}\n"
        )
        result = subprocess.run([str(binary), "check", "--root", str(root)],
                                capture_output=True, text=True,
                                env={"PATH": "/nonexistent"}, timeout=60)
        print(f"{repository}/{directory}: source-sha256={digest.hexdigest()}")
        print(result.stdout + result.stderr, end="")
        if result.returncode:
            raise SystemExit(result.returncode)
        # Demonstrate detection of a change to real, valid source, not only
        # agreement with the current repository. Mutate the temporary snapshot.
        if alias:
            target = root / sources[0].relative_to(siblings / repository)
            original = target.read_text()
            changed = original.replace('"../package/main.roc"', '"https://example.com/stale.tar.zst"', 1)
            if changed == original:
                raise SystemExit(f"probe did not find the expected dependency in {target.name}")
            target.write_text(changed)
            failed = subprocess.run([str(binary), "check", "--root", str(root)],
                                    capture_output=True, text=True,
                                    env={"PATH": "/nonexistent"}, timeout=60)
            if failed.returncode != 1 or "project/dependency" not in failed.stdout:
                raise SystemExit("mutated dependency was not detected: " + failed.stdout + failed.stderr)
            print("Changed snapshot dependency: correctly rejected")
