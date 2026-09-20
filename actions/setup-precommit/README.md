# `setup-precommit`

uv with a locked tool environment, plus a cached pre-commit hook environment.

## Inputs

| Input               | Default                   | Description                                                |
| ------------------- | ------------------------- | ---------------------------------------------------------- |
| `working-directory` | `.devtools`               | Directory holding `pyproject.toml` and `uv.lock`.          |
| `config-file`       | `.pre-commit-config.yaml` | Used as the cache key, so changing a hook busts the cache. |
| `uv-version`        | `0.12.17`                 | Pinned so CI and a developer's machine agree.              |

No outputs.

## Usage

```yaml
- uses: studiobimo/.github/actions/setup-precommit@<full-sha> # v1.0.0
- run: uv run --project .devtools pre-commit run --all-files
```

## Notes

**The hook environments are what's worth caching**, not uv itself: every linter in
`.pre-commit-config.yaml` installs its own toolchain on first run. The cache key is the hash of that
file, so adding or bumping a hook rebuilds the environment and nothing else does.

**`uv sync --frozen`** fails rather than silently updating when `uv.lock` doesn't match
`pyproject.toml`. That is deliberate: CI should not resolve dependencies.
