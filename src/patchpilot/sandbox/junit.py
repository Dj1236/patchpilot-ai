"""Defensive parsing of JUnit XML written inside the sandbox.

The report is produced next to untrusted code, so it is treated as untrusted
input: it is size-capped and any document type declaration is refused, which
rules out entity-expansion and external-entity tricks before parsing.
"""

import xml.etree.ElementTree as ET

from patchpilot.sandbox.models import CaseOutcome, CaseResult, Counts

MAX_JUNIT_BYTES = 2 * 1_048_576
_MAX_MESSAGE_CHARS = 500
_MAX_CASES = 20_000


class JUnitError(ValueError):
    """The JUnit report is missing, malformed or unsafe to parse."""


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


def parse_junit(xml_text: str) -> tuple[Counts, tuple[CaseResult, ...]]:
    """Parse a JUnit report into counts and per-test results."""
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
    return counts, tuple(cases)
