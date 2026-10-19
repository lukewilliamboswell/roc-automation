#!/usr/bin/env bash
# The action only locates and executes the supplied native utility.
set -euo pipefail

renderer=${ROC_DOCS_BINARY:-roc-docs}
arguments=(build)
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
