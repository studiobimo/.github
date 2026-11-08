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
# gh api [-H <header>] <endpoint> [--jq <query>]: answers from $GH_FIXTURES.
for arg in "$@"; do case "${arg}" in repos/*) endpoint="${arg}" ;; esac; done
[[ -z "${GH_DOWN:-}" ]] || { echo "gh: Bad Gateway (HTTP 502)" >&2; exit 1; }
case "${endpoint}" in
    */contents/*) file="${GH_FIXTURES}/base.json" ;;
    */issues/*) file="${GH_FIXTURES}/issue-${endpoint##*/}" ;;
esac
[[ -f "${file}" ]] || { echo "gh: Not Found (HTTP 404)" >&2; exit 1; }
cat "${file}"
"""


def git_repo(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp())
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    return root


class Canned(rh.Repo):
    """A Repo whose tools print what the test says they print."""

    def __init__(self, files: dict[str, str], outputs: dict[str, str], **args):
        root = git_repo(files)
        defaults = {"sqlfluff_dialect": "ansi", "semgrep_config": ""}
        super().__init__(root, Path(tempfile.mkdtemp()), argparse.Namespace(**{**defaults, **args}))
        self.outputs = outputs
        self.commands: dict[str, list[str]] = {}

    def run(self, tool, cmd, ok=(0,)):
        self.commands[tool] = cmd
        if tool == "jscpd":
            (self.work / "jscpd").mkdir()
            (self.work / "jscpd" / "jscpd-report.json").write_text(self.outputs[tool])
            return ""
        return self.outputs[tool]


def rows(findings):
    return [(f.tool, f.rule, f.path, f.line, f.anchor) for f in findings]


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

    def test_knip_needs_a_package_json(self):
        with self.assertRaises(rh.Skip):
            rh.knip(Canned({"a.py": ""}, {}))

    def test_jscpd(self):
        files = {"a.py": "x\n", "b.py": "x\n", "c.py": "x\n"}
        dup = lambda first, second: {  # noqa: E731
            "firstFile": {"name": first, "start": 9},
            "secondFile": {"name": second, "start": 4},
            "fragment": "def f():\n    return 1",
            "lines": 6,
        }
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
        [finding] = rh.lizard(Canned({"src/a.py": ""}, {"lizard": out}))
        self.assertEqual((finding.rule, finding.line, finding.anchor), ("complexity", 26, "Parser::parse"))
        self.assertTrue(finding.message.startswith("Parser::parse has 17 NLOC, 16 CCN"))

    def test_ast_grep(self):
        match = {
            "text": "console.log(  x )",
            "range": {"start": {"line": 2}},
            "file": "src/i.js",
            "ruleId": "no-console",
            "message": "Avoid console.log",
        }
        repo = Canned({"sgconfig.yml": "", "src/i.js": ""}, {"ast-grep": json.dumps(match) + "\n"})
        self.assertEqual(rows(rh.ast_grep(repo)), [("ast-grep", "no-console", "src/i.js", 3, "console.log( x )")])

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
        self.assertEqual(rows(rh.sqlfluff(repo)), [("sqlfluff", "LT09", "q.sql", 1, "select a,b from t")])
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
        self.env = {
            "PATH": f"{self.dir / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "GH_FIXTURES": str(self.fixtures),
            "GH_REPO": "studiobimo/example",
            "GITHUB_ACTIONS": "",
            "GITHUB_STEP_SUMMARY": "",
            "GH_DOWN": "",
        }
        self.issue(7, "open")

    def issue(self, number, state, pull_request=False):
        (self.fixtures / f"issue-{number}").write_text(f"{state}\t{str(pull_request).lower()}\n")

    def files(self, found, baseline=None, base=None):
        (self.dir / "report.json").write_text(json.dumps({"version": 1, "tools": {}, "findings": found}))
        if baseline is not None:
            (self.dir / "baseline.json").write_text(json.dumps({"version": 1, "findings": baseline}))
        if base is not None:
            (self.fixtures / "base.json").write_text(json.dumps({"version": 1, "findings": base}))

    def check(self, *extra, **env):
        args = ["check", "--report", str(self.dir / "report.json"), "--baseline", str(self.dir / "baseline.json")]
        out = io.StringIO()
        saved = {k: os.environ.get(k) for k in self.env}
        os.environ.update({**self.env, **env})
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = rh.main([*args, *extra])
        finally:
            for k, v in saved.items():
                os.environ.pop(k) if v is None else os.environ.__setitem__(k, v)
        self.output = out.getvalue()
        return code

    def test_nothing_found_nothing_baselined(self):
        self.files([])
        self.assertEqual(self.check(), 0)

    def test_a_new_finding_fails(self):
        self.files([finding("aa")])
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
        self.files([finding("aa"), finding("bb")], [{"fingerprint": "aa", "issue": 7}, {"fingerprint": "bb", "issue": 7}], [
            {"fingerprint": "aa", "issue": 7},
            {"fingerprint": "cc", "issue": 7},
        ])
        self.assertEqual(self.check("--base", "main"), 1)
        self.assertIn("Added to the baseline since main", self.output)

    def test_a_pull_request_has_to_resolve_baselined_findings(self):
        base = [{"fingerprint": "aa", "issue": 7}, {"fingerprint": "bb", "issue": 7}]
        self.files([finding("aa"), finding("bb")], base, base)
        self.assertEqual(self.check("--base", "develop"), 1)
        self.assertIn("resolves 0 baselined findings; resolve at least 1", self.output)
        self.assertEqual(self.check("--base", "develop", "--minimum-reduction", "0"), 0)

    def test_resolving_one_is_enough_by_default(self):
        self.files([finding("aa")], [{"fingerprint": "aa", "issue": 7}], [
            {"fingerprint": "aa", "issue": 7},
            {"fingerprint": "bb", "issue": 7},
        ])
        self.assertEqual(self.check("--base", "main"), 0, self.output)
        self.assertEqual(self.check("--base", "main", "--minimum-reduction", "2"), 1)

    def test_fewer_left_than_the_minimum_means_all_of_them(self):
        self.files([], [], [{"fingerprint": "aa", "issue": 7}])
        self.assertEqual(self.check("--base", "main", "--minimum-reduction", "5"), 0, self.output)

    def test_an_empty_base_baseline_asks_for_nothing(self):
        self.files([], [], [])
        self.assertEqual(self.check("--base", "main"), 0, self.output)

    def test_deleting_an_entry_without_the_fix_resolves_nothing(self):
        self.files([finding("aa"), finding("bb")], [{"fingerprint": "aa", "issue": 7}], [
            {"fingerprint": "aa", "issue": 7},
            {"fingerprint": "bb", "issue": 7},
        ])
        self.assertEqual(self.check("--base", "main"), 1)
        self.assertIn("New findings", self.output)
        self.assertIn("resolves 0 baselined findings", self.output)

    def test_the_first_baseline_lands_when_the_base_has_none(self):
        self.files([finding("aa")], [{"fingerprint": "aa", "issue": 7}])
        self.assertEqual(self.check("--base", "main"), 0, self.output)
        self.assertIn("this baseline is the first one", self.output)

    def test_the_proposal_is_the_baseline_this_run_would_accept(self):
        self.files([finding("bb", "b.py"), finding("aa")], [{"fingerprint": "aa", "issue": 7}, {"fingerprint": "gone", "issue": 7}])
        self.check("--proposal", str(self.dir / "proposal.json"))
        entries = json.loads((self.dir / "proposal.json").read_text())["findings"]
        self.assertEqual([(e["fingerprint"], e["issue"]) for e in entries], [("aa", 7), ("bb", None)])
        self.assertNotIn("line", entries[0])

    def test_a_pull_request_sees_each_new_finding_on_its_line(self):
        self.files([finding("aa")])
        self.check(GITHUB_ACTIONS="true")
        self.assertIn("::error file=a.py,line=1,title=repo-health::", self.output)


if __name__ == "__main__":
    # Named rather than discovered, so vulture sees them used.
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite(loader.loadTestsFromTestCase(case) for case in (Parsers, Fingerprints, Ratchet))
    sys.exit(not unittest.TextTestRunner().run(suite).wasSuccessful())
