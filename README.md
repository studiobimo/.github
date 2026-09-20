# studiobimo/.github

Reusable workflows, composite actions and org defaults for [studiobimo](https://github.com/studiobimo).

Repositories here call these instead of copying CI into every project, so a fix to a check lands
everywhere at once.

## Reusable workflows

| Workflow                               | What it does                                                                     |
| -------------------------------------- | -------------------------------------------------------------------------------- |
| `.github/workflows/pr-checks.yml`      | Conventional PR title and branch name, PR size limit, commitlint                 |
| `.github/workflows/ci-java-gradle.yml` | Java toolchain, Gradle build, test reports and artifacts                         |
| `.github/workflows/release-please.yml` | Keeps a release PR open; tags and releases on merge                              |
| `.github/workflows/publish-mod.yml`    | Builds, attests and publishes a Minecraft mod to Modrinth, CurseForge and GitHub |

## Composite actions

| Action                              | What it does                                                 |
| ----------------------------------- | ------------------------------------------------------------ |
| `.github/actions/setup-java-gradle` | Temurin JDK + Gradle with branch-aware caching               |
| `.github/actions/setup-precommit`   | uv + pre-commit with a cached hook environment               |
| `.github/actions/pr-checks`         | The PR title, branch, size and commit checks, for direct use |
| `.github/actions/build-summary`     | JUnit + JaCoCo numbers into the job summary                  |

## Calling them

Call a workflow at `@v1`. The tag floats: a release moves it, so a fix lands everywhere at once.

```yaml
jobs:
  pr-checks:
    uses: studiobimo/.github/.github/workflows/pr-checks.yml@v1
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

## Contributing

[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) and
[Conventional Branch](https://conventionalbranch.org/) apply here too, and a PR changes at most 20
files. See [AGENTS.md](AGENTS.md).
