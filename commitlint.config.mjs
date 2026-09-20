/**
 * Conventional Commits, enforced locally by the lefthook `commit-msg` hook and in CI on
 * every commit in a branch (see .github/workflows/ci-pr.yml). Inherit
 * @commitlint/config-conventional and add only org-specific rules here.
 */
export default {
  extends: ["@commitlint/config-conventional"],
  rules: {
    // The scopes AGENTS.md documents, made binding. A scope stays optional --
    // `docs: ...` is fine -- but an invented one is not, so the list in AGENTS.md
    // and the list commits actually use cannot drift apart.
    "scope-enum": [2, "always", ["actions", "workflows", "devtools", "templates", "docs", "deps"]],
  },
};
