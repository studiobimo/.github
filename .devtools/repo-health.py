#!/usr/bin/env python3
"""Repo health: one fingerprinted report from many analysers, held to a baseline ratchet.

    repo-health.py scan  [--tools a,b] [--semgrep-config CFG] [--sqlfluff-dialect D] --output REPORT
    repo-health.py check --report REPORT --baseline BASELINE [--base REF] [--minimum-reduction N]
                         [--proposal FILE]

`scan` runs every analyser the repository has something for, over its git-tracked files only,
and writes their findings into one JSON report in one shape. A tool runs when the repository
has what it reads, and is configured the way it always is, by its own file:

    knip       a package.json                  (knip.json and friends)
    jscpd      any tracked file                (.jscpd.json)
    vulture    *.py                            ([tool.vulture] in pyproject.toml)
    lizard     any tracked file it can parse   (default thresholds)
    ast-grep   sgconfig.yml                    (its rule directories)
    sqlfluff   *.sql                           (.sqlfluff, else --sqlfluff-dialect)
    semgrep    .semgrep.yml or .semgrep/       (or --semgrep-config)
    uncomment  .uncomment.toml                 (the same file)

Every finding gets a fingerprint that survives line moves: a hash of the tool, the rule, the
path and an anchor that is the thing found (a symbol name, or the flagged source line with its
whitespace collapsed), plus an index among identical ones. The line number is reported but
never hashed.

`check` holds the report to the baseline, a reviewed list of the findings a repository still
carries, each tracked by an open issue:

    1. no finding is missing from the baseline (nothing new);
    2. no baseline entry is missing from the report (a fixed finding leaves the baseline);
    3. every entry names an open issue of this repository;
    and, given --base, against the baseline on that branch:
    4. nothing was added to the baseline;
    5. at least --minimum-reduction of its entries were resolved, or all of them if fewer.

A base branch with no baseline yet skips 4 and 5, which is how the first baseline lands.
--proposal writes the baseline this run would accept: the entries still found, plus every new
finding with `"issue": null` for a person to fill in.

`check` reads the base baseline and the issues with gh: GH_TOKEN and GH_REPO, with
contents: read and issues: read. Exit codes: 0 passes, 1 does not, 2 could not tell.
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
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

REPORT_VERSION = 1
TOOLS = ("knip", "jscpd", "vulture", "lizard", "ast-grep", "sqlfluff", "semgrep", "uncomment")
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
    fingerprint: str = ""

    def as_json(self) -> dict:
        return {
            "fingerprint": self.fingerprint,
            "tool": self.tool,
            "rule": self.rule,
            "path": self.path,
            "line": self.line,
            "message": self.message,
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

    def run(self, tool: str, cmd: list[str], ok: tuple[int, ...] = (0,)) -> str:
        try:
            proc = subprocess.run(cmd, cwd=self.root, capture_output=True, text=True, check=False)
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


def knip(repo: Repo) -> list[Finding]:
    if not repo.has("package.json"):
        raise Skip("no package.json")
    out = repo.run("knip", ["knip", "--reporter", "json", "--no-progress", "--no-exit-code"])
    found = []
    for entry in json.loads(out).get("issues", []):
        path = repo.rel(entry["file"])
        for kind, items in entry.items():
            if kind == "file":
                continue
            # Most kinds are a list of {name, line}; enum and namespace members can come
            # grouped under their parent, and a duplicate is a list of the names it joins.
            if isinstance(items, dict):
                items = [{**i, "name": f"{parent}.{i['name']}"} for parent, group in items.items() for i in group]
            for item in items or []:
                if isinstance(item, list):
                    item = {"name": " = ".join(sorted(i["name"] for i in item)), "line": item[0].get("line")}
                name = item["name"]
                message = f"{KNIP.get(kind, kind)}: {name}"
                found.append(Finding("knip", kind, path, item.get("line") or 1, message, name))
    return found


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


LIZARD = re.compile(r"^(?P<path>.+?):(?P<line>\d+): warning: (?P<name>.+) has (?P<metrics>\d+ NLOC, .*)$")


def lizard(repo: Repo) -> list[Finding]:
    if not repo.tracked:
        raise Skip("no tracked files")
    # 1 means a function is over a threshold.
    out = repo.run("lizard", ["lizard", "--warnings_only", "--input_file", repo.listfile("lizard", repo.tracked)], ok=(0, 1))
    found = []
    for raw in out.splitlines():
        m = LIZARD.match(raw)
        if m:
            message = f"{m['name']} has {m['metrics']}"
            found.append(Finding("lizard", "complexity", repo.rel(m["path"]), int(m["line"]), message, m["name"]))
    return found


def ast_grep(repo: Repo) -> list[Finding]:
    if not repo.has("sgconfig.yml", "sgconfig.yaml"):
        raise Skip("no sgconfig.yml")
    # 1 means a rule of severity error matched; anything else is ast-grep failing.
    out = repo.run("ast-grep", ["ast-grep", "scan", "--json=stream"], ok=(0, 1))
    found = []
    for raw in out.splitlines():
        if raw.strip():
            m = json.loads(raw)
            line = m["range"]["start"]["line"] + 1
            anchor = " ".join(m["text"].split())
            found.append(Finding("ast-grep", m["ruleId"], repo.rel(m["file"]), line, m["message"], anchor))
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
            if v.get("warning"):
                continue
            line = v["start_line_no"]
            found.append(Finding("sqlfluff", v["code"], path, line, v["description"], repo.line(path, line)))
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


def scan(args: argparse.Namespace) -> int:
    selected = set(args.tools.split(",")) if args.tools else set(TOOLS)
    unknown = selected - set(TOOLS)
    if unknown:
        raise Fail(f"unknown tools: {', '.join(sorted(unknown))}; known: {', '.join(TOOLS)}")
    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
    findings: list[Finding] = []
    tools: dict[str, dict] = {}
    with tempfile.TemporaryDirectory() as work:
        repo = Repo(root, Path(work), args)
        for name in TOOLS:
            if name not in selected:
                tools[name] = {"status": "disabled"}
                continue
            try:
                found = [f for f in RUNNERS[name](repo) if f.path in repo.tracked_set]
            except Skip as skip:
                tools[name] = {"status": "skipped", "reason": str(skip)}
                print(f"- {name}: skipped, {skip}")
                continue
            tools[name] = {"status": "ran", "findings": len(found)}
            findings += found
            print(f"✔ {name}: {len(found)} findings")
    report = {"version": REPORT_VERSION, "tools": tools, "findings": [f.as_json() for f in fingerprint(findings)]}
    write_json(Path(args.output), report)
    print(f"{len(findings)} findings in {args.output}")
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


def base_baseline(repo: str, path: str, ref: str) -> dict[str, dict] | None:
    """The baseline on the base branch, or None where that branch has none yet."""
    endpoint = f"repos/{repo}/contents/{urllib.parse.quote(path)}?ref={urllib.parse.quote(ref, safe='')}"
    code, out, err = gh("api", "-H", "Accept: application/vnd.github.raw", endpoint)
    if code != 0:
        if not_found(err):
            return None
        raise Fail(f"could not read {path} on {ref}: {err.strip()}")
    return load_baseline(out, f"{path} on {ref}")


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


def base_rules(found: dict, head: dict, base: dict, ref: str, minimum: int) -> tuple[list[Rule], str]:
    """Rules 4-5: the baseline against the one on the base branch, and a note on the reduction."""
    added = [(fp, e, f"not in the baseline on {ref}") for fp, e in head.items() if fp not in base]
    # Dropping an entry only counts when its finding is gone too; otherwise it is a new finding.
    resolved = [fp for fp in base if fp not in head and fp not in found]
    needed = min(minimum, len(base))
    rules: list[Rule] = [(f"Added to the baseline since {ref}", added)]
    if len(resolved) < needed:
        why = f"resolves {len(resolved)} baselined findings; resolve at least {needed}"
        rules.append((f"Not enough resolved since {ref}", [("", {}, why)]))
    note = f"{len(resolved)} of the {len(base)} findings baselined on `{ref}` resolved; {needed} required."
    return rules, note


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
            if not entry:
                print(f"    {why}")
                continue
            print(f"    {fp}  {describe(entry)}  ({why})")
            annotate("error", entry, f"{heading}: {describe(entry)} [{fp}]")


def check(args: argparse.Namespace) -> int:
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    found = {f["fingerprint"]: f for f in report["findings"]}
    baseline_path = Path(args.baseline)
    head = load_baseline(baseline_path.read_text(encoding="utf-8"), args.baseline) if baseline_path.is_file() else {}
    repo = os.environ.get("GH_REPO", "")
    if not repo and (head or args.base):
        raise Fail("GH_REPO is not set: it names the repository the issues and the base branch are in")

    rules = local_rules(found, head, issue_problems(repo, head) if head else {})
    notes = []
    if args.base:
        base = base_baseline(repo, args.baseline, args.base)
        if base is None:
            notes.append(f"`{args.base}` has no `{args.baseline}` yet, so this baseline is the first one.")
        else:
            more, note = base_rules(found, head, base, args.base, args.minimum_reduction)
            rules += more
            notes.append(note)
    if args.proposal:
        write_json(Path(args.proposal), proposal(found, head))

    failed = [(heading, rows) for heading, rows in rules if rows]
    print_failures(failed)
    for note in notes:
        print(f"- {note.replace('`', '')}")
    verdict = "✖ repo health does not hold" if failed else "✔ repo health holds"
    print(f"{verdict}: {len(found)} findings, {len(head)} baselined")
    summarize(report, len(head), failed, notes)
    return 1 if failed else 0


def summarize(report: dict, baselined: int, failed: list, notes: list[str]) -> None:
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    lines = ["### Repo health " + ("does not hold" if failed else "holds"), ""]
    lines.append(f"{len(report['findings'])} findings, {baselined} baselined.")
    ran = [f"{name} ({t['findings']})" for name, t in report["tools"].items() if t["status"] == "ran"]
    skipped = [f"{name} ({t['reason']})" for name, t in report["tools"].items() if t["status"] == "skipped"]
    lines.append(f"Ran {', '.join(ran) or 'nothing'}." + (f" Skipped {', '.join(skipped)}." if skipped else ""))
    lines += [f"{n}" for n in notes]
    for heading, rows in failed:
        lines += ["", f"#### {heading}", "", "| Fingerprint | Finding | Why |", "| --- | --- | --- |"]
        for fp, entry, why in rows[:SUMMARY_ROWS]:
            finding = describe(entry).replace("|", "\\|") if entry else ""
            lines.append(f"| `{fp}` | {finding} | {why} |" if fp else f"| | | {why} |")
        if len(rows) > SUMMARY_ROWS:
            lines.append(f"\n…and {len(rows) - SUMMARY_ROWS} more, in the job log.")
    with open(target, "a", encoding="utf-8") as out:
        out.write("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- main


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("scan", help="run the analysers and write the report")
    s.add_argument("--output", required=True)
    s.add_argument("--tools", default="", help=f"comma-separated subset of {','.join(TOOLS)}")
    s.add_argument("--semgrep-config", default="")
    s.add_argument("--sqlfluff-dialect", default="ansi")
    c = sub.add_parser("check", help="hold the report to the baseline")
    c.add_argument("--report", required=True)
    c.add_argument("--baseline", required=True)
    c.add_argument("--base", default="", help="branch whose baseline this one may only shrink")
    c.add_argument("--minimum-reduction", type=int, default=1)
    c.add_argument("--proposal", default="")
    args = parser.parse_args(argv)
    try:
        return scan(args) if args.command == "scan" else check(args)
    except Fail as err:
        print(f"✖ {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
