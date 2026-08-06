"""The provider registry and per-role routing — the Arena's first organ (ARENA.md §2).

Phoenix has exactly one seam where a model plugs in (``brain._chat``). This module is
the switchboard behind that seam: a registry of named providers, a role → provider map,
the human-maintained price table, and the dollar ceiling that governs an experiment.

Three constitutional rules live here, not as comments but as code (Article X):

  1. **Assignment is a human-only power.** ``assign`` is reachable only from the
     token-gated console or the environment. Nothing in the fleet, the board or the
     tacit-consent clock can call it.
  2. **Every call and every decision records its model.** ``resolve`` returns the model
     name with the provider, ``brain`` logs it per call, ``anchor`` stores it per
     decision. Per-model scoring is then a query, not a bolt-on.
  3. **An outage is an abstention, never a substitution.** A failed role is marked
     offline (``note_call``); the board reads ``offline_seats`` and that seat votes
     *unknown*. No other model is quietly borrowed to fill the chair.

Configuration (env, or the anchor config which the console writes):

  MODEL_REGISTRY        JSON: {"name": {"base_url","kind","key_env"|"key","model","tier"}}
  MODEL_ROLE_GOVERNOR   "openai" or "openai:gpt-5-mini" — likewise PRUDENCE, GROWTH,
  MODEL_ROLE_...        LEDGER, FLEET, WORKER, RETROSPECTIVE, DEFAULT
  MODEL_PRICES          JSON: {"model-prefix": {"in": $/1M, "out": $/1M}}
  EVAL_BUDGET_USD       ceiling for this process; 0/unset = no ceiling
  EVAL_PROVIDER_CAP_USD JSON: {"provider": usd} — optional per-provider sub-caps

With every role unset this module resolves to nothing and ``brain`` behaves exactly as
it did before it existed — the single-brain settlement is untouched by the arena.
"""

import json
import os
import time

# The seats and jobs a model can be assigned to. 'default' backs every unset role.
ROLES = ("governor", "Prudence", "Growth", "Ledger", "fleet", "worker",
         "retrospective", "default")

# Which role each call site belongs to, keyed by the `purpose` already carried by every
# model call. A purpose with no entry inherits 'default'.
PURPOSE_ROLE = {
    "chat-reply": "governor",
    "think": "governor",              # overridden per call site where the seat is known
    "dev-proposal": "governor",
    "research": "governor",
    "retrospective": "retrospective",
    "choose-resource": "fleet",
    "should-advance": "fleet",
    "worker-patch": "worker",
}

# Direct, first-class providers (ARENA.md §1), extensible via MODEL_REGISTRY.
# The endpoints are facts about the protocol and stable; MODEL NAMES ARE NOT, and this
# module ships none. A model id invented here would end up in a scorecard, a price
# lookup and a leaderboard row, all of them wrong in the same direction and none of
# them obviously wrong. So a provider is a place to send a request; WHICH model runs is
# named by the operator ("openai:<model-id>", or `model` in MODEL_REGISTRY), and a
# provider assigned without one is a configuration error that says so.
BUILTIN = {
    "openai": {"kind": "openai", "base_url": "https://api.openai.com/v1",
               "key_env": "OPENAI_API_KEY", "model": "", "tier": "ranked"},
    "gemini": {"kind": "openai",         # Gemini's OpenAI-compatible endpoint
               "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "key_env": "GEMINI_API_KEY", "model": "", "tier": "ranked"},
    "anthropic": {"kind": "anthropic", "base_url": "https://api.anthropic.com",
                  "key_env": "ANTHROPIC_API_KEY", "model": "", "tier": "ranked"},
    "deepseek": {"kind": "openai", "base_url": "https://api.deepseek.com",
                 "key_env": "DEEPSEEK_API_KEY", "model": "", "tier": "ranked"},
    # A router is one key and hundreds of models: breadth, not ranked results. Model
    # identity drifts, cost/latency measure the router, and silent failover is exactly
    # what the constitution forbids — so anything served through it is SCOUTING tier.
    "openrouter": {"kind": "openai", "base_url": "https://openrouter.ai/api/v1",
                   "key_env": "OPENROUTER_API_KEY", "model": "", "tier": "scouting"},
}

# The price table ships EMPTY. Published prices change without notice, and a stale
# number here would silently multiply through every $-per-vision-point column while
# looking authoritative. The operator loads the table (MODEL_PRICES or /providers) and
# owns it; until then a model is *unpriced* — reported as unpriced, never as $0.00, and
# a dollar ceiling refuses to pretend it is enforcing anything (see check_budget).
SEED_PRICES: dict = {}

ROUTERS = ("openrouter.ai", "/router", "litellm")   # base URLs that resolve elsewhere

# Why a role has no usable provider. 'unset' is normal — the role inherits the default
# brain. Every other reason is a MISCONFIGURATION, and the difference matters: falling
# back to the default brain when an assigned seat is broken would be exactly the silent
# substitution Article X.3 forbids.
UNSET, NO_KEY, NO_MODEL, UNKNOWN_PROVIDER = "unset", "no-key", "no-model", "unknown-provider"


class BudgetExceeded(RuntimeError):
    """The run's dollar ceiling is spent. The run halts; it does not finish cheaper —
    finishing on a different model would be a substitution (ARENA.md §5)."""


class SeatMisconfigured(RuntimeError):
    """A seat was assigned a provider it cannot use. The seat abstains and says why;
    nothing quietly answers in its place."""


# ── configuration: env first, then the anchor config the console writes ──────────

def _anchor_json(key: str) -> dict:
    try:
        import anchor
        raw = anchor.config_get(key, "")
    except Exception:
        return {}
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def _env_json(key: str) -> dict:
    raw = os.environ.get(key, "").strip()
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def registry() -> dict:
    """Every registered provider: the four built-ins plus anything the human added.
    Adding a fifth lab is a registry entry, not a code change."""
    out = {k: dict(v) for k, v in BUILTIN.items()}
    for name, spec in list(_anchor_json("models.registry").items()) + \
            list(_env_json("MODEL_REGISTRY").items()):
        if isinstance(spec, dict):
            out.setdefault(name, {}).update(spec)
    return out


def roles() -> dict:
    """role → "provider" or "provider:model". Env wins over the console-stored map so a
    deployment can pin its arena regardless of what a previous run left behind."""
    out = {k: v for k, v in _anchor_json("models.roles").items() if k in ROLES}
    for role in ROLES:
        v = os.environ.get(f"MODEL_ROLE_{role.upper()}", "").strip()
        if v:
            out[role] = v
    return out


def assign(role: str, spec: str) -> tuple[bool, str]:
    """Assign a model to a role. HUMAN-ONLY (Article X.1): the console gates this behind
    the operator token, and tacit consent never reaches it. Empty spec clears the role."""
    role = role.strip()
    if role not in ROLES:
        return False, f"unknown role '{role}'"
    spec = (spec or "").strip()
    name = spec.split(":", 1)[0]
    if spec and name not in registry():
        return False, f"unknown provider '{name}' — register it first"
    current = {k: v for k, v in _anchor_json("models.roles").items() if k in ROLES}
    if spec:
        current[role] = spec
    else:
        current.pop(role, None)
    try:
        import anchor
        anchor.config_set("models.roles", json.dumps(current))
    except Exception as e:
        return False, str(e)
    return True, f"{role} → {spec or 'default'}"


def resolve_detail(role: str) -> tuple[dict | None, str]:
    """(provider, reason) for a role. reason is '' when a provider was resolved, UNSET
    when the role simply isn't assigned, and a misconfiguration reason otherwise.

    Callers MUST distinguish the two failures. An unset role inherits the default brain;
    a broken assigned role must not — quietly answering with another model would change
    who governs, in secret (Article X.3)."""
    if not role:
        return None, UNSET
    spec = roles().get(role) or roles().get("default", "")
    if not spec:
        return None, UNSET
    name, _, model = spec.partition(":")
    entry = registry().get(name)
    if not entry:
        return None, UNKNOWN_PROVIDER
    key = (entry.get("key") or os.environ.get(entry.get("key_env", ""), "")).strip()
    if not key:
        return None, NO_KEY
    model = (model or entry.get("model") or "").strip()
    if not model:
        return None, NO_MODEL         # a provider is a place, not a mind: name the model
    base = entry.get("base_url", "")
    return {"name": name, "kind": entry.get("kind") or
            ("anthropic" if "anthropic" in base else "openai"),
            "base_url": base, "key": key, "model": model,
            "tier": entry.get("tier") or ("scouting" if is_router(base) else "ranked")}, ""


def resolve(role: str) -> dict | None:
    """The provider for a role, or None. Prefer resolve_detail when the *reason* for a
    miss matters — which, at the seam, it always does."""
    return resolve_detail(role)[0]


def misconfigured(role: str) -> str:
    """'' if the role is fine (resolved, or deliberately unset); otherwise the reason
    it is broken. A broken seat is a human's problem to fix, and is never papered over."""
    p, reason = resolve_detail(role)
    return "" if (p or reason == UNSET) else reason


def explain(reason: str, role: str = "") -> str:
    spec = roles().get(role, "") or roles().get("default", "")
    name = spec.split(":", 1)[0]
    return {
        NO_KEY: f"'{name}' has no API key — set its key environment variable",
        NO_MODEL: (f"'{name}' has no model named — assign it as '{name}:<model-id>'; "
                   "this platform never guesses a model name"),
        UNKNOWN_PROVIDER: f"'{name}' is not in the registry — add it to MODEL_REGISTRY",
        UNSET: "unset — inherits the default brain",
    }.get(reason, reason)


def is_router(base_url: str) -> bool:
    return any(m in (base_url or "").lower() for m in ROUTERS)


def catalog() -> list:
    """The registry as the console may see it — never the keys. `keyed` says whether a
    provider is usable; the secret itself never leaves the process."""
    out = []
    for name, e in sorted(registry().items()):
        key = (e.get("key") or os.environ.get(e.get("key_env", ""), "")).strip()
        out.append({"name": name, "kind": e.get("kind", "openai"),
                    "base_url": e.get("base_url", ""), "model": e.get("model", ""),
                    "tier": e.get("tier", "ranked"), "key_env": e.get("key_env", ""),
                    "keyed": bool(key)})
    return out


def assignments() -> dict:
    """role → the model actually serving it, "" when the role falls through to the
    default brain. What the console shows and what every scorecard records."""
    out = {}
    for role in ROLES:
        p = resolve(role)
        out[role] = f"{p['name']}:{p['model']}" if p else ""
    return out


def seat_faults() -> dict:
    """role → why its assignment cannot be used. Empty when every seat is either
    working or deliberately unset. These are surfaced, never worked around."""
    return {r: explain(reason, r) for r in ROLES
            if (reason := misconfigured(r))}


def active() -> bool:
    """Is any role routed? False means this is a plain single-brain deployment."""
    return any(assignments().values())


def tier() -> str:
    """'ranked' only if every routed role is a direct provider AND every assignment
    actually resolves. One router anywhere, or one broken seat, and the run is not a
    ranked result — a row is only as pinned as its weakest seat."""
    if seat_faults():
        return "incomplete"
    tiers = [p["tier"] for r in ROLES if (p := resolve(r))]
    return "scouting" if any(t == "scouting" for t in tiers) else "ranked"


# ── the price table and cost per call ────────────────────────────────────────────

def prices() -> dict:
    out = dict(SEED_PRICES)
    for src in (_anchor_json("models.prices"), _env_json("MODEL_PRICES")):
        for k, v in src.items():
            if isinstance(v, dict):
                out[k] = {"in": float(v.get("in", 0)), "out": float(v.get("out", 0))}
    return out


def price_for(model: str) -> dict | None:
    """Longest-prefix match, so 'claude-sonnet-5-20260101' finds 'claude-sonnet'."""
    m = (model or "").lower()
    best = None
    for prefix, p in prices().items():
        if m.startswith(prefix.lower()) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, p)
    return best[1] if best else None


def cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Dollars for one call, computed at log time (ARENA.md §5) — prices drift, so a
    cost recomputed later would silently rewrite history."""
    p = price_for(model)
    if not p:
        return 0.0
    return round((prompt_tokens / 1e6) * p.get("in", 0)
                 + (completion_tokens / 1e6) * p.get("out", 0), 6)


def unpriced(model: str) -> bool:
    return price_for(model) is None


# ── the budget ceiling (ARENA.md §5) ─────────────────────────────────────────────

_SPENT: dict = {"total": 0.0}          # this process's spend, by provider
_SEAT: dict = {}                       # role → {"ok": bool, "at": ts, "error": str}
_FALLBACKS: dict = {"n": 0}            # times a rule wrote the line the model owed us


def budget_usd() -> float:
    try:
        return float(os.environ.get("EVAL_BUDGET_USD", "0") or 0)
    except ValueError:
        return 0.0


def provider_caps() -> dict:
    return {k: float(v) for k, v in _env_json("EVAL_PROVIDER_CAP_USD").items()}


def note_call(role: str, provider_name: str, model: str, cost: float, ok: bool,
              error: str = "") -> None:
    """Record one call's dollars and one seat's health. Called from the seam."""
    _SPENT["total"] = _SPENT.get("total", 0.0) + max(0.0, cost)
    if provider_name:
        _SPENT[provider_name] = _SPENT.get(provider_name, 0.0) + max(0.0, cost)
    if role:
        _SEAT[role] = {"ok": ok, "at": time.time(), "error": error[:120],
                       "model": model}


def note_fallback(role: str = "") -> None:
    """A configured model failed and a rule-based line stood in for it. Under eval that
    is not a rescue, it is a data point: fairness says a model that errors scores the
    error, never the fallback's competence (EVAL.md §5 / ARENA.md §6). We keep the
    fallback — a live settlement must not stall on a provider hiccup — and COUNT it, so
    a scorecard can never mistake the rules' work for the model's."""
    _FALLBACKS["n"] = _FALLBACKS.get("n", 0) + 1
    if role:
        _FALLBACKS[role] = _FALLBACKS.get(role, 0) + 1


def fallbacks(role: str = "") -> int:
    return _FALLBACKS.get(role or "n", 0)


def spent_usd(provider_name: str = "") -> float:
    return round(_SPENT.get(provider_name or "total", 0.0), 6)


def budget_state() -> dict:
    limit = budget_usd()
    spent = spent_usd()
    caps = provider_caps()
    breached = [n for n, cap in caps.items() if spent_usd(n) >= cap > 0]
    return {"limit": limit, "spent": round(spent, 4),
            "remaining": round(limit - spent, 4) if limit else None,
            "over": bool(limit and spent >= limit) or bool(breached),
            "provider_caps": caps, "breached_providers": breached}


def check_budget(model: str = "") -> None:
    """Raised at the seam before a call: the ceiling is structural, like the token cap.

    A ceiling over an UNPRICED model is refused rather than enforced. Counting an
    unpriced call as $0 would let a run sail past its ceiling while the page reported
    a tidy $0.00 — a budget you cannot measure is not a budget, and pretending
    otherwise is worse than having none."""
    limit = budget_usd()
    if not limit:
        return
    if model and unpriced(model):
        raise BudgetExceeded(
            f"'{model}' is unpriced and EVAL_BUDGET_USD is set — load a price table "
            f"(MODEL_PRICES or /providers) or unset the ceiling. This platform will not "
            f"enforce a budget it cannot measure.")
    st = budget_state()
    if st["over"]:
        why = (f"provider cap reached: {', '.join(st['breached_providers'])}"
               if st["breached_providers"]
               else f"${st['spent']:.4f} of ${st['limit']:.2f} spent")
        raise BudgetExceeded(why)


def preflight(expected_calls: int, model: str = "", avg_prompt: int = 0,
              avg_completion: int = 0) -> dict:
    """Estimate a run's cost before it starts (ARENA.md §5): expected calls × OBSERVED
    average tokens × price.

    'Observed' is load-bearing. With no call history and no explicit averages there is
    nothing to estimate from, so this returns estimate_usd=None and says why, rather
    than multiplying a plausible-looking guess into a number an operator would trust.
    An unpriced model likewise yields no estimate — and, with a ceiling set, a refusal."""
    basis, samples = "given", 0
    if not (avg_prompt and avg_completion):
        try:
            import anchor
            st = anchor.model_calls_stats()
            samples = st.get("calls", 0)
            if samples:
                avg_prompt = avg_prompt or round(st.get("prompt_tokens", 0) / samples)
                avg_completion = avg_completion or round(st.get("completion_tokens", 0) / samples)
                basis = f"observed over {samples:,} logged calls"
        except Exception:
            samples = 0
    model = model or (resolve("default") or {}).get("model", "")
    limit = budget_usd()
    out = {"model": model, "calls": expected_calls, "avg_prompt": avg_prompt,
           "avg_completion": avg_completion, "limit": limit,
           "unpriced": bool(model) and unpriced(model), "basis": basis,
           "samples": samples, "estimate_usd": None, "refused": False, "why": ""}
    if not (avg_prompt and avg_completion):
        out["why"] = ("no call history to estimate from — run once without a ceiling, "
                      "or pass explicit average token counts")
        return out
    if not model:
        out["why"] = "no model resolved for the default role — nothing to price"
        return out
    if out["unpriced"]:
        out["why"] = f"'{model}' is unpriced — load a price table to get a cost estimate"
        out["refused"] = bool(limit)      # a ceiling we cannot measure is not enforced
        return out
    est = cost_usd(model, avg_prompt * expected_calls, avg_completion * expected_calls)
    out["estimate_usd"] = round(est, 4)
    out["refused"] = bool(limit and est > limit)
    return out


# ── outages abstain, they never substitute (Article X.3) ─────────────────────────

OFFLINE_WINDOW_S = 300          # a seat stays abstaining until a call succeeds again


def offline_seats(now: float | None = None) -> list:
    """Board seats that cannot vote — because their model failed recently, OR because
    their assignment is unusable at all. Their ballot is *unknown*, and the reason says
    so; borrowing another model would change who governs, in secret.

    A misconfigured seat counts from the moment it is misconfigured, not from the first
    failed call: a chair nobody can sit in is empty whether or not anyone has tried it."""
    now = now or time.time()
    out = []
    for r in ("Prudence", "Growth", "Ledger"):
        failed_recently = (not _SEAT.get(r, {}).get("ok", True)
                           and now - _SEAT.get(r, {}).get("at", 0) < OFFLINE_WINDOW_S)
        if failed_recently or misconfigured(r):
            out.append(r)
    return out


def seat_status() -> dict:
    return {r: dict(v) for r, v in _SEAT.items()}


def model_for_role(role: str) -> str:
    p = resolve(role)
    if p:
        return p["model"]
    try:
        import brain
        return brain.brain_name()
    except Exception:
        return ""


def model_for_actor(actor: str) -> str:
    """Which mind made this decision. Actors are free text ('board', 'Prudence',
    'governor', 'vil-03'); anything unrecognised is the default brain."""
    a = (actor or "").strip()
    for seat in ("Prudence", "Growth", "Ledger"):
        if a.lower().startswith(seat.lower()):
            return model_for_role(seat)
    if a.lower().startswith(("gov", "chief", "herald", "board")):
        return model_for_role("governor" if not a.lower().startswith("board") else "default")
    if a.lower().startswith(("dev-", "worker")):
        return model_for_role("worker")
    if a.lower().startswith(("vil", "fleet", "scout")):
        return model_for_role("fleet")
    return model_for_role("default")


def reset_run_state() -> None:
    """Clear the per-process spend, seat health and fallback count — between eval runs."""
    _SPENT.clear()
    _SPENT["total"] = 0.0
    _SEAT.clear()
    _FALLBACKS.clear()
    _FALLBACKS["n"] = 0
