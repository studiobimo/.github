#!/usr/bin/env python3
"""Repo health: one fingerprinted report from many analysers, held to a baseline ratchet.

    repo-health.py scan  --output REPORT [SCAN OPTIONS]
    repo-health.py check --report REPORT --baseline BASELINE [--base-dir DIR] [--minimum-reduction N]
                         [--allow-config-change] [--proposal FILE] [SCAN OPTIONS]

    SCAN OPTIONS: [--tools a,b] [--semgrep-config CFG] [--sqlfluff-dialect D] [--install]

`scan` runs every analyser the repository has something for, over its git-tracked files only,
and writes their findings into one JSON report in one shape. A tool runs when the repository
has what it reads, and is configured the way it always is, by its own file:

    knip       a package.json                  (knip.json and friends)
    jscpd      any tracked file                (.jscpd.json)
    vulture    *.py                            ([tool.vulture] in pyproject.toml)
    ruff       *.py                            (a fixed rule set; no config, no noqa)
    lizard     any tracked file it can parse   (CCN 15, 80 lines, 6 parameters; .lizardignore)
    ast-grep   JS, TS or Python, or            (the rules beside the repo-health action,
               an sgconfig.yml                  and the repository's own)
    sqlfluff   *.sql                           (.sqlfluff, else --sqlfluff-dialect)
    semgrep    .semgrep.yml or .semgrep/       (or --semgrep-config)
    uncomment  .uncomment.toml                 (the same file)

--install runs `pnpm install --frozen-lockfile` or `npm ci` beside each tracked lockfile
before knip, with lifecycle scripts off: knip ties a binary a script runs to its package, and
loads configs that import dependencies, through node_modules.

ruff and the bundled ast-grep rules ignore the repository's config on purpose: they are the
floor every repository is held to, and a `# noqa` or `eslint-disable` is itself a finding.

A finding is an error or a warning. Warnings, the bundled magic-number and JSX-text rules and
whatever a repository's own config demotes, are in the report and on the lines a pull request
changes, but are not held to the baseline: they never fail the check.

Every finding gets a fingerprint that survives line moves: a hash of the tool, the rule, the
path and an anchor that is the thing found (a symbol name, or the flagged source line with its
whitespace collapsed), plus an index among identical ones. The line number is reported but
never hashed.

`check` holds the report to the baseline, a reviewed list of the findings a repository still
carries, each tracked by an open issue. The baseline file has to exist, empty or not:

    1. no finding is missing from the baseline (nothing new);
    2. no baseline entry is missing from the report (a fixed finding leaves the baseline);
    3. every entry names an open issue of this repository;
    and, given --base-dir, a checkout of the commit a pull request merges into:
    4. nothing was added to its baseline, except a finding its own code already has: one a
       newer tool or rule found in code this pull request did not write;
    5. at least --minimum-reduction of its entries were resolved, or all of them if fewer;
    6. no analyser's config changed, unless --allow-config-change, since a config can hide
       findings and deleting one can turn its tool off.

A base with no baseline file yet skips 4 to 6: that is the pull request adopting the check.
For 4, only a pull request that adds entries pays for scanning the base.
--proposal writes the baseline this run would accept: the entries still found, plus every new
finding with `"issue": null` for a person to fill in.

`check` reads the issues with gh: GH_TOKEN and GH_REPO, with issues: read.
Exit codes: 0 passes, 1 does not, 2 could not tell.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

REPORT_VERSION = 1
TOOLS = ("knip", "jscpd", "vulture", "ruff", "lizard", "ast-grep", "sqlfluff", "semgrep", "uncomment")
RULES = Path(__file__).resolve().parent.parent / ".github" / "actions" / "repo-health" / "rules.yml"
# Test code may assert, hardcode a fake secret and use a plain random, and its numbers are data.
TEST_FILE = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*\.py$|_test\.py$|\.(test|spec)\.[cm]?[jt]sx?$")
TEST_EXEMPT = {"S101", "S105", "S106", "S107", "S311", "repo-health-magic-number"}
SUMMARY_ROWS = 50


class Skip(Exception):
    """The repository has nothing for this tool."""


class Fail(Exception):
    """Something that is not a finding stopped the run: exit 2."""


@dataclass
class Finding:
    tool: str
    rule: str
    path: str
    line: int
    message: str
    anchor: str
    severity: str = "error"
    fingerprint: str = ""

    def as_json(self) -> dict:
        return {
            "fingerprint": self.fingerprint,
            "tool": self.tool,
            "rule": self.rule,
            "path": self.path,
            "line": self.line,
            "message": self.message,
            "severity": self.severity,
        }


# --------------------------------------------------------------------------- scan


class Repo:
    def __init__(self, root: Path, work: Path, args: argparse.Namespace):
        self.root = root
        self.work = work
        self.args = args
        listed = git(root, "ls-files", "-z").split("\0")
        # A submodule, or a file deleted but not yet staged, is listed but is no file.
        self.tracked = sorted(p for p in listed if p and (root / p).is_file())
        self.tracked_set = set(self.tracked)
        self._lines: dict[str, list[str]] = {}

    def has(self, *names: str) -> bool:
        return any(n in self.tracked_set for n in names)

    def matching(self, *suffixes: str) -> list[str]:
        return [p for p in self.tracked if p.endswith(suffixes)]

    def rel(self, path: str) -> str:
        """A tool's path, as git lists it."""
        p = Path(path)
        if p.is_absolute():
            p = Path(os.path.relpath(p.resolve(), self.root.resolve()))
        return p.as_posix().removeprefix("./")

    def line(self, path: str, number: int) -> str:
        """Line `number` of a tracked file, whitespace collapsed: the anchor of a finding."""
        if path not in self._lines:
            text = (self.root / path).read_text(encoding="utf-8", errors="replace")
            self._lines[path] = text.splitlines()
        lines = self._lines[path]
        return " ".join(lines[number - 1].split()) if 0 < number <= len(lines) else ""

    def run(self, tool: str, cmd: list[str], ok: tuple[int, ...] = (0,), cwd: Path | None = None) -> str:
        env = {**os.environ, "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0"}
        try:
            proc = subprocess.run(cmd, cwd=cwd or self.root, env=env, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            raise Fail(f"{tool}: {cmd[0]} is not installed") from None
        if proc.returncode not in ok:
            tail = (proc.stderr or proc.stdout).strip()[-2000:]
            raise Fail(f"{tool} exited {proc.returncode}:\n{tail}")
        return proc.stdout

    def listfile(self, name: str, paths: list[str]) -> str:
        f = self.work / name
        f.write_text("".join(f"{p}\n" for p in paths), encoding="utf-8")
        return str(f)


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise Fail(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


KNIP = {
    "files": "unused file",
    "dependencies": "unused dependency",
    "devDependencies": "unused devDependency",
    "optionalPeerDependencies": "unused optional peer dependency",
    "unlisted": "unlisted dependency",
    "binaries": "unlisted binary",
    "unresolved": "unresolved import",
    "exports": "unused export",
    "types": "unused exported type",
    "enumMembers": "unused enum member",
    "namespaceMembers": "unused namespace member",
    "duplicates": "duplicate export",
    "catalog": "unused catalog entry",
    "catalogReferences": "unresolved catalog reference",
}


def install(repo: Repo) -> None:
    """The dependencies beside each tracked lockfile, so knip sees what a developer's checkout has."""
    for lockfile in repo.tracked:
        name = Path(lockfile).name
        if name not in LOCKFILES:
            continue
        where = repo.root / Path(lockfile).parent
        print(f"  installing in {Path(lockfile).parent.as_posix()}/ with {name}")
        repo.run("install", LOCKFILES[name], cwd=where)


# Lifecycle scripts stay off: the pull request's code is analysed, never run.
LOCKFILES = {
    "pnpm-lock.yaml": ["corepack", "pnpm", "install", "--frozen-lockfile", "--ignore-scripts"],
    "package-lock.json": ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
    "npm-shrinkwrap.json": ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
}


def knip(repo: Repo) -> list[Finding]:
    if not repo.has("package.json"):
        raise Skip("no package.json")
    if repo.args.install:
        install(repo)
    out = repo.run("knip", ["knip", "--reporter", "json", "--no-progress", "--no-exit-code"])
    found = []
    for entry in json.loads(out).get("issues", []):
        path = repo.rel(entry["file"])
        for kind, items in entry.items():
            if kind == "file":
                continue
            for item in knip_items(items):
                message = f"{KNIP.get(kind, kind)}: {item['name']}"
                found.append(Finding("knip", kind, path, item.get("line") or 1, message, item["name"]))
    return found


def knip_items(items) -> list[dict]:
    """Most kinds are a list of {name, line}; enum and namespace members can come grouped under
    their parent, and a duplicate is a list of the names it joins."""
    if isinstance(items, dict):
        return [{**i, "name": f"{parent}.{i['name']}"} for parent, group in items.items() for i in group]
    return [knip_duplicate(i) if isinstance(i, list) else i for i in items or []]


def knip_duplicate(group: list[dict]) -> dict:
    return {"name": " = ".join(sorted(i["name"] for i in group)), "line": group[0].get("line")}


JSCPD_IGNORE = "**/*lock.json,**/*lock.yaml,**/*.lock,**/.mise/locks/**"


def jscpd(repo: Repo) -> list[Finding]:
    if not repo.tracked:
        raise Skip("no tracked files")
    out = repo.work / "jscpd"
    # Lockfiles repeat themselves by design. A repository with its own .jscpd.json says what to skip.
    ignore = [] if repo.has(".jscpd.json") else ["--ignore", JSCPD_IGNORE]
    cmd = ["jscpd", "--reporters", "json", "--output", str(out), "--silent", "--absolute", "--no-tips", *ignore, "."]
    repo.run("jscpd", cmd)
    report = json.loads((out / "jscpd-report.json").read_text(encoding="utf-8"))
    found = []
    for dup in report.get("duplicates", []):
        a, b = sorted(((repo.rel(f["name"]), f["start"]) for f in (dup["firstFile"], dup["secondFile"])))
        # A clone is in two files; it only counts if both are tracked.
        if b[0] not in repo.tracked_set:
            continue
        fragment = " ".join(dup.get("fragment", "").split())
        anchor = f"{b[0]}\0{digest(fragment)}"
        message = f"{dup['lines']} lines duplicated at {b[0]}:{b[1]}"
        found.append(Finding("jscpd", "duplicate", a[0], a[1], message, anchor))
    return found


VULTURE = re.compile(r"^(?P<path>.+?):(?P<line>\d+): (?P<msg>.+?) \(\d+% confidence(?:, \d+ lines?)?\)$")
VULTURE_UNUSED = re.compile(r"^unused (?P<kind>\w+(?: \w+)?) '(?P<name>.+)'$")


def vulture(repo: Repo) -> list[Finding]:
    files = repo.matching(".py", ".pyi")
    if not files:
        raise Skip("no Python files")
    # 3 means it found dead code.
    out = repo.run("vulture", ["vulture", *files], ok=(0, 3))
    found = []
    for raw in out.splitlines():
        m = VULTURE.match(raw)
        if not m:
            continue
        path, line, msg = repo.rel(m["path"]), int(m["line"]), m["msg"]
        unused = VULTURE_UNUSED.match(msg)
        if unused:
            found.append(Finding("vulture", f"unused-{slug(unused['kind'])}", path, line, msg, unused["name"]))
        else:
            found.append(Finding("vulture", slug(msg), path, line, msg, repo.line(path, line)))
    return found


# Security, likely bugs, bare and blind excepts, naive datetimes, blocking calls in async code,
# logging misuse, pylint's warnings and needless code.
RUFF_RULES = "S,B,BLE,E722,DTZ,ASYNC,LOG,PLW,PIE"
# Every subprocess call, a fixed argument list like ["git", ...] included.
RUFF_IGNORED = "S603,S607"


def ruff(repo: Repo) -> list[Finding]:
    files = repo.matching(".py", ".pyi")
    if not files:
        raise Skip("no Python files")
    cmd = ["ruff", "check", "--isolated", "--ignore-noqa", "--select", RUFF_RULES, "--ignore", RUFF_IGNORED]
    out = repo.run("ruff", [*cmd, "--output-format", "json", "--exit-zero", *files])
    found = []
    for d in json.loads(out):
        path, line = repo.rel(d["filename"]), d["location"]["row"]
        found.append(Finding("ruff", d["code"], path, line, d["message"], repo.line(path, line)))
    return found


LIZARD_LIMITS = ["--CCN", "15", "--length", "80", "--arguments", "6"]
LIZARD = re.compile(r"^(?P<path>.+?):(?P<line>\d+): warning: (?P<name>.+) has (?P<metrics>\d+ NLOC, .*)$")


LIZARD_IGNORE = ".lizardignore"


def glob_regex(pattern: str) -> re.Pattern[str]:
    """A .lizardignore line as a regex over a git path. `*` and `?` stop at a slash and `**` does
    not; a pattern with no slash in it matches a name at any depth, and a trailing slash a folder."""
    folder = pattern.endswith("/")
    anchored = "/" in pattern.rstrip("/")
    body = ""
    parts = re.split(r"(\*\*/|\*\*|\*|\?)", pattern.strip("/"))
    for part in parts:
        body += {"**/": "(?:.*/)?", "**": ".*", "*": "[^/]*", "?": "[^/]"}.get(part) or re.escape(part)
    return re.compile(("" if anchored else "(?:.*/)?") + body + ("/.*" if folder else "(?:/.*)?"), re.DOTALL)


def lizard_ignored(repo: Repo) -> list[re.Pattern[str]]:
    """What the repository leaves out of lizard, for code it vendors and does not own."""
    if not repo.has(LIZARD_IGNORE):
        return []
    text = (repo.root / LIZARD_IGNORE).read_text(encoding="utf-8", errors="replace")
    lines = (line.strip() for line in text.splitlines())
    return [glob_regex(line.removeprefix("/")) for line in lines if line and not line.startswith("#")]


def lizard(repo: Repo) -> list[Finding]:
    ignored = lizard_ignored(repo)
    files = [p for p in repo.tracked if not any(rx.fullmatch(p) for rx in ignored)]
    if not files:
        raise Skip("no tracked files" if not repo.tracked else f"every tracked file is in {LIZARD_IGNORE}")
    # 1 means a function is over a threshold.
    cmd = ["lizard", "--warnings_only", *LIZARD_LIMITS, "--input_file", repo.listfile("lizard", files)]
    out = repo.run("lizard", cmd, ok=(0, 1))
    found = []
    for raw in out.splitlines():
        m = LIZARD.match(raw)
        if m:
            message = f"{m['name']} has {m['metrics']}"
            found.append(Finding("lizard", "complexity", repo.rel(m["path"]), int(m["line"]), message, m["name"]))
    return found


AST_GREP_FILES = (".ts", ".mts", ".cts", ".tsx", ".js", ".mjs", ".cjs", ".jsx", ".py")


def ast_grep_findings(repo: Repo, out: str) -> list[Finding]:
    found = []
    for raw in out.splitlines():
        if raw.strip():
            m = json.loads(raw)
            line = m["range"]["start"]["line"] + 1
            anchor = " ".join(m["text"].split())
            severity = "error" if m["severity"] == "error" else "warning"
            found.append(Finding("ast-grep", m["ruleId"], repo.rel(m["file"]), line, m["message"], anchor, severity))
    return found


def ast_grep(repo: Repo) -> list[Finding]:
    own = repo.has("sgconfig.yml", "sgconfig.yaml")
    if not own and not repo.matching(*AST_GREP_FILES):
        raise Skip("no sgconfig.yml, and no JavaScript, TypeScript or Python")
    # 1 means a rule of severity error matched; anything else is ast-grep failing.
    found = ast_grep_findings(repo, repo.run("ast-grep", ["ast-grep", "scan", "--rule", str(RULES), "--json=stream", "."], ok=(0, 1)))
    if own:
        found += ast_grep_findings(repo, repo.run("ast-grep", ["ast-grep", "scan", "--json=stream"], ok=(0, 1)))
    return found


def sqlfluff(repo: Repo) -> list[Finding]:
    files = repo.matching(".sql")
    if not files:
        raise Skip("no SQL files")
    configured = repo.has(".sqlfluff") or (
        repo.has("pyproject.toml") and "[tool.sqlfluff" in (repo.root / "pyproject.toml").read_text(encoding="utf-8")
    )
    dialect = [] if configured else ["--dialect", repo.args.sqlfluff_dialect]
    # 1 means violations; 2 is sqlfluff failing.
    out = repo.run("sqlfluff", ["sqlfluff", "lint", "--format", "json", "--nocolor", *dialect, *files], ok=(0, 1))
    found = []
    for result in json.loads(out):
        path = repo.rel(result["filepath"])
        for v in result["violations"]:
            # A rule the repository's config demotes to a warning does not fail sqlfluff either.
            severity = "warning" if v.get("warning") else "error"
            line = v["start_line_no"]
            found.append(Finding("sqlfluff", v["code"], path, line, v["description"], repo.line(path, line), severity))
    return found


def semgrep(repo: Repo) -> list[Finding]:
    config = repo.args.semgrep_config
    if not config:
        for candidate in (".semgrep.yml", ".semgrep.yaml"):
            if repo.has(candidate):
                config = candidate
                break
        else:
            if any(p.startswith(".semgrep/") for p in repo.tracked):
                config = ".semgrep/"
    if not config:
        raise Skip("no .semgrep.yml or .semgrep/")
    cmd = ["semgrep", "scan", "--config", config, "--json", "--metrics=off", "--disable-version-check", "--quiet"]
    found = []
    for r in json.loads(repo.run("semgrep", cmd))["results"]:
        path, line = repo.rel(r["path"]), r["start"]["line"]
        found.append(Finding("semgrep", r["check_id"], path, line, r["extra"]["message"], repo.line(path, line)))
    return found


def uncomment(repo: Repo) -> list[Finding]:
    if not repo.has(".uncomment.toml"):
        raise Skip("no .uncomment.toml")
    # 1 means a comment would be removed; 2 is uncomment failing.
    out = repo.run("uncomment", ["uncomment", "--check", "--format", "json", "."], ok=(0, 1))
    found = []
    for v in json.loads(out)["violations"]:
        excerpt = " ".join(v["excerpt"].split())
        message = f"removable comment: {excerpt[:80]}"
        found.append(Finding("uncomment", "removable-comment", repo.rel(v["path"]), v["line"], message, excerpt))
    return found


RUNNERS = {
    "knip": knip,
    "jscpd": jscpd,
    "vulture": vulture,
    "ruff": ruff,
    "lizard": lizard,
    "ast-grep": ast_grep,
    "sqlfluff": sqlfluff,
    "semgrep": semgrep,
    "uncomment": uncomment,
}


def fingerprint(findings: list[Finding]) -> list[Finding]:
    """Hashes each finding's identity, never its line, so moving code keeps its fingerprint."""
    groups: dict[tuple[str, str, str, str], list[Finding]] = defaultdict(list)
    for f in findings:
        groups[(f.tool, f.rule, f.path, f.anchor)].append(f)
    for key, group in groups.items():
        group.sort(key=lambda f: (f.line, f.message))
        for index, f in enumerate(group):
            f.fingerprint = digest(json.dumps([*key, index]))
    return sorted(findings, key=lambda f: (f.path, f.line, f.tool, f.rule, f.fingerprint))


def exempt(f: Finding) -> bool:
    return f.rule in TEST_EXEMPT and bool(TEST_FILE.search(f.path))


def collect(root: Path, args: argparse.Namespace) -> dict:
    """Every analyser's findings in the repository at `root`, as a report."""
    selected = set(args.tools.split(",")) if args.tools else set(TOOLS)
    unknown = selected - set(TOOLS)
    if unknown:
        raise Fail(f"unknown tools: {', '.join(sorted(unknown))}; known: {', '.join(TOOLS)}")
    findings: list[Finding] = []
    tools: dict[str, dict] = {}
    with tempfile.TemporaryDirectory() as work:
        repo = Repo(root, Path(work), args)
        for name in TOOLS:
            if name not in selected:
                tools[name] = {"status": "disabled"}
                continue
            try:
                found = [f for f in RUNNERS[name](repo) if f.path in repo.tracked_set and not exempt(f)]
            except Skip as skip:
                tools[name] = {"status": "skipped", "reason": str(skip)}
                print(f"- {name}: skipped, {skip}")
                continue
            tools[name] = {"status": "ran", "findings": len(found)}
            findings += found
            print(f"✔ {name}: {len(found)} findings")
    return {"version": REPORT_VERSION, "tools": tools, "findings": [f.as_json() for f in fingerprint(findings)]}


def toplevel() -> Path:
    return Path(git(Path.cwd(), "rev-parse", "--show-toplevel").strip())


def scan(args: argparse.Namespace) -> int:
    report = collect(toplevel(), args)
    write_json(Path(args.output), report)
    print(f"{len(report['findings'])} findings in {args.output}")
    return 0


# -------------------------------------------------------------------------- check


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def gh(*args: str) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise Fail("gh is not installed") from None
    return proc.returncode, proc.stdout, proc.stderr


def not_found(stderr: str) -> bool:
    return "(HTTP 404)" in stderr or "(HTTP 410)" in stderr


def load_baseline(text: str, where: str) -> dict[str, dict]:
    try:
        data = json.loads(text)
        entries = data["findings"]
    except (ValueError, KeyError, TypeError) as err:
        raise Fail(f"{where} is not a baseline: {err}") from None
    baseline: dict[str, dict] = {}
    for entry in entries:
        fp = entry.get("fingerprint") if isinstance(entry, dict) else None
        if not isinstance(fp, str) or not fp:
            raise Fail(f"{where}: an entry has no fingerprint: {entry!r}")
        if fp in baseline:
            raise Fail(f"{where}: {fp} is listed twice")
        baseline[fp] = entry
    return baseline


def read_baseline(root: Path, path: str) -> dict[str, dict] | None:
    """The baseline committed at `root`, or None where there is none."""
    file = root / path
    return load_baseline(file.read_text(encoding="utf-8"), str(file)) if file.is_file() else None


SGCONFIG_DIRS = re.compile(r"^(ruleDirs|utilDirs)\s*:\s*(.*)$")


def sgconfig_dirs(text: str) -> list[str]:
    """The rule and util directories an sgconfig.yml names, in either YAML list form."""
    dirs: list[str] = []
    listing = False
    for line in text.splitlines():
        m = SGCONFIG_DIRS.match(line)
        if m:
            rest = m[2].strip()
            listing = not rest
            dirs += [d.strip().strip("'\"") for d in rest.strip("[]").split(",") if d.strip()]
            continue
        item = re.match(r"^\s+-\s*(.+?)\s*$", line)
        if listing and item:
            dirs.append(item[1].strip("'\""))
        elif line.strip() and not line[0].isspace():
            listing = False
    return [d.rstrip("/") for d in dirs]


# Each analyser's own config, by where it lives. pyproject.toml and package.json hold other
# things too, so only the analysers' sections of them are compared.
CONFIG_FILES = (
    "knip.json", "knip.jsonc", ".knip.json", ".knip.jsonc", "knip.ts", "knip.config.ts",
    ".jscpd.json", "sgconfig.yml", "sgconfig.yaml", ".semgrep.yml", ".semgrep.yaml",
    ".semgrepignore", ".uncomment.toml", ".lizardignore",
)
CONFIG_SECTIONS = {"pyproject.toml": ("vulture", "sqlfluff"), "package.json": ("knip", "jscpd")}


def config_sections(tracked: list[str], read) -> dict[str, str]:
    """The analysers' sections of pyproject.toml and package.json."""
    found = {}
    for name, keys in CONFIG_SECTIONS.items():
        if name not in tracked:
            continue
        try:
            data = tomllib.loads(read(name)).get("tool", {}) if name.endswith(".toml") else json.loads(read(name))
        except ValueError:
            continue
        found.update({f"{name} ({k})": json.dumps(data[k], sort_keys=True) for k in keys if k in data})
    return found


def is_config(path: str) -> bool:
    return path in CONFIG_FILES or Path(path).name == ".sqlfluff" or path.startswith(".semgrep/")


def gate_config(root: Path) -> dict[str, str]:
    """Every analyser setting at `root` that could hide a finding, keyed by where it is."""
    tracked = [p for p in git(root, "ls-files", "-z").split("\0") if p and (root / p).is_file()]

    def read(path: str) -> str:
        return (root / path).read_text(encoding="utf-8", errors="replace")

    config = {p: read(p) for p in tracked if is_config(p)}
    rule_dirs = [d for sg in ("sgconfig.yml", "sgconfig.yaml") if sg in config for d in sgconfig_dirs(config[sg])]
    config.update({p: read(p) for p in tracked if any(p.startswith(f"{d}/") for d in rule_dirs)})
    return config | config_sections(tracked, read)


def config_changes(base: Path, head: Path) -> list[str]:
    before, after = gate_config(base), gate_config(head)
    return sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))


def issue_problem(repo: str, issue: int) -> str:
    """What stops `issue` tracking a finding, or nothing when it is an open issue."""
    code, out, err = gh("api", f"repos/{repo}/issues/{issue}", "--jq", '[.state, (has("pull_request"))] | @tsv')
    if code != 0:
        if not not_found(err):
            raise Fail(f"could not read issue #{issue}: {err.strip()}")
        return f"#{issue} does not exist"
    state, is_pr = out.strip().split("\t")
    if is_pr == "true":
        return f"#{issue} is a pull request"
    return "" if state == "open" else f"#{issue} is {state}"


def issue_problems(repo: str, baseline: dict[str, dict]) -> dict[str, str]:
    """Each baseline entry whose issue is not an open issue, with what is wrong with it."""
    problems: dict[str, str] = {}
    by_issue: dict[int, list[str]] = defaultdict(list)
    for fp, entry in baseline.items():
        issue = entry.get("issue")
        if isinstance(issue, int) and not isinstance(issue, bool) and issue > 0:
            by_issue[issue].append(fp)
        else:
            problems[fp] = "names no issue"
    for issue in sorted(by_issue):
        problem = issue_problem(repo, issue)
        if problem:
            problems.update(dict.fromkeys(by_issue[issue], problem))
    return problems


def describe(entry: dict) -> str:
    where = entry.get("path", "?") + (f":{entry['line']}" if entry.get("line") else "")
    return f"{entry.get('tool', '?')}/{entry.get('rule', '?')} {where} {entry.get('message', '')}".rstrip()


def annotate(level: str, entry: dict, text: str) -> None:
    """A workflow command, so a pull request shows the finding on its line."""
    if os.environ.get("GITHUB_ACTIONS") != "true" or "path" not in entry:
        return
    message = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level} file={entry['path']},line={entry.get('line', 1)},title=repo-health::{message}")


# A failed rule: its heading, and a row per finding it failed on, as (fingerprint, entry, why).
Rule = tuple[str, list[tuple[str, dict, str]]]


def local_rules(found: dict, head: dict, problems: dict[str, str]) -> list[Rule]:
    """Rules 1-3: the report against the baseline it was committed with."""
    return [
        ("New findings: fix them", [(fp, f, "not in the baseline") for fp, f in found.items() if fp not in head]),
        (
            "Resolved findings still in the baseline: delete their entries",
            [(fp, e, "no longer found") for fp, e in head.items() if fp not in found],
        ),
        ("Baseline entries without an open issue", [(fp, head[fp], why) for fp, why in sorted(problems.items())]),
    ]


def base_rules(found: dict, head: dict, base: dict, preexisting: set[str], minimum: int) -> tuple[list[Rule], str]:
    """Rules 4-5: the baseline against the base's, and a note on the reduction."""
    added = [fp for fp in head if fp not in base]
    adopted = [fp for fp in added if fp in preexisting]
    why = "not in the base's baseline, and not in the base's code"
    rejected = [(fp, head[fp], why) for fp in added if fp not in preexisting]
    # Dropping an entry only counts when its finding is gone too; otherwise it is a new finding.
    resolved = [fp for fp in base if fp not in head and fp not in found]
    needed = min(minimum, len(base))
    rules: list[Rule] = [("Added to the baseline: fix them instead", rejected)]
    if len(resolved) < needed:
        short = f"resolves {len(resolved)} baselined findings; resolve at least {needed}"
        rules.append(("Not enough resolved", [("", {}, short)]))
    note = f"{len(resolved)} of the {len(base)} baselined findings resolved; {needed} required."
    if adopted:
        note += f" {len(adopted)} entries added for findings the base's code already has, from a newer tool or rule."
    return rules, note


def config_rule(base_dir: Path, root: Path, allowed: bool) -> list[Rule]:
    """Rule 6: an analyser's config is the same as the base's."""
    if allowed:
        return []
    changed = config_changes(base_dir, root)
    why = "changed; a reviewer allows it with allow-config-change"
    return [("Analyser config changed", [("", {"path": p}, why) for p in changed])]


def proposal(found: dict, head: dict) -> dict:
    """The baseline this run would accept: what is still found, and what is new, untracked."""
    entries = [{**head[fp], **f} if fp in head else {**f, "issue": None} for fp, f in found.items()]
    for entry in entries:
        entry.pop("line", None)
    entries.sort(key=lambda e: (e["path"], e["tool"], e["rule"], e["fingerprint"]))
    return {"version": REPORT_VERSION, "findings": entries}


def print_failures(failed: list[Rule]) -> None:
    for heading, rows in failed:
        print(f"✖ {heading}")
        for fp, entry, why in rows:
            if not fp:
                print("    " + "  ".join(x for x in (entry.get("path", ""), why) if x))
                continue
            print(f"    {fp}  {describe(entry)}  ({why})")
            annotate("error", entry, f"{heading}: {describe(entry)} [{fp}]")


def pull_request_rules(args: argparse.Namespace, root: Path, found: dict, head: dict) -> tuple[list[Rule], list[str]]:
    """Rules 4-6, against the checkout at --base-dir."""
    base_dir = Path(args.base_dir)
    base = read_baseline(base_dir, args.baseline)
    if base is None:
        return [], [f"The base has no `{args.baseline}`, so this pull request adopts the check."]
    preexisting: set[str] = set()
    if any(fp not in base for fp in head):
        print("The baseline grew; scanning the base for findings its code already has.")
        preexisting = {f["fingerprint"] for f in collect(base_dir, args)["findings"]}
    rules, note = base_rules(found, head, base, preexisting, args.minimum_reduction)
    return rules + config_rule(base_dir, root, args.allow_config_change), [note]


def changed_files(base_dir: Path, root: Path) -> set[str]:
    base = git(base_dir, "rev-parse", "HEAD").strip()
    return set(git(root, "diff", "--name-only", "-z", base, "HEAD").split("\0")) - {""}


def annotate_warnings(warnings: list[dict], changed: set[str]) -> None:
    """Only on the files a pull request changed: elsewhere they are not its to fix."""
    for w in warnings:
        if w["path"] in changed:
            annotate("warning", w, describe(w))


def baseline_rules(args: argparse.Namespace, root: Path, found: dict) -> tuple[list[Rule], dict]:
    """Rules 1-3, and the baseline they were held to."""
    committed = read_baseline(root, args.baseline)
    head = committed or {}
    repo = os.environ.get("GH_REPO", "")
    if not repo and head:
        raise Fail("GH_REPO is not set: it names the repository the issues are in")
    rules = local_rules(found, head, issue_problems(repo, head) if head else {})
    if committed is None:
        why = "commit it, empty or with the proposed entries"
        rules.append(("No baseline file", [("", {"path": args.baseline}, why)]))
    return rules, head


def check(args: argparse.Namespace) -> int:
    root = toplevel()
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    found = {f["fingerprint"]: f for f in report["findings"] if f.get("severity") != "warning"}
    warnings = [f for f in report["findings"] if f.get("severity") == "warning"]
    rules, head = baseline_rules(args, root, found)
    notes: list[str] = []
    if args.base_dir:
        more, notes = pull_request_rules(args, root, found, head)
        rules += more
    if args.proposal:
        write_json(Path(args.proposal), proposal(found, head))

    failed = [(heading, rows) for heading, rows in rules if rows]
    print_failures(failed)
    if warnings:
        notes.append(f"{len(warnings)} warnings, which do not fail the check; see the report.")
    if warnings and args.base_dir:
        annotate_warnings(warnings, changed_files(Path(args.base_dir), root))
    for note in notes:
        print(f"- {note.replace('`', '')}")
    verdict = "✖ repo health does not hold" if failed else "✔ repo health holds"
    print(f"{verdict}: {len(found)} findings, {len(head)} baselined")
    summarize(report, len(head), failed, notes)
    return 1 if failed else 0


def summary_table(heading: str, rows: list) -> list[str]:
    lines = ["", f"#### {heading}", "", "| Fingerprint | Finding | Why |", "| --- | --- | --- |"]
    for fp, entry, why in rows[:SUMMARY_ROWS]:
        if fp:
            finding = describe(entry).replace("|", "\\|")
        else:
            finding = f"`{entry['path']}`" if entry else ""
        lines.append(f"| {f'`{fp}`' if fp else ''} | {finding} | {why} |")
    if len(rows) > SUMMARY_ROWS:
        lines.append(f"\n…and {len(rows) - SUMMARY_ROWS} more, in the job log.")
    return lines


def summarize(report: dict, baselined: int, failed: list, notes: list[str]) -> None:
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    tools = report["tools"].items()
    ran = ", ".join(f"{name} ({t['findings']})" for name, t in tools if t["status"] == "ran") or "nothing"
    skipped = ", ".join(f"{name} ({t['reason']})" for name, t in tools if t["status"] == "skipped")
    lines = ["### Repo health " + ("does not hold" if failed else "holds"), ""]
    lines.append(f"{len(report['findings'])} findings, {baselined} baselined.")
    lines.append(f"Ran {ran}." + (f" Skipped {skipped}." if skipped else ""))
    lines += notes
    for heading, rows in failed:
        lines += summary_table(heading, rows)
    with open(target, "a", encoding="utf-8") as out:
        out.write("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- main


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    scanning = argparse.ArgumentParser(add_help=False)
    scanning.add_argument("--tools", default="", help=f"comma-separated subset of {','.join(TOOLS)}")
    scanning.add_argument("--semgrep-config", default="")
    scanning.add_argument("--sqlfluff-dialect", default="ansi")
    scanning.add_argument("--install", action="store_true", help="install Node dependencies before knip")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("scan", parents=[scanning], help="run the analysers and write the report")
    s.add_argument("--output", required=True)
    c = sub.add_parser("check", parents=[scanning], help="hold the report to the baseline")
    c.add_argument("--report", required=True)
    c.add_argument("--baseline", required=True, help="path from the repository root")
    c.add_argument("--base-dir", default="", help="checkout of the commit a pull request merges into")
    c.add_argument("--minimum-reduction", type=int, default=1)
    c.add_argument("--allow-config-change", action="store_true")
    c.add_argument("--proposal", default="")
    args = parser.parse_args(argv)
    try:
        return scan(args) if args.command == "scan" else check(args)
    except Fail as err:
        print(f"✖ {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
