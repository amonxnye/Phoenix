"""The cage a proof-of-concept runs in (SECURITY.md §1).

Every PoC the harness executes is executed here, in a subprocess whose environment
has already been stripped of keys and proxies by the caller, and whose *capabilities*
are removed before the PoC is ever imported:

  - no socket, no name resolution, no HTTP — there is no route out of the box;
  - no subprocess, no exec, no fork — the PoC cannot shell out to get one;
  - no ctypes — it cannot go around the hook;
  - no write anywhere outside the sandbox.

An audit hook (``sys.addaudithook``) is the enforcement, and it cannot be removed once
installed. This is Article V read literally: an agent cannot reach an out-of-scope
system even if the model decides it should, because the capability was never granted.
Attempts are not merely refused, they are recorded and returned as evidence.

The verdict this prints is NOT the PoC's opinion. The PoC provokes the replica; the
invariants in ``sandbox/replica/invariants.py`` — which no agent may write — read the
resulting trace and decide what actually happened.

Usage (the harness calls this, not a human):
    python3 gov/pocrunner.py <poc.py>       → one line: __POC__ {json verdict}
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SANDBOX = os.path.realpath(os.path.join(os.path.dirname(HERE), "sandbox"))
REPLICA = os.path.join(SANDBOX, "replica")

DENIED_EVENTS = (
    "socket.", "urllib.Request", "http.client", "ftplib.", "smtplib.",
    "subprocess.", "os.system", "os.exec", "os.spawn", "os.posix_spawn",
    "os.fork", "os.forkpty", "pty.spawn", "ctypes.", "winreg.",
)
WRITE_EVENTS = ("os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.truncate",
                "os.link", "os.symlink", "shutil.copyfile", "shutil.copymode",
                "shutil.copystat", "shutil.move", "shutil.rmtree")

DENIALS: list[str] = []


def _inside(path) -> bool:
    try:
        return os.path.realpath(str(path)).startswith(SANDBOX + os.sep)
    except (TypeError, ValueError, OSError):
        return False


def _hook(event: str, args) -> None:
    if event.startswith(DENIED_EVENTS):
        DENIALS.append(event)
        raise PermissionError(
            f"REFUSED: {event} — the sandbox has no network and no subprocess "
            f"capability (Article V)")
    if event == "open":
        path, mode = (args + (None, None))[:2]
        if mode and any(c in str(mode) for c in "wxa+") and not _inside(path):
            DENIALS.append(f"open:{mode}")
            raise PermissionError(f"REFUSED: write to {path} outside the sandbox")
    elif event.startswith(WRITE_EVENTS):
        if args and not _inside(args[0]):
            DENIALS.append(event)
            raise PermissionError(f"REFUSED: {event} outside the sandbox")


def main(poc_path: str) -> int:
    sys.dont_write_bytecode = True             # so `denied` records the PoC's attempts,
    sys.addaudithook(_hook)                    # not the import machinery's caches
    # from here on nothing can re-grant what the hook removes

    sys.path.insert(0, REPLICA)
    import importlib.util

    import invariants
    import vault as vault_mod

    # Bind the oracle and the ground truth BEFORE the PoC can execute a single
    # statement. Rebinding invariants.check or editing the credential store afterwards
    # changes nothing: this runner is holding the originals.
    check = invariants.check
    creds = dict(vault_mod.Vault.credentials())
    roles = dict(vault_mod.Vault.roles())

    verdict = {"reproduced": False, "violations": [], "denied": [],
               "requests": 0, "error": ""}
    v = None
    try:
        spec = importlib.util.spec_from_file_location("poc_under_test", poc_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not hasattr(module, "exploit"):
            raise AttributeError("the PoC defines no exploit(vault) function")
        v = vault_mod.Vault()
        module.exploit(v)                      # its return value is deliberately ignored
        verdict["requests"] = len(v.trace)
        verdict["violations"] = check(v, creds=creds, roles=roles)
    except BaseException as e:                 # a broken PoC is a non-finding, not a crash
        verdict["error"] = f"{type(e).__name__}: {e}"[:400]
        if v is not None:
            try:
                verdict["violations"] = check(v, creds=creds, roles=roles)
                verdict["requests"] = len(v.trace)   # a partial trace still counts
            except Exception:
                pass
    verdict["reproduced"] = bool(verdict["violations"])
    verdict["denied"] = sorted(set(DENIALS))
    print("__POC__ " + json.dumps(verdict))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: pocrunner.py <poc.py>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
