#!/usr/bin/env bash
# Tests issue-title.sh against a stand-in for gh, so nothing here reaches GitHub. It lints
# with pr-checks' commitlint, so run `npm ci --prefix .github/actions/pr-checks
# --ignore-scripts` first. Run: bash .devtools/test-issue-title.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
script="${here}/issue-title.sh"
failures=0

work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

# expect <exit> <what> <command...>: runs the command, keeping its output in ${work}/out.
expect() {
    local want="$1" what="$2" got=0
    shift 2
    "$@" >"${work}/out" 2>&1 || got=$?
    if [[ "${got}" == "${want}" ]]; then
        echo "✔ ${what}"
    else
        echo "✖ ${what}: expected exit ${want}, got ${got}" >&2
        sed 's/^/    /' "${work}/out" >&2
        failures=$((failures + 1))
    fi
}

[[ -d "${here}/../.github/actions/pr-checks/node_modules" ]] || {
    echo "✖ run: npm ci --prefix .github/actions/pr-checks --ignore-scripts" >&2
    exit 2
}

# The stand-in. The comment lookup answers with GH_COMMENT_ID; every other call is recorded
# in calls, and a comment's body is kept in body.
mkdir "${work}/bin"
cat >"${work}/bin/gh" <<'STUB'
#!/usr/bin/env bash
case "$1 $2" in
    "api repos/{owner}/{repo}/issues/"*/comments) echo "${GH_COMMENT_ID:-}" ;;
    "issue comment") echo "$*" >>"${GH_LOG}/calls"; cat "$5" >"${GH_LOG}/body" ;;
    "api -X") echo "$*" >>"${GH_LOG}/calls"; [[ "${6:-}" != body=@* ]] || cat "${6#body=@}" >"${GH_LOG}/body" ;;
    *) echo "$*" >>"${GH_LOG}/calls" ;;
esac
STUB
chmod +x "${work}/bin/gh"
export PATH="${work}/bin:${PATH}" GH_LOG="${work}/gh"
unset GH_COMMENT_ID ISSUE_TITLE_LABEL
label=needs-conventional-title

# The repository the issue is in, with its own scopes.
mkdir "${work}/repo" "${GH_LOG}"
cd "${work}/repo"
printf 'rules:\n  scope-enum: [2, always, [api]]\n' >.commitlintrc.yaml

reset() { rm -f "${GH_LOG}/calls" "${GH_LOG}/body"; }
called() { grep -qxF -- "$1" "${GH_LOG}/calls"; }

echo "issue-title"
reset
expect 1 "rejects a title that is not a Conventional Commit" "${script}" 7 'Export drops the last row'
expect 0 "comments on the issue" grep -q '^issue comment 7 --body-file ' "${GH_LOG}/calls"
expect 0 "under the marker the next run finds it by" grep -qxF '<!-- issue-title -->' "${GH_LOG}/body"
expect 0 "naming each rule it broke" grep -qF "(\`type-empty\`)" "${GH_LOG}/body"
expect 1 "without repeating the title" grep -qF 'Export drops the last row' "${GH_LOG}/body"
expect 0 "and labels it" called "issue edit 7 --add-label ${label}"

export GH_COMMENT_ID=42
reset
expect 1 "rewrites that comment while the title still fails" "${script}" 7 'fix: Export drops the last row'
expect 0 "in place" grep -q '^api -X PATCH repos/{owner}/{repo}/issues/comments/42 ' "${GH_LOG}/calls"
expect 0 "with the new problem" grep -qF "(\`subject-case\`)" "${GH_LOG}/body"
reset
expect 1 "holds the title to the repository's scopes" "${script}" 7 'fix(web): export drops the last row'
expect 0 "and says so" grep -qF "(\`scope-enum\`)" "${GH_LOG}/body"
reset
expect 0 "passes a Conventional Commit" "${script}" 7 'fix(api): export drops the last row'
expect 0 "and takes the comment down" called 'api -X DELETE repos/{owner}/{repo}/issues/comments/42'
expect 0 "and the label" called "issue edit 7 --remove-label ${label}"
unset GH_COMMENT_ID

reset
expect 0 "passes a title with nothing to tidy up" "${script}" 8 'feat: export to csv'
expect 1 "and changes nothing" test -e "${GH_LOG}/calls"
reset
expect 1 "uses the label it is given" env ISSUE_TITLE_LABEL=bad-title "${script}" 9 'Nope'
expect 0 "for the issue" called 'issue edit 9 --add-label bad-title'
expect 2 "refuses a call without a number" "${script}" 'feat: export to csv'

if ((failures > 0)); then
    echo "${failures} failed" >&2
    exit 1
fi
echo "All passed"
