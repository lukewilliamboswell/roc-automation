#!/usr/bin/env python3
"""Stage a manual with a precompiled Roc renderer or explicit legacy backend."""

from __future__ import annotations

import argparse
import copy
import json
import tempfile
import os
import shutil
import subprocess
from pathlib import Path


ACTION_ROOT = Path(__file__).resolve().parent
DEFAULT_THEME = ACTION_ROOT / "theme"
IMAGE = "roc-automation-docs:local"


def run(*command: str, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def contained_path(root: Path, value: str, label: str, *, must_exist: bool = False) -> Path:
    if not value or Path(value).is_absolute():
        raise SystemExit(f"{label} must be a non-empty relative path")
    relative = Path(value)
    if any(part in ("..", ".git") for part in relative.parts) or any(c in value for c in "\r\n\0"):
        raise SystemExit(f"{label} contains an unsafe path component")
    result = root / relative
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise SystemExit(f"{label} must not traverse a symlink")
    result = result.resolve()
    if not result.is_relative_to(root):
        raise SystemExit(f"{label} must stay inside the repository")
    if must_exist and not result.exists():
        raise SystemExit(f"{label} does not exist: {result}")
    return result


def copy_static_files(docs: Path, site: Path, theme: Path) -> None:
    for source in sorted(docs.iterdir()):
        if source.suffix == ".adoc" or source.resolve() == theme.resolve():
            continue
        destination = site / source.name
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        elif source.is_file():
            shutil.copy2(source, destination)


def build_inside_container(args: argparse.Namespace, workspace: Path) -> tuple[Path, Path]:
    docs = contained_path(workspace, args.docs_directory, "docs-directory", must_exist=True)
    if not docs.is_dir():
        raise SystemExit("docs-directory must name a directory")
    entrypoint = contained_path(docs, args.entrypoint, "entrypoint", must_exist=True)
    if entrypoint.suffix != ".adoc":
        raise SystemExit("entrypoint must be an AsciiDoc file")
    output = contained_path(workspace, args.output_directory, "output-directory")
    theme = (DEFAULT_THEME if not args.theme_directory else
             contained_path(workspace, args.theme_directory, "theme-directory", must_exist=True))
    for required in ("docs.css", "docs-theme.yml", "mermaid-config.json", "fonts"):
        if not (theme / required).exists():
            raise SystemExit(f"theme is missing {required}")

    site = output / "site"
    if site.exists():
        shutil.rmtree(site)
    site.mkdir(parents=True)

    diagram = [
        "-r", "asciidoctor-diagram",
        "-r", str(ACTION_ROOT / "rouge_roc.rb"),
        "-a", "mermaid-format=svg",
        "-a", "mermaid-background=FAFAF7",
        "-a", "mermaid-scale=2",
        "-a", f"mermaid-config={theme / 'mermaid-config.json'}",
        "-a", f"mermaid-puppeteer-config={ACTION_ROOT / 'mermaid-puppeteer.json'}",
    ]
    common = [
        "-a", f"docs-version={args.docs_version}",
        "-a", "idprefix=", "-a", "idseparator=-",
        "--failure-level", "WARN",
    ]
    html_theme = ["-a", f"stylesdir={theme}", "-a", "stylesheet=docs.css"]
    sources = sorted(docs.glob("*.adoc"))
    if entrypoint not in sources:
        raise SystemExit("entrypoint must be a top-level AsciiDoc file")
    for source in sources:
        run(
            "asciidoctor", *diagram, *html_theme, *common,
            "-a", "source-highlighter=rouge", "-a", "toc=left", "-a", "sectanchors",
            "-D", str(site), str(source), cwd=workspace,
        )

    copy_static_files(docs, site, theme)
    shutil.copytree(theme / "fonts", site / "fonts", dirs_exist_ok=True)
    index = site / "index.html"
    if not index.is_file() or "Roc documentation theme" not in index.read_text(encoding="utf-8"):
        raise SystemExit("the documentation entrypoint did not use the shared stylesheet")

    pdf = output / args.pdf_filename
    if Path(args.pdf_filename).name != args.pdf_filename or not args.pdf_filename.endswith(".pdf"):
        raise SystemExit("pdf-filename must be a plain .pdf filename")
    run(
        "asciidoctor-pdf", *diagram[:-2], *common,
        "-a", "source-highlighter=rouge", "-a", "rouge-style=rocgui",
        "-a", f"pdf-themesdir={theme}", "-a", "pdf-theme=docs",
        "-a", "toclevels=2", "-a", f"pdf-fontsdir={theme / 'fonts'};GEM_FONTS_DIR",
        "-a", "mermaid-format=png",
        "-a", f"mermaid-puppeteer-config={ACTION_ROOT / 'mermaid-puppeteer.json'}",
        "-o", str(pdf), str(entrypoint), cwd=workspace,
    )
    if not pdf.is_file() or not pdf.stat().st_size:
        raise SystemExit("PDF manual was not generated")
    shutil.copy2(pdf, site / pdf.name)
    return site, pdf


def build_api(args: argparse.Namespace, workspace: Path, site: Path) -> None:
    if not args.api_entrypoint:
        return
    entrypoint = contained_path(workspace, args.api_entrypoint, "api-entrypoint", must_exist=True)
    executable = shutil.which(args.roc_command)
    if executable is None:
        raise SystemExit(f"Roc executable was not found: {args.roc_command}")
    api = site / "api"
    if api.exists():
        shutil.rmtree(api)
    run(executable, "docs", str(entrypoint), f"--output={api}", cwd=workspace)
    if not (api / "index.html").is_file():
        raise SystemExit("Roc API reference was not generated")


def write_outputs(site: Path, pdf: Path) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(f"site-directory={site}\npdf-path={pdf}\n")
    print(f"Site: {site / 'index.html'}")
    print(f"Manual: {pdf}")


def reject_symlinks(root: Path) -> None:
    for item in root.rglob("*"):
        if item.is_symlink():
            raise SystemExit(f"documentation tree contains a symlink: {item}")
        if not item.is_file() and not item.is_dir():
            raise SystemExit(f"documentation tree contains a special file: {item}")


def validate(args: argparse.Namespace, workspace: Path) -> tuple[Path, Path]:
    docs = contained_path(workspace, args.docs_directory, "docs-directory", must_exist=True)
    if not docs.is_dir():
        raise SystemExit("docs-directory must name a directory")
    reject_symlinks(docs)
    entrypoint = contained_path(docs, args.entrypoint, "entrypoint", must_exist=True)
    if not entrypoint.is_file() or entrypoint.parent != docs:
        raise SystemExit("entrypoint must be a top-level documentation file")
    output = contained_path(workspace, args.output_directory, "output-directory")
    if output == workspace or output.is_relative_to(docs) or docs.is_relative_to(output):
        raise SystemExit("output-directory must not overlap documentation sources or workspace root")
    if output.exists() and not output.is_dir():
        raise SystemExit("output-directory must name a directory")
    for name in ("api_entrypoint", "theme_directory"):
        value = getattr(args, name, "")
        if value:
            source = contained_path(workspace, value, name.replace("_", "-"), must_exist=True)
            if source == output or source.is_relative_to(output) or output.is_relative_to(source):
                raise SystemExit("output-directory must not overlap API or theme sources")
    if (Path(args.pdf_filename).name != args.pdf_filename or not args.pdf_filename.endswith(".pdf")
            or any(c in args.pdf_filename for c in "\r\n\0")):
        raise SystemExit("pdf-filename must be a plain .pdf filename")
    if not 72 <= args.pdf_image_dpi <= 300:
        raise SystemExit("pdf-image-dpi must be between 72 and 300")
    for name in ("docs_version", "title", "author", "language"):
        if any(c in getattr(args, name, "") for c in "\r\n\0"):
            raise SystemExit(f"{name.replace('_', '-')} must be a single line")
    return docs, output


def renderer_executable(value: str) -> str:
    command = value or os.environ.get("ROC_DOCS_BINARY", "") or "roc-docs"
    executable = shutil.which(command)
    if executable is None:
        raise SystemExit("precompiled roc-docs not found; provide --renderer-binary or ROC_DOCS_BINARY")
    return executable


def manifest_path(prepared: Path, value: object, label: str) -> Path:
    if not isinstance(value, str):
        raise SystemExit(f"invalid prepared {label}")
    path = contained_path(prepared, value, f"prepared {label}", must_exist=True)
    if not path.is_file():
        raise SystemExit(f"prepared {label} must be a file")
    return path


def build_roc(args: argparse.Namespace, workspace: Path, stage: Path) -> None:
    if args.theme_directory:
        raise SystemExit("theme-directory uses Asciidoctor themes; choose --backend legacy or the built-in Roc theme")
    executable = renderer_executable(args.renderer_binary)
    prepared = stage / "prepared"
    helper = ACTION_ROOT.parents[1] / "tools" / "roc-docs" / "prepare_docs.py"
    if not helper.is_file():
        raise SystemExit("Roc documentation preparation helper is missing from this action revision")
    command = ["python3", str(helper), "--workspace", str(workspace), "--docs-directory", args.docs_directory,
               "--entrypoint", args.entrypoint, "--output-directory", str(prepared.relative_to(workspace)),
               "--pdf-filename", args.pdf_filename, "--docs-version", args.docs_version]
    run(*command, cwd=workspace)
    try:
        manifest = json.loads((prepared / "manifest.json").read_text(encoding="utf-8"))
        if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
            raise ValueError("unsupported schema")
        metadata = manifest["metadata"]
        for name in ("title", "author", "language"):
            if getattr(args, name):
                metadata[name] = getattr(args, name)
        base = ["--title", metadata["title"], "--author", metadata["author"],
                "--version", metadata["version"], "--language", metadata["language"],
                "--pdf-filename", args.pdf_filename]
        if not all(isinstance(value, str) and not any(c in value for c in "\r\n\0") for value in base):
            raise ValueError("metadata must contain strings")
        assets = []
        for asset in manifest["assets"]:
            href = asset["href"]
            if (not isinstance(href, str) or not href or Path(href).is_absolute()
                    or ".." in Path(href).parts or any(c in href for c in "\r\n\0")):
                raise ValueError("unsafe asset href")
            width, height = asset["width"], asset["height"]
            if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
                raise ValueError("invalid asset dimensions")
            assets.extend(["--asset", href, str(manifest_path(prepared, asset["png"], "PNG")),
                           str(manifest_path(prepared, asset["rgb"], "RGB")), str(width), str(height)])
        pages = manifest["pages"]
        if not isinstance(pages, list) or not pages:
            raise ValueError("no HTML pages")
        site = stage / "site"
        site.mkdir()
        destinations = set()
        for number, page in enumerate(pages):
            destination = page["output"]
            if (not isinstance(destination, str) or Path(destination).name != destination
                    or not destination.endswith(".html") or destination in destinations):
                raise ValueError("invalid or duplicate HTML route")
            destinations.add(destination)
            source = manifest_path(prepared, page["input"], "page")
            rendered = stage / f"render-page-{number}"
            run(executable, "render", str(source), str(rendered), *base, *assets,
                "--html-only", cwd=workspace)
            reject_symlinks(rendered)
            if not (rendered / "index.html").is_file():
                raise SystemExit("Roc renderer did not produce index.html")
            shutil.copy2(rendered / "index.html", site / destination)
            # Renderer-owned CSS and assets have stable names shared by all pages.
            for item in rendered.iterdir():
                if item.name != "index.html":
                    if item.is_dir():
                        shutil.copytree(item, site / item.name, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, site / item.name)
        if "index.html" not in destinations:
            raise ValueError("site must contain index.html")
        manual = manifest_path(prepared, manifest["manual"]["input"], "manual")
        rendered = stage / "render-manual"
        run(executable, "render", str(manual), str(rendered), *base, *assets,
            "--pdf-image-dpi", str(args.pdf_image_dpi), "--pdf-only", cwd=workspace)
        reject_symlinks(rendered)
        pdf = rendered / args.pdf_filename
        if not pdf.is_file() or not pdf.stat().st_size:
            raise SystemExit("Roc renderer did not produce the PDF")
        shutil.copy2(pdf, stage / args.pdf_filename)
        shutil.copy2(pdf, site / args.pdf_filename)
        copy_roc_static(workspace / args.docs_directory, site)
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise SystemExit(f"invalid prepared documentation: {error}") from error


def copy_roc_static(docs: Path, site: Path) -> None:
    for source in sorted(docs.rglob("*")):
        if source.is_dir() or source.suffix.lower() in (".adoc", ".md", ".markdown"):
            continue
        relative = source.relative_to(docs)
        # Themes contain backend-specific source resources, not website assets.
        if relative.parts[0] == "theme":
            continue
        destination = site / relative
        if destination.exists():
            if not destination.is_file() or destination.read_bytes() != source.read_bytes():
                raise ValueError(f"static file collides with rendered output: {relative}")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def publish(stage: Path, output: Path) -> None:
    # Keep existing output untouched until conversion, rendering and API all succeed.
    backup = stage.parent / "previous-output"
    if output.exists():
        output.rename(backup)
    try:
        stage.rename(output)
    except BaseException:
        if backup.exists():
            backup.rename(output)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def build(args: argparse.Namespace, workspace: Path) -> tuple[Path, Path]:
    _, output = validate(args, workspace)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".roc-docs-", dir=output.parent) as temporary:
        temporary_path = Path(temporary)
        stage = temporary_path / "output"
        stage.mkdir()
        if args.backend == "roc":
            build_roc(args, workspace, stage)
        else:
            if shutil.which("docker") is None:
                raise SystemExit("Docker is required for the explicit legacy documentation backend")
            run("docker", "build", "--tag", IMAGE, "--file", str(ACTION_ROOT / "Dockerfile"),
                str(ACTION_ROOT), cwd=workspace)
            staged_args = copy.copy(args)
            staged_args.output_directory = str(stage.relative_to(workspace))
            command = [
                "docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
                "--env", "XDG_CACHE_HOME=/tmp", "--env", "XDG_CONFIG_HOME=/tmp",
                "--volume", f"{workspace}:/workspace", "--workdir", "/workspace", IMAGE,
                "python3", "/opt/roc-docs/build_docs.py", "--inside-container",
                "--workspace", "/workspace", "--docs-directory", args.docs_directory,
                "--entrypoint", args.entrypoint, "--output-directory", staged_args.output_directory,
                "--pdf-filename", args.pdf_filename, "--docs-version", args.docs_version,
                "--theme-directory", args.theme_directory,
            ]
            run(*command, cwd=workspace)
        build_api(args, workspace, stage / "site")
        reject_symlinks(stage / "site")
        # Working inputs and per-page render directories are never published.
        for item in stage.iterdir():
            if item.name not in ("site", args.pdf_filename):
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
        publish(stage, output)
    return output / "site", output / args.pdf_filename


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--docs-directory", default="docs")
    parser.add_argument("--entrypoint", default="index.adoc")
    parser.add_argument("--output-directory", default=".docs-out")
    parser.add_argument("--pdf-filename", default="manual.pdf")
    parser.add_argument("--docs-version", default="unreleased")
    parser.add_argument("--pdf-image-dpi", type=int, default=144)
    parser.add_argument("--api-entrypoint", default="")
    parser.add_argument("--roc-command", default="roc")
    parser.add_argument("--theme-directory", default="")
    parser.add_argument("--backend", choices=("roc", "legacy"), default="roc")
    parser.add_argument("--renderer-binary", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--author", default="")
    parser.add_argument("--language", default="")
    parser.add_argument("--inside-container", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        raise SystemExit("workspace must be an existing directory")
    if args.inside_container:
        validate(args, workspace)
        site, pdf = build_inside_container(args, workspace)
    else:
        site, pdf = build(args, workspace)
    write_outputs(site, pdf)


if __name__ == "__main__":
    main()
