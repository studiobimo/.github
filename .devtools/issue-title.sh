#!/usr/bin/env bash
# Holds an issue's title to Conventional Commits, as ci-pr holds pull request titles and
# commits: `<type>(<scope>): <summary>`, linted by pr-checks/lint.mjs against
# @commitlint/config-conventional plus the rules of the repository's .commitlintrc.yaml.
#
# Usage, from the root of the repository the issue is in:
#   issue-title.sh <number> <title>
#
# A title that fails gets a label and one comment saying what to change. The comment is
# rewritten as the title changes, and once the title passes, the comment and the label
# both go. ci-issue-title runs this through .github/actions/issue-title.
#
# Needs gh, signed in to the repository (GH_TOKEN, and GH_REPO outside a checkout), and
# `npm ci` run in .github/actions/pr-checks. ISSUE_TITLE_LABEL names the label.
# Exit codes: 0 the title passes, 1 it does not, 2 anything else.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
lint="${here}/../.github/actions/pr-checks/lint.mjs"

# Every string this script prints or writes, in one place.
label="${ISSUE_TITLE_LABEL:-needs-conventional-title}"
label_color=D93F0B
label_description='The title is not a Conventional Commit'
# The comment's first line: how the next run finds the comment again.
marker='<!-- issue-title -->'
msg_usage='usage: issue-title.sh <number> <title>'
msg_no_verdict='lint.mjs gave no verdict on the title of #%s:\n%s'
msg_valid='✔ #%s: the title is a Conventional Commit\n'
msg_invalid='✖ #%s: the title is not a Conventional Commit; commented and labelled it\n'
# %s is the problems lint.mjs found, one Markdown list item each.
comment_format="${marker}
This issue's title is not a [Conventional Commit](https://www.conventionalcommits.org/en/v1.0.0/).
Issues are titled like the commits and pull requests that resolve them:

    <type>(<scope>): <summary>

for example \`fix(api): return 404 for a missing invoice\`.

%s

The type is one of \`feat\`, \`fix\`, \`docs\`, \`style\`, \`refactor\`, \`perf\`, \`test\`, \`build\`,
\`ci\`, \`chore\` or \`revert\`. A scope is optional, and \`.commitlintrc.yaml\` lists the ones
this repository takes. The summary is lowercase, with no full stop.

Edit the title and this comment, and the \`${label}\` label, go away.
"

die() {
    # shellcheck disable=SC2059 # the format is one of the messages above
    printf "✖ $1\n" "${@:2}" >&2
    exit 2
}

[[ $# -eq 2 && "$1" =~ ^[0-9]+$ ]] || die "${msg_usage}"
number="$1"
title="$2"

work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

# lint.mjs exits 1 for a title it rejects and 2 when it cannot lint at all. The comment is
# built from commitlint's findings alone, one `✖ <problem> [<rule>]` line each, so the
# title itself is never written back into the issue.
status=0
node "${lint}" --label 'issue title' "${title}" >"${work}/lint" 2>&1 || status=$?
# commitlint colours its output on a CI runner, so the escapes go before the findings are read.
esc=$'\033'
# shellcheck disable=SC2016 # the backticks are Markdown, written as they are
sed -e "s/${esc}\[[0-9;]*m//g" "${work}/lint" \
    | sed -n 's/^✖[[:space:]]*\(.*\) \[\([a-z-]*\)\]$/- \1 (`\2`)/p' >"${work}/problems"
if [[ "${status}" -ne 0 && ( "${status}" -ne 1 || ! -s "${work}/problems" ) ]]; then
    die "${msg_no_verdict}" "${number}" "$(cat "${work}/lint")"
fi

comment="$(gh api "repos/{owner}/{repo}/issues/${number}/comments" --paginate \
    --jq ".[] | select(.user.type == \"Bot\" and (.body | startswith(\"${marker}\"))) | .id" \
    | head -n 1)"

if [[ "${status}" -eq 0 ]]; then
    if [[ -n "${comment}" ]]; then
        gh api -X DELETE "repos/{owner}/{repo}/issues/comments/${comment}" >/dev/null
        gh issue edit "${number}" --remove-label "${label}" >/dev/null
    fi
    # shellcheck disable=SC2059 # the format is one of the messages above
    printf "${msg_valid}" "${number}"
    exit 0
fi

# shellcheck disable=SC2059 # the format is one of the messages above
printf "${comment_format}" "$(cat "${work}/problems")" >"${work}/body.md"
if [[ -n "${comment}" ]]; then
    gh api -X PATCH "repos/{owner}/{repo}/issues/comments/${comment}" -F "body=@${work}/body.md" >/dev/null
else
    gh issue comment "${number}" --body-file "${work}/body.md" >/dev/null
fi
# --force makes this a no-op when the label already exists.
gh label create "${label}" --force --color "${label_color}" --description "${label_description}" >/dev/null
gh issue edit "${number}" --add-label "${label}" >/dev/null
# shellcheck disable=SC2059 # the format is one of the messages above
printf "${msg_invalid}" "${number}" >&2
exit 1
