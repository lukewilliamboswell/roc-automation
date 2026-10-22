#!/usr/bin/env bash
# The action only locates and executes the supplied native utility.
set -euo pipefail

renderer=${ROC_DOCS_BINARY:-roc-docs}
arguments=(build)
markdown_seen=false
while (( $# )); do
    case "$1" in
        --renderer-binary)
            if (( $# < 2 )); then
                printf 'build-docs: %s requires a value\n' "$1" >&2
                exit 2
            fi
            if [[ -n "$2" ]]; then renderer=$2; fi
            shift 2
            ;;
        --markdown)
            if (( $# < 2 )); then
                printf 'build-docs: %s requires a value\n' "$1" >&2
                exit 2
            fi
            if [[ "$markdown_seen" == true ]]; then
                printf 'build-docs: duplicate option --markdown\n' >&2
                exit 2
            fi
            markdown_seen=true
            case "$2" in
                true) arguments+=(--markdown true) ;;
                false) arguments+=(--markdown false) ;;
                *) printf 'build-docs: markdown must be true or false\n' >&2; exit 2 ;;
            esac
            shift 2
            ;;
        --site-url|--workspace|--docs-directory|--entrypoint|--output-directory|--pdf-filename|--docs-version|--pdf-image-dpi|--api-entrypoint|--roc-command|--title|--author|--language)
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

if [[ "$renderer" != */* ]]; then
    renderer=$(type -P -- "$renderer" || true)
fi
if [[ -z "$renderer" || ! -f "$renderer" || ! -x "$renderer" ]]; then
    printf 'build-docs: precompiled roc-docs not found; provide renderer-binary or ROC_DOCS_BINARY\n' >&2
    exit 2
fi
exec "$renderer" "${arguments[@]}"
