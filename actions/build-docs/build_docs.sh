#!/usr/bin/env bash
# The default action path only locates and executes the supplied native utility.
set -euo pipefail

backend=roc
renderer=${ROC_DOCS_BINARY:-roc-docs}
theme=
arguments=(build)
while (( $# )); do
    case "$1" in
        --backend|--renderer-binary|--theme-directory)
            if (( $# < 2 )); then
                printf 'build-docs: %s requires a value\n' "$1" >&2
                exit 2
            fi
            case "$1" in
                --backend) backend=$2 ;;
                --renderer-binary) if [[ -n "$2" ]]; then renderer=$2; fi ;;
                --theme-directory) theme=$2 ;;
            esac
            shift 2
            ;;
        --workspace|--docs-directory|--entrypoint|--output-directory|--pdf-filename|--docs-version|--pdf-image-dpi|--api-entrypoint|--roc-command|--title|--author|--language)
            if (( $# < 2 )); then
                printf 'build-docs: %s requires a value\n' "$1" >&2
                exit 2
            fi
            arguments+=("$1" "$2")
            shift 2
            ;;
        *)
            printf 'build-docs: unknown option %s\n' "$1" >&2
            exit 2
            ;;
    esac
done

case "$backend" in
    roc)
        if [[ -n "$theme" ]]; then
            printf 'build-docs: theme-directory uses Asciidoctor themes; choose backend legacy or the built-in Roc theme\n' >&2
            exit 2
        fi
        if [[ "$renderer" != */* ]]; then
            renderer=$(type -P -- "$renderer" || true)
        fi
        if [[ -z "$renderer" || ! -f "$renderer" || ! -x "$renderer" ]]; then
            printf 'build-docs: precompiled roc-docs not found; provide renderer-binary or ROC_DOCS_BINARY\n' >&2
            exit 2
        fi
        exec "$renderer" "${arguments[@]}"
        ;;
    legacy)
        python=$(type -P python3 || true)
        if [[ -z "$python" ]]; then
            printf 'build-docs: Python 3 is required for the explicit legacy backend\n' >&2
            exit 2
        fi
        script=${BASH_SOURCE[0]}
        directory=${script%/*}
        if [[ "$directory" == "$script" ]]; then directory=.; fi
        directory=$(cd -- "$directory" && pwd)
        exec "$python" "$directory/build_docs.py" --backend legacy \
            --theme-directory "$theme" "${arguments[@]:1}"
        ;;
    *)
        printf 'build-docs: backend must be roc or legacy\n' >&2
        exit 2
        ;;
esac
