#!/usr/bin/env bash
# Tests repo-settings.sh against a stand-in for gh that answers from files, so nothing
# here reaches GitHub. Run: bash .devtools/test-repo-settings.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "${here}/.." && pwd)"
failures=0

work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

# The stand-in. A GET of <path> prints ${GH_STUB}/<path with / as _>.json. A file
# beside it ending in .status holds "<code> <message>" and turns the answer into the
# failure gh reports: the body on stdout, the message on stderr, exit 1. Any other
# method is recorded in ${GH_STUB}/calls with its input, and succeeds.
mkdir "${work}/bin"
cat >"${work}/bin/gh" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
[[ "$1" == api ]] || { echo "the stand-in only knows gh api" >&2; exit 64; }
shift
method=GET path="" input=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -X) method="$2"; shift 2 ;;
        --input) input="$2"; shift 2 ;;
        --paginate) shift ;;
        *) path="$1"; shift ;;
    esac
done
if [[ "${method}" != GET ]]; then
    echo "${method} ${path} $([[ -z "${input}" ]] || jq -c .)" >>"${GH_STUB}/calls"
    exit 0
fi
file="${GH_STUB}/${path//\//_}"
if [[ -f "${file}.status" ]]; then
    read -r code message <"${file}.status"
    jq -cn --arg m "${message}" --arg s "${code}" '{message: $m, status: $s}'
    echo "gh: ${message} (HTTP ${code})" >&2
    exit 1
fi
[[ -f "${file}.json" ]] || { echo "the stand-in has no answer for ${path}" >&2; exit 64; }
cat "${file}.json"
STUB
chmod +x "${work}/bin/gh"
export PATH="${work}/bin:${PATH}"

settings="${root}/settings/repository.json"

# fixture <dir> <repo> public|private: a repository already in step with settings/.
# A private one answers as GitHub does on the Free plan.
fixture() {
    local dir="$1" repo="$2" kind="$3" base id=0 name
    base="${dir}/repos_studiobimo_${repo}"
    mkdir -p "${dir}"
    jq -c .repository "${settings}" >"${base}.json"
    jq -c .actions "${settings}" >"${base}_actions_permissions.json"
    jq -c .workflow "${settings}" >"${base}_actions_permissions_workflow.json"
    : >"${base}_vulnerability-alerts.json"
    echo '{"enabled":true}' >"${base}_automated-security-fixes.json"
    if [[ "${kind}" == private ]]; then
        jq -c '.repository | .security_and_analysis = null' "${settings}" >"${base}.json"
        echo "404 Not Found" >"${base}_private-vulnerability-reporting.status"
        echo "403 Upgrade to GitHub Pro or make this repository public to enable this feature." \
            >"${base}_rulesets.status"
        return
    fi
    echo '{"enabled":true}' >"${base}_private-vulnerability-reporting.json"
    echo '[]' >"${base}_rulesets.json"
    while IFS= read -r name; do
        id=$((id + 1))
        jq -c --argjson id "${id}" '. + {id: $id, source_type: "Repository"}' \
            "${root}/rulesets/${name}.json" >"${base}_rulesets_${id}.json"
        jq -c --slurpfile r "${base}_rulesets_${id}.json" '. + $r' "${base}_rulesets.json" \
            >"${base}_rulesets.json.new"
        mv "${base}_rulesets.json.new" "${base}_rulesets.json"
    done < <(jq -r '.default[]' "${root}/settings/rulesets.json")
}

# run <dir> <arguments...>: the output in $out, the exit code in $code.
run() {
    local dir="$1"
    shift
    code=0
    out="$(GH_STUB="${dir}" bash "${here}/repo-settings.sh" "$@" 2>&1)" || code=$?
}

check() {
    if "${@:2}"; then
        echo "✔ $1"
    else
        echo "✖ $1" >&2
        echo "      ${out//$'\n'/$'\n'      }" >&2
        failures=$((failures + 1))
    fi
}

exits() { [[ "${code}" == "$1" ]]; }
says() { grep -qF -- "$1" <<<"${out}"; }
silent_on() { ! grep -qF -- "$1" <<<"${out}"; }
called() { grep -qE -- "$1" "$2/calls" 2>/dev/null; }
never_called() { ! called "$@"; }

echo "a public repository"
d="${work}/public"
fixture "${d}" app public
run "${d}" --check app
check "in step exits 0" exits 0
check "and says so" says "✔ in step"
check "and skips nothing" silent_on "(skipped)"

rm "${d}/repos_studiobimo_app_rulesets_1.json"
jq -c 'del(.[0])' "${d}/repos_studiobimo_app_rulesets.json" >"${d}/r" && mv "${d}/r" "${d}/repos_studiobimo_app_rulesets.json"
run "${d}" --check app
check "a missing ruleset is a difference: --check exits 1" exits 1
check "and changes nothing" never_called . "${d}"
run "${d}" app
check "apply creates it and exits 0" exits 0
check "with a POST" called '^POST repos/studiobimo/app/rulesets ' "${d}"

echo "a private repository on the Free plan"
d="${work}/private"
fixture "${d}" secret private
run "${d}" --check secret
check "what the plan lacks is not a difference: --check exits 0" exits 0
check "skips the rulesets, with a note" says "! rulesets: not offered"
check "skips secret scanning" says "! secret scanning: not offered"
check "skips private vulnerability reporting" says "! private vulnerability reporting: not offered"
check "prints no error body" silent_on '"message"'

jq -c '.delete_branch_on_merge = false' "${d}/repos_studiobimo_secret.json" >"${d}/r" && mv "${d}/r" "${d}/repos_studiobimo_secret.json"
run "${d}" --check secret
check "a real difference still exits 1" exits 1
run "${d}" secret
check "apply exits 0" exits 0
check "patches the settings" called '^PATCH repos/studiobimo/secret \{.*"delete_branch_on_merge":true' "${d}"
check "without the secret scanning it cannot have" never_called 'security_and_analysis' "${d}"
check "and writes no ruleset" never_called 'rulesets' "${d}"

echo "several repositories"
d="${work}/both"
fixture "${d}" a-secret private
fixture "${d}" b-app public
run "${d}" --check a-secret b-app
check "a private one does not stop the run" says "studiobimo/b-app"
check "exits 0 when both are in step" exits 0

echo "failures"
d="${work}/broken"
fixture "${d}" app public
rm "${d}/repos_studiobimo_app_rulesets.json"
echo "500 Server Error" >"${d}/repos_studiobimo_app_rulesets.status"
run "${d}" --check app
check "a refused request exits 2, not 1" exits 2
check "and names it" says "GET repos/studiobimo/app/rulesets: HTTP 500"

d="${work}/forbidden"
fixture "${d}" app public
rm "${d}/repos_studiobimo_app_rulesets.json"
echo "403 Resource not accessible by personal access token" >"${d}/repos_studiobimo_app_rulesets.status"
run "${d}" --check app
check "a 403 that is not about the plan exits 2" exits 2

d="${work}/gone"
mkdir "${d}"
echo "404 Not Found" >"${d}/repos_studiobimo_nope.status"
run "${d}" --check nope
check "a repository that does not exist exits 2" exits 2

if ((failures > 0)); then
    echo "${failures} failed" >&2
    exit 1
fi
echo "All passed"
