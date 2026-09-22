#!/usr/bin/env python3
"""Build a project manual with the shared Roc AsciiDoc toolchain."""

from __future__ import annotations

import argparse
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
    result = (root / value).resolve()
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--docs-directory", default="docs")
    parser.add_argument("--entrypoint", default="index.adoc")
    parser.add_argument("--output-directory", default=".docs-out")
    parser.add_argument("--pdf-filename", default="manual.pdf")
    parser.add_argument("--docs-version", default="unreleased")
    parser.add_argument("--api-entrypoint", default="")
    parser.add_argument("--roc-command", default="roc")
    parser.add_argument("--theme-directory", default="")
    parser.add_argument("--inside-container", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        raise SystemExit("workspace must be an existing directory")
    if args.inside_container:
        site, pdf = build_inside_container(args, workspace)
        write_outputs(site, pdf)
        return

    if shutil.which("docker") is None:
        raise SystemExit("Docker is required to build the documentation")
    run("docker", "build", "--tag", IMAGE, "--file", str(ACTION_ROOT / "Dockerfile"),
        str(ACTION_ROOT), cwd=workspace)
    command = [
        "docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
        "--env", "XDG_CACHE_HOME=/tmp", "--env", "XDG_CONFIG_HOME=/tmp",
        "--volume", f"{workspace}:/workspace", "--workdir", "/workspace", IMAGE,
        "python3", "/opt/roc-docs/build_docs.py", "--inside-container",
        "--workspace", "/workspace", "--docs-directory", args.docs_directory,
        "--entrypoint", args.entrypoint, "--output-directory", args.output_directory,
        "--pdf-filename", args.pdf_filename, "--docs-version", args.docs_version,
        "--theme-directory", args.theme_directory,
    ]
    run(*command, cwd=workspace)
    output = contained_path(workspace, args.output_directory, "output-directory", must_exist=True)
    site, pdf = output / "site", output / args.pdf_filename
    build_api(args, workspace, site)
    write_outputs(site, pdf)


if __name__ == "__main__":
    main()
