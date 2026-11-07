"""Fail CI when a required JUnit test report contains skipped tests."""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Sequence


def count_skipped_tests(report_path: Path) -> int:
    """Return the aggregate skipped-test count from pytest JUnit XML."""

    root = ET.parse(report_path).getroot()
    if root.tag not in {"testsuite", "testsuites"}:
        raise ValueError(f"unexpected JUnit root element: {root.tag}")
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    if not suites:
        raise ValueError("JUnit report contains no test suites")
    return sum(int(suite.attrib.get("skipped", "0")) for suite in suites)


def main(argv: Sequence[str] | None = None) -> int:
    """Check a JUnit report and return a shell-friendly status code."""

    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    args = parser.parse_args(argv)

    try:
        skipped = count_skipped_tests(args.report)
    except FileNotFoundError:
        print(f"JUnit report not found: {args.report}", file=sys.stderr)
        return 2
    except ET.ParseError as exc:
        print(f"Invalid JUnit XML report {args.report}: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"Invalid JUnit report {args.report}: {exc}", file=sys.stderr)
        return 2
    if skipped:
        print(f"PostgreSQL integration tests skipped: {skipped}", file=sys.stderr)
        return 1

    print("PostgreSQL integration tests skipped: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
