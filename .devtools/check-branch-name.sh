#!/usr/bin/env bash
# Validates a branch name against Conventional Branch (https://conventionalbranch.org/).
#
# Usage: check-branch-name.sh [branch]   (defaults to the current branch)
#
# Canonical copy. Repos keep a local copy for their git hooks until the
# pre-commit hook repo exists (studiobimo/.github#10); keep the two in step.
set -euo pipefail

branch="${1:-$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)}"

# Detached HEAD (e.g. during rebase) has nothing to validate.
if [[ -z "${branch}" ]]; then
    exit 0
fi

# Bot branches are not ours to name. Dependabot's format is fixed --
# dependabot/<ecosystem>/<group>-<hash>, with underscores and an extra path
# segment -- and there is no setting to change it, so every dependency PR would
# fail this check forever and the check would be the thing that gets disabled.
# Nothing is lost: the title and commits a bot writes are Conventional, and the
# title is what becomes the squash commit.
#
# release-please is the same story with a different shape:
# release-please--branches--<target>, plus --components--<name> in a monorepo.
# The double hyphen is its separator and cannot be reconfigured, so it can never
# satisfy the single-hyphen rule below. Its PR title -- chore(main): release
# X.Y.Z -- is Conventional, and that title is what the tag and changelog are cut
# from, so the branch name carries no meaning worth enforcing.
case "${branch}" in
    dependabot/* | renovate/* | release-please--*) exit 0 ;;
esac

types='feature|feat|bugfix|fix|hotfix|release|chore|ai|claude|codex|copilot|cursor'
segment='[a-z0-9]+(\.[a-z0-9]+)*'
pattern="^(main|master|develop|(${types})/${segment}(-${segment})*)$"

if [[ "${branch}" =~ ${pattern} ]]; then
    exit 0
fi

cat >&2 <<MSG
✖ Branch name '${branch}' does not follow Conventional Branch.

    Format:  <type>/<description>
    Types:   ${types//|/, }
    Rules:   lowercase a-z, 0-9 and single hyphens; dots only in release versions
    Example: feat/offline-credit, fix/backlog-overflow, release/v1.2.0

    Rename with: git branch -m <new-name>
    Spec: https://conventionalbranch.org/
MSG
exit 1
