import pytest

from exprcalc import ExpressionError
from exprcalc.tokens import tokenize


def test_tokens_carry_kind_text_and_position():
    tokens = tokenize("12 + (3.5)")

    assert [(token.kind, token.text, token.position) for token in tokens] == [
        ("number", "12", 0),
        ("operator", "+", 3),
        ("operator", "(", 5),
        ("number", "3.5", 6),
        ("operator", ")", 9),
        ("end", "", 10),
    ]


def test_empty_text_gives_only_the_end_marker():
    assert [token.kind for token in tokenize("   ")] == ["end"]


def test_unknown_characters_are_rejected():
    with pytest.raises(ExpressionError, match="position 2"):
        tokenize("1 $ 2")
