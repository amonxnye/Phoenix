"""AI slop — the tells of unreviewed machine-generated code, found as FACTS.

"Slop" is a judgement about origin; the mechanic does not claim to know who wrote a
line. What it can state as fact is the presence of constructs that reviewed code
rarely keeps and generated code often does. Each detector below produces a text or
graph fact with a file and line range, so every finding here is machine-verified and
checkable by anyone with the file open. The judgement calls — restating comments,
redundant guards, wrappers around one call — belong to the panel's slop analyst,
where a challenger can refute them.

One rule keeps the scanner honest about itself: a swallowed exception WITH a stated
reason is a decision, not slop. Only the unexplained swallow is a finding.
"""

import ast
import hashlib
import os
import re
import tokenize

MIN_DUP_LINES = 6
_NAME_SPLIT = re.compile(r"[_\W]+|(?<=[a-z])(?=[A-Z])")


def _lines(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return []


def _finding(unit, kind, line, end, title, why, fix, severity="low", symbol=""):
    return {
        "unit": unit["module"], "file": unit["file"], "symbol": symbol,
        "line_range": f"{line}-{end}", "category": "slop", "severity": severity,
        "confidence": 0.9, "basis": "machine-verified", "title": title,
        "description": why, "recommendation": fix, "claim_kind": "text",
        "claim": f"{kind} at {unit['file']}:{line}", "proposed_by": "slop-scan",
        "evidence": [{"file": unit["file"], "line_range": f"{line}-{end}",
                      "reason": f"text fact: {kind} at lines {line}-{end}"}],
    }


def _has_comment_near(lines: list[str], start: int, end: int) -> bool:
    lo, hi = max(1, start - 1), min(len(lines), end)
    return any("#" in lines[i - 1] for i in range(lo, hi + 1))


def _name_words(name: str) -> set[str]:
    return {w.lower() for w in _NAME_SPLIT.split(name) if w}


class _Scan(ast.NodeVisitor):
    def __init__(self, unit, lines, tree):
        self.unit, self.lines, self.tree = unit, lines, tree
        self.findings: list[dict] = []
        self.bodies: list[tuple[str, str, int, int, str]] = []   # (hash, qual, line, end, name)
        self._stack: list[str] = []
        self._bases: list[bool] = []                  # per enclosing class: has bases?

    def _qual(self, name):
        return ".".join([self.unit["module"], *self._stack, name])

    # ── swallowed exceptions without a reason ────────────────────────────────
    def visit_ExceptHandler(self, node):
        body = node.body
        only_pass = all(isinstance(b, ast.Pass) or
                        (isinstance(b, ast.Expr) and isinstance(getattr(b, "value", None), ast.Constant)
                         and b.value.value is Ellipsis) for b in body)
        broad = node.type is None or (isinstance(node.type, ast.Name)
                                      and node.type.id in ("Exception", "BaseException"))
        if only_pass and broad and not _has_comment_near(self.lines, node.lineno,
                                                          getattr(node, "end_lineno", node.lineno)):
            self.findings.append(_finding(
                self.unit, "unexplained exception swallow", node.lineno,
                getattr(node, "end_lineno", node.lineno),
                "a broad `except` that swallows silently, with no stated reason",
                "Every failure on this path disappears. Reviewed code that swallows an "
                "exception says why on the same line; generated code often does not.",
                "Narrow the exception type, or handle it, or state the reason in a comment "
                "on the handler so the swallow is a decision rather than an accident.",
                severity="medium"))
        self.generic_visit(node)

    # ── stubs, restating docstrings, duplicate bodies ────────────────────────
    def _function(self, node):
        qual = self._qual(node.name)
        end = getattr(node, "end_lineno", node.lineno)
        body = node.body
        doc = ast.get_docstring(node) or ""
        rest = body[1:] if (body and isinstance(body[0], ast.Expr)
                            and isinstance(getattr(body[0], "value", None), ast.Constant)
                            and isinstance(body[0].value.value, str)) else body
        stub = rest and all(isinstance(b, ast.Pass) or
                            (isinstance(b, ast.Expr) and isinstance(getattr(b, "value", None), ast.Constant)
                             and b.value.value is Ellipsis) or
                            (isinstance(b, ast.Raise) and isinstance(getattr(b, "exc", None), ast.Call)
                             and getattr(b.exc.func, "id", "") == "NotImplementedError")
                            for b in rest)
        abstract = any((isinstance(d, ast.Name) and "abstract" in d.id.lower()) or
                       (isinstance(d, ast.Attribute) and "abstract" in d.attr.lower())
                       for d in node.decorator_list)
        overload = any(isinstance(d, ast.Name) and d.id == "overload" for d in node.decorator_list)
        override = bool(self._bases and self._bases[-1])   # a no-op method in a subclass
        if stub and not abstract and not overload and not override \
                and not node.name.startswith("test_"):
            # (a `pass` method in a class WITH bases is how Python silences inherited
            #  behaviour — log_message in an HTTP handler — so it is an override, not a stub)
            self.findings.append(_finding(
                self.unit, "stub body", node.lineno, end,
                f"`{node.name}` has no body — a stub that shipped",
                "The function is declared but does nothing (pass, ..., or NotImplementedError) "
                "and is not marked abstract. Callers get nothing and no error they can act on.",
                "Implement it, mark it abstract if it is an interface, or delete it and its "
                "callers.", symbol=qual))
        if doc:
            words = {w.lower() for w in re.findall(r"[A-Za-z]+", doc)}
            nm = _name_words(node.name) - {"get", "set", "the", "a", "an", "of", "to", "and"}
            if 0 < len(words) <= 8 and nm and nm <= words:
                self.findings.append(_finding(
                    self.unit, "docstring restates the name", node.lineno, node.lineno + 1,
                    f"`{node.name}`'s docstring only restates its name",
                    f"The docstring ({doc[:60]!r}) adds nothing the name does not already say. "
                    "Reviewed code either documents a contract or has no docstring.",
                    "Say what the caller must know — inputs, effects, failure modes — or "
                    "remove the docstring.", symbol=qual))
        if end - node.lineno + 1 >= MIN_DUP_LINES and rest:
            norm = ast.dump(ast.Module(body=list(rest), type_ignores=[]), annotate_fields=False)
            norm = re.sub(r"Name\('[^']*'", "Name('_'", norm)      # names may differ
            h = hashlib.sha256(norm.encode()).hexdigest()[:16]
            self.bodies.append((h, qual, node.lineno, end, node.name))
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    def visit_FunctionDef(self, node):
        self._function(node)

    def visit_AsyncFunctionDef(self, node):
        self._function(node)

    def visit_ClassDef(self, node):
        self._stack.append(node.name)
        self._bases.append(bool(node.bases))
        self.generic_visit(node)
        self._bases.pop()
        self._stack.pop()


def _unused_imports(unit, tree, lines) -> list[dict]:
    imported = {}                                     # local name → (line, what)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                local = (a.asname or a.name).split(".")[0]
                imported[local] = (node.lineno, a.name)
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == "*":
                    continue
                imported[a.asname or a.name] = (node.lineno, f"{node.module}.{a.name}")
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            base = node
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Name):
                used.add(base.id)
    exported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__"
                                                 for t in node.targets):
            exported |= {e.value for e in getattr(node.value, "elts", [])
                         if isinstance(e, ast.Constant)}
    out = []
    src = "\n".join(lines)
    for local, (line, what) in imported.items():
        if local in used or local in exported or local.startswith("_"):
            continue
        if re.search(rf"noqa|re-?export|{re.escape(local)}", lines[line - 1].split("#")[-1]) \
                and "#" in lines[line - 1]:
            continue                                  # a commented import is a decision
        if os.path.basename(unit["file"]) == "__init__.py":
            continue                                  # packages re-export by importing
        if f'"{local}"' in src or f"'{local}'" in src:
            continue                                  # referenced by name as a string
        out.append(_finding(unit, "unused import", line, line,
                            f"`{local}` is imported and never used",
                            f"`{what}` is imported at line {line} and referenced nowhere in the "
                            f"module. Unused imports accrete when code is generated in pieces.",
                            "Remove the import."))
    return out


def _commented_out_code(unit, lines) -> list[dict]:
    """Runs of three or more full-line comments that parse as Python. Read through the
    tokenizer, not the raw text, so a `#` inside a string literal is never a comment —
    the mechanic's own suite keeps such fixtures inside triple-quoted strings."""
    out, run, start = [], [], 0

    def flush():
        if len(run) >= 3:
            block = "\n".join(re.sub(r"^#[ ]?", "", l).rstrip() for l in run)   # keep the indent
            try:
                ast.parse(block)
                if any(k in block for k in ("(", "=", "return", "import", "def ", "if ")):
                    out.append(_finding(unit, "commented-out code", start, start + len(run) - 1,
                                        f"{len(run)} lines of commented-out code",
                                        "The block parses as Python. Code kept as comments is "
                                        "dead weight that reads as a plan nobody finished.",
                                        "Delete it; version control keeps the history."))
            except SyntaxError:
                pass                                  # prose, not code — nothing to report
    try:
        toks = list(tokenize.generate_tokens(iter([l + "\n" for l in lines] + [""]).__next__))
        # (a bare "" is how the tokenizer hears EOF — every real line must carry its newline)
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return out                                    # the AST already parsed; be safe anyway
    last = 0
    for t in toks:
        if t.type != tokenize.COMMENT:
            continue
        row, col = t.start
        s = t.string
        full_line = lines[row - 1][:col].strip() == ""
        if not full_line or s.startswith("#!") or s.startswith("# noqa"):
            flush(); run = []; continue
        if run and row != last + 1:
            flush(); run = []
        if not run:
            start = row
        run.append(s)
        last = row
    flush()
    return out


def analyse(units: list[dict], root: str) -> dict:
    """Every detector over every non-test unit. Returns {findings, counts}."""
    findings, bodies = [], []
    for u in units:
        if u["is_test"]:
            continue
        lines = _lines(os.path.join(root, u["file"]))
        try:
            tree = ast.parse("\n".join(lines))
        except SyntaxError:
            continue
        sc = _Scan(u, lines, tree)
        sc.visit(tree)
        findings += sc.findings
        findings += _unused_imports(u, tree, lines)
        findings += _commented_out_code(u, lines)
        bodies += [(u, *b) for b in sc.bodies]
    by_hash: dict[str, list] = {}
    for u, h, qual, line, end, name in bodies:
        by_hash.setdefault(h, []).append((u, qual, line, end, name))
    for h, group in by_hash.items():
        if len(group) < 2:
            continue
        first = group[0]
        others = ", ".join(f"{g[1]} ({g[0]['file']}:{g[2]})" for g in group[1:])
        u, qual, line, end, name = first
        findings.append(_finding(
            u, "duplicated function body", line, end,
            f"`{name}` is duplicated {len(group) - 1}× elsewhere",
            f"The body of `{qual}` is structurally identical (names aside) to {others}. "
            "Generated code repeats itself where reviewed code would share.",
            "Keep one implementation and call it from the others.", severity="medium",
            symbol=qual))
    counts: dict[str, int] = {}
    for f in findings:
        k = f["claim"].split(" at ")[0]
        counts[k] = counts.get(k, 0) + 1
    return {"findings": findings, "counts": counts}
