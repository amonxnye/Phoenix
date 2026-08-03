"""The toy codebase the first real-work agents operate on.

Deliberately shipped with defects: the failing tests in sandbox/tests are the open
TASKS. An agent's contribution is measured by which of them it turns green — the
test suite is the oracle, exactly as REALWORK.md specifies.
"""


def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    # BUG: adds instead of multiplying
    return a + b


def divide(a, b):
    # BUG: no zero guard — should raise ValueError on b == 0
    return a / b


def power(a, b):
    # NOT IMPLEMENTED
    raise NotImplementedError
