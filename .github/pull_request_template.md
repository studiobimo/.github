## What changed, and why

<!-- The PR title becomes the squash commit and is what semantic-release reads to compute
     the next version, so make it a Conventional Commit that describes the release impact. -->

## How it was tested

<!-- Required. "It should work" is not a test result.

     Most changes are covered from inside this repo: the self-* workflows run each ci-*
     reusable against this repo's own source, so opening this PR exercises them.

     Anything that only differs cross-repo is not covered that way — above all whether a
     `$/` ref resolves here rather than to the caller, and whether a first-party action ref
     is pinned in a form a consumer will accept. For those, point a consumer repo's wrapper
     at this branch's SHA, open a draft PR there, and link the run. -->

## Checklist

- [ ] Title is a [Conventional Commit](https://www.conventionalcommits.org/en/v1.0.0/);
      branch follows [Conventional Branch](https://conventionalbranch.org/)
- [ ] At most 20 files changed (split with `gh stack` if not)
- [ ] Third-party `uses:` pinned to a full SHA with a `# vX.Y.Z` comment
- [ ] Our own actions referenced with `$/` from a workflow, SHA-pinned from a template
      (`sh .devtools/check-action-pins.sh`)
- [ ] `permissions: {}` at workflow level; each job asks for the least it needs
- [ ] Breaking change for consumers is called out above, since they ride the floating `v1`
