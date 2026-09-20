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
