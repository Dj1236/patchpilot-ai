import pytest

from exprcalc import ExpressionError, evaluate


@pytest.mark.parametrize(
    "text,expected",
    [
        ("7", 7.0),
        ("1.5", 1.5),
        (".5", 0.5),
        ("  42  ", 42.0),
    ],
    ids=["integer", "decimal", "leading-dot", "padded"],
)
def test_numbers(text, expected):
    assert evaluate(text) == expected


def test_addition():
    assert evaluate("1 + 2 + 3") == 6.0


def test_subtraction():
    assert evaluate("10 - 4") == 6.0


def test_multiplication():
    assert evaluate("2 * 3 * 4") == 24.0


def test_division():
    assert evaluate("9 / 3") == 3.0


def test_multiplication_binds_tighter_than_addition():
    assert evaluate("2 + 3 * 4") == 14.0
    assert evaluate("2 * 3 + 4") == 10.0


def test_multiplication_binds_tighter_than_subtraction():
    assert evaluate("20 - 3 * 4") == 8.0


def test_parentheses_override_precedence():
    assert evaluate("(2 + 3) * 4") == 20.0
    assert evaluate("10 - (4 - 3)") == 9.0


def test_unary_minus():
    assert evaluate("-5 + 2") == -3.0
    assert evaluate("-(1.5 + 0.5)") == -2.0
    assert evaluate("2 * -3") == -6.0


def test_division_by_zero_is_an_error():
    with pytest.raises(ExpressionError, match="division by zero"):
        evaluate("1 / 0")


MALFORMED = {
    "empty": "",
    "dangling-operator": "2 +",
    "unclosed": "(1 + 2",
    "unopened": "1 + 2)",
    "adjacent-numbers": "2 3",
    "double-operator": "1 + * 2",
    "letter": "a + 1",
    "double-dot": "1..2",
}


@pytest.mark.parametrize("text", MALFORMED.values(), ids=MALFORMED.keys())
def test_malformed_input_is_an_error(text):
    with pytest.raises(ExpressionError):
        evaluate(text)
