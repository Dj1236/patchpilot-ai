"""Recursive-descent evaluation of arithmetic expressions.

Grammar::

    expression := term (("+" | "-") term)*
    term       := factor (("*" | "/") factor)*
    factor     := "-" factor | number | "(" expression ")"
"""

from exprcalc.errors import ExpressionError
from exprcalc.tokens import Token, tokenize


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self._tokens = tokens
        self._index = 0

    def parse(self) -> float:
        value = self._expression()
        token = self._peek()
        if token.kind != "end":
            raise ExpressionError(f"unexpected {token.text!r} at position {token.position}")
        return value

    def _peek(self) -> Token:
        return self._tokens[self._index]

    def _advance(self) -> Token:
        token = self._tokens[self._index]
        self._index += 1
        return token

    def _accept(self, operator: str) -> bool:
        token = self._peek()
        if token.kind == "operator" and token.text == operator:
            self._index += 1
            return True
        return False

    def _expression(self) -> float:
        left = self._term()
        if self._accept("+"):
            return left + self._expression()
        if self._accept("-"):
            return left - self._expression()
        return left

    def _term(self) -> float:
        left = self._factor()
        if self._accept("*"):
            return left * self._term()
        if self._accept("/"):
            divisor = self._term()
            if divisor == 0:
                raise ExpressionError("division by zero")
            return left / divisor
        return left

    def _factor(self) -> float:
        if self._accept("-"):
            return -self._factor()
        token = self._advance()
        if token.kind == "number":
            return float(token.text)
        if token.kind == "operator" and token.text == "(":
            value = self._expression()
            if not self._accept(")"):
                raise ExpressionError(f"missing ')' for '(' at position {token.position}")
            return value
        if token.kind == "end":
            raise ExpressionError("unexpected end of expression")
        raise ExpressionError(f"unexpected {token.text!r} at position {token.position}")


def evaluate(text: str) -> float:
    """Evaluate ``text`` and return the result as a float."""
    return _Parser(tokenize(text)).parse()
