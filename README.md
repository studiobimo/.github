# studiobimo/.github

Reusable workflows, composite actions and org defaults for [studiobimo](https://github.com/studiobimo).

Repositories here call these instead of copying CI into every project, so a fix to a check lands
everywhere at once.

## Reusable workflows

| Workflow                                  | What it does                                                                     |
| ----------------------------------------- | -------------------------------------------------------------------------------- |
| `.github/workflows/ci-pr.yml`             | Conventional PR title and branch name, PR size limit, Conventional Commits       |
| `.github/workflows/ci-java-gradle.yml`    | Java toolchain, Gradle build, test reports and artifacts                         |
| `.github/workflows/ci-workflows.yml`      | actionlint, zizmor and shellcheck over a repo's own workflows                    |
| `.github/workflows/ci-pre-commit.yml`     | A repo's own pre-commit hooks, over the whole tree                               |
| `.github/workflows/ci-gitleaks.yml`       | Secret scan of what a pull request adds                                          |
| `.github/workflows/ci-template-drift.yml` | Compares a repo with the project template and tracks the difference in one issue |
| `.github/workflows/release-please.yml`    | Grooms a release pull request from Conventional Commits, then tags and releases  |
| `.github/workflows/publish-mod.yml`       | Builds, attests and publishes a Minecraft mod to Modrinth, CurseForge and GitHub |

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

## Pre-commit hooks

This repo is also a [pre-commit](https://pre-commit.com/) hook repo. The hooks are the same
scripts `ci-pr.yml` runs, so what passes locally passes on the pull request.

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

Two environment variables make the hooks usable from a script, such as an agent guard that has to
judge a command before it runs:

```sh
BRANCH_NAME=feat/new-thing pre-commit run conventional-branch --hook-stage manual
PR_BASE=main pre-commit run pr-size --hook-stage manual
```

`PR_MAX_FILES` changes the limit. Without `PR_BASE` the base is the branch below this one in a
`gh stack`, else the remote's default branch.

## Calling them

Call a workflow at `@v1`. The tag floats: a release moves it, so a fix lands everywhere at once.

```yaml
jobs:
  pr-checks:
    uses: studiobimo/.github/.github/workflows/ci-pr.yml@v1
    with:
      max-files: 20
```

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
sh .devtools/install-hooks.sh
bash .devtools/test-hooks.sh   # the check scripts behind ci-pr and the pre-commit hooks
```

pnpm installs the Node toolchain (commitlint, Prettier, lefthook, semantic-release) and wires the
git hooks; Homebrew adds actionlint, zizmor, shellcheck and gitleaks. The hooks warn and skip when
a linter is missing, so a fresh clone can always commit — CI has no such escape hatch.

## Contributing

[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) and
[Conventional Branch](https://conventionalbranch.org/) apply here too, and a PR changes at most 20
files. See [AGENTS.md](AGENTS.md).

## License

[MIT](LICENSE)
