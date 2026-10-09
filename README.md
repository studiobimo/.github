# studiobimo/.github

[![Release](https://github.com/studiobimo/.github/actions/workflows/release.yml/badge.svg?branch=main)](https://github.com/studiobimo/.github/actions/workflows/release.yml)

Reusable workflows, composite actions and org defaults for [studiobimo](https://github.com/studiobimo).

Repositories here call these instead of copying CI into every project, so a fix to a check lands
everywhere at once.

## Reusable workflows

| Workflow                                  | What it does                                                                       |
| ----------------------------------------- | ---------------------------------------------------------------------------------- |
| `.github/workflows/ci-pr.yml`             | Conventional PR title and branch name, PR size limit, Conventional Commits         |
| `.github/workflows/ci-java-gradle.yml`    | Java toolchain, Gradle build, test reports and artifacts                           |
| `.github/workflows/ci-go.yml`             | Go modules, formatting, golangci-lint, race tests, coverage, govulncheck and build |
| `.github/workflows/ci-workflows.yml`      | actionlint, zizmor and shellcheck over a repo's own workflows                      |
| `.github/workflows/ci-lint.yml`           | A repo's own lefthook hooks, over the whole tree, with the tools mise pins         |
| `.github/workflows/ci-pre-commit.yml`     | Deprecated, use `ci-lint.yml`. A repo's own pre-commit hooks                       |
| `.github/workflows/ci-gitleaks.yml`       | Secret scan of what a pull request adds                                            |
| `.github/workflows/ci-template-drift.yml` | Compares a repo with the project template and tracks the difference in one issue   |
| `.github/workflows/release-please.yml`    | Grooms a release pull request from Conventional Commits, then tags and releases    |
| `.github/workflows/publish-mod.yml`       | Builds, attests and publishes a Minecraft mod to Modrinth, CurseForge and GitHub   |

A repo with a `.pre-commit-config.yaml` should call `ci-pre-commit.yml` **instead of**
`ci-workflows.yml` and `ci-gitleaks.yml`, not alongside them. Those two download actionlint,
zizmor, shellcheck and gitleaks for repos that have no pre-commit config; a repo that has one
already pins the same four by frozen `rev:`, so calling both runs them twice at two sets of
version numbers that drift. One source of truth per repo.

Every `ci-*` file is `on: workflow_call` — library code, not something that runs on this repo's
own pull requests. The `self-*` files are this repo's thin callers of them, which is how the
library is proven before a consumer pins `@v1`. `release.yml` is neither: it is this repo's own
release, and the only thing here that writes a tag.

`release-please.yml` and `publish-mod.yml` are library code too, under a plain name rather than a
`ci-` one: they run at release time in a consumer, not on its pull requests. Neither has a `self-*`
caller — there is no mod to publish here, and this repo releases itself with semantic-release, so
running release-please against it would fight over the same tags. Both are therefore tested
cross-repo; see [AGENTS.md](AGENTS.md).

## Composite actions

| Action                              | What it does                                                 |
| ----------------------------------- | ------------------------------------------------------------ |
| `.github/actions/setup-java-gradle` | Temurin JDK + Gradle with branch-aware caching               |
| `.github/actions/pr-checks`         | The PR title, branch, size and commit checks, for direct use |
| `.github/actions/build-summary`     | JUnit + JaCoCo numbers into the job summary                  |

## Git hooks

studiobimo repositories run their git hooks with [lefthook](https://lefthook.dev/) and pin every
tool with [mise](https://mise.jdx.dev/). A repository's own `lefthook.yml` holds its linters, and
[`ci-lint.yml`](.github/workflows/ci-lint.yml) runs that same file in CI.

The org's own rules come from here. [`lefthook/org.yml`](lefthook/org.yml) checks the branch name
and the pull request size on push, with the scripts `ci-pr.yml` runs, so what passes locally
passes on the pull request. A repository pulls it in by tag:

```yaml
# lefthook.yml
remotes:
  - git_url: https://github.com/studiobimo/.github
    ref: v1.9.0
    configs:
      - lefthook/org.yml
```

lefthook fetches a remote once per `ref`, so pin a release tag there, never the floating `v1`.

Two environment variables make the checks usable from a script, such as an agent guard that has
to judge a command before it runs:

```sh
BRANCH_NAME=feat/new-thing lefthook run pre-push --job branch-name
PR_BASE=main lefthook run pre-push --job pr-size
```

`PR_MAX_FILES` changes the limit. Without `PR_BASE` the base is the branch below this one in a
`gh stack`, else the remote's default branch.

Commit messages are not part of `org.yml`: each repository runs commitlint against its own
`.commitlintrc.yaml` in a `commit-msg` job, and `ci-pr.yml` reads the same file.

## Pre-commit hooks (deprecated)

This repo is still a [pre-commit](https://pre-commit.com/) hook repo for the repositories that
have not moved to lefthook yet. It goes away when the last one has.

| Hook id               | Stage                | What it checks                                                 |
| --------------------- | -------------------- | -------------------------------------------------------------- |
| `conventional-commit` | `commit-msg`         | The commit subject is a Conventional Commit                    |
| `conventional-branch` | `pre-push`, `manual` | The branch name follows Conventional Branch                    |
| `pr-size`             | `pre-push`, `manual` | The branch changes at most 20 files against the layer below it |

```yaml
# .pre-commit-config.yaml
default_install_hook_types: [pre-commit, commit-msg, pre-push]
repos:
  - repo: https://github.com/studiobimo/.github
    rev: <full-sha> # frozen: v1.5.0
    hooks:
      - id: conventional-commit
      - id: conventional-branch
      - id: pr-size
```

Pin `rev:` to a release SHA with the version in a `frozen:` comment; Dependabot's `pre-commit`
ecosystem bumps both. The floating `v1` tag is for workflows only: pre-commit caches a hook repo by
`rev`, so a moving tag would never be re-fetched.

## Calling them

Call a workflow at `@v1`. The tag floats: a release moves it, so a fix lands everywhere at once.

```yaml
jobs:
  pr-checks:
    uses: studiobimo/.github/.github/workflows/ci-pr.yml@v1
    with:
      max-files: 20
```

`ci-pr` lints the pull request title and every commit with
[commitlint](https://commitlint.js.org/), against
[`@commitlint/config-conventional`](https://github.com/conventional-changelog/commitlint/tree/master/%40commitlint/config-conventional):
a known type, a lowercase subject with no full stop, and at most 100 characters in the header and
in each body line. A repository adds its own rules with a `.commitlintrc.yaml` at its root, most
often to make its scopes the only ones allowed:

```yaml
# .commitlintrc.yaml
extends:
  - "@commitlint/config-conventional"
rules:
  scope-enum: [2, always, [api, docs, deps]]
```

Only `rules` is read from that file on a pull request. `extends`, `plugins` and `parserPreset`
are ignored there, because each can name a script and that script would be the author's; the
base is always config-conventional. The `extends` line is for the local `commit-msg` hook, which
reads the whole file.

Dependabot's pull requests are not linted. Its titles and commit bodies run past 100 characters
and it offers no way to shorten them.

A **composite action** is different — reference it by full path and full SHA:

```yaml
- uses: studiobimo/.github/.github/actions/setup-java-gradle@<full-sha> # v1.0.0
```

GitHub's `sha_pinning_required` policy exempts reusable-workflow refs but not composite actions,
and in your repo this one is a foreign action. Dependabot bumps the SHA from the version comment.

Secrets are passed explicitly. Nothing here uses `secrets: inherit`.

```yaml
publish:
  uses: studiobimo/.github/.github/workflows/publish-mod.yml@v1
  secrets:
    modrinth-token: ${{ secrets.MODRINTH_TOKEN }}
    curseforge-token: ${{ secrets.CURSEFORGE_TOKEN }}
```

Releasing is two workflows in one run, and they must stay in one run: a tag created with
`GITHUB_TOKEN` does not trigger a separate `on: push: tags` workflow, so the publish step is gated
on the release step's output instead.

```yaml
jobs:
  release:
    permissions:
      contents: write
      pull-requests: write
    uses: studiobimo/.github/.github/workflows/release-please.yml@v1

  publish:
    needs: release
    if: ${{ needs.release.outputs.release-created == 'true' }}
    permissions:
      contents: write
      id-token: write
      attestations: write
    uses: studiobimo/.github/.github/workflows/publish-mod.yml@v1
    with:
      tag-name: ${{ needs.release.outputs.tag-name }}
```

`workflow-templates/release-mod.yml` is that wiring, ready to copy.

Each workflow and action documents its own inputs: the workflows in their `workflow_call` block, the
actions in a README beside them.

A reusable workflow here reaches its own composite actions with `$/.github/actions/<name>` —
GitHub's self-repository form, which resolves to this repository at the exact commit the caller
pinned. No second checkout, and no self-referential SHA to bump on every release.

## Conventions

- Third-party `uses:` are pinned to a full-length commit SHA, with the version in a comment.
  Our own workflows are called at `@v1`; our own actions are reached with `$/` from here and
  SHA-pinned from a workflow template. `sh .devtools/check-action-pins.sh` enforces it.
- `permissions: {}` at workflow level; each job asks for the least it needs.
- `persist-credentials: false` on every checkout.
- No secret is ever read in a workflow that also runs untrusted code from a fork.

## Rulesets and repository settings

Every repository carries the same settings and the same branch rules, defined here once.

| Path                         | What it holds                                                                 |
| ---------------------------- | ----------------------------------------------------------------------------- |
| `rulesets/<name>.json`       | One ruleset each, in the format GitHub's **Import a ruleset** reads           |
| `settings/repository.json`   | Merge options, features, security and Actions settings every repo should have |
| `settings/rulesets.json`     | Which rulesets a repository gets; `default` unless it is listed by name       |
| `.devtools/repo-settings.sh` | Compares every repository with the above, and applies it                      |

| Ruleset              | Applies to                   | Rules                                                                                           |
| -------------------- | ---------------------------- | ----------------------------------------------------------------------------------------------- |
| `default-branch`     | the default branch           | Pull request required, squash only, linear history, resolved threads, no force push or deletion |
| `release-tags`       | `v*.*.*` and `<name>-v*.*.*` | A release tag cannot be moved or deleted. The floating `v1` is not matched                      |
| `checks-project`     | the default branch           | Requires `pr-checks` and `lint`                                                                 |
| `checks-java-gradle` | the default branch           | Requires the Gradle `check`                                                                     |
| `checks-dotgithub`   | the default branch           | Requires this repo's own `self-*` checks                                                        |

Rulesets stack, so a repository takes the pieces that fit it. Required checks are separate from
`default-branch` because the check names differ by stack.

```sh
bash .devtools/repo-settings.sh --check            # what differs, in every repo; changes nothing
bash .devtools/repo-settings.sh                    # apply to every repo
bash .devtools/repo-settings.sh --check datapacks  # one repo
```

It needs `gh` signed in as an admin of the repositories. Only keys written in `settings/` are
compared, and a ruleset that is on a repository but not listed for it is reported, never deleted.
To add one ruleset by hand instead: Settings → Rules → Rulesets → New ruleset → Import a ruleset.

A private repository gets less. On the Free plan GitHub offers it no rulesets, no secret scanning
and no private vulnerability reporting, so the script skips those with a note and applies the
rest: its default branch is not protected. A skip is not a difference. The script exits 0 when
everything is in step or applied, 1 when `--check` found differences, and 2 on any failure.

These are repository rulesets applied one repo at a time, because organization-wide rulesets
are not available on the Free plan.

Repository admins can bypass the branch rules on a pull request, and one case needs it: the
release pull request that release-please opens with `GITHUB_TOKEN` does not trigger workflows, so
its required checks never report. Merge it with the bypass checkbox, or give release-please its
own token (`release-token`) so the checks run.

## Workflow templates

`workflow-templates/` holds starters offered in the "New workflow" UI of other studiobimo
repositories. Unlike a reusable workflow, a template is **copied into the consuming repo** and
owned by it from then on, so it uses `$default-branch` for branch refs and stays editable.

That copy is also why the pinning rule differs there. A template runs in the consumer's repo,
where `studiobimo/.github` is a foreign repository: `$/` would resolve to _their_ repo, and a
`@v1` tag on a composite action would be rejected by `sha_pinning_required`. Reusable-workflow
refs stay `@v1`; any action ref in a template must be a full SHA.
`sh .devtools/check-action-pins.sh` enforces exactly this split.

One caveat. Templates are not audited by zizmor — it only collects files under
`.github/workflows/` — so `check-action-pins` and review are the whole safety net for them.

## Working on this repo

```sh
mise install                    # every tool, at the versions in mise.toml and mise.lock
mise exec -- lefthook install   # wire the git hooks
npm ci --prefix .github/actions/pr-checks --ignore-scripts   # the commitlint ci-pr installs
bash .devtools/test-hooks.sh    # the check scripts behind ci-pr and the git hooks
bash .devtools/test-repo-settings.sh   # repo-settings.sh, against a stand-in for gh
```

[mise](https://mise.jdx.dev/getting-started.html) is the one thing to install by hand. It pins
lefthook, Prettier, commitlint, actionlint, zizmor, shellcheck and gitleaks, and the hooks run
each through `mise exec`, so they work whether or not your shell has mise activated. Nothing is
skipped when a tool is missing: `mise install` is what makes it present.

### Bumping a tool

Dependabot does not read `mise.toml`, so the tools pinned there are bumped by hand. Nothing
reminds you; check them when you touch the file, and at least when a linter reports something
its newer release has fixed.

```sh
mise outdated --bump            # what has a newer release
$EDITOR mise.toml               # change the version
mise lock                       # record the new checksums
mise install && mise exec -- lefthook run pre-commit --all-files
git add mise.toml mise.lock .mise/locks
```

`.mise/locks/` holds the dependency graph of each npm tool, so it is committed with the other
two. Leave a release a week before taking it, as Dependabot's cooldown does for everything else.

pnpm is only here for semantic-release, which `release.yml` runs.

## Contributing

[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) and
[Conventional Branch](https://conventionalbranch.org/) apply here too, and a PR changes at most 20
files. See [AGENTS.md](AGENTS.md).

## License

[MIT](LICENSE)
