#!/usr/bin/env python3
"""Tests repo-health.py: each tool's output into findings, the fingerprints, and the ratchet.

The tools are stood in for by the output they print, and gh by a script that answers from
files, so nothing here installs an analyser or reaches GitHub. The real tools run in
self-repo-health. Run: python3 .devtools/test-repo-health.py
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("repo_health", HERE / "repo-health.py")
rh = importlib.util.module_from_spec(spec)
# A dataclass looks its module up by name.
sys.modules["repo_health"] = rh
spec.loader.exec_module(rh)

GH = """#!/usr/bin/env bash
# gh api repos/<repo>/issues/<n> --jq <query>: answers from $GH_FIXTURES/issue-<n>.
for arg in "$@"; do case "${arg}" in repos/*) endpoint="${arg}" ;; esac; done
[[ -z "${GH_DOWN:-}" ]] || { echo "gh: Bad Gateway (HTTP 502)" >&2; exit 1; }
file="${GH_FIXTURES}/issue-${endpoint##*/}"
[[ -f "${file}" ]] || { echo "gh: Not Found (HTTP 404)" >&2; exit 1; }
cat "${file}"
"""


def add(root: Path, files: dict[str, str]) -> None:
    """Writes the files and stages them, so git lists them as tracked."""
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)


def git_repo(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    add(root, files)
    return root


class Canned(rh.Repo):
    """A Repo whose tools print what the test says they print."""

    def __init__(self, files: dict[str, str], outputs: dict[str, str], **args):
        root = git_repo(files)
        defaults = {"sqlfluff_dialect": "ansi", "semgrep_config": "", "install": False}
        super().__init__(root, Path(tempfile.mkdtemp()), argparse.Namespace(**{**defaults, **args}))
        self.outputs = outputs
        self.commands: dict[str, list[str]] = {}
        self.installs: list[tuple[str, str]] = []

    def run(self, tool, cmd, ok=(0,), cwd=None):
        self.commands[tool] = cmd
        if tool == "install":
            self.installs.append((cmd[0], Path(cwd).relative_to(self.root).as_posix()))
            return ""
        if tool == "jscpd":
            (self.work / "jscpd").mkdir()
            (self.work / "jscpd" / "jscpd-report.json").write_text(self.outputs[tool])
            return ""
        out = self.outputs[tool]
        # A tool run more than once prints each run's output in turn.
        return out.pop(0) if isinstance(out, list) else out


def dup(first, second):
    """A jscpd clone between two files."""
    return {
        "firstFile": {"name": first, "start": 9},
        "secondFile": {"name": second, "start": 4},
        "fragment": "def f():\n    return 1",
        "lines": 6,
    }


def rows(findings):
    return [(f.tool, f.rule, f.path, f.line, f.anchor) for f in findings]


def match(rule, file, line, text, severity="error"):
    """One line of ast-grep's --json=stream."""
    found = {"text": text, "range": {"start": {"line": line - 1}}, "file": file, "ruleId": rule}
    return json.dumps({**found, "message": rule, "severity": severity}) + "\n"


class Parsers(unittest.TestCase):
    def test_knip(self):
        out = {
            "issues": [
                {"file": "package.json", "dependencies": [{"name": "left-pad", "line": 3}], "files": []},
                {"file": "src/orphan.js", "files": [{"name": "src/orphan.js"}], "exports": []},
                {"file": "src/e.ts", "enumMembers": {"Color": [{"name": "Red", "line": 2}]}},
                {"file": "src/d.ts", "duplicates": [[{"name": "b", "line": 4}, {"name": "a", "line": 4}]]},
            ]
        }
        repo = Canned({"package.json": "{}\n"}, {"knip": json.dumps(out)})
        self.assertEqual(
            rows(rh.knip(repo)),
            [
                ("knip", "dependencies", "package.json", 3, "left-pad"),
                ("knip", "files", "src/orphan.js", 1, "src/orphan.js"),
                ("knip", "enumMembers", "src/e.ts", 2, "Color.Red"),
                ("knip", "duplicates", "src/d.ts", 4, "a = b"),
            ],
        )

    def test_knip_installs_beside_each_lockfile_first(self):
        files = {"package.json": "{}", "pnpm-lock.yaml": "", "tools/package-lock.json": "{}", "tools/package.json": "{}"}
        repo = Canned(files, {"knip": '{"issues": []}'}, install=True)
        rh.knip(repo)
        self.assertEqual(repo.installs, [("corepack", "."), ("npm", "tools")])
        self.assertIn("--ignore-scripts", repo.commands["install"])

    def test_knip_installs_nothing_unless_asked(self):
        repo = Canned({"package.json": "{}", "pnpm-lock.yaml": ""}, {"knip": '{"issues": []}'})
        rh.knip(repo)
        self.assertEqual(repo.installs, [])

    def test_knip_needs_a_package_json(self):
        with self.assertRaises(rh.Skip):
            rh.knip(Canned({"a.py": ""}, {}))

    def test_jscpd(self):
        files = {"a.py": "x\n", "b.py": "x\n", "c.py": "x\n"}
        repo = Canned(files, {})
        report = {"duplicates": [dup(str(repo.root / "b.py"), str(repo.root / "a.py")), dup("b.py", "gone.py")]}
        repo.outputs["jscpd"] = json.dumps(report)
        [finding] = rh.jscpd(repo)
        # The pair is ordered, so the clone is filed under the same file whichever jscpd lists first.
        self.assertEqual((finding.path, finding.line, finding.message), ("a.py", 4, "6 lines duplicated at b.py:9"))
        self.assertIn("--ignore", repo.commands["jscpd"])

    def test_jscpd_leaves_ignores_to_the_repository_config(self):
        repo = Canned({".jscpd.json": "{}"}, {"jscpd": '{"duplicates": []}'})
        rh.jscpd(repo)
        self.assertNotIn("--ignore", repo.commands["jscpd"])

    def test_vulture(self):
        out = (
            "src/a.py:1: unused import 'os' (90% confidence)\n"
            "src/a.py:7: unused function 'helper' (60% confidence, 3 lines)\n"
            "src/a.py:9: unreachable code after 'return' (100% confidence, 2 lines)\n"
        )
        repo = Canned({"src/a.py": "\n" * 8 + "  x = 1\n"}, {"vulture": out})
        self.assertEqual(
            rows(rh.vulture(repo)),
            [
                ("vulture", "unused-import", "src/a.py", 1, "os"),
                ("vulture", "unused-function", "src/a.py", 7, "helper"),
                ("vulture", "unreachable-code-after-return", "src/a.py", 9, "x = 1"),
            ],
        )
        self.assertEqual(repo.commands["vulture"][1:], ["src/a.py"])

    def test_lizard(self):
        out = "src/a.py:26: warning: Parser::parse has 17 NLOC, 16 CCN, 112 token, 1 PARAM, 17 length, 0 ND\n"
        repo = Canned({"src/a.py": ""}, {"lizard": out})
        [finding] = rh.lizard(repo)
        self.assertEqual(repo.commands["lizard"][2:8], ["--CCN", "15", "--length", "80", "--arguments", "6"])
        self.assertEqual((finding.rule, finding.line, finding.anchor), ("complexity", 26, "Parser::parse"))
        self.assertTrue(finding.message.startswith("Parser::parse has 17 NLOC, 16 CCN"))

    def test_ast_grep_runs_the_bundled_rules_and_the_repository_s(self):
        bundled = match("repo-health-suppression", "src/i.js", 2, "// eslint-disable-line")
        bundled += match("repo-health-magic-number", "src/i.js", 3, "86400", "warning")
        own = match("no-console", "src/i.js", 3, "console.log(  x )", "hint")
        repo = Canned({"sgconfig.yml": "", "src/i.js": ""}, {"ast-grep": [bundled, own]})
        found = rh.ast_grep(repo)
        self.assertEqual(
            [(f.rule, f.line, f.anchor, f.severity) for f in found],
            [
                ("repo-health-suppression", 2, "// eslint-disable-line", "error"),
                ("repo-health-magic-number", 3, "86400", "warning"),
                ("no-console", 3, "console.log( x )", "warning"),
            ],
        )

    def test_ast_grep_brings_its_rules_to_any_python_or_javascript(self):
        repo = Canned({"a.py": ""}, {"ast-grep": ""})
        self.assertEqual(rh.ast_grep(repo), [])
        self.assertEqual(repo.commands["ast-grep"][2:4], ["--rule", str(rh.RULES)])
        with self.assertRaises(rh.Skip):
            rh.ast_grep(Canned({"README.md": ""}, {}))

    def test_ruff(self):
        out = [{"code": "S307", "filename": "a.py", "location": {"row": 2}, "message": "Use of eval"}]
        repo = Canned({"a.py": "\n  eval(x)  # noqa: S307\n"}, {"ruff": json.dumps(out)})
        self.assertEqual(rows(rh.ruff(repo)), [("ruff", "S307", "a.py", 2, "eval(x) # noqa: S307")])
        self.assertIn("--isolated", repo.commands["ruff"])
        self.assertIn("--ignore-noqa", repo.commands["ruff"])

    def test_test_code_may_assert_and_use_numbers(self):
        cases = {("S101", "tests/test_a.py"): True, ("S101", "src/a.py"): False,
                 ("repo-health-magic-number", "src/a.test.ts"): True, ("S307", "tests/test_a.py"): False}
        for (rule, path), exempt in cases.items():
            with self.subTest(rule=rule, path=path):
                self.assertEqual(rh.exempt(rh.Finding("ruff", rule, path, 1, "", "")), exempt)

    def test_sqlfluff(self):
        out = [
            {
                "filepath": "q.sql",
                "violations": [
                    {"start_line_no": 1, "code": "LT09", "description": "Select targets", "warning": False},
                    {"start_line_no": 1, "code": "AM04", "description": "demoted", "warning": True},
                ],
            }
        ]
        repo = Canned({"q.sql": "select a,b  from t\n"}, {"sqlfluff": json.dumps(out)})
        found = rh.sqlfluff(repo)
        self.assertEqual(rows(found), [("sqlfluff", "LT09", "q.sql", 1, "select a,b from t"), ("sqlfluff", "AM04", "q.sql", 1, "select a,b from t")])
        # A rule the repository's config demotes is reported as a warning.
        self.assertEqual([f.severity for f in found], ["error", "warning"])
        self.assertEqual(repo.commands["sqlfluff"][-3:], ["--dialect", "ansi", "q.sql"])

    def test_sqlfluff_takes_the_repository_dialect(self):
        repo = Canned({"q.sql": "", ".sqlfluff": "[sqlfluff]\ndialect = postgres\n"}, {"sqlfluff": "[]"})
        rh.sqlfluff(repo)
        self.assertNotIn("--dialect", repo.commands["sqlfluff"])

    def test_semgrep(self):
        out = {"results": [{"check_id": "py.eval", "path": "a.py", "start": {"line": 2}, "extra": {"message": "eval"}}]}
        repo = Canned({".semgrep/rules.yml": "", "a.py": "\n  eval(x)\n"}, {"semgrep": json.dumps(out)})
        self.assertEqual(rows(rh.semgrep(repo)), [("semgrep", "py.eval", "a.py", 2, "eval(x)")])
        self.assertEqual(repo.commands["semgrep"][2:4], ["--config", ".semgrep/"])

    def test_semgrep_needs_rules(self):
        with self.assertRaises(rh.Skip):
            rh.semgrep(Canned({"a.py": ""}, {}))

    def test_uncomment(self):
        out = {"violations": [{"path": "./src/i.js", "line": 2, "excerpt": "// const old = used(1);"}]}
        repo = Canned({".uncomment.toml": "", "src/i.js": ""}, {"uncomment": json.dumps(out)})
        self.assertEqual(
            rows(rh.uncomment(repo)), [("uncomment", "removable-comment", "src/i.js", 2, "// const old = used(1);")]
        )


class Fingerprints(unittest.TestCase):
    def finding(self, line, anchor="x", path="a.py"):
        return rh.Finding("vulture", "unused-function", path, line, f"unused function '{anchor}'", anchor)

    def test_a_line_move_keeps_the_fingerprint(self):
        [before] = rh.fingerprint([self.finding(4)])
        [after] = rh.fingerprint([self.finding(40)])
        self.assertEqual(before.fingerprint, after.fingerprint)
        self.assertRegex(before.fingerprint, r"^[0-9a-f]{16}$")

    def test_what_was_found_and_where_set_it(self):
        prints = {f.fingerprint for f in rh.fingerprint([self.finding(4), self.finding(4, "y"), self.finding(4, path="b.py")])}
        self.assertEqual(len(prints), 3)

    def test_identical_findings_are_numbered_in_line_order(self):
        first, second = rh.fingerprint([self.finding(9), self.finding(3)])
        self.assertEqual((first.line, second.line), (3, 9))
        self.assertNotEqual(first.fingerprint, second.fingerprint)
        [alone] = rh.fingerprint([self.finding(3)])
        self.assertEqual(alone.fingerprint, first.fingerprint)


def finding(fp, path="a.py"):
    return {"fingerprint": fp, "tool": "vulture", "rule": "unused-function", "path": path, "line": 1, "message": fp}


class Ratchet(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "bin").mkdir()
        (self.dir / "bin" / "gh").write_text(GH)
        (self.dir / "bin" / "gh").chmod(0o755)
        self.fixtures = self.dir / "fixtures"
        self.fixtures.mkdir()
        self.root = git_repo({})
        self.base = git_repo({})
        self.env = {
            "PATH": f"{self.dir / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "GH_FIXTURES": str(self.fixtures),
            "GH_REPO": "studiobimo/example",
            "GITHUB_ACTIONS": "",
            "GITHUB_STEP_SUMMARY": "",
            "GH_DOWN": "",
        }
        self.issue(7, "open")
        self.scanned: list[Path] = []
        self.base_findings: list[dict] = []
        real_collect = rh.collect

        def collect(root, args):
            self.scanned.append(Path(root))
            return {"version": 1, "tools": {}, "findings": self.base_findings}

        rh.collect = collect
        self.addCleanup(setattr, rh, "collect", real_collect)

    def issue(self, number, state, pull_request=False):
        (self.fixtures / f"issue-{number}").write_text(f"{state}\t{str(pull_request).lower()}\n")

    def files(self, found, baseline=None, base=None):
        (self.dir / "report.json").write_text(json.dumps({"version": 1, "tools": {}, "findings": found}))
        if baseline is not None:
            add(self.root, {"baseline.json": json.dumps({"version": 1, "findings": baseline})})
        if base is not None:
            add(self.base, {"baseline.json": json.dumps({"version": 1, "findings": base})})

    def check(self, *extra, **env):
        args = ["check", "--report", str(self.dir / "report.json"), "--baseline", "baseline.json"]
        out = io.StringIO()
        saved = {k: os.environ.get(k) for k in self.env}
        cwd = os.getcwd()
        os.environ.update({**self.env, **env})
        os.chdir(self.root)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = rh.main([*args, *extra])
        finally:
            os.chdir(cwd)
            for k, v in saved.items():
                os.environ.pop(k) if v is None else os.environ.__setitem__(k, v)
        self.output = out.getvalue()
        return code

    def pull_request(self, *extra, **env):
        return self.check("--base-dir", str(self.base), *extra, **env)

    def test_nothing_found_nothing_baselined(self):
        self.files([], [])
        self.assertEqual(self.check(), 0, self.output)

    def test_the_baseline_file_has_to_exist(self):
        self.files([])
        self.assertEqual(self.check(), 1)
        self.assertIn("No baseline file", self.output)

    def test_a_new_finding_fails(self):
        self.files([finding("aa")], [])
        self.assertEqual(self.check(), 1)
        self.assertIn("New findings", self.output)

    def test_a_baselined_finding_with_an_open_issue_passes(self):
        self.files([finding("aa")], [{"fingerprint": "aa", "issue": 7}])
        self.assertEqual(self.check(), 0, self.output)

    def test_a_resolved_finding_has_to_leave_the_baseline(self):
        self.files([], [{"fingerprint": "aa", "issue": 7}])
        self.assertEqual(self.check(), 1)
        self.assertIn("still in the baseline", self.output)

    def test_every_entry_needs_an_open_issue(self):
        self.issue(8, "closed")
        self.issue(9, "open", pull_request=True)
        cases = {None: "names no issue", 8: "#8 is closed", 9: "#9 is a pull request", 10: "#10 does not exist"}
        for issue, why in cases.items():
            with self.subTest(issue=issue):
                self.files([finding("aa")], [{"fingerprint": "aa", "issue": issue}])
                self.assertEqual(self.check(), 1)
                self.assertIn(why, self.output)

    def test_a_malformed_baseline_cannot_be_judged(self):
        self.files([finding("aa")], [{"fingerprint": "aa", "issue": 7}, {"fingerprint": "aa", "issue": 7}])
        self.assertEqual(self.check(), 2)
        self.assertIn("listed twice", self.output)

    def test_gh_failing_cannot_be_judged(self):
        self.files([finding("aa")], [{"fingerprint": "aa", "issue": 7}])
        self.assertEqual(self.check(GH_DOWN="1"), 2)

    def test_nothing_may_be_added_to_the_base_baseline(self):
        both = [{"fingerprint": "aa", "issue": 7}, {"fingerprint": "bb", "issue": 7}]
        self.files([finding("aa"), finding("bb")], both, [both[0], {"fingerprint": "cc", "issue": 7}])
        self.assertEqual(self.pull_request(), 1)
        self.assertIn("Added to the baseline", self.output)
        self.assertEqual(self.scanned, [self.base])

    def test_a_finding_the_base_code_already_has_may_be_added(self):
        # A newer tool or rule found it in code the pull request did not write.
        both = [{"fingerprint": "aa", "issue": 7}, {"fingerprint": "bb", "issue": 7}]
        self.base_findings = [finding("bb")]
        self.files([finding("aa"), finding("bb")], both, [both[0], {"fingerprint": "cc", "issue": 7}])
        self.assertEqual(self.pull_request(), 0, self.output)
        self.assertIn("1 entries added for findings the base's code already has", self.output)

    def one_of_two_resolved(self):
        """The base baselines aa and bb; this pull request fixed bb."""
        base = [{"fingerprint": "aa", "issue": 7}, {"fingerprint": "bb", "issue": 7}]
        self.files([finding("aa")], base[:1], base)

    def test_the_base_is_only_scanned_when_the_baseline_grows(self):
        self.one_of_two_resolved()
        self.assertEqual(self.pull_request(), 0, self.output)
        self.assertEqual(self.scanned, [])

    def test_a_pull_request_has_to_resolve_baselined_findings(self):
        base = [{"fingerprint": "aa", "issue": 7}, {"fingerprint": "bb", "issue": 7}]
        self.files([finding("aa"), finding("bb")], base, base)
        self.assertEqual(self.pull_request(), 1)
        self.assertIn("resolves 0 baselined findings; resolve at least 1", self.output)
        self.assertEqual(self.pull_request("--minimum-reduction", "0"), 0)

    def test_resolving_one_is_enough_by_default(self):
        self.one_of_two_resolved()
        self.assertEqual(self.pull_request(), 0, self.output)
        self.assertEqual(self.pull_request("--minimum-reduction", "2"), 1)

    def test_fewer_left_than_the_minimum_means_all_of_them(self):
        self.files([], [], [{"fingerprint": "aa", "issue": 7}])
        self.assertEqual(self.pull_request("--minimum-reduction", "5"), 0, self.output)

    def test_an_empty_base_baseline_asks_for_nothing(self):
        self.files([], [], [])
        self.assertEqual(self.pull_request(), 0, self.output)

    def test_deleting_an_entry_without_the_fix_resolves_nothing(self):
        self.files([finding("aa"), finding("bb")], [{"fingerprint": "aa", "issue": 7}], [
            {"fingerprint": "aa", "issue": 7},
            {"fingerprint": "bb", "issue": 7},
        ])
        self.assertEqual(self.pull_request(), 1)
        self.assertIn("New findings", self.output)
        self.assertIn("resolves 0 baselined findings", self.output)

    def test_the_first_baseline_lands_when_the_base_has_none(self):
        self.files([finding("aa")], [{"fingerprint": "aa", "issue": 7}])
        add(self.root, {"knip.json": "{}"})
        self.assertEqual(self.pull_request(), 0, self.output)
        self.assertIn("adopts the check", self.output)

    def test_an_analyser_config_may_not_change(self):
        self.files([], [], [])
        add(self.base, {"knip.json": "{}", "pyproject.toml": "[tool.vulture]\nmin_confidence = 60\n"})
        add(self.root, {"knip.json": '{"ignore": ["src/**"]}', "pyproject.toml": "[tool.vulture]\nmin_confidence = 90\n"})
        self.assertEqual(self.pull_request(), 1)
        self.assertIn("knip.json", self.output)
        self.assertIn("pyproject.toml (vulture)", self.output)
        self.assertEqual(self.pull_request("--allow-config-change"), 0, self.output)

    def test_deleting_a_config_turns_nothing_off_quietly(self):
        self.files([], [], [])
        add(self.base, {"sgconfig.yml": "ruleDirs:\n  - rules\n", "rules/no-eval.yml": "id: no-eval\n"})
        add(self.root, {"sgconfig.yml": "ruleDirs:\n  - rules\n"})
        self.assertEqual(self.pull_request(), 1)
        self.assertIn("rules/no-eval.yml", self.output)

    def test_the_rest_of_pyproject_may_change(self):
        self.files([], [], [])
        add(self.base, {"pyproject.toml": '[project]\nname = "a"\n'})
        add(self.root, {"pyproject.toml": '[project]\nname = "b"\n'})
        self.assertEqual(self.pull_request(), 0, self.output)

    def test_the_proposal_is_the_baseline_this_run_would_accept(self):
        self.files([finding("bb", "b.py"), finding("aa")], [{"fingerprint": "aa", "issue": 7}, {"fingerprint": "gone", "issue": 7}])
        self.check("--proposal", str(self.dir / "proposal.json"))
        entries = json.loads((self.dir / "proposal.json").read_text())["findings"]
        self.assertEqual([(e["fingerprint"], e["issue"]) for e in entries], [("aa", 7), ("bb", None)])
        self.assertNotIn("line", entries[0])

    def test_warnings_never_fail_and_show_only_where_the_pull_request_changed(self):
        add(self.base, {"old.py": "x = 86400\n", "new.py": "x = 1\n"})
        subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-qm", "base"], cwd=self.base, check=True)
        add(self.root, {"old.py": "x = 86400\n", "new.py": "x = 3600\n"})
        subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-qm", "base"], cwd=self.root, check=True)
        base_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.base, capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "fetch", "-q", str(self.base), base_sha], cwd=self.root, check=True)
        warn = {"severity": "warning", "rule": "repo-health-magic-number"}
        self.files([{**finding("w1", "old.py"), **warn}, {**finding("w2", "new.py"), **warn}], [], [])
        self.assertEqual(self.pull_request(GITHUB_ACTIONS="true"), 0, self.output)
        self.assertIn("2 warnings", self.output)
        self.assertIn("::warning file=new.py,", self.output)
        self.assertNotIn("::warning file=old.py,", self.output)

    def test_a_pull_request_sees_each_new_finding_on_its_line(self):
        self.files([finding("aa")], [])
        self.check(GITHUB_ACTIONS="true")
        self.assertIn("::error file=a.py,line=1,title=repo-health::", self.output)


class SgconfigDirs(unittest.TestCase):
    def test_both_list_forms(self):
        text = "ruleDirs:\n  - rules\n  - 'more/'\nutilDirs: [utils, \"lib\"]\ntestConfigs:\n  - testDir: t\n"
        self.assertEqual(rh.sgconfig_dirs(text), ["rules", "more", "utils", "lib"])


if __name__ == "__main__":
    # Named rather than discovered, so vulture sees them used.
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite(loader.loadTestsFromTestCase(case) for case in (Parsers, Fingerprints, Ratchet, SgconfigDirs))
    sys.exit(not unittest.TextTestRunner().run(suite).wasSuccessful())
