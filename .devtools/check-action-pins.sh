#!/bin/sh
#
# Guards the three-way pinning rule for refs to THIS repo's own composite actions.
#
# Why this can't be left to zizmor or to CI passing: GitHub's "require actions pinned
# to a full-length commit SHA" policy (already on for studiobimo/.github) exempts
# reusable-WORKFLOW refs but not composite actions. Our own `self-*` runs are same-repo,
# where GitHub's same-repo exemption hides a bad ref entirely -- so a mistake here ships
# green and breaks every consumer instead.
#
# The rule, by where the file ends up running:
#
#   .github/workflows/**   -> `$/.github/actions/<name>`. These run in the CONSUMER's
#                             context, but `$/` is a self-repository ref: it resolves to
#                             this repo at the commit the caller pinned, and is exempt
#                             from the SHA policy. A `studiobimo/...@<ref>` form here
#                             would work but needlessly hardcodes a version.
#
#   workflow-templates/**  -> `studiobimo/.github/.github/actions/<name>@<40-hex sha>`.
#                             These are COPIED INTO the consumer's repo and run as their
#                             own workflow, where `$/` would resolve to THEIR repo. So
#                             the full foreign path is required, and the SHA policy
#                             applies with no exemption.
#
# Reusable-workflow refs (studiobimo/.github/.github/workflows/<name>.yml@v1) are
# deliberately NOT flagged: they are policy-exempt, and the moving tag is the whole
# delivery mechanism.
#
# Usage: sh .devtools/check-action-pins.sh
set -eu

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"

status=0

report() {
  # $1 = message, $2 = offending lines
  [ -n "$2" ] || return 0
  echo "error: $1" >&2
  echo "$2" >&2
  echo >&2
  status=1
}

scan() {
  # $1 = grep -E pattern, rest = files
  pattern="$1"
  shift
  for f in "$@"; do
    [ -f "$f" ] || continue
    grep -nE "$pattern" "$f" | sed "s|^|  $f:|" || true
  done
}

workflows="$(git ls-files '.github/workflows/*.yml' '.github/workflows/*.yaml')"
templates="$(git ls-files 'workflow-templates/*.yml' 'workflow-templates/*.yaml')"

# --- 1. The doubled `.github` is not optional, anywhere. ----------------------------
# studiobimo/.github/actions/... drops the repo's own .github/ directory and 404s.
# shellcheck disable=SC2086 # deliberate word splitting: these are file lists
bad_path="$(scan 'uses:[[:space:]]*studiobimo/\.github/actions/' $workflows $templates)"
report "composite-action ref(s) missing the doubled '.github' (repo name, then its .github/actions/):
  want: studiobimo/.github/.github/actions/<name>@<sha>" "$bad_path"

# --- 2. Inside our own workflows, use the self-repository form. ----------------------
# shellcheck disable=SC2086
bad_self="$(scan 'uses:[[:space:]]*studiobimo/\.github/\.github/actions/' $workflows)"
report "composite-action ref(s) in .github/workflows/ should use the self-repository form:
  want: \$/.github/actions/<name>   (resolves to this repo at the caller's pinned commit)" "$bad_self"

# A workspace-relative ACTION ref loads whatever is on disk at that path, which in a
# called workflow is the CALLER's checkout -- the bug `$/` exists to remove. Local
# reusable-WORKFLOW calls (uses: ./.github/workflows/<name>.yml) are a different thing
# and entirely correct: that is how this repo dogfoods its own library, so they are
# excluded by their .yml/.yaml suffix.
# shellcheck disable=SC2086
bad_rel="$(scan 'uses:[[:space:]]*\./' $workflows | grep -vE '\.ya?ml[[:space:]]*$' || true)"
report "workspace-relative action ref(s) in .github/workflows/ resolve against the CALLER's
  checkout, not this repo:
  want: \$/.github/actions/<name>" "$bad_rel"

# --- 3. Inside templates, the full foreign path, SHA-pinned. ------------------------
if [ -n "$templates" ]; then
  # shellcheck disable=SC2086
  bad_selfref="$(scan 'uses:[[:space:]]*\$/' $templates)"
  report "self-repository ref(s) in workflow-templates/ would resolve to the CONSUMER's repo
  once copied there:
  want: studiobimo/.github/.github/actions/<name>@<sha> # vX.Y.Z" "$bad_selfref"

  bad_pin=""
  for f in $templates; do
    [ -f "$f" ] || continue
    hits="$(grep -nE 'uses:[[:space:]]*studiobimo/\.github/\.github/actions/[^@]+@[^[:space:]]+' "$f" \
      | { grep -vE '@[0-9a-f]{40}([[:space:]]|$)' || true; } \
      | sed "s|^|  $f:|")"
    [ -n "$hits" ] && bad_pin="${bad_pin}${hits}
"
  done
  report "composite-action ref(s) in workflow-templates/ not pinned to a full 40-char SHA:
  want: studiobimo/.github/.github/actions/<name>@<sha> # vX.Y.Z" "$bad_pin"
fi

if [ "$status" -eq 0 ]; then
  echo "check-action-pins: all first-party action refs follow the pinning rule."
fi
exit "$status"
