"""Evaluate arithmetic expressions given as text."""

from exprcalc.errors import ExpressionError
from exprcalc.parser import evaluate

__all__ = ["ExpressionError", "evaluate"]
