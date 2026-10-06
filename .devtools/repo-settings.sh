#!/usr/bin/env bash
# Makes every repository in the organization match settings/ and rulesets/.
#
# Usage: repo-settings.sh [--check] [repo...]
#
#   (no flag)  apply the settings and rulesets
#   --check    change nothing; print what differs and exit 1 if anything does
#   repo...    repository names; default is every unarchived source repo in the org
#
# settings/repository.json is the desired state, settings/rulesets.json says which
# rulesets each repository gets, and rulesets/<name>.json is each ruleset in the
# format GitHub's "Import a ruleset" reads.
#
# Only keys written in those files are compared and set, so a setting this repo has
# no opinion about is left as it is. A ruleset that exists on a repository but is not
# listed for it is reported and never deleted.
#
# Needs gh, authenticated as an admin of the repositories, and jq.
set -euo pipefail

org="${ORG:-studiobimo}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
settings="${root}/settings/repository.json"
assignments="${root}/settings/rulesets.json"
mode=apply
repos=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check) mode=check; shift ;;
        -h | --help) sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) echo "unknown argument: $1" >&2; exit 2 ;;
        *) repos+=("$1"); shift ;;
    esac
done

if [[ ${#repos[@]} -eq 0 ]]; then
    while IFS= read -r name; do
        repos+=("${name}")
    done < <(gh repo list "${org}" --no-archived --source --limit 200 --json name --jq '.[].name' | sort)
fi

differences=0

# changes <current-json> <desired-json>: one line per key of desired that differs.
changes() {
    jq -rn --argjson c "$1" --argjson d "$2" '
        [$d | paths(scalars)] | .[] as $p
        | {key: ($p | map(tostring) | join(".")), is: ($c | getpath($p)), want: ($d | getpath($p))}
        | select(.is != .want)
        | "      \(.key): \(.is | tojson) -> \(.want | tojson)"'
}

# reconcile <label> <current-json> <desired-json> <command...>: report, and in apply
# mode run the command with the desired JSON on stdin.
reconcile() {
    local label="$1" current="$2" desired="$3" found
    shift 3
    found="$(changes "${current}" "${desired}")"
    [[ -n "${found}" ]] || return 0
    differences=$((differences + 1))
    echo "    ${label}"
    echo "${found}"
    if [[ "${mode}" == apply ]]; then
        "$@" --input - <<<"${desired}" >/dev/null
    fi
}

# toggle <label> <path> <want: true|false> <is: true|false>
toggle() {
    [[ "$3" != "$4" ]] || return 0
    differences=$((differences + 1))
    echo "    $1: $4 -> $3"
    if [[ "${mode}" == apply ]]; then
        if [[ "$3" == true ]]; then
            gh api -X PUT "$2" >/dev/null
        else
            gh api -X DELETE "$2" >/dev/null
        fi
    fi
}

# A ruleset as the API returns it carries ids, links and defaulted parameters.
# Keying rules by type makes the comparison independent of their order.
comparable='{name, target, enforcement, conditions,
    bypass_actors: ((.bypass_actors // []) | sort_by(.actor_type, .actor_id)),
    rules: ((.rules // []) | map({key: .type, value: (.parameters // {})}) | from_entries)}'

for repo in "${repos[@]}"; do
    api="repos/${org}/${repo}"
    echo "${org}/${repo}"
    before="${differences}"

    reconcile "settings" "$(gh api "${api}")" "$(jq -c .repository "${settings}")" \
        gh api -X PATCH "${api}"
    reconcile "actions" "$(gh api "${api}/actions/permissions")" "$(jq -c .actions "${settings}")" \
        gh api -X PUT "${api}/actions/permissions"
    reconcile "workflow permissions" "$(gh api "${api}/actions/permissions/workflow")" \
        "$(jq -c .workflow "${settings}")" gh api -X PUT "${api}/actions/permissions/workflow"

    # This endpoint answers with a status code, not a body: 204 is on, 404 is off.
    alerts=false
    if gh api "${api}/vulnerability-alerts" >/dev/null 2>&1; then alerts=true; fi
    toggle "vulnerability alerts" "${api}/vulnerability-alerts" \
        "$(jq -r .vulnerability_alerts "${settings}")" "${alerts}"
    toggle "automated security fixes" "${api}/automated-security-fixes" \
        "$(jq -r .automated_security_fixes "${settings}")" \
        "$(gh api "${api}/automated-security-fixes" --jq .enabled 2>/dev/null || echo false)"
    toggle "private vulnerability reporting" "${api}/private-vulnerability-reporting" \
        "$(jq -r .private_vulnerability_reporting "${settings}")" \
        "$(gh api "${api}/private-vulnerability-reporting" --jq .enabled 2>/dev/null || echo false)"

    existing="$(gh api "${api}/rulesets" --paginate --jq '[.[] | select(.source_type == "Repository") | {name, id}]')"
    wanted="$(jq -c --arg r "${repo}" '.repositories[$r] // .default' "${assignments}")"

    while IFS= read -r name; do
        file="${root}/rulesets/${name}.json"
        [[ -f "${file}" ]] || { echo "settings/rulesets.json names ${name}, but rulesets/${name}.json is missing" >&2; exit 2; }
        desired="$(jq -c "${comparable}" "${file}")"
        id="$(jq -r --arg n "${name}" '.[] | select(.name == $n) | .id' <<<"${existing}")"
        if [[ -z "${id}" ]]; then
            differences=$((differences + 1))
            echo "    ruleset ${name}: missing"
            if [[ "${mode}" == apply ]]; then
                gh api -X POST "${api}/rulesets" --input "${file}" >/dev/null
            fi
            continue
        fi
        current="$(gh api "${api}/rulesets/${id}" --jq "${comparable}")"
        extra="$(jq -rn --argjson c "${current}" --argjson d "${desired}" \
            '($c.rules | keys) - ($d.rules | keys) | map("      rule \(.): not in the file") | .[]')"
        found="$(changes "${current}" "${desired}")"
        if [[ -n "${found}${extra}" ]]; then
            differences=$((differences + 1))
            echo "    ruleset ${name}"
            [[ -z "${found}" ]] || echo "${found}"
            [[ -z "${extra}" ]] || echo "${extra}"
            if [[ "${mode}" == apply ]]; then
                gh api -X PUT "${api}/rulesets/${id}" --input "${file}" >/dev/null
            fi
        fi
    done < <(jq -r '.[]' <<<"${wanted}")

    jq -r --argjson w "${wanted}" '.[] | select(.name as $n | $w | index($n) | not) | "    ruleset \(.name): on the repository but not listed for it (left alone)"' <<<"${existing}"

    if [[ "${differences}" == "${before}" ]]; then
        echo "    ✔ in step"
    fi
done

if ((differences == 0)); then
    exit 0
fi
if [[ "${mode}" == check ]]; then
    echo "${differences} difference(s). Apply with: bash .devtools/repo-settings.sh"
    exit 1
fi
echo "Applied ${differences} change(s)."
