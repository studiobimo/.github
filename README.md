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

| Action                      | What it does                                                    |
| --------------------------- | --------------------------------------------------------------- |
| `actions/setup-java-gradle` | Temurin JDK + Gradle with branch-aware caching                  |
| `actions/setup-precommit`   | uv + pre-commit with a cached hook environment                  |
| `actions/pr-checks`         | The PR title, branch, size and commit checks, for direct use    |

## Calling them

Pin by full commit SHA, never by tag — the org requires it, and a tag can be moved under you.

```yaml
jobs:
  pr-checks:
    uses: studiobimo/.github/.github/workflows/pr-checks.yml@<full-sha>  # v1.0.0
    with:
      max-files: 20
```

Secrets are passed explicitly. Nothing here uses `secrets: inherit`.

```yaml
  publish:
    uses: studiobimo/.github/.github/workflows/publish-mod.yml@<full-sha>  # v1.0.0
    secrets:
      modrinth-token: ${{ secrets.MODRINTH_TOKEN }}
      curseforge-token: ${{ secrets.CURSEFORGE_TOKEN }}
```

Each workflow and action documents its own inputs: the workflows in their `workflow_call` block, the
actions in a README beside them.

A reusable workflow here checks this repository out at `github.job_workflow_sha` — its own commit —
so the scripts and actions it runs always match the version the caller pinned, with no
self-referential SHA to bump on every release.

## Conventions

- Every `uses:` is pinned to a full-length commit SHA, with the human-readable version in a comment.
- `permissions: {}` at workflow level; each job asks for the least it needs.
- `persist-credentials: false` on every checkout.
- No secret is ever read in a workflow that also runs untrusted code from a fork.

## Contributing

[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) and
[Conventional Branch](https://conventionalbranch.org/) apply here too, and a PR changes at most 20
files. See [AGENTS.md](AGENTS.md).
