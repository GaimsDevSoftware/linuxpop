"""Evaluate a math expression in the selection and show the result."""
from __future__ import annotations

import ast
import math
import operator
import re
import subprocess

from classifier import ContentType
from plugin_base import Plugin

# An expression must contain at least one digit AND at least one operator
# (or one of the allowed function/constant names) — anything else is just
# prose and would never evaluate.
_HAS_DIGIT = re.compile(r"\d")
_HAS_OP_OR_CALL = re.compile(
    r"[+\-*/^%()]"
    r"|\b(?:pi|e|tau|sqrt|log|log10|log2|exp|sin|cos|tan|"
    r"asin|acos|atan|floor|ceil|round|abs)\b"
)
# Only letters that are part of allowed function/constant names are OK.
_ALLOWED_WORD_RE = re.compile(
    r"^(?:pi|e|tau|sqrt|log|log10|log2|exp|sin|cos|tan|"
    r"asin|acos|atan|floor|ceil|round|abs)$"
)


def _looks_like_math(text: str) -> bool:
    s = text.strip()
    if not s or len(s) > 200:
        return False
    if not _HAS_DIGIT.search(s):
        return False
    if not _HAS_OP_OR_CALL.search(s):
        return False
    # Any word longer than 1 char that isn't an allowed function/constant
    # disqualifies the selection (e.g. "I have 3 cats" -> 'have', 'cats').
    for word in re.findall(r"[A-Za-z][A-Za-z_]*", s):
        if not _ALLOWED_WORD_RE.match(word):
            return False
    return True

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UN_OPS = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_NAMES = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
}

_FUNCS = {
    "sqrt": math.sqrt, "log": math.log, "log10": math.log10, "log2": math.log2,
    "exp": math.exp, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "floor": math.floor, "ceil": math.ceil, "round": round, "abs": abs,
}


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Num):  # py<3.12
        return node.n
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left = _eval(node.left)
        right = _eval(node.right)
        # ** with large operands can produce astronomically large ints
        # that take minutes of pure-CPU time to construct + format.
        # Selection like "9**9**9" used to freeze the worker thread. Cap
        # both operand magnitude and exponent at sane values.
        if isinstance(node.op, ast.Pow):
            if abs(right) > 1000 or abs(left) > 1e6:
                raise ValueError("exponent too large — refusing to evaluate")
        return _BIN_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UN_OPS:
        return _UN_OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Name) and node.id in _NAMES:
        return _NAMES[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
        return _FUNCS[node.func.id](*(_eval(a) for a in node.args))
    raise ValueError(f"not allowed: {ast.dump(node)}")


def _calc(text: str) -> None:
    expr = text.strip()
    try:
        tree = ast.parse(expr, mode="eval")
        result = _eval(tree.body)
    except Exception as exc:  # noqa: BLE001
        subprocess.run(
            ["notify-send", "--hint=byte:transient:1", "-t", "3000",  "-i", "dialog-error", "Calculator error", str(exc)[:200]],
            check=False,
        )
        return
    result_str = str(result)
    subprocess.run(
        ["xclip", "-selection", "clipboard"],
        input=result_str.encode("utf-8"),
        check=False,
        timeout=2.0,
    )
    subprocess.run(
        ["notify-send", "--hint=byte:transient:1", "-t", "3000",  "-i", "accessories-calculator", "Result", f"{expr} = {result_str}"],
        check=False,
    )


def register(register_plugin) -> None:
    register_plugin(Plugin(
        name="calculator",
        icon="linuxpop-calculator-symbolic",
        tooltip="Calculate",
        handler=_calc,
        content_types=(ContentType.PLAIN_TEXT,),
        priority=40,
        predicate=_looks_like_math,
    ))
