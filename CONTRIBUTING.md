# Contributing

This file is the org-wide default: it is served to every studiobimo repository that does not
ship its own, so it stays general. Conventions specific to this repo — pinning, permissions,
the release mechanics — live in [AGENTS.md](AGENTS.md).

A few conventions keep history and releases predictable across our repositories:

- **Commit messages and PR titles** follow
  [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/). Pull requests are
  squash-merged, so the title becomes the commit on the default branch — write it to describe
  the change and its release impact, not the branch.
- **Branch names** follow [Conventional Branch](https://conventionalbranch.org/) — `feat/…`,
  `fix/…`, `chore/…`.
- **Keep pull requests small.** At most 20 changed files; split larger work into a stack.
- **Open an issue before a large change**, so we can agree on the approach before you build it.
- **Say how you tested it.** Describe what you ran and what it showed.
