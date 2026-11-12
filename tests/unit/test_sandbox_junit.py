import pytest

from patchpilot.sandbox import junit
from patchpilot.sandbox.junit import JUnitError, node_id_for, parse_junit
from patchpilot.sandbox.models import CaseOutcome

REPORT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="1" failures="1" skipped="1" tests="4" time="0.05">
    <testcase classname="tests.test_cache" name="test_ok" time="0.001" />
    <testcase classname="tests.test_cache" name="test_bad" time="0.002">
      <failure message="assert 1 == 2">trace</failure>
    </testcase>
    <testcase classname="tests.test_cache" name="test_broken" time="0.000">
      <error message="fixture failed">trace</error>
    </testcase>
    <testcase classname="tests.test_cache" name="test_later" time="0.000">
      <skipped message="not yet" />
    </testcase>
  </testsuite>
</testsuites>
"""


def test_counts_and_outcomes_come_from_the_test_cases() -> None:
    counts, cases = parse_junit(REPORT)

    assert (counts.total, counts.passed, counts.failed, counts.errors, counts.skipped) == (
        4,
        1,
        1,
        1,
        1,
    )
    assert {case.node_id: case.outcome for case in cases} == {
        "tests/test_cache.py::test_ok": CaseOutcome.PASSED,
        "tests/test_cache.py::test_bad": CaseOutcome.FAILED,
        "tests/test_cache.py::test_broken": CaseOutcome.ERROR,
        "tests/test_cache.py::test_later": CaseOutcome.SKIPPED,
    }
    assert cases[1].message == "assert 1 == 2"
    assert cases[1].duration_seconds == pytest.approx(0.002)


def test_suite_level_totals_are_not_trusted() -> None:
    forged = '<testsuite tests="99" failures="0"><testcase classname="t" name="a"/></testsuite>'

    counts, _ = parse_junit(forged)

    assert counts.total == 1


def test_node_id_is_rebuilt_from_the_module_name() -> None:
    assert node_id_for("tests.unit.test_x", "test_y[1]") == "tests/unit/test_x.py::test_y[1]"
    assert node_id_for("", "test_y") == "test_y"


@pytest.mark.parametrize(
    "document",
    [
        '<!DOCTYPE x [<!ENTITY a "aaaa">]><testsuite>&a;</testsuite>',
        '<!doctype x SYSTEM "file:///etc/passwd"><testsuite/>',
        '<?xml version="1.0"?><!ENTITY a "b"><testsuite/>',
    ],
)
def test_document_type_declarations_are_refused(document: str) -> None:
    with pytest.raises(JUnitError, match="document type"):
        parse_junit(document)


def test_malformed_xml_is_reported_as_a_junit_error() -> None:
    with pytest.raises(JUnitError, match="well-formed"):
        parse_junit("<testsuite><testcase></testsuite>")


def test_an_oversized_report_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(junit, "MAX_JUNIT_BYTES", 32)

    with pytest.raises(JUnitError, match="size limit"):
        parse_junit(REPORT)


def test_long_failure_messages_are_bounded() -> None:
    document = (
        '<testsuite><testcase classname="t" name="a">'
        f'<failure message="{"m" * 5000}"/></testcase></testsuite>'
    )

    _, cases = parse_junit(document)

    assert len(cases[0].message) == 500


def test_a_bad_duration_does_not_break_parsing() -> None:
    _, cases = parse_junit('<testsuite><testcase classname="t" name="a" time="soon"/></testsuite>')

    assert cases[0].duration_seconds == 0.0
