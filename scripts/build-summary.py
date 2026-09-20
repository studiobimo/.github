#!/usr/bin/env python3
"""Writes a JUnit and JaCoCo summary into a GitHub Actions job summary.

Reads the report files Gradle already produces -- no plugin, no extra
permissions, no third-party action that would need `checks: write` on every
job that runs tests.

Usage: build-summary.py [root]   (default: the current directory)
"""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# How many failing tests to name before saying "and N more". The full report is
# uploaded as an artifact; this is a summary.
MAX_FAILURES = 20


def junit(root: Path) -> tuple[dict[str, int], list[str]]:
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    failures: list[str] = []
    for report in sorted(root.glob("**/build/test-results/**/TEST-*.xml")):
        try:
            suite = ET.parse(report).getroot()
        except ET.ParseError:
            # A run killed mid-write leaves a truncated file; the others still count.
            continue
        for key in totals:
            totals[key] += int(suite.get(key, 0))
        for case in suite.iter("testcase"):
            for outcome in ("failure", "error"):
                if case.find(outcome) is not None:
                    failures.append(f"{case.get('classname', '?')}.{case.get('name', '?')}")
    return totals, failures


def jacoco(root: Path) -> tuple[int, int] | None:
    """Covered and total lines across every JaCoCo XML report found."""
    covered = missed = 0
    found = False
    for report in sorted(root.glob("**/build/reports/jacoco/**/*.xml")):
        try:
            tree = ET.parse(report).getroot()
        except ET.ParseError:
            continue
        # The last LINE counter at report level is the project total.
        for counter in tree.findall("counter"):
            if counter.get("type") == "LINE":
                covered += int(counter.get("covered", 0))
                missed += int(counter.get("missed", 0))
                found = True
    return (covered, covered + missed) if found else None


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    lines: list[str] = []

    totals, failures = junit(root)
    if totals["tests"]:
        bad = totals["failures"] + totals["errors"]
        mark = "❌" if bad else "✅"
        lines.append(
            f"{mark} **{totals['tests']} tests** — "
            f"{bad} failed, {totals['skipped']} skipped"
        )
        if failures:
            lines.append("")
            lines.extend(f"- `{name}`" for name in failures[:MAX_FAILURES])
            if len(failures) > MAX_FAILURES:
                lines.append(f"- …and {len(failures) - MAX_FAILURES} more")

    coverage = jacoco(root)
    if coverage:
        covered, total = coverage
        percent = 100.0 * covered / total if total else 0.0
        lines.append("")
        lines.append(f"📊 **Line coverage {percent:.1f}%** — {covered:,} of {total:,} lines")

    if not lines:
        return 0

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    text = "## Tests\n\n" + "\n".join(lines) + "\n"
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
