"""Hidden regression tests. Never copied into an agent workspace."""

import pytest

from exprcalc import ExpressionError, evaluate


def test_chained_subtraction_runs_left_to_right():
    assert evaluate("10 - 4 - 3") == 3.0
    assert evaluate("100 - 10 - 20 - 30") == 40.0


def test_subtraction_then_addition_runs_left_to_right():
    assert evaluate("10 - 4 + 3") == 9.0


def test_chained_division_runs_left_to_right():
    assert evaluate("100 / 10 / 5") == 2.0


def test_division_then_multiplication_runs_left_to_right():
    assert evaluate("8 / 4 * 2") == 4.0


def test_mixed_expression():
    assert evaluate("20 - 2 * 3 - 4 / 2 / 2") == 13.0


def test_parenthesized_chain():
    assert evaluate("(9 - 3 - 2) * (12 / 3 / 2)") == 8.0


def test_right_nested_forms_are_unchanged():
    assert evaluate("10 - (4 - 3)") == 9.0
    assert evaluate("100 / (10 / 5)") == 50.0


def test_division_by_zero_inside_a_chain_is_still_an_error():
    with pytest.raises(ExpressionError, match="division by zero"):
        evaluate("8 / 2 / 0")
    with pytest.raises(ExpressionError, match="division by zero"):
        evaluate("8 / (2 - 2) / 4")
