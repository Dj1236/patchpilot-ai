# exprcalc

Evaluate arithmetic expressions given as text.

```python
from exprcalc import evaluate

evaluate("2 + 3 * 4")      # 14.0
evaluate("(2 + 3) * 4")    # 20.0
evaluate("-(1.5 + 0.5)")   # -2.0
```

Supported: numbers, `+`, `-`, `*`, `/`, unary minus and parentheses, with the
usual precedence. Invalid input raises `ExpressionError`.

Run the tests with `python -m pytest tests`.
