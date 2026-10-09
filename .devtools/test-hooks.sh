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

printf 'fix(devtools): handle an empty diff\n\nA body may say anything at all.\n' >"${work}/MSG_OK"

# The repo fixture has no .commitlintrc.yaml, so these run on config-conventional alone.
echo "pr-checks/lint.mjs"
lint=(node "${here}/../.github/actions/pr-checks/lint.mjs")
[[ -d "${here}/../.github/actions/pr-checks/node_modules" ]] || {
    echo "✖ run: npm ci --prefix .github/actions/pr-checks --ignore-scripts" >&2
    exit 2
}
expect pass "accepts a scoped subject" "${lint[@]}" 'feat(api): add cursor pagination'
expect pass "accepts a breaking change" "${lint[@]}" 'fix!: drop support for Node 18'
expect pass "accepts a generated merge" "${lint[@]}" "Merge branch 'main' into feat/x"
expect pass "accepts a body" "${lint[@]}" "$(cat "${work}/MSG_OK")"
expect fail "rejects a header over 100 characters" "${lint[@]}" \
    'chore(deps): bump check-jsonschema from 0.38.0 to 0.38.2 in /.devtools in the devtools group across 1 directory'
expect fail "rejects a capitalised subject" "${lint[@]}" 'feat(api): Add cursor pagination'
expect fail "rejects a subject ending in a full stop" "${lint[@]}" 'feat(api): add cursor pagination.'
expect fail "rejects an unknown type" "${lint[@]}" 'feature: add pagination'
expect fail "rejects a missing description" "${lint[@]}" 'feat:'
expect fail "rejects a subject with no type" "${lint[@]}" 'handled an empty diff'
expect pass "accepts any scope without a config" "${lint[@]}" 'feat(anything): add a thing'

mkdir "${work}/scoped"
cd "${work}/scoped"
printf 'rules:\n  scope-enum: [2, always, [api, docs]]\n' >.commitlintrc.yaml
expect pass "accepts a scope the repository lists" "${lint[@]}" 'feat(api): add a thing'
expect pass "accepts no scope" "${lint[@]}" 'feat: add a thing'
expect fail "rejects a scope the repository does not list" "${lint[@]}" 'feat(nope): add a thing'
expect fail "keeps config-conventional next to the repository's rules" "${lint[@]}" 'feature(api): add a thing'

# A config can name JavaScript to load. On a pull request that file is the author's.
printf 'extends: [./run.cjs]\nplugins: [./run.cjs]\nparserPreset: ./run.cjs\n' >.commitlintrc.yaml
printf 'require("fs").writeFileSync("%s/RAN", ""); module.exports = {};\n' "${work}" >run.cjs
expect pass "lints with a config that names a script" "${lint[@]}" 'feat: add a thing'
expect fail "and never runs the script" test -e "${work}/RAN"
cd "${work}/repo"

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

# A stale local default branch: origin has since gained what feat/small added, so
# only the one file from feat/big is this branch's own.
git update-ref refs/remotes/origin/main feat/small
expect pass "prefers origin for a worked-out default branch" "${size}"
expect fail "keeps a named base local" "${size}" --base main
git update-ref -d refs/remotes/origin/main

expect fail "fails when a named base is missing" "${size}" --base no-such-branch
git branch --quiet -m main trunk
expect pass "skips when a guessed base is missing" "${size}"

echo "prune-merged-branches"
prune="${here}/prune-merged-branches.sh"

# gh stands in for GitHub: it reports GH_MERGED_OID as the head of a merged pull request.
mkdir "${work}/bin"
# shellcheck disable=SC2016  # the stub expands it, not this script
printf '#!/bin/sh\n[ -z "${GH_MERGED_OID:-}" ] || echo "${GH_MERGED_OID}"\n' >"${work}/bin/gh"
chmod +x "${work}/bin/gh"
mkdir "${work}/bin-fail"
printf '#!/bin/sh\nexit 1\n' >"${work}/bin-fail/gh"
chmod +x "${work}/bin-fail/gh"
no_gh=(env PATH="${work}/bin-fail:${PATH}")

git init --quiet --bare --initial-branch=main "${work}/origin.git"
git clone --quiet "${work}/origin.git" "${work}/prune" 2>/dev/null
cd "${work}/prune"
git commit --quiet --allow-empty -m 'chore: initial commit'
git push --quiet origin main
git remote set-head origin main >/dev/null

# branch_off <name> <file>: a pushed branch with one commit, back on main afterwards.
branch_off() {
    git switch --quiet -c "$1" main
    echo "$1" >"$2"
    git add "$2"
    git commit --quiet -m "feat: add $2"
    git push --quiet -u origin "$1" 2>/dev/null
    git switch --quiet main
}
# remote_gone <name>: what GitHub does to a head branch when its pull request merges.
remote_gone() { git push --quiet origin --delete "$1" 2>/dev/null; }
has_branch() { git show-ref --verify --quiet "refs/heads/$1"; }

branch_off feat/ancestry a.txt
git merge --quiet --no-ff -m 'feat: merge ancestry' feat/ancestry
git push --quiet origin main
remote_gone feat/ancestry

branch_off feat/squashed b.txt
squashed_tip="$(git rev-parse feat/squashed)"
git merge --quiet --squash feat/squashed >/dev/null
git commit --quiet -m 'feat: squash b'
git push --quiet origin main
remote_gone feat/squashed

branch_off feat/closed c.txt
remote_gone feat/closed
branch_off feat/live d.txt
branch_off feat/worktree e.txt
remote_gone feat/worktree
git worktree add --quiet "${work}/wt" feat/worktree 2>/dev/null

expect pass "dry run exits 0" "${prune}" --dry-run
expect pass "dry run deletes nothing" has_branch feat/ancestry
expect pass "ignores a branch that is not the default" bash -c "git switch --quiet -c feat/elsewhere && '${prune}' && git switch --quiet main"
expect pass "and deletes nothing there" has_branch feat/ancestry
expect pass "keeps a squash merge it cannot confirm without gh" "${no_gh[@]}" "${prune}"
expect pass "keeps it" has_branch feat/squashed
expect fail "deletes a branch merged by ancestry" has_branch feat/ancestry
expect pass "keeps a branch with a live remote" has_branch feat/live
expect pass "keeps a branch whose pull request closed unmerged" has_branch feat/closed
expect pass "keeps a branch checked out in a worktree" has_branch feat/worktree
expect pass "keeps a squash merge whose head moved on" env PATH="${work}/bin:${PATH}" GH_MERGED_OID=0000000000000000000000000000000000000000 "${prune}"
expect pass "and the branch with it" has_branch feat/squashed
expect pass "runs with a squash merge gh confirms" env PATH="${work}/bin:${PATH}" GH_MERGED_OID="${squashed_tip}" "${prune}"
expect fail "and deletes it" has_branch feat/squashed
expect pass "still keeps the unmerged and the live" has_branch feat/closed
expect pass "and the checked out one" has_branch feat/worktree
cd "${work}/repo"

if ((failures > 0)); then
    echo "${failures} failed" >&2
    exit 1
fi
echo "All passed"
