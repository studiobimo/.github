#!/usr/bin/env bash
# Fails when a branch changes more files than a pull request may contain.
#
# Usage: check-pr-size.sh [--base <ref>] [--head <ref>] [--max <n>]
#
# Two callers, one script. ci-pr passes both ends of the pull request. The
# `pr-size` lefthook job passes nothing, so the base is worked out here:
# $PR_BASE, then the branch below this one in a gh stack, then the remote's
# default branch. A default branch that was worked out is read from origin, since
# the local copy only moves on a pull.
#
# Either way a stacked branch is measured against the layer below it, so each
# layer is counted on its own rather than accumulating the whole stack. On a
# pull request GitHub has already set the base to the parent branch.
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

stack_parent() {
    command -v gh >/dev/null 2>&1 || return 0
    command -v jq >/dev/null 2>&1 || return 0
    local branch
    branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
    [[ -n "${branch}" ]] || return 0
    # `gh stack view --json` lists branches bottom-to-top; a layer's base is the one below it.
    gh stack view --json 2>/dev/null | jq -r --arg b "${branch}" '
        ([.branches[].name] | index($b)) as $i
        | if $i == null then empty
        elif $i == 0 then .trunk
        else .branches[$i - 1].name end' 2>/dev/null || true
}

default_branch() {
    git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##' || true
}

resolve() {
    # Prefer the local ref (a stack's parents are local branches), fall back to origin.
    if git rev-parse --verify --quiet "$1" >/dev/null; then
        printf '%s' "$1"
    elif git rev-parse --verify --quiet "origin/$1" >/dev/null; then
        printf 'origin/%s' "$1"
    fi
}

resolve_remote_first() {
    if git rev-parse --verify --quiet "origin/$1" >/dev/null; then
        printf 'origin/%s' "$1"
    else
        resolve "$1"
    fi
}

# A base nobody named is a guess, and a wrong guess must not block a push: CI
# measures the real pull request. A base somebody named has to resolve.
guessed=false
default=""
if [[ -z "${base}" ]]; then
    guessed=true
    default="$(default_branch)"
    [[ -n "${default}" ]] || default="main"
    base="$(stack_parent)"
    [[ -n "${base}" ]] || base="${default}"
fi

# Nobody commits to the default branch locally, so the local copy is as old as
# the last pull. A branch cut from a fresher origin then counts every file the
# default branch gained in between as its own. origin's copy is what the pull
# request is measured against, so it wins here; a stack's parent and a named
# base stay local-first.
if [[ "${guessed}" == true && "${base}" == "${default}" ]]; then
    base_ref="$(resolve_remote_first "${base}")"
else
    base_ref="$(resolve "${base}")"
fi
head_resolved="$(resolve "${head_ref}")"

if [[ -z "${base_ref}" || -z "${head_resolved}" ]]; then
    if [[ "${guessed}" == true ]]; then
        echo "⚠ Base '${base}' not found; skipping PR size check." >&2
        exit 0
    fi
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
✖ This branch changes ${count} files vs ${base_ref} (max ${max}).

    Split it into a stack of smaller PRs:
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
