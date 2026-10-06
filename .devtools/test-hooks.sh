#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
failures=0

expect() {
    local want="$1" what="$2" got=pass
    shift 2
    "$@" >/dev/null 2>&1 || got=fail
    if [[ "${got}" == "${want}" ]]; then
        echo "✔ ${what}"
    else
        echo "✖ ${what}: expected ${want}, got ${got}" >&2
        failures=$((failures + 1))
    fi
}

work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

# Keep the developer's own git config and any hooks out of the fixture.
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.com
export GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.com
unset BRANCH_NAME PR_BASE PR_HEAD PR_MAX_FILES

# The repository sits beside the message fixtures, not around them, so
# `git add .` below never sweeps them into a commit.
mkdir "${work}/repo"
cd "${work}/repo"
git init --quiet --initial-branch=main .
git commit --quiet --allow-empty -m 'chore: initial commit'

add_files() {
    local i
    for ((i = 1; i <= $1; i++)); do
        echo "$i" >"$2-$i.txt"
    done
    git add .
    git commit --quiet -m "feat: add $1 files"
}

echo "check-branch-name"
branch="${here}/check-branch-name.sh"
expect pass "accepts feat/cursor-pagination" "${branch}" feat/cursor-pagination
expect pass "accepts release/v1.2.0" "${branch}" release/v1.2.0
expect pass "accepts claude/fix-login" "${branch}" claude/fix-login
expect pass "accepts main" "${branch}" main
expect pass "accepts a Dependabot branch" "${branch}" dependabot/github_actions/actions-1a2b3c
expect pass "accepts a release-please branch" "${branch}" release-please--branches--main
expect fail "rejects an unknown type" "${branch}" wip/thing
expect fail "rejects uppercase" "${branch}" feat/Cursor-Pagination
expect fail "rejects an underscore" "${branch}" feat/cursor_pagination
expect fail "rejects a double hyphen" "${branch}" feat/cursor--pagination
expect pass "falls back to the current branch" "${branch}"
expect fail "reads BRANCH_NAME" env BRANCH_NAME=Bad_Name "${branch}"
expect pass "prefers the argument over BRANCH_NAME" env BRANCH_NAME=Bad_Name "${branch}" fix/login-timeout

echo "check-conventional-commit"
commit="${here}/check-conventional-commit.sh"
expect pass "accepts a scoped subject" "${commit}" 'feat(api): add cursor pagination'
expect pass "accepts a breaking change" "${commit}" 'fix!: drop support for Node 18'
expect pass "accepts a generated merge" "${commit}" "Merge branch 'main' into feat/x"
expect fail "rejects an unknown type" "${commit}" 'feature: add pagination'
expect fail "rejects a missing description" "${commit}" 'feat:'
printf 'fix(devtools): handle an empty diff\n\nA body may say anything at all.\n' >"${work}/MSG_OK"
printf 'handled an empty diff\n' >"${work}/MSG_BAD"
expect pass "reads a good message with --file" "${commit}" --file "${work}/MSG_OK"
expect fail "reads a bad message with --file" "${commit}" --file "${work}/MSG_BAD"

echo "check-pr-size"
size="${here}/check-pr-size.sh"
git switch --quiet -c feat/small
add_files 20 small
expect pass "allows 20 files, base worked out" "${size}"
expect pass "allows 20 files, explicit refs" "${size}" --base main --head feat/small
expect fail "honours --max" "${size}" --max 19
expect fail "honours PR_MAX_FILES" env PR_MAX_FILES=19 "${size}"

git switch --quiet -c feat/big
add_files 1 big
expect fail "blocks 21 files, base worked out" "${size}"
expect fail "blocks 21 files, explicit refs" "${size}" --base main --head feat/big
expect pass "measures a layer against PR_BASE" env PR_BASE=feat/small "${size}"
expect pass "measures a layer against --base" "${size}" --base feat/small

expect fail "fails when a named base is missing" "${size}" --base no-such-branch
git branch --quiet -m main trunk
expect pass "skips when a guessed base is missing" "${size}"

if ((failures > 0)); then
    echo "${failures} failed" >&2
    exit 1
fi
echo "All passed"
