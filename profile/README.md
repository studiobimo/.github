# Studio Bimo

An independent software studio. Studio Bimo designs and builds web products end to end: the interface, the API behind it, and the infrastructure it runs on.

## What gets built here

- **Web applications and platforms.** Member directories, planning tools, dashboards and the admin surfaces behind them.
- **Marketing and content sites.** Fast, statically rendered where they can be, dynamic where they need to be.
- **Internal tools and integrations.** The glue between the services a team already uses, and the reporting on top of it.
- **Developer tooling.** Shared CI, project templates and conventions, so every repository starts from the same baseline.

## Stack

|                |                                                                     |
| -------------- | ------------------------------------------------------------------- |
| Language       | TypeScript, end to end                                              |
| Interface      | React, TanStack Router and Query, Tailwind CSS, Radix UI primitives |
| Content sites  | Astro                                                               |
| API and data   | Hono on Cloudflare Workers, D1 with Drizzle, Zod at every boundary  |
| Build and test | Vite, Vitest, Testing Library, pnpm                                 |
| Delivery       | GitHub Actions, deployed to Cloudflare's edge                       |

## How the work is done

- **Accessibility first.** It is a design constraint from the first sketch, not an audit at the end.
- **One standard, enforced.** Conventional Commits, small pull requests, secret scanning and linting run in git hooks and again in CI, for people and for AI agents alike.
- **CI as a library.** Checks live in one place and every repository calls them, so a fix lands everywhere at once. Third-party actions are pinned to a commit and workflows run with the minimum permissions they need.
- **Automated releases.** Versions, changelogs and tags come from the commit history, not from memory.

## In the open

Most of the studio's work is for clients and lives in private repositories. What is public:

- [.github](https://github.com/studiobimo/.github): the reusable workflows, composite actions and pre-commit hooks behind every repository here.
- [project-template](https://github.com/studiobimo/project-template): the starting point for a new repository, in any language.
- [Tally Hopper](https://github.com/studiobimo/tallyhopper) and [Datapacks](https://github.com/studiobimo/datapacks): Minecraft side projects, built for fun and held to the same standard.

[studio.bimo.dev](https://studio.bimo.dev)
