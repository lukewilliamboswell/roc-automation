"""Local downstream corpus preparation and HTML reporting; never run a compiler.

The Roc entrypoint owns compiler execution and observations. This helper uses
only the Python standard library, plus gh (when logged in) or curl for public
release downloads, and the shared examples ZIP admission code.
"""
import contextlib
import datetime
import hashlib
import html
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]
MAX_DOWNLOAD = 50_000_000
VERSION = re.compile(r"v?\d+\.\d+\.\d+\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
REPO_NAME = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
NIGHTLY = re.compile(r"nightly-\d{4}-\d{2}-\d{2}-[0-9a-f]{7,40}\Z")


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as incoming:
        for chunk in iter(lambda: incoming.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def absolute(value, base):
    if not isinstance(value, str) or not value:
        raise ValueError("paths must be nonempty strings")
    path = Path(value).expanduser()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def load_config(source):
    source = Path(source).expanduser().resolve()
    with source.open(encoding="utf-8") as incoming:
        config = json.load(incoming)
    if not isinstance(config, dict) or set(config) - {"nightlies", "cache_dir", "output"}:
        raise ValueError("configuration accepts only nightlies, cache_dir and output")
    paths = config.get("nightlies")
    if not isinstance(paths, list) or not paths or any(not isinstance(p, str) or not p for p in paths):
        raise ValueError("nightlies must be a nonempty list of installed Roc executable paths")
    return {
        "nightlies": [str(absolute(path, source.parent)) for path in paths],
        "cache": str(absolute(config.get("cache_dir", ".downstream-cache"), source.parent)),
        "output": str(absolute(config.get("output", "downstream-report.html"), source.parent)),
    }


def atomic_json(destination, value):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                         prefix="." + destination.name + ".", delete=False) as output:
            temporary = Path(output.name)
            json.dump(value, output, ensure_ascii=True, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class GitHub:
    def __init__(self):
        self.authenticated = False
        if shutil.which("gh"):
            self.authenticated = subprocess.run(
                ["gh", "auth", "status"], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=15, check=False).returncode == 0
        if not self.authenticated and not shutil.which("curl"):
            raise ValueError("install curl or authenticate gh to download published examples")

    def command(self, endpoint, binary=False):
        if self.authenticated:
            return ["gh", "api", "--method", "GET", "-H",
                    "Accept: application/octet-stream" if binary else "Accept: application/vnd.github+json",
                    endpoint]
        return ["curl", "--fail", "--silent", "--show-error", "--location",
                "--retry", "2", "--max-time", "120", "--max-filesize", str(MAX_DOWNLOAD),
                "--header", "Accept: application/octet-stream" if binary else
                "Accept: application/vnd.github+json", "https://api.github.com/" + endpoint]

    def metadata(self, endpoint):
        result = subprocess.run(self.command(endpoint), capture_output=True, timeout=130, check=False)
        if result.returncode:
            raise ValueError("GitHub metadata request failed for " + endpoint)
        return json.loads(result.stdout)

    def download(self, repository, asset, destination):
        endpoint = f"repos/{repository}/releases/assets/{asset['id']}"
        with Path(destination).open("wb") as output:
            result = subprocess.run(self.command(endpoint, binary=True), stdout=output,
                                    stderr=subprocess.PIPE, timeout=130, check=False)
        if result.returncode:
            raise ValueError("GitHub examples download failed")


def latest_release(client, repository):
    releases = []
    for page in range(1, 21):
        found = client.metadata(f"repos/{repository}/releases?per_page=100&page={page}")
        if not isinstance(found, list):
            raise ValueError("invalid release listing")
        releases.extend(release for release in found if not release.get("draft") and
                        not release.get("prerelease") and VERSION.fullmatch(release.get("tag_name", "")))
        if len(found) < 100:
            break
    else:
        raise ValueError("release listing exceeds 2000 entries")
    if not releases:
        raise ValueError("no published final product release")
    return max(releases, key=lambda release: release.get("published_at") or "")


def examples_asset(release, package):
    name = package + "-examples-" + release["tag_name"] + ".zip"
    assets = [asset for asset in release.get("assets", []) if asset.get("name") == name]
    if len(assets) != 1:
        raise ValueError("latest product release lacks one " + name)
    asset = assets[0]
    if (not isinstance(asset.get("id"), int) or asset["id"] <= 0 or
            not isinstance(asset.get("size"), int) or not 0 < asset["size"] <= MAX_DOWNLOAD or
            not DIGEST.fullmatch(asset.get("digest") or "")):
        raise ValueError("examples asset needs an ID, a size at most 50 MB and a SHA-256 digest")
    # GitHub also returns mutable counters and timestamps. These do not change
    # the corpus identity and must not invalidate otherwise reusable observations.
    return {key: asset[key] for key in ("id", "name", "size", "digest")}


def cached_asset(client, cache, repository, asset):
    directory = Path(cache) / "archives"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (asset["digest"].removeprefix("sha256:") + ".zip")
    expected = asset["digest"].removeprefix("sha256:")
    if path.is_file() and not path.is_symlink() and path.stat().st_size == asset["size"] and file_hash(path) == expected:
        return path
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix="download-", delete=False) as output:
            temporary = Path(output.name)
        client.download(repository, asset, temporary)
        if temporary.stat().st_size != asset["size"] or file_hash(temporary) != expected:
            raise ValueError("examples download size or SHA-256 differs from release metadata")
        os.replace(temporary, path)
        return path
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def extract(archive, asset, work):
    source = REPOSITORY / "actions/release-compatibility/archive.py"
    spec = importlib.util.spec_from_file_location("downstream_archive", source)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    destination = Path(work) / ("archive-" + asset["digest"][7:23])
    root = asset["name"].removesuffix(".zip")
    # The archive helper prints its inert inventory; stdout here is the manifest.
    with contextlib.redirect_stdout(io.StringIO()):
        helper.prepare(archive, asset["digest"], root, destination)
    return destination / root


def relative_path(value):
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        raise ValueError("example paths must be relative")
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError("example path escapes its archive")
    return value


def case_record(case):
    if case.get("stderr_match", "exact") != "exact":
        raise ValueError("selected case needs unsupported stderr comparison semantics")
    skip = case.get("skip") or {}
    if not isinstance(skip, dict):
        raise ValueError("case skip must have a reason and issue")
    result = {"name": case.get("name", ""), "args": case.get("args", []),
              "stdin": case.get("stdin", ""), "stdout": case.get("stdout", ""),
              "stderr": case.get("stderr", ""), "exit_code": case.get("exit_code", 0),
              "skip": " ".join(str(skip.get(key, "")) for key in ("reason", "issue")).strip()}
    if (not all(isinstance(result[key], str) for key in ("name", "stdin", "stdout", "stderr")) or
            not result["name"] or not isinstance(result["args"], list) or
            not all(isinstance(arg, str) for arg in result["args"]) or
            type(result["exit_code"]) is not int or not -(2**31) <= result["exit_code"] < 2**31):
        raise ValueError("invalid example case")
    return result


def base_app(repository, entry, note="", skip="", release="", date=""):
    path = entry.get("path", "")
    return {"id": repository + (":" + path if path else ""), "repository": repository,
            "release": release, "date": date, "root": "", "path": path,
            "cwd": ".", "cases": [], "note": note, "skip": skip,
            "expectations": validate_expectations(entry.get("expectations", []))}


def validate_expectations(values):
    if not isinstance(values, list):
        raise ValueError("example expectations must be a list")
    checked, seen = [], set()
    for value in values:
        if (not isinstance(value, dict) or set(value) - {"nightly", "stage", "code", "note", "case_name"} or
                not isinstance(value.get("nightly"), str) or not NIGHTLY.fullmatch(value["nightly"]) or
                value.get("stage") not in ("check", "test", "build", "run", "binary") or
                type(value.get("code")) is not int or not -(2**31) <= value["code"] < 2**31 or
                not isinstance(value.get("note"), str) or not value["note"].strip() or
                not isinstance(value.get("case_name", ""), str)):
            raise ValueError("expectation needs an exact nightly tag, stage, Int32 code and explanatory note")
        datetime.date.fromisoformat(value["nightly"][8:18])
        case_name = value.get("case_name", "")
        if case_name and value["stage"] not in ("run", "binary"):
            raise ValueError("case_name applies only to run or binary expectations")
        identity = (value["nightly"], value["stage"], case_name)
        if identity in seen:
            raise ValueError("duplicate nightly, stage and case expectation")
        seen.add(identity)
        checked.append({**value, "case_name": case_name})
    return checked


def prepare(config_source, catalog_source=None, client=None):
    config = load_config(config_source)
    cache = Path(config["cache"])
    cache.mkdir(parents=True, exist_ok=True)
    with Path(catalog_source or HERE / "index.json").open(encoding="utf-8") as incoming:
        catalog = json.load(incoming)
    if catalog.get("schema_version") != 1 or not isinstance(catalog.get("repositories"), list):
        raise ValueError("app index must use schema_version 1 and a repositories list")
    work = Path(tempfile.mkdtemp(prefix="run-", dir=cache))
    apps, identities = [], []
    for project in catalog["repositories"]:
        repository = project.get("repository", "")
        if not REPO_NAME.fullmatch(repository):
            raise ValueError("invalid indexed repository")
        entries = project.get("examples") or [{}]
        # Invalid local declarations are configuration errors, rather than
        # downstream skips silently obscured inside a broad preparation run.
        for entry in entries:
            validate_expectations(entry.get("expectations", []))
        release, date = "", ""
        note = project.get("note", "")
        if project.get("skip"):
            apps.extend(base_app(repository, entry, note, project["skip"]) for entry in entries)
            continue
        try:
            if client is None:
                client = GitHub()
            selected = latest_release(client, repository)
            release = selected["tag_name"]
            date = (selected.get("published_at") or "")[:10]
            asset = examples_asset(selected, project.get("package", repository.split("/")[1]))
            identities.append({"repository": repository, "release_id": selected.get("id"),
                               "release": release, "date": date, "asset": asset})
            archive = cached_asset(client, cache, repository, asset)
            root = extract(archive, asset, work)
            with (root / "spec.json").open(encoding="utf-8") as incoming:
                suite = json.load(incoming)
            if suite.get("schema_version") != 1 or suite.get("release") != release:
                raise ValueError("examples specification does not match selected release")
            dependencies = suite.get("dependencies")
            prefix = f"https://github.com/{repository}/releases/download/{release}/"
            if (not isinstance(dependencies, list) or not dependencies or
                    not all(isinstance(url, str) and url.startswith(prefix) and
                            url.endswith(".tar.zst") and relative_path(url[len(prefix):]) for url in dependencies)):
                raise ValueError("examples must name immutable bundles from the selected release")
            published_apps = suite.get("apps")
            if not isinstance(published_apps, list) or not published_apps or len(published_apps) > 100:
                raise ValueError("examples specification needs 1–100 apps")
            paths = [app.get("path") for app in published_apps]
            if len(set(paths)) != len(paths):
                raise ValueError("duplicate published app paths")
            for entry in entries:
                app = base_app(repository, entry, " ".join(filter(None, [note, entry.get("note", "")])),
                               entry.get("skip", ""), release, date)
                app["root"] = str(root)
                path = relative_path(entry["path"])
                app["cwd"] = entry.get("cwd", str(Path(path).parent))
                if app["cwd"] != ".":
                    relative_path(app["cwd"])
                found = next((value for value in published_apps if value.get("path") == path), None)
                if found is None:
                    app["skip"] = app["skip"] or "selected app is absent from latest release examples"
                elif (found.get("target", "native") != "native" or found.get("build_args") or
                      (found.get("run") and not (entry.get("cwd") == "." and found["run"] ==
                       ["python3", "{suite}/run-example.py", "{suite}", "{executable}"]))):
                    app["skip"] = app["skip"] or "example needs a non-native target, runtime wrapper or specialized build"
                elif not (root / path).is_file():
                    app["skip"] = app["skip"] or "selected app source is absent from examples archive"
                else:
                    cases = found.get("cases")
                    if not isinstance(cases, list) or not 1 <= len(cases) <= 100:
                        raise ValueError("app requires 1–100 cases")
                    app["cases"] = [case_record(case) for case in cases]
                    if len({case["name"] for case in app["cases"]}) != len(app["cases"]):
                        raise ValueError("duplicate example case names")
                apps.append(app)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
            # A missing release or prerequisite is a visible coverage gap, not a
            # reason to stop preparing independent downstream repositories.
            known = {app["id"] for app in apps}
            for entry in entries:
                app = base_app(repository, entry, note, str(error), release, date)
                if app["id"] not in known:
                    apps.append(app)
    nightlies = []
    for path in config["nightlies"]:
        try:
            digest = file_hash(path)
        except OSError:
            digest = "unavailable"
        nightlies.append({"path": path, "sha256": digest})
    stable_apps = [{key: value for key, value in app.items()
                    if key != "root" and (key != "expectations" or value)} for app in apps]
    corpus_hash = hashlib.sha256(json.dumps({"catalog": catalog, "releases": identities,
                                            "apps": stable_apps}, sort_keys=True).encode()).hexdigest()
    manifest = {"cache": str(cache), "output": config["output"], "nightlies": nightlies,
                "apps": apps, "host": f"{platform.system()} {platform.machine()} {platform.release()}",
                "observations": str(cache / "observations.json"), "corpus_hash": corpus_hash}
    atomic_json(cache / "manifest.json", manifest)
    return manifest


def copy_workspace(source, destination, cache):
    source = Path(source).resolve()
    destination = Path(os.path.abspath(destination))
    allowed = Path(cache).resolve() / "work"
    try:
        parts = destination.relative_to(allowed).parts
    except ValueError:
        raise ValueError("workspace destination must be below cache/work") from None
    if len(parts) < 2:
        raise ValueError("workspace destination must include compiler and app directories")
    checked = allowed
    for part in parts:
        if checked.is_symlink():
            raise ValueError("workspace destination crosses a symbolic link")
        checked /= part
    if checked.is_symlink():
        raise ValueError("workspace destination is a symbolic link")
    if (not source.is_dir() or source == destination or source in destination.parents or
            destination in source.parents):
        raise ValueError("workspace copy requires a directory and a separate fresh destination")
    for root, directories, files in os.walk(source):
        if any((Path(root) / name).is_symlink() for name in directories + files):
            raise ValueError("workspace source contains a symbolic link")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)


def record_result(record):
    status = record.get("status", "missing")
    if status != "exited":
        return status
    if record.get("code") != record.get("expected_code", 0):
        return "exit " + str(record.get("code"))
    if record.get("stage") in ("run", "binary"):
        if record.get("stdout") != record.get("expected_stdout", ""):
            return "stdout differs"
        if record.get("stderr") != record.get("expected_stderr", ""):
            return "stderr differs"
    return "pass"


def result_class(result):
    if result == "pass":
        return "pass"
    if result in ("skipped", "not-applicable", "missing", "not observed"):
        return "missing"
    return "failure"


def compiler_identity(record):
    return record.get("compiler_path", ""), record.get("compiler_hash", "")


def expected_record(record, app):
    # NIGHTLY is anchored for validation; version output also contains a prefix.
    nightly = re.search(r"(?<![A-Za-z0-9_-])(nightly-\d{4}-\d{2}-\d{2}-[0-9a-f]{7,40})(?![A-Za-z0-9_-])",
                        record.get("version", ""))
    if nightly is None:
        return record
    matches = [expectation for expectation in app.get("expectations", [])
               if expectation["nightly"] == nightly.group(1) and expectation["stage"] == record.get("stage") and
               (not expectation["case_name"] or expectation["case_name"] == record.get("case_name", ""))]
    if not matches:
        return record
    match = max(matches, key=lambda expectation: bool(expectation["case_name"]))
    note = f"Declared expectation for {match['nightly']} {match['stage']}: {match['note']}"
    return {**record, "expected_code": match["code"],
            "note": "\n".join(filter(None, [record.get("note", ""), note]))}


def overview(app_ids, app_index, by_app, compilers, ordered, escape):
    fragments = ["<h2>Examples × compiler builds</h2>",
                 '<p><label>Repository or example <input id="search" type="search"></label> ',
                 '<label>Result <select id="status"><option value="">All</option><option value="pass">Pass</option>',
                 '<option value="failure">Failure</option><option value="missing">Skipped / unobserved</option></select></label></p>',
                 '<table id="overview"><thead><tr><th>Example / release</th>']
    for identity in ordered:
        date, version, path = compilers[identity]
        fragments.append("<th>" + escape(date or version or "unavailable") + "<br><small>" +
                         escape(version) + "<br>" + escape(identity[1][:12]) + "<br>" + escape(path) + "</small></th>")
    fragments.append("</tr></thead><tbody>")
    for app_number, app_id in enumerate(app_ids):
        app = app_index.get(app_id, {})
        observations = by_app.get(app_id, [])
        cells, classes = [], set()
        for identity in ordered:
            latest = {}
            for _, record in observations:
                if compiler_identity(record) == identity:
                    latest[(record.get("stage", ""), record.get("case_name", ""))] = record
            if not latest:
                text, kind = "not observed", "missing"
            elif all(record_result(record) in ("skipped", "not-applicable") for record in latest.values()):
                text = "; ".join(sorted({record.get("note") or record_result(record) for record in latest.values()}))
                kind = "missing"
            else:
                summaries, results = [], []
                for stage in ("check", "test", "build", "run", "binary"):
                    selected = [record_result(record) for (name, _), record in latest.items() if name == stage]
                    if selected:
                        results.extend(selected)
                        summaries.append(stage + ": " + (selected[0] if len(selected) == 1 else
                                         f"{selected.count('pass')}/{len(selected)} pass"))
                required = {(stage, "") for stage in ("check", "test", "build")}
                required.update((stage, case["name"]) for case in app.get("cases", [])
                                for stage in ("run", "binary"))
                if not required.issubset(latest):
                    results.append("not observed")
                    summaries.append("incomplete observations")
                text = " · ".join(summaries) or "; ".join(sorted({record_result(record) for record in latest.values()}))
                kind = "pass" if results and all(result == "pass" for result in results) else "failure"
            classes.add(kind)
            cells.append('<td class="' + kind + '"><a href="#app-' + str(app_number) + '">' + escape(text) + "</a></td>")
        if not ordered:
            classes.add("missing")
        fragments.append('<tr data-search="' + escape(app_id.lower()) + '" data-status="' +
                         " ".join(sorted(classes)) + '"><th><a href="#app-' + str(app_number) + '">' +
                         escape(app_id) + "</a><br><small>" + escape(app.get("release", "")) +
                         " · " + escape(app.get("date", "")) + "</small>" +
                         ("<br>SKIP: " + escape(app["skip"]) if app.get("skip") else "") + "</th>" + "".join(cells) + "</tr>")
    fragments.append("</tbody></table>")
    fragments.append("""<script>
const search = document.getElementById('search');
const status = document.getElementById('status');
function filterMatrix() {
  const query = search.value.toLowerCase();
  for (const row of document.querySelectorAll('#overview tbody tr')) {
    row.hidden = !row.dataset.search.includes(query) ||
      (status.value !== '' && !row.dataset.status.split(' ').includes(status.value));
  }
}
search.addEventListener('input', filterMatrix);
status.addEventListener('change', filterMatrix);
</script>""")
    return fragments


def render(config_source, observation_source):
    config = load_config(config_source)
    with Path(observation_source).open(encoding="utf-8") as incoming:
        records = json.load(incoming)
    if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
        raise ValueError("observations must be a JSON list of records")
    manifest_path = Path(config["cache"]) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    if manifest:
        prefix = manifest["corpus_hash"] + "|" + manifest["host"] + "|"
        configured = {(compiler["path"], compiler["sha256"]) for compiler in manifest["nightlies"]}
        records = [record for record in records if record.get("key", "").startswith(prefix) and
                   compiler_identity(record) in configured]
    app_index = {app["id"]: app for app in manifest.get("apps", [])}
    records = [expected_record(record, app_index.get(record.get("app_id", ""), {})) for record in records]
    escape = lambda value: html.escape(str(value), quote=True)
    compilers = {}
    for record in records:
        identity = compiler_identity(record)
        compilers[identity] = (record.get("nightly_date", ""), record.get("version", ""),
                               record.get("compiler_path", ""))
    ordered = sorted(compilers, key=lambda identity: (compilers[identity][0], compilers[identity][1], identity))
    app_ids = list(app_index)
    app_ids.extend(sorted({record.get("app_id", "") for record in records} - set(app_ids) - {""}))
    by_app = {}
    for number, record in enumerate(records):
        by_app.setdefault(record.get("app_id", ""), []).append((number, record))
    fragments = ["<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">",
                 "<meta name=\"viewport\" content=\"width=device-width\"><title>Roc downstream regressions</title>",
                 "<style>body{font:15px system-ui;margin:2rem;color:#20252b;background:#fff}h1{font-size:1.7rem}",
                 "table{border-collapse:collapse;display:block;overflow:auto}th,td{border:1px solid #aeb8c1;padding:.5rem;text-align:left;vertical-align:top}",
                 ".pass{background:#d9f4de}.failure{background:#ffe2dc}.missing{background:#f0f0f0}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:30rem;overflow:auto}",
                 "code{overflow-wrap:anywhere}details{margin:.5rem 0}summary{cursor:pointer}small{font-weight:normal}</style>",
                 "<h1>Roc downstream regressions</h1>",
                 "<p>Report only. Each result is an observation on the recorded host. A pass covers the selected command and examples. ",
                 "Skips and unavailable compilers provide no compatibility evidence. Adjacent failures narrow a change to the observed builds; ",
                 "they do not identify a compiler commit as the cause.</p>",
                 "<p>Host: <code>" + escape(manifest.get("host", "unrecorded")) + "</code>. Generated: " +
                 escape(datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")) + "</p>",
                 "<p>Test runs exercise app and local-module expectations. Roc does not run expectations inside URL dependencies.</p>"]
    if not records:
        fragments.append("<p>No compiler observations have been recorded.</p>")
    fragments.extend(overview(app_ids, app_index, by_app, compilers, ordered, escape))
    for app_number, app_id in enumerate(app_ids):
        app = app_index.get(app_id, {})
        observations = by_app.get(app_id, [])
        fragments.append('<h2 id="app-' + str(app_number) + '">' + escape(app_id) + "</h2><p>Release: " + escape(app.get("release", "")) +
                         " · Published: " + escape(app.get("date", "")) + "</p>")
        if app.get("note"):
            fragments.append("<p>" + escape(app["note"]) + "</p>")
        if app.get("skip"):
            fragments.append("<p><strong>SKIP:</strong> " + escape(app["skip"]) + "</p>")
        if not observations:
            continue
        stages = sorted({(record.get("stage", ""), record.get("case_name", "")) for _, record in observations},
                        key=lambda item: (["version", "check", "test", "build", "run", "binary"].index(item[0])
                                          if item[0] in ["version", "check", "test", "build", "run", "binary"] else 9, item[1]))
        fragments.append("<table><thead><tr><th>Command / case</th>")
        for identity in ordered:
            date, version, path = compilers[identity]
            fragments.append("<th>" + escape(date or version or "unavailable") + "<br><small>" +
                             escape(version) + "<br>" + escape(identity[1][:12]) + "<br>" + escape(path) + "</small></th>")
        fragments.append("</tr></thead><tbody>")
        for stage, case_name in stages:
            fragments.append("<tr><th>" + escape(stage + (" / " + case_name if case_name else "")) + "</th>")
            previous = None
            for identity in ordered:
                matches = [(number, record) for number, record in observations
                           if compiler_identity(record) == identity and record.get("stage") == stage and
                           record.get("case_name", "") == case_name]
                if not matches:
                    fragments.append('<td class="missing">not observed</td>')
                    previous = None
                    continue
                number, record = matches[-1]
                result = record_result(record)
                changed = " · changed" if previous is not None and previous != result else ""
                previous = result
                fragments.append('<td class="' + result_class(result) + '"><a href="#record-' +
                                 str(number) + '">' + escape(result + changed) + "</a></td>")
            fragments.append("</tr>")
        fragments.append("</tbody></table>")
        for number, record in observations:
            fragments.append('<details id="record-' + str(number) + '"><summary>' + escape(
                record.get("version", record.get("compiler_path", "")) + " · " + record.get("stage", "") +
                " " + record.get("case_name", "") + " · " + record_result(record)) + "</summary>")
            for field in ("argv", "cwd", "status", "code", "signal", "duration_ms", "note", "stdin",
                          "expected_code", "expected_stdout", "expected_stderr", "stdout", "stderr"):
                value = record.get(field, "")
                if isinstance(value, list):
                    value = json.dumps(value, ensure_ascii=False)
                fragments.append("<strong>" + escape(field) + "</strong><pre>" + escape(value) + "</pre>")
            fragments.append("</details>")
    fragments.append("</html>\n")
    destination = Path(config["output"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("".join(fragments), encoding="utf-8")
    return {"output": str(destination), "observations": len(records)}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(argv) == 2 and argv[0] == "prepare":
            print(json.dumps(prepare(argv[1])))
        elif len(argv) == 3 and argv[0] == "render":
            print(json.dumps(render(argv[1], argv[2])))
        elif len(argv) == 2 and argv[0] == "save":
            records = json.load(sys.stdin)
            if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
                raise ValueError("observations must be a JSON list of records")
            atomic_json(argv[1], records)
        elif len(argv) == 4 and argv[0] == "copy":
            copy_workspace(argv[1], argv[2], argv[3])
        else:
            raise ValueError("usage: support.py prepare CONFIG | render CONFIG OBSERVATIONS | save OBSERVATIONS | copy ROOT DEST CACHE")
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print("downstream regressions: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
