"""Every outbound request in the project retries the same way — Article VII in
practice: a failure is measured, retried where retrying can help, and the record
shows the attempts.

A server that times out, resets the connection, answers 5xx, or rate-limits (429)
gets retried with exponential backoff and jitter — up to NET_RETRIES times after the
first attempt (default 5), honouring a Retry-After header when one is sent. A server
that answers 401, 402, 404 or a bad-request 4xx is NOT retried: the answer will not
change, and five more tries would only hide the real cause behind a delay.

Cloudflare's 5xx family (520–529, including the 524 origin timeout seen on the
platform gateway) counts as retryable: the origin may simply have been busy.

The SDK-level retries are turned off wherever this module is used, so every attempt
is one this module made and counted — nothing is retried invisibly.
"""

import os
import random
import socket
import time
import urllib.error
import urllib.request

RETRIES = int(os.environ.get("NET_RETRIES", "5"))          # after the first attempt
BACKOFF_S = float(os.environ.get("NET_BACKOFF_S", "1"))    # first wait; doubles each retry
BACKOFF_CAP_S = 30.0
RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504,
                          520, 521, 522, 523, 524, 525, 526, 527, 529})
LAST: dict = {}                    # the most recent call's attempts — a readout
_SLEEP = time.sleep                # replaced by the suite so backoff is checked, not waited
_OPEN = urllib.request.urlopen     # replaced by the suite to script a failing server


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


def call(fn, what: str = "request", retries: int | None = None, on_attempt=None):
    """Run fn() with the project's retry policy. Returns its result; raises the last
    exception (with .attempts set) once the policy is exhausted or the failure is one
    retrying cannot fix. LAST records what happened either way."""
    limit = RETRIES if retries is None else int(retries)
    attempts, waited, errors = 0, 0.0, []
    while True:
        attempts += 1
        try:
            out = fn()
            LAST.update(what=what, attempts=attempts, waited_s=round(waited, 1),
                        errors=errors, ok=True)
            return out
        except Exception as e:                       # noqa: BLE001 — classified below
            ok, why = retryable(e)
            errors.append(f"attempt {attempts}: {why}: {str(e)[:100]}")
            if on_attempt:
                on_attempt(attempts, e)
            if not ok or attempts > limit:
                LAST.update(what=what, attempts=attempts, waited_s=round(waited, 1),
                            errors=errors, ok=False,
                            gave_up="not retryable" if not ok else f"after {attempts} attempts")
                try:
                    e.attempts = attempts
                except Exception:                    # noqa: BLE001 — some exceptions forbid attributes
                    pass
                raise
            delay = retry_after(e) or min(BACKOFF_CAP_S, BACKOFF_S * 2 ** (attempts - 1))
            delay *= random.uniform(0.75, 1.25)
            waited += delay
            _SLEEP(delay)


def urlopen(req, timeout: float, context=None, what: str = "request", retries=None):
    """urllib.request.urlopen under the retry policy. The response is returned open;
    a failure while READING a response is the caller's to retry (see ingest)."""
    return call(lambda: _OPEN(req, timeout=timeout, context=context), what, retries)


def describe(exc) -> str:
    """'…after N attempts' for a message, when the exception went through call()."""
    n = getattr(exc, "attempts", 0)
    return f" (after {n} attempts)" if n and n > 1 else ""
