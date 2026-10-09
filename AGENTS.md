# Agent guide: studiobimo/.github

Instructions for AI coding agents working in this repo.

## Project

Org-wide CI for [studiobimo](https://github.com/studiobimo): reusable workflows under
`.github/workflows/`, composite actions under `.github/actions/<name>/`, shared check scripts
under `.devtools/`, and `lefthook/org.yml`, which exposes those scripts to consumers' git hooks.
`rulesets/` and `settings/` hold the branch rules and repository settings every repo shares;
`.devtools/repo-settings.sh` checks and applies them. Nothing here is application code;
everything here runs in other repositories' CI or on their contributors' machines.

Consumers: every studiobimo project, through `studiobimo/project-template`.

Tooling: mise pins every tool (`mise.toml`, `mise.lock`), lefthook runs the hooks
(`lefthook.yml`), commitlint checks commits (`.commitlintrc.yaml`). pnpm and `package.json` exist
only for semantic-release. This repo releases with semantic-release, not release-please like
the projects: it has no version file or changelog to maintain, and `release.yml` has to move the
floating `v1` tag. `.pre-commit-hooks.yaml` and `ci-pre-commit.yml` are deprecated and stay
until no consumer uses them.

## Non-negotiables

- **Pinning.** Three rules, split by who controls the tag. `sha_pinning_required` is **on** for
  this repo (`gh api repos/studiobimo/.github/actions/permissions`), and it exempts
  reusable-workflow refs and local `./` / `$/` refs, but _not_ composite actions.

  | Ref                                    | Form                          |
  | -------------------------------------- | ----------------------------- |
  | Third-party action                     | full 40-char SHA + `# vX.Y.Z` |
  | Our workflow, called by a consumer     | `@v1`                         |
  | Our action, from our own workflow      | `$/.github/actions/<name>`    |
  | Our action, from `workflow-templates/` | full 40-char SHA + `# vX.Y.Z` |

  The last row is the trap: a template is **copied into the consumer's repo** and runs there, so
  `$/` would resolve to _their_ repo and a `@v1` tag would be rejected by the SHA policy. Run
  `sh .devtools/check-action-pins.sh` — `self-lint` does too, because same-repo dogfooding can't
  catch a bad ref (GitHub's same-repo exemption hides it here and it breaks only consumers).

  Resolve a tag to its commit with `gh api repos/<owner>/<repo>/git/ref/tags/<tag>`,
  dereferencing an annotated tag object.

- **Permissions.** `permissions: {}` at workflow level. Each job requests the minimum it needs, and a
  job that reads a secret never runs untrusted code from a fork.
- **Checkout.** `persist-credentials: false` everywhere.
- **Secrets.** Passed explicitly by the caller. Never `secrets: inherit`.
- **Commits:** [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/).
  Scopes: `actions`, `workflows`, `devtools`, `templates`, `docs`, `deps`.
- **Branches:** [Conventional Branch](https://conventionalbranch.org/), e.g. `feat/publish-mod`.
- **Templates.** `workflow-templates/` is copied into consumers, not called by them, so it is
  the one place the SHA rule bites: `$/` there would resolve to _their_ repo. zizmor does not
  audit these files at all, so `check-action-pins` and review are the only net.
- **PR size:** at most 20 changed files. Split bigger work with `gh stack`.
- **Naming.** `ci-*` is library code (`on: workflow_call`), called by consumers at `@v1`.
  `self-*` is this repo's own caller of one, invoked by local path
  (`uses: ./.github/workflows/ci-*.yml`) so the library is dogfooded before anyone pins it.
  `release` is the one bare name: it is genuinely not a caller. Any new caller is `self-*`.

## Testing a change

Most of it is covered from inside this repo: the `self-*` workflows call each `ci-*` reusable by
local path, so a pull request here runs the library against its own source.

What that cannot cover is anything that only differs cross-repo — above all whether a `$/` ref
resolves to this repository rather than the caller's, and whether a first-party action ref is
pinned in a form a consumer will accept. For those, point a consumer repo's wrapper at your branch
SHA and open a draft PR there.

The check scripts have their own test, `bash .devtools/test-hooks.sh`, which `self-lint` runs. It
also covers `pr-checks/lint.mjs`, so run `npm ci --prefix .github/actions/pr-checks --ignore-scripts`
first.
They are called two ways — by `ci-pr` with explicit arguments and by the git hooks with none — so
a change to one has to keep both working. Try the shared hooks end to end from a consumer checkout
by pointing its `remotes` entry at your branch: `ref: <branch>` with `refetch: true`.

Say in the PR which one you did. "It should work" is not a test result.

## Versioning

Tagged `vX.Y.Z` with a floating `vX` tag. Consumers call the workflows at `@v1`; a release moves
that tag, which is how a fix reaches everyone at once. Don't hand-edit tags — `release.yml` owns
both.
