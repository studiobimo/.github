#!/usr/bin/env bash
# Fails when a pull request changes more files than a PR may contain.
#
# Usage: check-pr-size.sh --base <ref> --head <ref> [--max <n>]
#
# For a stacked PR the base is the layer below it, so each layer is measured on
# its own rather than accumulating the whole stack. GitHub already sets the PR's
# base to the parent branch, so nothing special is needed here.
#
# Canonical copy. Repos keep a local copy for their git hooks until the
# pre-commit hook repo exists (studiobimo/.github#10); keep the two in step.
set -euo pipefail

max="${PR_MAX_FILES:-20}"
base="${PR_BASE:-}"
head_ref="${PR_HEAD:-HEAD}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --base)
            base="$2"
            shift 2
            ;;
        --head)
            head_ref="$2"
            shift 2
            ;;
        --max)
            max="$2"
            shift 2
            ;;
        *)
            echo "unknown argument: $1" >&2
            exit 64
            ;;
    esac
done

if [[ -z "${base}" ]]; then
    echo "✖ --base is required" >&2
    exit 64
fi

resolve() {
    if git rev-parse --verify --quiet "$1" >/dev/null; then
        printf '%s' "$1"
    elif git rev-parse --verify --quiet "origin/$1" >/dev/null; then
        printf 'origin/%s' "$1"
    fi
}

base_ref="$(resolve "${base}")"
head_resolved="$(resolve "${head_ref}")"

if [[ -z "${base_ref}" || -z "${head_resolved}" ]]; then
    echo "✖ Cannot resolve ${base}...${head_ref}; fetch both refs first." >&2
    exit 1
fi

# The merge base, not the tip: files the base gained since the branch started
# are not this PR's doing.
merge_base="$(git merge-base "${base_ref}" "${head_resolved}")"
files="$(git diff --name-only "${merge_base}" "${head_resolved}")"
if [[ -z "${files}" ]]; then
    count=0
else
    count="$(wc -l <<<"${files}" | tr -d ' ')"
fi

if ((count <= max)); then
    echo "✔ ${count}/${max} files changed vs ${base_ref}"
    exit 0
fi

cat >&2 <<MSG
✖ This PR changes ${count} files (max ${max}).

    Split it into a stack:
    gh extension install github/gh-stack   # once
    gh stack init <first-branch>           # or adopt existing branches
    gh stack add <next-branch>             # commit a slice, repeat
    gh stack submit                        # push and open linked PRs

    Each layer is measured against the one below it, so a stack of small PRs
    passes where one large PR does not.

    Docs: https://docs.github.com/en/pull-requests/get-started/about-stacked-prs
MSG
printf '\nFiles changed:\n' >&2
printf '%s\n' "${files}" >&2
exit 1
