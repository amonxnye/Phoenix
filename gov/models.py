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

# Direct, first-class providers (ARENA.md §1). Model names are defaults only — every
# entry is overridable by MODEL_REGISTRY or by a "provider:model" role spec.
BUILTIN = {
    "openai": {"kind": "openai", "base_url": "https://api.openai.com/v1",
               "key_env": "OPENAI_API_KEY", "model": "gpt-5", "tier": "ranked"},
    "gemini": {"kind": "openai",         # Gemini's OpenAI-compatible endpoint
               "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "key_env": "GEMINI_API_KEY", "model": "gemini-2.5-pro", "tier": "ranked"},
    "anthropic": {"kind": "anthropic", "base_url": "https://api.anthropic.com",
                  "key_env": "ANTHROPIC_API_KEY", "model": "claude-sonnet-5",
                  "tier": "ranked"},
    "deepseek": {"kind": "openai", "base_url": "https://api.deepseek.com",
                 "key_env": "DEEPSEEK_API_KEY", "model": "deepseek-v4-flash",
                 "tier": "ranked"},
    # A router is one key and hundreds of models: breadth, not ranked results. Model
    # identity drifts, cost/latency measure the router, and silent failover is exactly
    # what the constitution forbids — so anything served through it is SCOUTING tier.
    "openrouter": {"kind": "openai", "base_url": "https://openrouter.ai/api/v1",
                   "key_env": "OPENROUTER_API_KEY", "model": "", "tier": "scouting"},
}

# Seed price table, $ per 1M tokens, matched by longest model-name prefix. Prices change;
# this is human-maintained (ARENA.md §5) — override wholesale with MODEL_PRICES or the
# console. A model with no entry costs $0 and is reported as unpriced, never guessed.
SEED_PRICES = {
    "gpt-5":            {"in": 1.25, "out": 10.00},
    "gpt-5-mini":       {"in": 0.25, "out": 2.00},
    "gemini-2.5-pro":   {"in": 1.25, "out": 10.00},
    "gemini-2.5-flash": {"in": 0.30, "out": 2.50},
    "claude-opus":      {"in": 15.00, "out": 75.00},
    "claude-sonnet":    {"in": 3.00, "out": 15.00},
    "claude-haiku":     {"in": 0.80, "out": 4.00},
    "deepseek-v4-flash": {"in": 0.28, "out": 0.42},
    "deepseek-v4-pro":  {"in": 0.55, "out": 2.19},
}

ROUTERS = ("openrouter.ai", "/router", "litellm")   # base URLs that resolve elsewhere


class BudgetExceeded(RuntimeError):
    """The run's dollar ceiling is spent. The run halts; it does not finish cheaper —
    finishing on a different model would be a substitution (ARENA.md §5)."""


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


def resolve(role: str) -> dict | None:
    """The provider for a role, or None when the role is unset (the caller then uses the
    default brain — behaviour identical to a Phoenix without an arena).

    Returns {name, kind, base_url, key, model, tier}."""
    if not role:
        return None
    spec = roles().get(role) or roles().get("default", "")
    if not spec:
        return None
    name, _, model = spec.partition(":")
    entry = registry().get(name)
    if not entry:
        return None
    key = (entry.get("key") or os.environ.get(entry.get("key_env", ""), "")).strip()
    if not key:
        return None                       # no key is not a substitution — it is silence
    base = entry.get("base_url", "")
    return {"name": name, "kind": entry.get("kind") or
            ("anthropic" if "anthropic" in base else "openai"),
            "base_url": base, "key": key,
            "model": (model or entry.get("model") or "").strip(),
            "tier": entry.get("tier") or ("scouting" if is_router(base) else "ranked")}


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
    """role → the model actually serving it (or "" when the role falls through to the
    default brain). What the console shows and what every scorecard records."""
    out = {}
    for role in ROLES:
        p = resolve(role)
        out[role] = f"{p['name']}:{p['model']}" if p else ""
    return out


def active() -> bool:
    """Is any role routed? False means this is a plain single-brain deployment."""
    return any(assignments().values())


def tier() -> str:
    """'ranked' only if every routed role is a direct provider. One router anywhere and
    the whole run is scouting — a leaderboard row is only as pinned as its weakest seat."""
    tiers = [resolve(r)["tier"] for r in ROLES if resolve(r)]
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


def check_budget() -> None:
    """Raised at the seam before a call: the ceiling is structural, like the token cap."""
    st = budget_state()
    if st["over"]:
        why = (f"provider cap reached: {', '.join(st['breached_providers'])}"
               if st["breached_providers"]
               else f"${st['spent']:.4f} of ${st['limit']:.2f} spent")
        raise BudgetExceeded(why)


def preflight(expected_calls: int, model: str = "", avg_prompt: int = 0,
              avg_completion: int = 0) -> dict:
    """Estimate a run's cost before it starts (ARENA.md §5): expected calls × observed
    average tokens × price. Observed averages come from the anchor when it has history."""
    if not (avg_prompt and avg_completion):
        try:
            import anchor
            st = anchor.model_calls_stats()
            n = max(1, st.get("calls", 0))
            avg_prompt = avg_prompt or round(st.get("prompt_tokens", 0) / n) or 900
            avg_completion = avg_completion or round(st.get("completion_tokens", 0) / n) or 180
        except Exception:
            avg_prompt, avg_completion = avg_prompt or 900, avg_completion or 180
    model = model or (resolve("default") or {}).get("model", "")
    est = cost_usd(model, avg_prompt * expected_calls, avg_completion * expected_calls)
    limit = budget_usd()
    return {"model": model, "calls": expected_calls, "avg_prompt": avg_prompt,
            "avg_completion": avg_completion, "estimate_usd": round(est, 4),
            "limit": limit, "unpriced": unpriced(model),
            "refused": bool(limit and est > limit)}


# ── outages abstain, they never substitute (Article X.3) ─────────────────────────

OFFLINE_WINDOW_S = 300          # a seat stays abstaining until a call succeeds again


def offline_seats(now: float | None = None) -> list:
    """Board seats whose model failed recently. Their ballot is *unknown*, and the
    reason says so — borrowing another model would change who governs, in secret."""
    now = now or time.time()
    return [r for r in ("Prudence", "Growth", "Ledger")
            if not _SEAT.get(r, {}).get("ok", True)
            and now - _SEAT.get(r, {}).get("at", 0) < OFFLINE_WINDOW_S]


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
