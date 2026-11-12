"""Defensive parsing of JUnit XML written inside the sandbox.

The report is produced next to untrusted code, so it is treated as untrusted
input: it is size-capped and any document type declaration is refused, which
rules out entity-expansion and external-entity tricks before parsing.
"""

import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass

from patchpilot.sandbox.models import CaseOutcome, CaseResult, Counts

MAX_JUNIT_BYTES = 2 * 1_048_576
_MAX_MESSAGE_CHARS = 500
_MAX_CASES = 20_000


class JUnitError(ValueError):
    """The JUnit report is missing, malformed or unsafe to parse."""


@dataclass(frozen=True)
class JUnitReport:
    """A parsed report plus anything about it that looks wrong.

    ``issues`` is empty for a report pytest would normally write. Each entry
    describes an internal inconsistency, such as a test listed twice.
    """

    counts: Counts
    cases: tuple[CaseResult, ...]
    issues: tuple[str, ...]


def node_id_for(classname: str, name: str) -> str:
    """Rebuild a pytest node ID for a module-level test function.

    pytest reports ``tests.test_module`` as the class name of a function in
    ``tests/test_module.py``. Tests defined inside classes cannot be mapped
    back unambiguously, so V1 evaluation tasks use module-level functions.
    """
    if not classname:
        return name
    return f"{classname.replace('.', '/')}.py::{name}"


def _outcome(case: ET.Element) -> tuple[CaseOutcome, str]:
    for tag, outcome in (
        ("failure", CaseOutcome.FAILED),
        ("error", CaseOutcome.ERROR),
        ("skipped", CaseOutcome.SKIPPED),
    ):
        child = case.find(tag)
        if child is not None:
            return outcome, (child.get("message") or "")[:_MAX_MESSAGE_CHARS]
    return CaseOutcome.PASSED, ""


def _duration(case: ET.Element) -> float:
    try:
        return max(float(case.get("time", "0")), 0.0)
    except ValueError:
        return 0.0


def _declared_totals(root: ET.Element) -> tuple[int, int, int, int] | None:
    """Return the totals the report claims in its ``testsuite`` attributes."""
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if not suites:
        return None
    totals = [0, 0, 0, 0]
    for suite in suites:
        for index, name in enumerate(("tests", "failures", "errors", "skipped")):
            value = suite.get(name)
            if value is None or not value.isdigit():
                return None
            totals[index] += int(value)
    return totals[0], totals[1], totals[2], totals[3]


def parse_junit(xml_text: str) -> JUnitReport:
    """Parse a JUnit report into counts, per-test results and inconsistencies."""
    if len(xml_text.encode("utf-8", errors="replace")) > MAX_JUNIT_BYTES:
        raise JUnitError("JUnit report exceeds the size limit")
    upper = xml_text.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        raise JUnitError("JUnit report contains a document type declaration")
    try:
        # Safe to parse: the size is capped and DTDs were rejected above.
        root = ET.fromstring(xml_text)  # noqa: S314
    except ET.ParseError as exc:
        raise JUnitError(f"JUnit report is not well-formed XML: {exc}") from exc

    cases: list[CaseResult] = []
    for case in root.iter("testcase"):
        if len(cases) >= _MAX_CASES:
            raise JUnitError("JUnit report contains too many test cases")
        classname = case.get("classname", "")
        name = case.get("name", "")
        outcome, message = _outcome(case)
        cases.append(
            CaseResult(
                node_id=node_id_for(classname, name),
                classname=classname,
                name=name,
                outcome=outcome,
                duration_seconds=_duration(case),
                message=message,
            )
        )

    def count(outcome: CaseOutcome) -> int:
        return sum(1 for case in cases if case.outcome is outcome)

    counts = Counts(
        total=len(cases),
        passed=count(CaseOutcome.PASSED),
        failed=count(CaseOutcome.FAILED),
        errors=count(CaseOutcome.ERROR),
        skipped=count(CaseOutcome.SKIPPED),
    )
    issues: list[str] = []
    repeated = sorted(
        node_id for node_id, seen in Counter(case.node_id for case in cases).items() if seen > 1
    )
    if repeated:
        issues.append(f"tests reported more than once: {repeated[:5]}")
    unnamed = sum(1 for case in cases if not case.name)
    if unnamed:
        issues.append(f"{unnamed} test cases have no name")
    declared = _declared_totals(root)
    counted = (counts.total, counts.failed, counts.errors, counts.skipped)
    if declared is None:
        issues.append("the report does not declare its totals")
    elif declared != counted:
        issues.append(
            f"declared totals {declared} do not match the listed test cases {counted} "
            "(tests, failures, errors, skipped)"
        )
    return JUnitReport(counts=counts, cases=tuple(cases), issues=tuple(issues))
