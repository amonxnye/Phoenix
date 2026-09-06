"""Measured, not judged — complexity, and where to look first.

The classic metrics are facts about a text: McCabe's cyclomatic complexity is the
number of decision points plus one; Halstead's volume is N·log2(n) over the tokens;
the maintainability index is a fixed formula over both and the line count. They are
cheap, checkable by anyone with the file open, and well correlated with where bugs
are found. What they are NOT is a finding: "this function is too complex" is an
opinion (Charter §2), and the mechanic reports opinions only as judgements.

So the numbers do two things here and nothing else. They ORDER the work — after
centrality, the most complex and most churned unit is analysed first, so a budget
that halts partway has spent itself where the bugs are likeliest — and they are shown
to the reader and to the analysts as a place to look first. The risk score is the
transparent product of measured factors, weights stated in the code. It is not a
fitted model: there is no bug history here to fit one to, and a coefficient that was
invented would read as evidence.
"""

import ast
import io
import keyword
import math
import re
import tokenize

from . import polyglot

_DECISION_TEXT = re.compile(r"\b(?:if|else if|elif|for|while|case|catch|when|guard)\b|&&|\|\||\?\s*[^.:]")


def _cc(fn) -> int:
    n = 1
    for node in ast.walk(fn):
        if isinstance(node, (ast.If, ast.For, ast.While, ast.AsyncFor, ast.ExceptHandler,
                             ast.IfExp, ast.Assert)):
            n += 1
        elif isinstance(node, ast.BoolOp):
            n += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            n += 1 + len(node.ifs)
        elif isinstance(node, ast.match_case):
            n += 1
    return n


def python_functions(lines: list[str]) -> list[dict]:
    """[{name, line, end, cc}] for every function and method, or [] if it will not parse."""
    try:
        tree = ast.parse("\n".join(lines))
    except SyntaxError:
        return []
    return [{"name": n.name, "line": n.lineno, "end": getattr(n, "end_lineno", n.lineno),
             "cc": _cc(n)}
            for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _halstead_python(lines: list[str]) -> float:
    ops, opnds = [], []
    try:
        for t in tokenize.generate_tokens(io.StringIO("\n".join(lines) + "\n").readline):
            if t.type == tokenize.OP or (t.type == tokenize.NAME and keyword.iskeyword(t.string)):
                ops.append(t.string)
            elif t.type in (tokenize.NAME, tokenize.NUMBER, tokenize.STRING):
                opnds.append(t.string)
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return 0.0
    return _volume(ops, opnds)


def _halstead_text(lines: list[str]) -> float:
    ops, opnds = [], []
    for ln in lines:
        for tok in re.findall(r"[A-Za-z_$][\w$]*|\d+(?:\.\d+)?|\"[^\"]*\"|'[^']*'|[^\w\s]+", ln):
            (opnds if tok[0].isalnum() or tok[0] in "_$\"'" else ops).append(tok)
    return _volume(ops, opnds)


def _volume(ops, opnds) -> float:
    n = len(set(ops)) + len(set(opnds))
    total = len(ops) + len(opnds)
    return total * math.log2(n) if n > 1 else 0.0


def _text_functions(lines: list[str], lang: str) -> list[dict]:
    out = []
    for d in polyglot.declarations(lines, lang):
        body = "\n".join(lines[d["line"] - 1:d["end"]])
        out.append({"name": d["name"], "line": d["line"], "end": d["end"],
                    "cc": 1 + len(_DECISION_TEXT.findall(body))})
    return out


def unit_metrics(unit: dict, lines: list[str]) -> dict:
    """{complexity (max CC), hotspot (the function that carries it), cc_total,
    functions, maintainability (0-100), loc}. Python by the AST; other languages
    by shape, which counts decisions in the text and is labelled as such."""
    lang = unit.get("lang", "python")
    fns = python_functions(lines) if lang == "python" else _text_functions(lines, lang)
    volume = _halstead_python(lines) if lang == "python" else _halstead_text(lines)
    loc = max(1, sum(1 for l in lines if l.strip()))
    cc_total = sum(f["cc"] for f in fns) or 1
    top = max(fns, key=lambda f: f["cc"]) if fns else None
    mi = 171 - 5.2 * math.log(max(volume, 1.0)) - 0.23 * cc_total - 16.2 * math.log(loc)
    return {"complexity": top["cc"] if top else 0,
            "hotspot": f"{top['name']}@{top['line']}" if top else "",
            "cc_total": cc_total, "functions": len(fns),
            "maintainability": round(max(0.0, min(100.0, mi * 100 / 171)), 1),
            "loc": loc}


def risk(m: dict, hist: dict | None) -> float:
    """[0, 1]: half from the worst function's complexity (50 saturates), three tenths
    from the inverse maintainability index, two tenths from churn (30 commits
    saturate; 0 without a history). Weights are stated, not fitted."""
    churn = int((hist or {}).get("commits", 0))
    return round(0.5 * min(1.0, m.get("complexity", 0) / 50)
                 + 0.3 * (1 - m.get("maintainability", 100) / 100)
                 + 0.2 * min(1.0, churn / 30), 3)


def look_first(units: list[dict], n: int = 5) -> list[dict]:
    """The units to read first: by risk, then centrality. Non-test only."""
    ranked = sorted((u for u in units if not u.get("is_test")),
                    key=lambda u: (-u.get("risk", 0), -u.get("centrality", 0), u["module"]))
    return [{"module": u["module"], "file": u["file"], "risk": u.get("risk", 0),
             "complexity": u.get("complexity", 0), "hotspot": u.get("hotspot", ""),
             "maintainability": u.get("maintainability", 0), "loc": u.get("loc", 0),
             "centrality": u.get("centrality", 0)} for u in ranked[:n]]
