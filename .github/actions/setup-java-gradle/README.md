# `setup-java-gradle`

Temurin JDK plus Gradle, with wrapper validation and branch-aware caching.

## Inputs

| Input               | Default   | Description                                                                          |
| ------------------- | --------- | ------------------------------------------------------------------------------------ |
| `java-version`      | `25`      | JDK version. Prefer passing a `.java-version` file's contents.                       |
| `java-distribution` | `temurin` | Any distribution `actions/setup-java` understands.                                   |
| `validate-wrapper`  | `true`    | Check `gradle-wrapper.jar` against the known-good checksums, before Gradle runs.     |
| `cache-read-only`   | *(auto)*  | Unset means read-only everywhere except the default branch. `"false"` forces writes. |

No outputs.

## Usage

```yaml
- uses: studiobimo/.github/.github/actions/setup-java-gradle@<full-sha> # v1.0.0
  with:
    java-version: ${{ steps.java.outputs.version }}
```

## Notes

**Wrapper validation runs first.** A tampered `gradle-wrapper.jar` executes arbitrary code with the
job's token the moment Gradle starts, so there is no point validating it afterwards.

**The cache is read-only on pull requests.** A PR from a fork would otherwise be able to write into a
cache that later runs on the default branch restore. Pass `cache-read-only: "false"` only in a
workflow that never runs untrusted code.

**Gradle's dependency graph submission is off.** It needs `contents: write` and duplicates what
lockfiles and `verification-metadata.xml` already pin in the repos that use this.
