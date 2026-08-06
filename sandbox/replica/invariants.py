"""The security oracle — what the replica must never do (SECURITY.md §3).

This file is to the security harness what ``sandbox/tests/`` is to the software
harness: **the exam, and agents cannot write to it** (``redteam.apply_patch``
refuses it outright). A proof-of-concept does not get to declare that it worked.
It provokes the service; then this module reads the request trace and decides.

That inversion is the whole point. An LLM will happily narrate an exploit it did not
achieve; an invariant checked against a recorded trace cannot be talked into anything.
An unreproduced claim earns nothing (Article I.2).

Each invariant is stated as the property that must hold. A violation names the
vulnerability class it belongs to, so a finding is filed against the hardening
checklist rather than against an agent's prose.
"""

import ast
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.realpath(os.path.join(HERE, "docs"))

# The hardening checklist: the classes the Vision requires to be covered.
CLASSES = ["sqli", "traversal", "authz", "rce"]

TITLES = {
    "sqli": "authentication bypass via SQL injection",
    "traversal": "arbitrary file read via path traversal",
    "authz": "missing authorization on an admin route",
    "rce": "arbitrary code execution via eval of request data",
}


def _arithmetic_only(expr: str) -> bool:
    """True if the expression is nothing but literal arithmetic — the only thing a
    calculator route has any business evaluating."""
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
               ast.Pow, ast.USub, ast.UAdd)
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return True                                 # never evaluated → nothing proven
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            return False
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            return False
    return True


def check(vault, trace: list[dict] | None = None,
          creds: dict | None = None, roles: dict | None = None) -> list[dict]:
    """Read the trace of what actually happened and return every violation found.

    This is the verdict the whole harness is scored on. It never reads the PoC's
    return value, its prose, or its intent.

    ``creds``/``roles`` are the ground truth to check the trace against. The runner
    snapshots them *before* the PoC gets to run and passes them in, so a PoC cannot
    manufacture a violation by editing the credential store it is being judged against.
    """
    trace = vault.trace if trace is None else trace
    creds = vault.credentials() if creds is None else creds
    roles = vault.roles() if roles is None else roles
    out: list[dict] = []

    for i, ev in enumerate(trace):
        route = ev.get("route")

        # INV-AUTH — a session is issued only for a username/password pair that is
        # actually in the credential store.
        if route == "/login" and ev.get("session"):
            user, password = ev.get("user"), ev.get("password")
            if creds.get(user) != password:
                out.append({
                    "id": "INV-AUTH", "class": "sqli", "step": i,
                    "detail": f"session issued to {user!r} with password {password!r}, "
                              f"which is not this account's password"})

        # INV-PATH — every byte served by /doc comes from a file inside docs/.
        if route == "/doc" and ev.get("served_path"):
            served = os.path.realpath(ev["served_path"])
            if not (served == DOCS or served.startswith(DOCS + os.sep)):
                out.append({
                    "id": "INV-PATH", "class": "traversal", "step": i,
                    "detail": f"served {served} via name {ev.get('name')!r}, "
                              f"which is outside {DOCS}"})

        # INV-AUTHZ — the user directory is disclosed only to an admin session.
        if route == "/admin/users" and ev.get("records"):
            caller, role = ev.get("session"), ev.get("role")
            others = [u for u in ev["records"] if u != caller]
            if others and roles.get(caller) != "admin":
                out.append({
                    "id": "INV-AUTHZ", "class": "authz", "step": i,
                    "detail": f"session {caller!r} (role {role!r}) received records for "
                              f"{', '.join(others)} without admin authorization"})

        # INV-EXEC — /calc evaluates literal arithmetic and nothing else.
        if route == "/calc" and ev.get("ok") and not _arithmetic_only(ev.get("expr", "")):
            out.append({
                "id": "INV-EXEC", "class": "rce", "step": i,
                "detail": f"evaluated non-arithmetic expression {ev.get('expr')!r}"})

    return out
