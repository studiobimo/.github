# Agent guide: studiobimo/.github

Instructions for AI coding agents working in this repo.

## Project

Org-wide CI for [studiobimo](https://github.com/studiobimo): reusable workflows under
`.github/workflows/`, composite actions under `actions/<name>/`, shared check scripts under
`scripts/`. Nothing here is application code; everything here runs in other repositories' CI.

Consumers: `studiobimo/tallyhopper` (Minecraft mod, Java/Gradle) is the first.

## Non-negotiables

- **Pinning.** Every `uses:` is a full-length commit SHA with the version in a trailing comment
  (`# v4.2.2`). Never a tag or branch. Resolve a tag to its commit with
  `gh api repos/<owner>/<repo>/git/ref/tags/<tag>`, dereferencing an annotated tag object.
- **Permissions.** `permissions: {}` at workflow level. Each job requests the minimum it needs, and a
  job that reads a secret never runs untrusted code from a fork.
- **Checkout.** `persist-credentials: false` everywhere.
- **Secrets.** Passed explicitly by the caller. Never `secrets: inherit`.
- **Commits:** [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/).
  Scopes: `actions`, `workflows`, `scripts`, `docs`, `deps`.
- **Branches:** [Conventional Branch](https://conventionalbranch.org/), e.g. `feat/publish-mod`.
- **PR size:** at most 20 changed files. Split bigger work with `gh stack`.

## Testing a change

A reusable workflow cannot be tested from inside this repo alone. Either:

1. point a consumer repo's wrapper at your branch SHA and open a draft PR there; or
2. add a `workflow_dispatch` smoke-test caller under `.github/workflows/selftest-*.yml`.

Say in the PR which one you did. "It should work" is not a test result.

## Versioning

Tagged `vX.Y.Z` with a floating `vX` tag. Callers still pin by SHA; the floating tag exists so a
human can read what a SHA corresponds to.
