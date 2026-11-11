# studiobimo/.github

[![Release](https://github.com/studiobimo/.github/actions/workflows/release.yml/badge.svg?branch=main)](https://github.com/studiobimo/.github/actions/workflows/release.yml)

Reusable workflows, composite actions and org defaults for [studiobimo](https://github.com/studiobimo).

Repositories here call these instead of copying CI into every project, so a fix to a check lands
everywhere at once.

## Reusable workflows

| Workflow                                  | What it does                                                                       |
| ----------------------------------------- | ---------------------------------------------------------------------------------- |
| `.github/workflows/ci-pr.yml`             | Conventional PR title and branch name, PR size limit, Conventional Commits         |
| `.github/workflows/ci-issue-title.yml`    | Conventional issue titles, with one comment and a label on an issue that fails     |
| `.github/workflows/ci-java-gradle.yml`    | Java toolchain, Gradle build, test reports and artifacts                           |
| `.github/workflows/ci-go.yml`             | Go modules, formatting, golangci-lint, race tests, coverage, govulncheck and build |
| `.github/workflows/ci-workflows.yml`      | actionlint, zizmor and shellcheck over a repo's own workflows                      |
| `.github/workflows/ci-lint.yml`           | A repo's own lefthook hooks, over the whole tree, with the tools mise pins         |
| `.github/workflows/ci-gitleaks.yml`       | Secret scan of what a pull request adds                                            |
| `.github/workflows/ci-template-drift.yml` | Compares a repo with the project template and tracks the difference in one issue   |
| `.github/workflows/ci-tools-outdated.yml` | Tracks the tools pinned in `mise.toml` that have a newer release in one issue      |
| `.github/workflows/ci-repo-health.yml`    | Dead code, duplication, complexity, SAST and SQL lint, held to a baseline ratchet  |
| `.github/workflows/release-please.yml`    | Grooms a release pull request from Conventional Commits, then tags and releases    |
| `.github/workflows/promote-release.yml`   | Opens the `release/v<version>` pull request from `develop` into `main`             |
| `.github/workflows/semantic-release.yml`  | Tags and releases `main` straight from Conventional Commits                        |
| `.github/workflows/publish-mod.yml`       | Builds, attests and publishes a Minecraft mod to Modrinth, CurseForge and GitHub   |
| `.github/workflows/publish-package.yml`   | Builds and publishes an npm package to GitHub Packages for a release tag           |

A repo with a `lefthook.yml` should call `ci-lint.yml` **instead of** `ci-workflows.yml` and
`ci-gitleaks.yml`, not alongside them. Those two download actionlint, zizmor, shellcheck and
gitleaks for repos that have no hooks of their own; a repo that has them already pins the same
four in `mise.toml`, so calling both runs them twice at two sets of version numbers that drift.
One source of truth per repo.

Every `ci-*` file is `on: workflow_call` — library code, not something that runs on this repo's
own pull requests. The `self-*` files are this repo's thin callers of them, which is how the
library is proven before a consumer pins `@v1`. `release.yml` is neither: it is this repo's own
release, and the only thing here that writes a tag.

`release-please.yml`, `publish-mod.yml` and `publish-package.yml` are library code too, under a plain name rather than a
`ci-` one: they run at release time in a consumer, not on its pull requests. None has a `self-*`
caller — there is nothing to publish here, and this repo releases itself with semantic-release, so
running release-please against it would fight over the same tags. They are therefore tested
cross-repo; see [AGENTS.md](AGENTS.md).

### Skipping work a pull request does not touch

`ci-go`, `ci-java-gradle`, `ci-lint` and `ci-workflows` take a `paths` input: newline-separated
globs, relative to the repository root, in GitHub's `paths:` syntax. When a pull request touches
none of them, the job's steps are skipped and the check still reports success, so it can stay a
required check. A `paths:` filter on the caller's own `on:` block is the wrong tool for this: a
workflow it skips reports nothing, and a required check that never reports blocks the merge.

```yaml
jobs:
  go:
    uses: studiobimo/.github/.github/workflows/ci-go.yml@v1
    with:
      paths: |
        **/*.go
        go.mod
        go.sum
        .golangci-lint-version
```

Empty `paths` is the default and runs every time. Every event other than a pull request always
runs, so a push to the default branch is never skipped. List every file a job reads, including
the version files it pins its tools with, or a change to one of them is skipped.

## Composite actions

| Action                              | What it does                                                 |
| ----------------------------------- | ------------------------------------------------------------ |
| `.github/actions/setup-java-gradle` | Temurin JDK + Gradle with branch-aware caching               |
| `.github/actions/pr-checks`         | The PR title, branch, size and commit checks, for direct use |
| `.github/actions/issue-title`       | The issue title check, for direct use                        |
| `.github/actions/repo-health`       | The repo health check, for direct use after your own setup   |
| `.github/actions/build-summary`     | JUnit + JaCoCo numbers into the job summary                  |
| `.github/actions/changed-paths`     | Whether a pull request touches any of a list of globs        |

## Git hooks

studiobimo repositories run their git hooks with [lefthook](https://lefthook.dev/) and pin every
tool with [mise](https://mise.jdx.dev/). A repository's own `lefthook.yml` holds its linters, and
[`ci-lint.yml`](.github/workflows/ci-lint.yml) runs that same file in CI.

The org's own rules come from here. [`lefthook/org.yml`](lefthook/org.yml) checks the branch name
and the pull request size on push, with the scripts `ci-pr.yml` runs, so what passes locally
passes on the pull request. A repository pulls it in at the floating `v1` tag, the one the
workflows are called at:

```yaml
# lefthook.yml
remotes:
  - git_url: https://github.com/studiobimo/.github
    ref: v1
    refetch_frequency: 24h
    configs:
      - lefthook/org.yml
```

lefthook fetches a remote once per `ref` unless it is told to look again, so `refetch_frequency`
is what lets a moved `v1` reach the hooks: at most once a day. A fix here then lands in the hooks
and in CI together. `remotes` is only read from a repository's root `lefthook.yml`, not from a file
it `extends`.

Two environment variables make the checks usable from a script, such as an agent guard that has
to judge a command before it runs:

```sh
BRANCH_NAME=feat/new-thing lefthook run pre-push --job branch-name
PR_BASE=main lefthook run pre-push --job pr-size
```

`PR_MAX_FILES` changes the limit. Without `PR_BASE` the base is the branch below this one in a
`gh stack`, else the remote's default branch.

`org.yml` also tidies up after a merge. On the default branch, `post-merge` runs
[`prune-merged-branches.sh`](.devtools/prune-merged-branches.sh), which deletes the local branches
whose remote is gone and whose work is in: the tip is an ancestor of the default branch, or, for a
squash merge, `gh` reports a merged pull request with exactly that head. A branch checked out in
any worktree, or one it cannot show to be merged, is kept and named. Run it with `--dry-run` to
see what it would do. A repository gets the hook the next time it runs `lefthook install`.

Commit messages are not part of `org.yml`: each repository runs commitlint against its own
`.commitlintrc.yaml` in a `commit-msg` job, and `ci-pr.yml` reads the same file.

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

`ci-issue-title` holds issue titles to the same rules, from the same `.commitlintrc.yaml`, so an
issue is titled like the commit that will close it. A title that fails gets the
`needs-conventional-title` label and one comment listing what to change; both go once the title
passes. Issues a bot opens are left alone.

```yaml
on:
  issues:
    types: [opened, edited, reopened]

jobs:
  title:
    permissions:
      contents: read
      issues: write
    uses: studiobimo/.github/.github/workflows/ci-issue-title.yml@v1
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
    with:
      app-client-id: ${{ vars.RELEASE_APP_CLIENT_ID }}
    secrets:
      app-private-key: ${{ secrets.RELEASE_APP_PRIVATE_KEY }}

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

An npm package goes to GitHub Packages the same way, with `publish-package.yml` in place of
`publish-mod.yml`. It takes the release tag and the package directory, publishes with the run's
`GITHUB_TOKEN`, and needs no secret. `packages: write` is held by its one job only.

```yaml
publish:
  needs: release
  if: ${{ needs.release.outputs.release-created == 'true' }}
  permissions:
    contents: read
    packages: write
  uses: studiobimo/.github/.github/workflows/publish-package.yml@v1
  with:
    tag-name: ${{ needs.release.outputs.tag-name }}
    package-directory: packages/backstage
    build-command: pnpm build
```

Set `dry-run: true` to build and pack without publishing.

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

| Ruleset                      | Applies to                   | Rules                                                                                           |
| ---------------------------- | ---------------------------- | ----------------------------------------------------------------------------------------------- |
| `default-branch`             | the default branch           | Pull request required, squash only, linear history, resolved threads, no force push or deletion |
| `release-tags`               | `v*.*.*` and `<name>-v*.*.*` | A release tag cannot be moved or deleted. The floating `v1` is not matched                      |
| `checks-project`             | the default branch           | Requires `pr-checks` and `lint`                                                                 |
| `checks-java-gradle`         | the default branch           | Requires the Gradle `check`                                                                     |
| `checks-vite-react-tanstack` | the default branch           | Requires the web app's `check`: build, types, lint, tests, bundle budget, audit                 |
| `checks-dotgithub`           | the default branch           | Requires this repo's own `self-*` checks                                                        |

Rulesets stack, so a repository takes the pieces that fit it. Required checks are separate from
`default-branch` because the check names differ by stack.

A `checks-<profile>` ruleset follows the project-template profiles: a repository whose
`.template-profiles` names `java-gradle` gets `checks-java-gradle` on top of its listed
rulesets, with no entry in `settings/rulesets.json`. A new profile with a CI job adds its
ruleset here under that name.

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
its required checks never report. Merge it with the bypass checkbox, or have release-please act
as a GitHub App so the checks run:

1. Create an app owned by the organization with repository permissions **Contents: read and
   write** and **Pull requests: read and write**, no webhook, and install it on the repositories
   that release with release-please.
2. Store its client ID as the organization variable `RELEASE_APP_CLIENT_ID` and a private key as
   the organization secret `RELEASE_APP_PRIVATE_KEY`. On the Free plan those reach public
   repositories only, so a private one needs both added to the repository itself.
3. Pass both from the caller:

   ```yaml
   release:
     permissions:
       contents: write
       pull-requests: write
     uses: studiobimo/.github/.github/workflows/release-please.yml@v1
     with:
       app-client-id: ${{ vars.RELEASE_APP_CLIENT_ID }}
     secrets:
       app-private-key: ${{ secrets.RELEASE_APP_PRIVATE_KEY }}
   ```

With neither set the workflow falls back to `GITHUB_TOKEN`, so a caller can pass them before the
app exists. A tag or Release made by the app does trigger other workflows, unlike one made with
`GITHUB_TOKEN`: keep the publish step gated on `release-created`, and do not add an
`on: push: tags` or `on: release` workflow that would publish a second time.

## Releasing with semantic-release

Two workflows make one release path for a git-flow repository: merge pull requests into `develop`,
and a pull request into `main` appears when there is something to release; merge that when ready.
A repository uses these **instead of** `release-please.yml`, never alongside it, because both write
the same tags.

- [`promote-release.yml`](.github/workflows/promote-release.yml) runs on a push to `develop`. A
  semantic-release dry run works out the next version; if there is one, it makes a throwaway
  `release/v<version>` branch at that commit and opens `chore(release): v<version>` into `main`,
  holding the release notes and `Closes #N` for the issues the work closes. It comes from its own
  branch because merging a `develop` -> `main` pull request would delete `develop` wherever
  "Automatically delete head branches" is on. Nothing releasable, or a pull request already open
  for that version, ends the run with a summary and creates nothing. A pull request left open
  stays at the commit it was made at; if later commits change the version, a second one opens and
  the older is closed by hand.
- [`semantic-release.yml`](.github/workflows/semantic-release.yml) runs on a push to `main`, which
  is the merge. It creates the `vX.Y.Z` tag and the GitHub Release; the notes live on the Release
  and nothing is committed back.

```yaml
# .github/workflows/promote.yml
on:
  push:
    branches: [develop]

permissions: {}

jobs:
  promote:
    permissions:
      contents: write
      pull-requests: write
    uses: studiobimo/.github/.github/workflows/promote-release.yml@v1
    with:
      app-client-id: ${{ vars.RELEASE_APP_CLIENT_ID }}
    secrets:
      app-private-key: ${{ secrets.RELEASE_APP_PRIVATE_KEY }}
```

```yaml
# .github/workflows/release.yml
on:
  push:
    branches: [main]

permissions: {}

jobs:
  release:
    permissions:
      contents: write
    uses: studiobimo/.github/.github/workflows/semantic-release.yml@v1
```

- **Merge with a merge commit.** semantic-release reads every commit reachable from `main`, so the
  commits that came from `develop` must stay in `main`'s history. A squash leaves one commit.
- **The app matters here too.** A pull request made with `GITHUB_TOKEN` triggers no workflow, so
  its required checks never run. Set it up as for `release-please.yml` above; without it the run
  says so in its summary.
- **The size rule skips it.** `ci-pr` does not count the files in a `release/v*` pull request into
  the default branch, since each change in it passed the check in its own pull request.
- **The first release is 1.0.0** when there is no tag, because semantic-release has no pre-1.0
  mode. A repository that starts at 0.x first tags its starting point, for example `v0.0.0` on its
  first commit; then a `feat` is a minor bump and only a breaking change reaches 1.0.0.
- **Inputs.** `promote-release.yml` takes `source-branch`, `target-branch` and `node-version`.
  `semantic-release.yml` takes `branch`, `dry-run` and `major-tag` (also moves a floating `v1`, for
  a repository others pin by tag). Both take the optional `release-token` secret, for a personal
  token in place of the app. `semantic-release.yml` outputs `release-created`, `tag-name` and
  `version`: a tag made with `GITHUB_TOKEN` triggers no other workflow, so gate a build or deploy
  job in the same workflow on `release-created`.
- **Configuration.** A `.releaserc*` in the caller wins over the default, but only the plugins
  installed here (`commit-analyzer`, `release-notes-generator` and, on `main`, `github`) can be
  named. Both install a pinned semantic-release outside the caller's tree with install scripts off,
  so the caller needs no `package.json` for it.

Neither is run against this repository: it has no `develop`, and `release.yml` here is already
semantic-release with the floating `v1` tag. Test a change from a consumer's wrapper pinned to your
branch SHA, with `dry-run: true` for `semantic-release.yml`.

## Repo health

[`ci-repo-health.yml`](.github/workflows/ci-repo-health.yml) runs a set of analysers over a
repository's git-tracked files, writes every finding into one JSON report in one shape, and holds
that report to a baseline. The same commit gives the same report: each tool is pinned, with its
dependencies, in [the action's own `mise.toml`](.github/actions/repo-health/mise.toml) and
`mise.lock`, apart from the caller's.

A tool runs when the repository has what it reads, and is configured by its own file:

| Tool                                                | Runs when the repo has           | Configured by                                      |
| --------------------------------------------------- | -------------------------------- | -------------------------------------------------- |
| [knip](https://knip.dev/)                           | `package.json`                   | `knip.json`, `knip.jsonc`, …                       |
| [jscpd](https://github.com/kucherenko/jscpd)        | any file                         | `.jscpd.json`; without one, lockfiles are skipped  |
| [vulture](https://github.com/jendrikseipp/vulture)  | `*.py`                           | `[tool.vulture]` in `pyproject.toml`               |
| [ruff](https://docs.astral.sh/ruff/)                | `*.py`                           | nothing: a fixed rule set, and `# noqa` is ignored |
| [lizard](https://github.com/terryyin/lizard)        | any file it parses               | `.lizardignore` for files; the limits are fixed    |
| [ast-grep](https://ast-grep.github.io/)             | JS, TS or Python; `sgconfig.yml` | the bundled rules, and the ones it points at       |
| [sqlfluff](https://sqlfluff.com/)                   | `*.sql`                          | `.sqlfluff`, else the `sqlfluff-dialect` input     |
| [semgrep](https://semgrep.dev/)                     | `.semgrep.yml`, `.semgrep/`      | those rules, or the `semgrep-config` input         |
| [uncomment](https://github.com/Goldziher/uncomment) | `.uncomment.toml`                | the same file                                      |

ruff, lizard's limits (CCN 15, 80 lines, 6 parameters) and the bundled ast-grep rules are the
floor every repository is held to, so a repository's config does not reach them. ruff runs
security (`S`), likely bugs (`B`), blind and bare excepts (`BLE`, `E722`), naive datetimes
(`DTZ`), blocking calls in async code (`ASYNC`), logging misuse (`LOG`), pylint warnings (`PLW`)
and needless code (`PIE`), leaving out `S603` and `S607`, which flag every `subprocess` call. The
[bundled rules](.github/actions/repo-health/rules.yml) make a suppression comment
(`eslint-disable`, `@ts-ignore`, `# noqa`, `# type: ignore`, …) a finding of its own, since it
hides one from the analyser that made it.

lizard has one repository setting: `.lizardignore`, for code the repository vendors and does not
own, such as components copied from a registry and regenerated from it. One glob per line, `#`
starts a comment. `*` and `?` stop at a slash and `**` does not. A pattern with no slash matches a
name at any depth (`*.gen.ts`), one with a slash is anchored at the repository root
(`apps/admin/src/components/ui/**`), and a trailing slash matches a folder. The other analysers
still read those files. The file is analyser config, so changing it needs `allow-config-change`.

A finding is an error or a warning. Errors are held to the baseline. Warnings are in the report
and the summary, and annotated on the files a pull request changes, but never fail it and never
enter the baseline: the bundled magic-number and hardcoded-JSX-text rules, a repository's own
ast-grep rules below `error`, and sqlfluff rules its config demotes. Test files may assert,
hardcode a fake secret, use a plain random and spell out numbers.

semgrep and uncomment wait for a config on purpose. semgrep's registry rulesets change on
semgrep's schedule, so `p/default` would fail a pull request for a rule published after it was
opened; commit the rules you hold the repository to, or pass a registry ruleset knowing that.
uncomment would otherwise report every explanatory comment in a repository.

Each finding has a fingerprint: a hash of the tool, the rule, the path and what was found (a
symbol's name, or the flagged line with its whitespace collapsed). Moving code keeps it; renaming
the file does not.

### The baseline ratchet

`.repo-health/baseline.json` lists the findings a repository still carries:

```json
{
  "version": 1,
  "findings": [
    {
      "fingerprint": "a03607f31f6f3e76",
      "issue": 42,
      "tool": "vulture",
      "rule": "unused-function",
      "path": "src/report.py",
      "message": "unused function 'compute'"
    }
  ]
}
```

Only `fingerprint` and `issue` are read; the rest is for the reviewer. Commit the file even when
it is empty: its presence arms the pull request rules. The check fails when:

- a finding is not in the baseline: fix it;
- a baseline entry's finding is gone: delete the entry, as part of the fix;
- an entry's `issue` is not an open issue of the repository;
- the baseline file is missing.

On a pull request it is also compared with the commit the pull request lands on: `HEAD^1` of
the merge commit GitHub checks out, so the checkout needs `fetch-depth: 2`. For a pull request
into `develop` that is `develop`; for a stacked one, the layer below. It fails when:

- an entry was added, unless the base's own code already has that finding. A newer tool or
  rule, arriving at `@v1`, can find something in code nobody touched; that entry may join the
  baseline, with its issue, while a finding the pull request wrote never can. The base is only
  scanned when the baseline grows;
- fewer than `minimum-reduction` (default 1) of the base's entries were resolved, or fewer than
  all of them when fewer are left. Pass `0` for pull requests that cannot resolve anything,
  such as a bot's bump;
- an analyser's config changed: `knip.json`, `.jscpd.json`, `sgconfig.yml` and the rules it
  names, `.semgrep.yml` or `.semgrep/`, `.uncomment.toml`, `.lizardignore`, a `.sqlfluff`, or the vulture,
  sqlfluff, knip and jscpd sections of `pyproject.toml` and `package.json`. A config can hide
  findings, and deleting one turns its tool off. `allow-config-change` lets a reviewed change
  through: wire it to a label only reviewers can set.

The caller's workflow file is config too, through `tools` and `minimum-reduction`, and nothing
here can stop a pull request editing it. Without an organisation-required workflow, CODEOWNERS
on `.github/workflows/` is the guard.

Before knip, the action installs Node dependencies beside each tracked `pnpm-lock.yaml` or
`package-lock.json`, with lifecycle scripts off. knip ties a binary a workflow runs to its
package, and loads configs that import dependencies, through `node_modules`, so without them it
reports what a developer's checkout does not.

To adopt it, open one issue for the existing findings, let the check fail once, and download the
`repo-health` artifact: its `baseline.json` is the baseline that run would accept, with
`"issue": null` on every new entry. Fill in the issue, commit it as `.repo-health/baseline.json`
with any analyser config, and the base having no baseline yet lets this first one land.

```yaml
on:
  pull_request:
    types: [opened, synchronize, reopened, labeled, unlabeled]

jobs:
  health:
    permissions:
      contents: read
      issues: read
    uses: studiobimo/.github/.github/workflows/ci-repo-health.yml@v1
    with:
      allow-config-change: ${{ contains(github.event.pull_request.labels.*.name, 'repo-health-config-change') }}
      minimum-reduction: ${{ github.actor != 'dependabot[bot]' && 1 || 0 }}
```

To set up more than Node dependencies first, or to grant the token yourself, call the action
from your own job:

```yaml
steps:
  - uses: actions/checkout@<sha> # v7.0.1
    with:
      fetch-depth: 2
      persist-credentials: false
  - run: uv sync --frozen
  - uses: studiobimo/.github/.github/actions/repo-health@<sha> # v1.x.y
```

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
bash .devtools/test-issue-title.sh     # issue-title.sh, against a stand-in for gh
python3 .devtools/test-repo-health.py  # repo-health.py, against canned tool output and a stand-in for gh
```

[mise](https://mise.jdx.dev/getting-started.html) is the one thing to install by hand. It pins
lefthook, Prettier, commitlint, actionlint, zizmor, shellcheck and gitleaks, and the hooks run
each through `mise exec`, so they work whether or not your shell has mise activated. Nothing is
skipped when a tool is missing: `mise install` is what makes it present.

### Bumping a tool

Dependabot does not read `mise.toml`, so the tools pinned there are bumped by hand.
`self-tools-outdated` runs every Monday and keeps one `tools-outdated` issue open while a pinned
tool has a release a week old or more, then closes it. Consumers call
`ci-tools-outdated.yml@v1` the same way.

```sh
mise outdated --bump            # what has a newer release
$EDITOR mise.toml               # change the version
mise lock                       # record the new checksums
mise install && mise exec -- lefthook run pre-commit --all-files
git add mise.toml mise.lock .mise/locks
```

`.mise/locks/` holds the dependency graph of each npm tool, so it is committed with the other
two. Leave a release a week before taking it, as Dependabot's cooldown does for everything else.

The repo-health tools are pinned apart, in `.github/actions/repo-health/mise.toml`, and
`self-tools-outdated` does not look there. Bump them the same way from that directory, with a
ceiling so mise leaves the root `mise.toml` out:

```sh
cd .github/actions/repo-health
MISE_CEILING_PATHS="$(cd .. && pwd)" mise lock
git add mise.toml mise.lock .mise/locks
```

pnpm is only here for semantic-release, which `release.yml` runs.

## Contributing

[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) and
[Conventional Branch](https://conventionalbranch.org/) apply here too, and a PR changes at most 20
files. See [AGENTS.md](AGENTS.md).

## License

[MIT](LICENSE)
