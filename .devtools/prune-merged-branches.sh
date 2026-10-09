#!/usr/bin/env bash
# Deletes local branches whose pull request has been merged.
#
# Usage: prune-merged-branches.sh [--dry-run]
#
# Runs from the shared post-merge hook, so it fires on the `git pull` that brings a
# merged pull request into the default branch, and does nothing on any other branch.
# A branch goes only when all of these hold:
#   - its remote branch is gone (GitHub deletes it when the pull request merges)
#   - it is not checked out here or in another worktree
#   - its work is in the default branch: either its tip is an ancestor of it, or,
#     for a squash merge that leaves no ancestry, gh reports a merged pull request
#     whose head is exactly the local tip
#
# Anything else is kept and named, so commits made after the merge, or on a branch
# whose pull request was closed unmerged, are never lost. Without gh, a squash-merged
# branch is kept rather than guessed at. The exit status is 0 either way: a cleanup
# must not make a pull look like it failed.
set -euo pipefail

dry_run=false
case "${1:-}" in
    '') ;;
    --dry-run) dry_run=true ;;
    -h | --help)
        sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
        exit 0
        ;;
    *)
        echo "unknown argument: $1" >&2
        exit 2
        ;;
esac

default="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
default="${default#origin/}"
default="${default:-main}"
current="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
[[ "${current}" == "${default}" ]] || exit 0

# A pull does not prune on its own unless fetch.prune is set, and offline is fine.
git fetch --prune --quiet origin 2>/dev/null || true

checked_out="$(git worktree list --porcelain | sed -n 's#^branch refs/heads/##p')"

git for-each-ref --format='%(refname:short)%09%(upstream:track)%09%(objectname)' refs/heads |
    while IFS=$'\t' read -r branch track tip; do
        [[ "${track}" == '[gone]' ]] || continue

        if grep -Fxq "${branch}" <<<"${checked_out}"; then
            echo "– kept ${branch}: checked out in a worktree"
            continue
        fi

        merged=false
        if git merge-base --is-ancestor "${tip}" "${default}" 2>/dev/null; then
            merged=true
        elif command -v gh >/dev/null 2>&1; then
            # Captured first: grep -q closing the pipe early would fail it under pipefail.
            heads="$(gh pr list --head "${branch}" --state merged \
                --json headRefOid --jq '.[].headRefOid' 2>/dev/null || true)"
            if grep -Fxq "${tip}" <<<"${heads}"; then
                merged=true
            fi
        fi

        if [[ "${merged}" != true ]]; then
            echo "– kept ${branch}: not shown to be merged (closed unmerged, or commits after the merge)"
        elif [[ "${dry_run}" == true ]]; then
            echo "would delete ${branch}"
        else
            git branch --quiet -D "${branch}"
            echo "✔ deleted ${branch}"
        fi
    done

exit 0
