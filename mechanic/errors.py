"""Error handling — the analyst Rubio-González and Liblit describe, at the mechanic's
scale ("Finding Error-Handling Bugs in Systems Code Using Static Analysis", 2011).

Their finding, over 52 Linux file systems: error-handling code is the least read,
least tested and buggiest part of a system, and the bugs have three shapes —

  1. a DROPPED error: the value that carried it is overwritten, goes out of scope,
     or is returned and never saved by the caller. Unsaved results were 86% of the
     312 confirmed bugs. Their strongest signal was INCONSISTENCY: "one function whose
     returned error code is unsaved at 35 call sites, but saved at 17 others" — the
     saving callers are the specification, and the others are the bugs;
  2. a value that MAY HOLD AN ERROR used as if it were valid — "in many cases there
     is a check for NULL, however the error check is missing";
  3. DOCUMENTATION that does not match the code: errors returned that the manual page
     never mentions.

And one rule about the report: every bug comes with the PATH the error took, filtered
to the program points that matter, so a reader can follow it in a minute.

Their tool is an interprocedural dataflow analysis over C. This module keeps the
questions and answers them with what the mechanic has — Python's AST and the index
for Python, declarations by shape for JavaScript and TypeScript — and states every
claim as the fact it rests on: this function returns a value at line N; this call
discards it; these k other callers keep it. Resolution is by name, as the index
resolves, so a name defined more than once in the tree is not judged (Charter §4:
the expensive error is a wrong "bug"). Each finding carries its path as evidence.
"""

import ast
import re

from . import polyglot

HANDLE = "Handle or propagate the error result"
CHECK_NONE = "Check for None before use"
DOC = "Fix the docstring or the code"
PER_FUNCTION_CAP = 3


def _finding(unit, kind, line, end, title, why, fix, severity, assessment, trail, symbol=""):
    return {
        "unit": unit["module"], "file": unit["file"], "symbol": symbol,
        "line_range": f"{line}-{end}", "category": "error-handling", "severity": severity,
        "confidence": 0.85, "basis": "machine-verified", "title": title,
        "description": why, "recommendation": fix, "claim_kind": "graph",
        "claim": f"{kind} at {unit['file']}:{line}", "proposed_by": "error-handling",
        "assessment": assessment,
        # the path, in the paper's sense: every program point the claim rests on
        "evidence": [{"file": f, "line_range": lr, "reason": r} for f, lr, r in trail],
    }


# ── Python: what each function returns, by the AST ───────────────────────────

def _terminates(stmts: list) -> bool:
    """Does control leave this block only through return/raise? Conservative: a
    loop or a call that never returns reads as falling through."""
    if not stmts:
        return False
    last = stmts[-1]
    if isinstance(last, (ast.Return, ast.Raise)):
        return True
    if isinstance(last, ast.If):
        return _terminates(last.body) and _terminates(last.orelse)
    if isinstance(last, ast.Try):
        return (_terminates(last.finalbody) or
                (_terminates(last.body) and all(_terminates(h.body) for h in last.handlers)))
    if isinstance(last, (ast.With, ast.AsyncWith)):
        return _terminates(last.body)
    return False


def _walk_own(node):
    """Descend into a function's body without entering nested functions/classes."""
    stack = list(ast.iter_child_nodes(node))
    while stack:
        n = stack.pop()
        yield n
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            stack.extend(ast.iter_child_nodes(n))


def _is_status(expr) -> bool:
    if isinstance(expr, ast.Constant) and isinstance(expr.value, bool):
        return True
    if isinstance(expr, (ast.Compare, ast.BoolOp)) or (isinstance(expr, ast.UnaryOp)
                                                      and isinstance(expr.op, ast.Not)):
        return True
    if isinstance(expr, ast.Tuple) and expr.elts and _is_status(expr.elts[0]):
        return True
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.USub) \
            and isinstance(expr.operand, ast.Constant):
        return True                                    # the return-code idiom: -EIO, -1
    return isinstance(expr, ast.Call) and getattr(expr.func, "id", "") == "bool"


def _returns(fn) -> dict:
    """{value, none, status, value_line, none_line} for one function."""
    out = {"value": False, "none": False, "status": False, "value_line": 0, "none_line": 0}
    for n in _walk_own(fn):
        if isinstance(n, ast.Return):
            if n.value is None or (isinstance(n.value, ast.Constant) and n.value.value is None):
                out["none"], out["none_line"] = True, out["none_line"] or n.lineno
            else:
                out["value"], out["value_line"] = True, out["value_line"] or n.lineno
                out["status"] = out["status"] or _is_status(n.value)
    if out["value"] and not _terminates(fn.body):
        out["none"], out["none_line"] = True, out["none_line"] or getattr(fn.body[-1], "end_lineno", fn.lineno)
    if isinstance(fn, ast.AsyncFunctionDef) and any(isinstance(n, ast.Yield) for n in _walk_own(fn)):
        return {**out, "value": False}                 # a generator's returns are not results
    if any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in _walk_own(fn)):
        return {**out, "value": False}
    return out


def _callee(call: ast.Call, modules: set) -> str:
    """The name a call resolves to — or '' when the receiver is something the tree
    does not define (`ast.parse`, `thread.start`), where a same-named function of
    the tree would be a wrong match. `self.x()` and `module.x()` are kept."""
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
            and (f.value.id in ("self", "cls") or f.value.id in modules):
        return f.attr
    return ""


def _docstring_claims(fn) -> tuple[bool, set]:
    """(claims never to raise, the exceptions it documents). "Never raises, except
    ProviderDown" is not a never-raises claim: it is a documented raise."""
    doc = ast.get_docstring(fn) or ""
    names = {a or b for a, b in re.findall(r":raises?\s+(\w+):|^\s*Raises?:\s*\n?\s*(\w+)", doc, re.M)}
    never = False
    for m in re.finditer(r"\b(?:never raises|does not raise|doesn't raise)\b([^.]*)", doc, re.I):
        qualifier = m.group(1)
        if re.search(r"\b(?:except|other than|unless|but|save for|apart from)\b", qualifier, re.I):
            names |= set(re.findall(r"\b([A-Z]\w+)\b", qualifier))
        else:
            never = True
    return never, names


def _raises_escaping(fn) -> list[tuple[int, str]]:
    """(line, exception name) for each raise that can leave the function: not inside a
    try body (assumed caught — conservative), including a re-raise in a handler."""
    out = []

    def visit(node, in_try_body):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            if isinstance(child, ast.Raise):
                if not in_try_body:
                    exc = child.exc
                    name = (getattr(exc, "id", "") or getattr(getattr(exc, "func", None), "id", "")
                            or getattr(getattr(exc, "func", None), "attr", "") or getattr(exc, "attr", "")
                            or ("re-raise" if exc is None else "?"))
                    out.append((child.lineno, name))
                continue
            if isinstance(child, ast.Try):
                for s in child.body:
                    visit(s, True)
                for h in child.handlers:
                    visit(h, False)
                for s in child.orelse + child.finalbody:
                    visit(s, in_try_body)
                continue
            visit(child, in_try_body)
    visit(fn, False)
    return out


def _python_facts(units, texts) -> tuple[dict, list, list]:
    """defs: name → [(unit, fn node, returns)], calls: [(unit, name, line, discarded)],
    trees: [(unit, tree)]."""
    defs, calls, trees = {}, [], []
    modules = {u["module"].split(".")[-1] for u in units} | {u["module"] for u in units}
    for u in units:
        if u.get("lang", "python") != "python":
            continue
        try:
            tree = ast.parse("\n".join(texts.get(u["module"], [])))
        except SyntaxError:
            continue
        trees.append((u, tree))
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs.setdefault(n.name, []).append((u, n, _returns(n)))
        if polyglot.is_harness(u):
            continue                                   # a harness calls for effect; not judged
        dropped = set()                                # the walk visits a statement's call twice
        for n in ast.walk(tree):
            if isinstance(n, ast.Expr):
                v = n.value.value if isinstance(n.value, ast.Await) else n.value
                if isinstance(v, ast.Call) and _callee(v, modules):
                    calls.append((u, _callee(v, modules), n.lineno, True))
                    dropped.add(id(v))
            elif isinstance(n, ast.Call) and id(n) not in dropped and _callee(n, modules):
                calls.append((u, _callee(n, modules), n.lineno, False))
    return defs, calls, trees


def _discarded_calls(discards: dict, uses: dict, defs: dict) -> list[dict]:
    """A call whose result is discarded while other callers save it — the paper's
    inconsistency rule. Every discard of a name nobody uses is a chaining or
    side-effect convention and is not judged."""
    out = []
    for name, sites in discards.items():
        if name not in defs or len(defs[name]) != 1 or not uses.get(name):
            continue
        u_def, fn, ret = defs[name][0]
        if not ret["value"] or not (ret["status"] or ret["none"]):
            continue                                   # a plain value (a dict of counts) is not an error result
        saved = uses[name]
        for u, line in sites[:PER_FUNCTION_CAP]:
            ex = ", ".join(f"{s[0]['file']}:{s[1]}" for s in saved[:3])
            sev = "medium" if ret["status"] or ret["none"] else "low"
            out.append(_finding(
                u, "result discarded", line, line,
                f"`{name}`'s result is discarded at {u['file']}:{line} — saved at "
                f"{len(saved)} other call site{'s' if len(saved) > 1 else ''}",
                f"`{name}` returns a value ({u_def['file']}:{ret['value_line']}"
                + (", a status" if ret["status"] else "")
                + (f", and None at line {ret['none_line']}" if ret["none"] else "")
                + f"). This call drops it; {len(saved)} other caller{'s' if len(saved) > 1 else ''} "
                f"({ex}) keep it. Where the callers disagree, the ones that save the result "
                f"are the specification, and a dropped result is an error nobody will see.",
                "Save the result and act on it — propagate it, log it, or state on the line "
                "why this caller may ignore it.", sev, HANDLE,
                [(u_def["file"], f"{fn.lineno}-{fn.lineno}", f"graph fact: `{name}` returns a value at line {ret['value_line']}"),
                 (u["file"], f"{line}-{line}", "graph fact: the call is a statement — its result is not saved"),
                 (saved[0][0]["file"], f"{saved[0][1]}-{saved[0][1]}", f"graph fact: one of {len(saved)} call sites that save it")],
                symbol=f"{u['module']}:{line}"))
    return out


def _none_before_check(trees, defs) -> list[dict]:
    """x = f(...) where f may return None, then x.attr / x[...] before any check —
    the paper's bad dereference: a check for NULL was written elsewhere, not here."""
    out, count = [], {}

    def checks(stmt, x) -> bool:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Name) and n.id == x:
                p = getattr(n, "_parent", None)
                if isinstance(p, (ast.Compare, ast.BoolOp, ast.If, ast.While, ast.Assert, ast.IfExp)) \
                        or (isinstance(p, ast.UnaryOp) and isinstance(p.op, ast.Not)) \
                        or (isinstance(p, ast.Call) and getattr(p.func, "id", "") in ("isinstance", "bool")) \
                        or isinstance(p, ast.Return):
                    return True
        return False

    def deref(stmt, x):
        for n in ast.walk(stmt):
            if isinstance(n, (ast.Attribute, ast.Subscript)) and isinstance(n.value, ast.Name) and n.value.id == x:
                return n.lineno
            if isinstance(n, ast.For) and isinstance(n.iter, ast.Name) and n.iter.id == x:
                return n.lineno
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "len" and n.args \
                    and isinstance(n.args[0], ast.Name) and n.args[0].id == x:
                return n.lineno
        return 0

    for u, tree in trees:
        if polyglot.is_harness(u):
            continue
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                child._parent = parent
        for node in ast.walk(tree):
            for body in (getattr(node, "body", None), getattr(node, "orelse", None)):
                if not isinstance(body, list):
                    continue
                for k, stmt in enumerate(body):
                    if not (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                            and isinstance(stmt.targets[0], ast.Name)):
                        continue
                    v = stmt.value.value if isinstance(stmt.value, ast.Await) else stmt.value
                    if not isinstance(v, ast.Call):
                        continue
                    name = _callee(v, {u2["module"].split(".")[-1] for u2, _ in trees} | {u2["module"] for u2, _ in trees})
                    if name not in defs or len(defs[name]) != 1:
                        continue
                    u_def, fn, ret = defs[name][0]
                    if not (ret["none"] and ret["value"]) or count.get(name, 0) >= PER_FUNCTION_CAP:
                        continue
                    x = stmt.targets[0].id
                    for nxt in body[k + 1:]:
                        if checks(nxt, x):
                            break
                        if isinstance(nxt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == x for t in nxt.targets):
                            break
                        at = deref(nxt, x)
                        if at:
                            count[name] = count.get(name, 0) + 1
                            out.append(_finding(
                                u, "None used before a check", stmt.lineno, at,
                                f"`{x}` may be None at {u['file']}:{at} — `{name}` returns None and "
                                f"nothing checks it first",
                                f"`{name}` returns None at {u_def['file']}:{ret['none_line']} and a value at "
                                f"line {ret['value_line']}. `{x}` takes that result at line {stmt.lineno} and "
                                f"is used as a value at line {at} with no check between. On the None path "
                                f"this line raises where the failure should have been handled.",
                                f"Check `{x}` before line {at} — `if {x} is None:` — and handle that path, "
                                f"or make `{name}` raise instead of returning None.", "medium", CHECK_NONE,
                                [(u_def["file"], f"{ret['none_line']}-{ret['none_line']}", f"graph fact: `{name}` returns None here"),
                                 (u["file"], f"{stmt.lineno}-{stmt.lineno}", f"graph fact: `{x}` receives `{name}`'s result"),
                                 (u["file"], f"{at}-{at}", f"graph fact: `{x}` is used as a value — no check on the path")],
                                symbol=f"{u['module']}:{at}"))
                            break
                        if any(isinstance(n, (ast.Return, ast.Raise, ast.Break, ast.Continue)) for n in [nxt]):
                            break
    return out


def _doc_mismatch(trees) -> list[dict]:
    out = []
    for u, tree in trees:
        if polyglot.is_harness(u):
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            never, documented = _docstring_claims(fn)
            if not never and not documented:
                continue
            escaping = _raises_escaping(fn)
            if never and escaping:
                line, exc = escaping[0]
                out.append(_finding(
                    u, "docstring contradicts a raise", fn.lineno, line,
                    f"`{fn.name}` says it never raises, but line {line} raises {exc}",
                    f"The docstring promises callers they need no handler. Line {line} "
                    f"{'re-raises' if exc == 're-raise' else f'raises `{exc}`'} outside any try, so "
                    f"the promise is false and a caller that trusted it has no handler.",
                    "Say which exceptions leave the function, or catch them here as the "
                    "docstring promises.", "medium", DOC,
                    [(u["file"], f"{fn.lineno}-{fn.lineno}", "text fact: the docstring says it never raises"),
                     (u["file"], f"{line}-{line}", f"graph fact: a raise of {exc} outside any try")],
                    symbol=f"{u['module']}.{fn.name}"))
            elif documented:
                undocumented = [(l, e) for l, e in escaping if e not in documented and e != "re-raise"]
                if undocumented:
                    line, exc = undocumented[0]
                    out.append(_finding(
                        u, "undocumented raise", fn.lineno, line,
                        f"`{fn.name}` raises {exc} at line {line}, which its docstring does not list",
                        f"The docstring documents {', '.join(sorted(documented))}; line {line} raises "
                        f"`{exc}`. A caller handling only the documented set misses it.",
                        "Add it to the docstring's raises, or catch it.", "low", DOC,
                        [(u["file"], f"{fn.lineno}-{fn.lineno}", f"text fact: docstring lists {', '.join(sorted(documented))}"),
                         (u["file"], f"{line}-{line}", f"graph fact: raises {exc}")],
                        symbol=f"{u['module']}.{fn.name}"))
    return out


# ── JavaScript / TypeScript: the same inconsistency rule, by shape ───────────

_RETURN_VALUE = re.compile(r"^\s*return\s+[^;\s]")


def _js_findings(units, texts) -> list[dict]:
    decls = {}
    for u in units:
        if u.get("lang") not in polyglot.JS_LANGS:
            continue
        lines = texts.get(u["module"], [])
        for d in polyglot.declarations(lines, u["lang"]):
            body = lines[d["line"]:d["end"]]
            is_async = "async" in lines[d["line"] - 1]
            decls.setdefault(d["name"], []).append((u, d, any(_RETURN_VALUE.match(l) for l in body), is_async))
    out = []
    for name, ds in decls.items():
        if len(ds) != 1 or not ds[0][2] or len(name) < 3:
            continue
        u_def, d, _, is_async = ds[0]
        pat = re.compile(rf"(?<![\w$.]){re.escape(name)}\s*\(")
        stmt = re.compile(rf"^\s*(?:await\s+)?{re.escape(name)}\s*\(")
        discarded, saved = [], []
        for u in units:
            if u.get("lang") not in polyglot.JS_LANGS or polyglot.is_harness(u):
                continue
            for i, ln in enumerate(texts.get(u["module"], []), 1):
                if u is u_def and i == d["line"]:
                    continue
                if not pat.search(ln) or re.match(r"^\s*(?:void\s+|//)", ln):
                    continue
                (discarded if stmt.match(ln) else saved).append((u, i))
        if not discarded or not saved:
            continue
        for u, line in discarded[:PER_FUNCTION_CAP]:
            ex = ", ".join(f"{s[0]['file']}:{s[1]}" for s in saved[:3])
            out.append(_finding(
                u, "result discarded", line, line,
                f"`{name}`'s result is discarded at {u['file']}:{line} — saved at {len(saved)} "
                f"other call site{'s' if len(saved) > 1 else ''}",
                f"`{name}` ({u_def['file']}:{d['line']}) returns a value. This statement drops it"
                + ("; the function is async, so a rejection is dropped with it" if is_async else "")
                + f". {len(saved)} other caller{'s' if len(saved) > 1 else ''} ({ex}) keep it — where "
                f"callers disagree, the ones that save the result are the specification.",
                "Save the result and act on it, or write `void name(...)` to state that "
                "dropping it is the intent.", "medium" if is_async else "low", HANDLE,
                [(u_def["file"], f"{d['line']}-{d['line']}", f"text fact: `{name}` has a `return <value>` in its body"),
                 (u["file"], f"{line}-{line}", "text fact: the call is a statement on its own — its result is not saved"),
                 (saved[0][0]["file"], f"{saved[0][1]}-{saved[0][1]}", f"text fact: one of {len(saved)} call sites that save it")],
                symbol=f"{u['module']}:{line}"))
    return out


def analyse(units: list[dict], texts: dict) -> dict:
    """Returns {findings}. Never raises on a strange tree."""
    defs, calls, trees = _python_facts(units, texts)
    discards, uses = {}, {}
    for u, name, line, discarded in calls:
        (discards if discarded else uses).setdefault(name, []).append((u, line))
    findings = _discarded_calls(discards, uses, defs)
    findings += _none_before_check(trees, defs)
    findings += _doc_mismatch(trees)
    findings += _js_findings(units, texts)
    return {"findings": findings}
