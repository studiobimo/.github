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
# What GitHub does not offer a repository on its plan is skipped with a note, and is
# not a difference: on the Free plan a private repository has no rulesets, no secret
# scanning and no private vulnerability reporting.
#
# Needs gh, authenticated as an admin of the repositories, and jq.
# Exit codes: 0 in step (or applied), 1 differences found by --check, 2 anything else.
set -Eeuo pipefail

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

die() {
    echo "✖ $*" >&2
    exit 2
}

# Exit 1 means "differs" to callers, so a command that fails must not end in it.
trap 'die "repo-settings.sh failed at line ${LINENO}"' ERR

work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

# get <gh api arguments...>: the answer in $body and its HTTP status in $http, 200
# for any success. gh prints an error's JSON on stdout as well, so $body holds the
# message when the request was refused.
get() {
    http=200
    gh api "$@" >"${work}/stdout" 2>"${work}/stderr" || http=""
    body="$(cat "${work}/stdout")"
    [[ -z "${http}" ]] || return 0
    http="$(sed -n 's/.*(HTTP \([0-9][0-9]*\)).*/\1/p' "${work}/stderr" | tail -n 1)"
    [[ -n "${http}" ]] || die "gh api $1: $(cat "${work}/stderr")"
}

# refused <path>: the last get was not answered, and nothing here expects that.
refused() {
    die "GET $1: HTTP ${http}: $(jq -r '.message? // empty' <<<"${body}" 2>/dev/null || true)"
}

# skip <what>: GitHub does not offer it to this repository.
skip() {
    echo "    ! $1: not offered to this repository on its plan (skipped)"
}

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

    get "${api}"
    [[ "${http}" == 200 ]] || refused "${api}"
    current="${body}"
    desired="$(jq -c .repository "${settings}")"
    # Without the features the key is null. It goes in the same request as the merge
    # settings, so it is left out rather than risk that request being refused.
    if jq -e '.security_and_analysis == null' <<<"${current}" >/dev/null \
        && jq -e 'has("security_and_analysis")' <<<"${desired}" >/dev/null; then
        skip "secret scanning"
        desired="$(jq -c 'del(.security_and_analysis)' <<<"${desired}")"
    fi
    reconcile "settings" "${current}" "${desired}" gh api -X PATCH "${api}"

    get "${api}/actions/permissions"
    [[ "${http}" == 200 ]] || refused "${api}/actions/permissions"
    reconcile "actions" "${body}" "$(jq -c .actions "${settings}")" \
        gh api -X PUT "${api}/actions/permissions"

    get "${api}/actions/permissions/workflow"
    [[ "${http}" == 200 ]] || refused "${api}/actions/permissions/workflow"
    reconcile "workflow permissions" "${body}" "$(jq -c .workflow "${settings}")" \
        gh api -X PUT "${api}/actions/permissions/workflow"

    # This endpoint answers with a status code, not a body: 204 is on, 404 is off.
    get "${api}/vulnerability-alerts"
    case "${http}" in
        200) alerts=true ;;
        404) alerts=false ;;
        *) refused "${api}/vulnerability-alerts" ;;
    esac
    toggle "vulnerability alerts" "${api}/vulnerability-alerts" \
        "$(jq -r .vulnerability_alerts "${settings}")" "${alerts}"

    # 404 here is "not enabled", and the alerts above have to be on first.
    get "${api}/automated-security-fixes"
    case "${http}" in
        200) fixes="$(jq -r .enabled <<<"${body}")" ;;
        404) fixes=false ;;
        *) refused "${api}/automated-security-fixes" ;;
    esac
    toggle "automated security fixes" "${api}/automated-security-fixes" \
        "$(jq -r .automated_security_fixes "${settings}")" "${fixes}"

    # A repository that can have it answers 200 either way, so 404 is "not offered".
    get "${api}/private-vulnerability-reporting"
    case "${http}" in
        200)
            toggle "private vulnerability reporting" "${api}/private-vulnerability-reporting" \
                "$(jq -r .private_vulnerability_reporting "${settings}")" "$(jq -r .enabled <<<"${body}")"
            ;;
        404) skip "private vulnerability reporting" ;;
        *) refused "${api}/private-vulnerability-reporting" ;;
    esac

    get "${api}/rulesets" --paginate
    if [[ "${http}" == 403 ]] && jq -e '.message | test("^Upgrade to ")' <<<"${body}" >/dev/null 2>&1; then
        skip "rulesets"
        if [[ "${differences}" == "${before}" ]]; then
            echo "    ✔ in step"
        fi
        continue
    fi
    [[ "${http}" == 200 ]] || refused "${api}/rulesets"
    # --paginate prints one array per page.
    existing="$(jq -cs '[.[][] | select(.source_type == "Repository") | {name, id}]' <<<"${body}")"
    wanted="$(jq -c --arg r "${repo}" '.repositories[$r] // .default' "${assignments}")"

    while IFS= read -r name; do
        file="${root}/rulesets/${name}.json"
        [[ -f "${file}" ]] || die "settings/rulesets.json names ${name}, but rulesets/${name}.json is missing"
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
        get "${api}/rulesets/${id}"
        [[ "${http}" == 200 ]] || refused "${api}/rulesets/${id}"
        current="$(jq -c "${comparable}" <<<"${body}")"
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
