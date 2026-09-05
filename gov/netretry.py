"""Every outbound request in the project retries the same way — and the same layer
that retries is the layer that STOPS retrying. Article VII in practice: a failure is
measured, retried only where retrying can help, and the record shows the attempts.

What is retried: a timeout, a reset connection, a 429, any 5xx (Cloudflare's 52x
included — the 524 origin timeout seen on the platform gateway). Exponential backoff
with jitter, NET_RETRIES times after the first attempt (default 5), honouring a
Retry-After header. What is not: 401, 402, 404 and other bad-request answers — the
answer will not change, and five more tries would hide the cause behind a delay.

Three guards make one shared retry layer safe rather than dangerous:

1. **Only idempotent requests are retried.** A caller must say `idempotent=True`;
   the urlopen wrapper refuses to retry a POST/PUT/DELETE that has not. A retried
   write that the server had already acted on would act twice. Every call in this
   project today is a read — a completion, a listing, a download, a query — and the
   declaration is what keeps the next caller honest.
2. **A circuit breaker per host.** Retries multiply load on a server that is
   already struggling: 660 panel calls × 6 attempts against a hung gateway would
   run for days. After NET_BREAK_AFTER calls (default 2) have exhausted their
   retries against one host, the breaker OPENS: calls fail at once with
   CircuitOpen for NET_COOL_S seconds (default 60), then ONE probe call is let
   through; success closes the breaker, failure re-opens it.
3. **Nothing is retried invisibly.** SDK-level retries are turned off wherever this
   module is used. `last()` shows the calling thread's most recent attempts,
   `STATS` the totals, `breakers()` every host's state.

One cost is outside this module's sight and is stated here so it is not forgotten:
a completion that times out on the client may still be finished and billed by a
paid provider. The record cannot see that; on a self-hosted gateway it is only
wasted load.
"""

import os
import random
import socket
import threading
import time
import urllib.error
import urllib.request

RETRIES = int(os.environ.get("NET_RETRIES", "5"))          # after the first attempt
BACKOFF_S = float(os.environ.get("NET_BACKOFF_S", "1"))    # first wait; doubles each retry
BACKOFF_CAP_S = 30.0
BREAK_AFTER = int(os.environ.get("NET_BREAK_AFTER", "2"))  # exhausted calls that open a breaker
COOL_S = float(os.environ.get("NET_COOL_S", "60"))         # how long an open breaker fails fast
RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504,
                          520, 521, 522, 523, 524, 525, 526, 527, 529})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

STATS = {"calls": 0, "retried": 0, "attempts": 0, "gave_up": 0, "fast_failed": 0,
         "breaker_trips": 0}
_LOCK = threading.Lock()
_BREAKERS: dict[str, dict] = {}    # key → {failures, open_until, probing}
_local = threading.local()
_SLEEP = time.sleep                # replaced by the suite so backoff is checked, not waited
_NOW = time.time                   # replaced by the suite so cooling is checked, not waited
_OPEN = urllib.request.urlopen     # replaced by the suite to script a failing server


class CircuitOpen(Exception):
    """The breaker for this host is open: failing fast, not calling."""

    def __init__(self, key: str, seconds_left: float):
        super().__init__(f"circuit open for {key}: failing fast for another "
                         f"{seconds_left:.0f}s after repeated exhausted retries")
        self.key, self.seconds_left = key, seconds_left


def last() -> dict:
    """The calling thread's most recent call: attempts, waits, errors, outcome."""
    d = getattr(_local, "last", None)
    if d is None:
        d = _local.last = {}
    return d


def breakers() -> dict:
    now = _NOW()
    with _LOCK:
        return {k: {"failures": b["failures"],
                    "open": b["open_until"] > now,
                    "seconds_left": max(0, round(b["open_until"] - now))}
                for k, b in _BREAKERS.items()}


def reset() -> None:
    """Forget every breaker and total — the suite's fixture boundary."""
    with _LOCK:
        _BREAKERS.clear()
        for k in STATS:
            STATS[k] = 0


def status_of(exc) -> int | None:
    """The HTTP status an exception carries, whatever library raised it."""
    for attr in ("code", "status_code", "status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int) and 100 <= v <= 599:
            return v
    resp = getattr(exc, "response", None)
    v = getattr(resp, "status_code", None) or getattr(resp, "status", None)
    return v if isinstance(v, int) and 100 <= v <= 599 else None


def retry_after(exc) -> float | None:
    """A server's own Retry-After, in seconds, when it sends one (429/503)."""
    hdrs = getattr(exc, "headers", None) or getattr(getattr(exc, "response", None), "headers", None)
    try:
        v = hdrs.get("Retry-After") if hdrs is not None else None
        return min(BACKOFF_CAP_S * 2, float(v)) if v else None
    except (TypeError, ValueError, AttributeError):
        return None


def retryable(exc) -> tuple[bool, str]:
    """(should we retry, why) — the classification the record shows."""
    if isinstance(exc, CircuitOpen):
        return False, "circuit open"
    st = status_of(exc)
    if st is not None:
        return st in RETRY_STATUS, f"HTTP {st}"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return True, "timeout"
    if isinstance(exc, urllib.error.URLError):
        return True, f"connection: {getattr(exc, 'reason', exc)}"
    name = type(exc).__name__
    if name in ("APITimeoutError", "APIConnectionError", "RemoteProtocolError",
                "ReadTimeout", "ConnectTimeout", "ConnectionError", "IncompleteRead"):
        return True, name
    if isinstance(exc, (ConnectionError, OSError)):
        return True, f"network: {name}"
    return False, name


def _admit(key: str) -> None:
    """Raise CircuitOpen if the host's breaker is open; let one probe through when
    the cooling period has passed."""
    if not key:
        return
    now = _NOW()
    with _LOCK:
        b = _BREAKERS.setdefault(key, {"failures": 0, "open_until": 0.0, "probing": False})
        if b["open_until"] > now:
            STATS["fast_failed"] += 1
            raise CircuitOpen(key, b["open_until"] - now)
        if b["open_until"] and not b["probing"]:
            b["probing"] = True                     # half-open: this call is the probe
        elif b["open_until"] and b["probing"]:
            STATS["fast_failed"] += 1                # the probe is out; wait for its verdict
            raise CircuitOpen(key, 1.0)


def _report(key: str, exhausted: bool, ok: bool) -> None:
    if not key:
        return
    with _LOCK:
        b = _BREAKERS.setdefault(key, {"failures": 0, "open_until": 0.0, "probing": False})
        if ok:
            b.update(failures=0, open_until=0.0, probing=False)
        elif exhausted:
            b["failures"] += 1
            b["probing"] = False
            if b["failures"] >= BREAK_AFTER:
                b["open_until"] = _NOW() + COOL_S
                STATS["breaker_trips"] += 1


def call(fn, what: str = "request", retries: int | None = None, on_attempt=None,
         idempotent: bool = False, key: str = ""):
    """Run fn() under the policy. Returns its result; raises the last exception (with
    .attempts set) once the policy is exhausted or the failure is one retrying cannot
    fix. A request not declared idempotent is made ONCE. `key` names the host for
    the circuit breaker."""
    limit = (RETRIES if retries is None else int(retries)) if idempotent else 0
    _admit(key)
    attempts, waited, errors = 0, 0.0, []
    with _LOCK:
        STATS["calls"] += 1
    while True:
        attempts += 1
        with _LOCK:
            STATS["attempts"] += 1
        try:
            out = fn()
            last().update(what=what, attempts=attempts, waited_s=round(waited, 1),
                          errors=errors, ok=True)
            _report(key, False, True)
            if attempts > 1:
                with _LOCK:
                    STATS["retried"] += 1
            return out
        except Exception as e:                       # noqa: BLE001 — classified below
            ok, why = retryable(e)
            errors.append(f"attempt {attempts}: {why}: {str(e)[:100]}")
            if on_attempt:
                on_attempt(attempts, e)
            if not ok or attempts > limit:
                gave = ("not retryable" if not ok else
                        "not idempotent: one attempt only" if limit == 0 and RETRIES
                        else f"after {attempts} attempts")
                last().update(what=what, attempts=attempts, waited_s=round(waited, 1),
                              errors=errors, ok=False, gave_up=gave)
                _report(key, exhausted=ok and attempts > limit, ok=False)
                with _LOCK:
                    STATS["gave_up"] += 1
                try:
                    e.attempts = attempts
                except Exception:                    # noqa: BLE001 — some exceptions forbid attributes
                    pass
                raise
            delay = retry_after(e) or min(BACKOFF_CAP_S, BACKOFF_S * 2 ** (attempts - 1))
            delay *= random.uniform(0.75, 1.25)
            waited += delay
            _SLEEP(delay)


def urlopen(req, timeout: float, context=None, what: str = "request", retries=None,
            idempotent: bool | None = None, key: str = ""):
    """urllib.request.urlopen under the policy. A GET/HEAD is idempotent by the
    protocol; anything else must be declared so by its caller or it is made once.
    The response is returned open; a failure while READING it is the caller's."""
    method = req.get_method() if hasattr(req, "get_method") else "GET"
    if idempotent is None:
        idempotent = method in SAFE_METHODS
    return call(lambda: _OPEN(req, timeout=timeout, context=context), what, retries,
                idempotent=idempotent, key=key)


def describe(exc) -> str:
    """'…after N attempts' for a message, when the exception went through call()."""
    n = getattr(exc, "attempts", 0)
    return f" (after {n} attempts)" if n and n > 1 else ""
