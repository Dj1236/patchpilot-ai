"""Split expression text into tokens."""

from dataclasses import dataclass

from exprcalc.errors import ExpressionError

OPERATORS = frozenset("+-*/()")


@dataclass(frozen=True)
class Token:
    kind: str  # "number", "operator" or "end"
    text: str
    position: int


def tokenize(text: str) -> list[Token]:
    """Return the tokens of ``text``, followed by an end marker."""
    tokens: list[Token] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
        elif char in OPERATORS:
            tokens.append(Token("operator", char, index))
            index += 1
        elif char.isdigit() or char == ".":
            start = index
            seen_dot = False
            while index < len(text) and (text[index].isdigit() or text[index] == "."):
                if text[index] == ".":
                    if seen_dot:
                        raise ExpressionError(f"unexpected '.' at position {index}")
                    seen_dot = True
                index += 1
            number = text[start:index]
            if number == ".":
                raise ExpressionError(f"unexpected '.' at position {start}")
            tokens.append(Token("number", number, start))
        else:
            raise ExpressionError(f"unexpected character {char!r} at position {index}")
    tokens.append(Token("end", "", len(text)))
    return tokens
