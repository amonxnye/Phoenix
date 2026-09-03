"""The structural index — R2, and the component everything above it rests on.

The spec calls this load-bearing and it is: without a graph, every question an analyst
asks becomes a full-text search and the token cost is unbounded. With one, "what calls
this function" is a query, not a prompt.

It is also the only FAST ORACLE this product has. A finding about upstream fix rate
takes six months to verify; a finding that says "nothing calls this" is checkable in
milliseconds and is either true or false. That is why the index is built before any
model is wired in, and why the first analyst (liveness.py) needs no model at all.

What it will NOT do is guess. Python resolves names at runtime, so a static graph is
always partial, and the interesting failure is not a missing edge — it is a missing
edge nobody knew was missing. Every construct that can reach code invisibly is
recorded as a reason reachability cannot be proven in that module (Charter §4), so the
analyst above can refuse rather than assert.
"""

import ast
import builtins
import os
import sqlite3

# Base classes that dispatch to nothing of the user's but dunders: a subclass of one
# of these can be judged like any other class (see Index.foreign_base_classes).
_BUILTIN_TYPES = {n for n, v in builtins.__dict__.items() if isinstance(v, type)}
# (written without getattr(x, variable) — the index would refuse its own module,
#  and did, the first time this line was written)

# Constructs that can invoke code the call graph cannot see. Presence of any of these
# in a module means "unreachable" is unprovable there — not that it is false.
_DYNAMIC_CALLS = {"getattr", "eval", "exec", "__import__", "globals", "locals",
                  "vars", "compile", "import_module"}

# Names the language or the stdlib may call without anything in the repo doing so.
_MAGIC = ("__init__", "__new__", "__call__", "__enter__", "__exit__", "__iter__",
          "__next__", "__len__", "__getitem__", "__setitem__", "__contains__",
          "__repr__", "__str__", "__eq__", "__hash__", "__del__", "__getattr__",
          "__setattr__", "__post_init__")


def _module_name(root: str, path: str) -> str:
    rel = os.path.relpath(path, root)
    if rel.endswith(".py"):
        rel = rel[:-3]
    parts = [p for p in rel.split(os.sep) if p and p != "."]
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or "__root__"


class _Scan(ast.NodeVisitor):
    """One module, one pass. Collects symbols, edges, and honest reasons to doubt."""

    def __init__(self, module: str, path: str):
        self.module, self.path = module, path
        self.symbols: list[dict] = []
        # A SET, not a list: a name loaded fifty times in one scope is one edge. The
        # first build stored every load and reached 323k edges for 17k symbols, which
        # put unreachable_from at 982 ms against a 1 s gate — passing by the width of
        # a cold cache. Deduplicating here is where it belongs: before the store.
        self.edges: set[tuple] = set()          # (src, dst_name, kind)
        self.dynamic: list[str] = []          # why reachability is unprovable here
        self.exported: set[str] = set()       # __all__
        self.registered: set[str] = set()     # decorator-registered → externally called
        self.foreign_base: set[str] = set()   # classes whose base is outside the repo
        self._stack: list[str] = []
        self._class: list[str] = []

    # ── scope helpers ────────────────────────────────────────────────────────
    def _qual(self, name: str = "") -> str:
        parts = [self.module] + self._stack + ([name] if name else [])
        return ".".join(p for p in parts if p)

    def _here(self) -> str:
        return self._qual()

    # ── definitions ──────────────────────────────────────────────────────────
    def _function(self, node, kind: str):
        qual = self._qual(node.name)
        registered = False
        for dec in node.decorator_list:
            # A decorator we cannot resolve to a local function may hand this symbol to
            # a framework (@app.route, @register, @pytest.fixture). The symbol is then
            # called from outside the graph, so it can never be proven dead.
            if isinstance(dec, ast.Attribute) or (
                    isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                registered = True
            elif isinstance(dec, ast.Name) and dec.id not in ("staticmethod",
                                                              "classmethod", "property"):
                registered = True
        if registered:
            self.registered.add(qual)
        self.symbols.append({
            "qualname": qual, "name": node.name, "kind": kind, "module": self.module,
            "file": self.path, "line": node.lineno,
            "end_line": getattr(node, "end_lineno", node.lineno),
            "in_class": self._class[-1] if self._class else "",
            "registered": int(registered),
        })
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    def visit_FunctionDef(self, node):
        self._function(node, "method" if self._class else "function")

    def visit_AsyncFunctionDef(self, node):
        self._function(node, "method" if self._class else "function")

    def visit_ClassDef(self, node):
        qual = self._qual(node.name)
        self.symbols.append({
            "qualname": qual, "name": node.name, "kind": "class", "module": self.module,
            "file": self.path, "line": node.lineno,
            "end_line": getattr(node, "end_lineno", node.lineno),
            "in_class": self._class[-1] if self._class else "", "registered": 0,
        })
        for base in node.bases:
            bname = base.id if isinstance(base, ast.Name) else (
                base.attr if isinstance(base, ast.Attribute) else "")
            if bname:
                self.edges.add((qual, bname, "inherits"))
        self._stack.append(node.name)
        self._class.append(qual)
        self.generic_visit(node)
        self._class.pop()
        self._stack.pop()

    # ── imports ──────────────────────────────────────────────────────────────
    def visit_Import(self, node):
        for a in node.names:
            self.edges.add((self.module, a.name, "imports"))
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        mod = node.module or ""
        if mod:
            self.edges.add((self.module, mod, "imports"))
        for a in node.names:
            self.edges.add((self.module, f"{mod}.{a.name}" if mod else a.name,
                               "imports_symbol"))
        self.generic_visit(node)

    # ── calls, and the things that make calls unknowable ─────────────────────
    def visit_Call(self, node):
        f = node.func
        fname = f.id if isinstance(f, ast.Name) else (
            f.attr if isinstance(f, ast.Attribute) else "")
        if fname in _DYNAMIC_CALLS and self._is_dynamic(fname, node):
            self.dynamic.append(f"{fname}() at line {node.lineno}")
        if fname:
            self.edges.add((self._here(), fname, "calls"))
        self.generic_visit(node)

    @staticmethod
    def _is_dynamic(fname: str, node) -> bool:
        """Two ways a match on the NAME lies about dispatch, both seen on the first
        real repo:

        getattr(obj, "literal") is a spelling of obj.literal, not dispatch. Only a
        name the parser cannot see — a variable, an f-string — can reach code the graph
        does not show. Flagging the literal form would refuse to judge every module that
        ever wrote getattr(x, "y", default), which is most of them.

        obj.compile() is a METHOD — re.compile, graph.compile — and has nothing to do
        with the builtin. Only the bare-name form of a builtin is the builtin. The one
        attribute-form dispatch worth flagging is importlib.import_module()."""
        if isinstance(node.func, ast.Attribute):
            return fname == "import_module"
        if fname in ("getattr", "setattr", "hasattr", "delattr"):
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) \
                    and isinstance(node.args[1].value, str):
                return False
        return True

    # A function passed as a VALUE — Thread(target=fn), sorted(key=fn), a dict of
    # handlers — is never the subject of a Call node, so a pure call graph would report
    # it dead. That is the expensive error (Charter §4). Every name load is therefore a
    # reference edge; liveness follows references, callers_of follows calls only.
    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            self.edges.add((self._here(), node.id, "refs"))
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if isinstance(node.ctx, ast.Load):
            self.edges.add((self._here(), node.attr, "refs"))
        self.generic_visit(node)

    def visit_Assign(self, node):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == "__all__":
                for el in getattr(node.value, "elts", []):
                    if isinstance(el, ast.Constant) and isinstance(el.value, str):
                        self.exported.add(el.value)
        self.generic_visit(node)


def build(root: str, db_path: str, skip: tuple = (".git", "node_modules", "vendor",
                                                  "__pycache__", ".venv", "venv")) -> dict:
    """Index a tree into `db_path`. Returns a summary including what could not be read.

    Failures are counted, never swallowed: a file that will not parse is a capability
    gap with a reason, because a report that silently skipped a tenth of the repo is
    worse than one that says so (Charter §6)."""
    files, unreadable = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
        for fn in filenames:
            if fn.endswith(".py"):
                files.append(os.path.join(dirpath, fn))

    db = sqlite3.connect(db_path)
    db.executescript("""
        DROP TABLE IF EXISTS symbols; DROP TABLE IF EXISTS edges;
        DROP TABLE IF EXISTS modules;
        CREATE TABLE symbols(qualname TEXT PRIMARY KEY, name TEXT, kind TEXT,
            module TEXT, file TEXT, line INT, end_line INT, in_class TEXT,
            registered INT DEFAULT 0, exported INT DEFAULT 0);
        CREATE TABLE edges(src TEXT, dst TEXT, kind TEXT);
        CREATE TABLE modules(name TEXT PRIMARY KEY, file TEXT, loc INT,
            dynamic TEXT, is_test INT DEFAULT 0);
        CREATE INDEX idx_edge_dst ON edges(dst, kind);
        CREATE INDEX idx_edge_src ON edges(src, kind);
        CREATE INDEX idx_sym_name ON symbols(name);
        CREATE INDEX idx_sym_mod ON symbols(module);
    """)
    n_sym = n_edge = 0
    for path in sorted(files):
        mod = _module_name(root, path)
        try:
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            tree = ast.parse(src, filename=path)
        except (OSError, SyntaxError, UnicodeDecodeError) as e:
            unreadable.append((path, f"{type(e).__name__}: {e}"))
            continue
        sc = _Scan(mod, os.path.relpath(path, root))
        sc.visit(tree)
        is_test = int("test" in os.path.basename(path).lower()
                      or os.sep + "tests" + os.sep in path)
        db.execute("INSERT OR REPLACE INTO modules(name, file, loc, dynamic, is_test) "
                   "VALUES(?,?,?,?,?)",
                   (mod, os.path.relpath(path, root), src.count("\n") + 1,
                    "; ".join(sorted(set(sc.dynamic))[:6]), is_test))
        for s in sc.symbols:
            s["exported"] = int(s["name"] in sc.exported)
            db.execute(
                "INSERT OR REPLACE INTO symbols(qualname, name, kind, module, file, "
                "line, end_line, in_class, registered, exported) "
                "VALUES(:qualname,:name,:kind,:module,:file,:line,:end_line,"
                ":in_class,:registered,:exported)", s)
            n_sym += 1
        db.executemany("INSERT INTO edges(src, dst, kind) VALUES(?,?,?)", sorted(sc.edges))
        n_edge += len(sc.edges)
    db.commit()
    db.close()
    return {"files": len(files), "modules": len(files) - len(unreadable),
            "symbols": n_sym, "edges": n_edge, "unreadable": unreadable}


# ── the query API agents use instead of grepping ─────────────────────────────

class Index:
    """Read-only queries over a built index. These are the graph facts the Charter
    admits as evidence (§2) — each one is cheap, exact, and re-checkable at report
    time."""

    def __init__(self, db_path: str):
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row

    def close(self):
        self.db.close()

    def symbol(self, qualname: str) -> dict | None:
        r = self.db.execute("SELECT * FROM symbols WHERE qualname=?", (qualname,)).fetchone()
        return dict(r) if r else None

    def symbols(self, kind: str = "", module: str = "") -> list[dict]:
        q, args = "SELECT * FROM symbols WHERE 1=1", []
        if kind:
            q, _ = q + " AND kind=?", args.append(kind)
        if module:
            q, _ = q + " AND module=?", args.append(module)
        return [dict(r) for r in self.db.execute(q + " ORDER BY qualname", args)]

    def callers_of(self, qualname: str) -> list[str]:
        """Who calls this. Resolution is by name, which is what Python itself does at
        the call site — so a same-named symbol elsewhere WILL show up here. That
        imprecision errs in the safe direction for liveness: a shared name can make a
        dead symbol look alive, never a live one look dead."""
        sym = self.symbol(qualname)
        if not sym:
            return []
        rows = self.db.execute(
            "SELECT DISTINCT src FROM edges WHERE dst=? AND kind='calls'", (sym["name"],))
        return sorted(r[0] for r in rows if r[0] != qualname)

    def dependencies_of(self, module: str) -> list[str]:
        return sorted(r[0] for r in self.db.execute(
            "SELECT DISTINCT dst FROM edges WHERE src=? AND kind='imports'", (module,)))

    def dependents_of(self, module: str) -> list[str]:
        return sorted(r[0] for r in self.db.execute(
            "SELECT DISTINCT src FROM edges WHERE dst=? AND kind='imports'", (module,)))

    def tests_covering(self, qualname: str) -> list[str]:
        """Callers that live in a test module — the closest thing to coverage that can
        be had without running anything."""
        sym = self.symbol(qualname)
        if not sym:
            return []
        rows = self.db.execute(
            "SELECT DISTINCT e.src FROM edges e JOIN modules m "
            "ON e.src LIKE m.name || '%' WHERE e.dst=? AND e.kind='calls' AND m.is_test=1",
            (sym["name"],))
        return sorted(r[0] for r in rows)

    def dynamic_reasons(self, module: str) -> str:
        r = self.db.execute("SELECT dynamic FROM modules WHERE name=?", (module,)).fetchone()
        return (r[0] or "") if r else ""

    def modules(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM modules ORDER BY name")]

    def foreign_base_classes(self) -> set[str]:
        """Classes inheriting from a FRAMEWORK this repo does not define. Their methods
        may be invoked by that framework — `do_GET` is never called by our code, and
        reporting it dead would be wrong.

        A builtin base is not a framework. `Exception`, `object`, `dict` never look up
        a user-defined method by name; only dunders, which are roots already. The first
        public run refused 25 classes, and the samples were `ConfigError(Exception)` —
        a refusal that disclosed nothing and hid an uncalled method as live."""
        local = {r[0] for r in self.db.execute("SELECT name FROM symbols WHERE kind='class'")}
        out = set()
        for src, dst in self.db.execute("SELECT src, dst FROM edges WHERE kind='inherits'"):
            if dst not in local and dst not in _BUILTIN_TYPES:
                out.add(src)
        return out

    def entry_points(self) -> set[str]:
        """Where execution can begin. Deliberately generous: an entry point wrongly
        omitted turns live code into a false 'dead' finding, which is the expensive
        error (Charter §4)."""
        entries = set()
        foreign = self.foreign_base_classes()
        dynamic = {r[0] for r in self.db.execute(
            "SELECT name FROM modules WHERE dynamic IS NOT NULL AND dynamic<>''")}
        for r in self.db.execute("SELECT qualname, name, kind, module, registered, "
                                 "exported, in_class FROM symbols"):
            q, name, kind, mod, registered, exported, in_class = r
            if kind == "class":
                continue
            if registered or exported:
                entries.add(q)                      # framework-registered or re-exported
            elif name in ("main", "__main__"):
                entries.add(q)
            elif name.startswith("test_") or name.startswith("__") and name.endswith("__"):
                entries.add(q)
            elif in_class and in_class in foreign:
                # A REFUSAL MUST PROPAGATE. do_GET on a BaseHTTPRequestHandler subclass
                # is invoked by the stdlib, so everything it reaches may be alive. The
                # first run on a repo its author knew reported 27 dead functions, and
                # every one was reachable only through such a handler — the handler was
                # honestly refused, and the refusal stopped at its own boundary. Roots,
                # not skips.
                entries.add(q)
            elif mod in dynamic:
                entries.add(q)                      # any symbol here may be dispatched to
        for r in self.db.execute("SELECT name FROM modules WHERE is_test=1"):
            for s in self.db.execute("SELECT qualname FROM symbols WHERE module=?", (r[0],)):
                entries.add(s[0])                   # everything a test file defines
        return entries

    def unreachable_from(self, entries: set[str] | None = None) -> set[str]:
        """Symbols no path from any entry point reaches. This is a graph fact; whether
        it may be REPORTED as dead is a separate question the analyst answers."""
        entries = entries if entries is not None else self.entry_points()
        by_name: dict[str, list[str]] = {}
        for q, n in self.db.execute("SELECT qualname, name FROM symbols"):
            by_name.setdefault(n, []).append(q)
        reach: dict[str, set[str]] = {}
        # calls AND references: a function handed to Thread(target=...) is alive
        for src, dst in self.db.execute(
                "SELECT DISTINCT src, dst FROM edges WHERE kind IN ('calls','refs')"):
            reach.setdefault(src, set()).update(by_name.get(dst, ()))
        # Every module's top level runs on import, so it is a root — including modules
        # nothing imports, which is conservative in the safe direction (a function used
        # only by an orphan module reads as live, never as dead).
        roots = set(entries) | {r[0] for r in self.db.execute("SELECT name FROM modules")}
        seen, frontier = set(roots), list(roots)
        while frontier:
            cur = frontier.pop()
            for nxt in reach.get(cur, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append(nxt)
        allsym = {r[0] for r in self.db.execute(
            "SELECT qualname FROM symbols WHERE kind IN ('function','method')")}
        return allsym - seen

    def references_of(self, qualname: str) -> list[str]:
        """Every scope that names this symbol, called or not — the reachability
        evidence liveness cites, distinct from callers_of's true call graph."""
        sym = self.symbol(qualname)
        if not sym:
            return []
        rows = self.db.execute(
            "SELECT DISTINCT src FROM edges WHERE dst=? AND kind IN ('calls','refs')",
            (sym["name"],))
        return sorted(r[0] for r in rows if r[0] != qualname)
