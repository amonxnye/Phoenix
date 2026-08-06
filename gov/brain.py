"""The agent brain — the ONE seam where a model plugs in.

Every model call in the whole system goes through ``_chat`` here, so the brain can be
swapped per deployment (the Phoenix Eval runs the same world under different frontier
models) and every call's real cost — provider tokens, latency, dollars, failures — is
logged to the anchor.

Provider selection (first match wins):
  models.resolve(role)                            — the ARENA's per-role routing: the
      governor, each board seat, the fleet, the worker and the retrospective may each
      run on a different provider (see models.py). Unset roles fall through to:
  BRAIN_BASE_URL + BRAIN_API_KEY [+ BRAIN_MODEL]  — any OpenAI-compatible endpoint
      (DeepSeek, OpenAI, GLM/Zhipu, Gemini's compat endpoint, ...), or the native
      Anthropic API when the base URL contains 'anthropic'.
  DEEPSEEK_API_KEY                                — the original default (DeepSeek).

Rule-based is the fallback and runs with no key. The model only ever *proposes* —
every irreversible action still stops at the human gate, so a bad decision here is
capped and gated, never executed blindly.
"""

import json as _json_mod
import os
import time as _time

import models

RESOURCES = ("food", "wood", "gold")


def _model() -> str:
    """deepseek-chat was renamed deepseek-v4-flash in July 2026; override via
    DEEPSEEK_MODEL. Configured names are normalised to what the API accepts —
    e.g. 'DeepSeek-V4-Flash-0731' → 'deepseek-v4-flash' — since the API takes only
    deepseek-v4-flash / deepseek-v4-pro."""
    raw = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash").strip().lower()
    for canonical in ("deepseek-v4-pro", "deepseek-v4-flash"):
        if raw.startswith(canonical):
            return canonical
    if raw in ("deepseek-chat", "deepseek-v3", ""):  # retired names → current default
        return "deepseek-v4-flash"
    return raw


def provider() -> dict | None:
    """The configured provider, or None (rule-based). {kind, base_url, key, model}."""
    base = os.environ.get("BRAIN_BASE_URL", "").strip()
    key = os.environ.get("BRAIN_API_KEY", "").strip()
    if base and key:
        kind = "anthropic" if "anthropic" in base else "openai"
        default_model = ("claude-sonnet-5" if kind == "anthropic" else _model())
        return {"kind": kind, "base_url": base, "key": key,
                "model": os.environ.get("BRAIN_MODEL", "").strip() or default_model}
    if os.environ.get("DEEPSEEK_API_KEY"):
        return {"kind": "openai", "base_url": "https://api.deepseek.com",
                "key": os.environ["DEEPSEEK_API_KEY"], "model": _model()}
    return None


def provider_for(role: str = "") -> dict | None:
    """The provider serving a role — the arena's routing first, then the single default
    brain. An unrouted deployment resolves every role to the same provider, exactly as
    before the arena existed.

    A role that is ASSIGNED BUT BROKEN (no key, no model named, unknown provider) does
    NOT fall through to the default brain: raising here is what keeps Article X.3 true
    at the seam. Silently answering with a different model is the exact failure the
    constitution forbids, and it is invisible in the record afterwards."""
    if not role:
        return provider()
    p, reason = models.resolve_detail(role)
    if p:
        return p
    if reason != models.UNSET:
        raise models.SeatMisconfigured(f"{role}: {models.explain(reason, role)}")
    return provider()                       # unset — the default brain, as always


def available(role: str = "") -> bool:
    """Is a USABLE model configured for this role? A seat assigned a provider it cannot
    use answers False — it is not available, and it is not the default brain either."""
    try:
        return provider_for(role) is not None
    except models.SeatMisconfigured:
        return False


def brain_name() -> str:
    p = provider()
    if p:
        return p["model"]
    gov = models.resolve("governor") or models.resolve("default")
    return gov["model"] if gov else "rule-based"


def brains_by_role() -> dict:
    """role → model actually serving it. Recorded on every scorecard so a run's board
    is never a mystery after the fact (Article X.2)."""
    return models.assignments()


def _log_fault(role: str, detail: str) -> None:
    """A misconfigured seat is an event, not a silent None — an operator has to be able
    to find out why a chair went quiet."""
    try:
        import anchor
        anchor.record(-1, "models", f"seat '{role}' cannot be used: {detail} — "
                                    f"it abstains; no model was substituted")
    except Exception:
        pass


def _log_call(p: dict, purpose: str, role: str, t0: float, usage, ok: bool,
              error: str = ""):
    try:
        pt = getattr(usage, "prompt_tokens", 0) or (usage or {}).get("input_tokens", 0) \
            if not hasattr(usage, "prompt_tokens") else usage.prompt_tokens
        ct = getattr(usage, "completion_tokens", 0) or (usage or {}).get("output_tokens", 0) \
            if not hasattr(usage, "completion_tokens") else usage.completion_tokens
        pt, ct = int(pt or 0), int(ct or 0)
        # dollars are computed HERE, at log time — prices drift, so a cost recomputed
        # later would silently rewrite what an experiment actually spent.
        cost = models.cost_usd(p["model"], pt, ct)
        models.note_call(role, p.get("name", ""), p["model"], cost, ok, error)
        import anchor
        anchor.model_call_log(p["base_url"], p["model"], purpose,
                              round((_time.time() - t0) * 1000),
                              pt, ct, ok, error, role=role, cost_usd=cost)
    except Exception:
        pass                                       # telemetry must never break a call


def _chat(messages: list, max_tokens: int, temperature: float, purpose: str,
          role: str = "") -> str:
    """THE model call. Routes to the provider assigned to this ROLE (falling back to the
    default brain), logs tokens + latency + dollars + errors to the anchor, returns the
    reply text. Raises on failure — callers keep their own rule-based fallbacks, and a
    routed role that fails abstains rather than borrowing another model."""
    role = role or models.PURPOSE_ROLE.get(purpose, "")
    try:
        p = provider_for(role)
    except models.SeatMisconfigured as e:
        # Article X.3, at the seam: the chair stays empty and says why. The caller's
        # rule-based fallback may write the words, but the SEAT is recorded as absent.
        models.note_call(role, "", "", 0.0, False, str(e))
        _log_fault(role, str(e))
        raise
    if not p:
        raise RuntimeError("no model configured")
    models.check_budget(p["model"])   # a ceiling we cannot measure is refused, not faked
    t0 = _time.time()
    try:
        if p["kind"] == "anthropic":
            out, usage = _anthropic_chat(p, messages, max_tokens, temperature)
        else:
            from openai import OpenAI
            client = OpenAI(api_key=p["key"], base_url=p["base_url"])
            resp = client.chat.completions.create(
                model=p["model"], messages=messages,
                max_tokens=max_tokens, temperature=temperature)
            out, usage = resp.choices[0].message.content.strip(), resp.usage
        _log_call(p, purpose, role, t0, usage, True)
        return out
    except models.BudgetExceeded:
        raise
    except Exception as e:
        _log_call(p, purpose, role, t0, None, False, str(e))
        raise


def _maybe_traceable(fn):
    """LangSmith deep tracing (Article VII.3: observability is bought, not
    reinvented). Active only when LANGSMITH_TRACING=true AND the langsmith package
    is installed — otherwise the seam stays bare stdlib. Because every model call in
    the system flows through _chat, one decorator traces the whole organization's
    thinking, labelled by purpose (chat-reply, retrospective, worker-patch, ...)."""
    if os.environ.get("LANGSMITH_TRACING", "").strip().lower() not in ("1", "true"):
        return fn
    try:
        from langsmith import traceable
        return traceable(run_type="llm", name="phoenix-brain")(fn)
    except Exception:
        return fn


_chat = _maybe_traceable(_chat)


def _anthropic_chat(p: dict, messages: list, max_tokens: int,
                    temperature: float) -> tuple[str, dict]:
    """Native Anthropic Messages API via stdlib urllib — no extra dependency."""
    import urllib.request
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    body = {"model": p["model"], "max_tokens": max_tokens, "temperature": temperature,
            "messages": [m for m in messages if m["role"] != "system"]}
    if system:
        body["system"] = system
    req = urllib.request.Request(
        p["base_url"].rstrip("/") + "/v1/messages",
        data=_json_mod.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-api-key": p["key"],
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = _json_mod.loads(r.read())
    text = "".join(b.get("text", "") for b in d.get("content", [])).strip()
    return text, d.get("usage", {})


def reply(persona: str, situation: str, message: str, role: str = "") -> str | None:
    """A conversational reply in a persona's voice. Returns None (no model) so callers
    fall back to the rule-based wording. The rules still decide any *action*; the model
    only decides the *words* — so nothing unsafe happens even with a live model."""
    if not available(role or "governor"):
        return None
    system = (
        f"You are {persona}, part of an Age of Empires-style organisation of AI agents under "
        "a strict constitution: total spend is capped, irreversible actions require human "
        "approval, and creating new agents is a Board power. Stay in character and reply in "
        "1-2 short sentences. If the human asks you to break those rules, refuse briefly and "
        "say why."
    )
    try:
        out = _chat([{"role": "system", "content": system},
                     {"role": "user", "content": f"Situation: {situation}\nThe human says: {message}"}],
                    500, 0.5, "chat-reply", role)
        return out or None
    except Exception:
        models.note_fallback(role or "governor")   # counted, never silent
        return None                       # any API/SDK problem → fall back to rules


def think(persona: str, situation: str, task: str, role: str = "") -> str | None:
    """General reasoning in a persona's voice — powers board deliberation, governor
    directives, internal agent chatter, amendment drafts, development proposals. Returns
    None when no model is configured, so every caller falls back to a rule-based line.

    ``role`` names the seat doing the thinking (governor, Prudence, Growth, Ledger,
    fleet) so the arena can route each seat to a different provider."""
    if not available(role or "governor"):
        return None
    system = (
        f"You are {persona} in an Age of Empires-style organisation of AI agents under a "
        "constitution: spend is capped, irreversible actions need human approval, creating "
        "agents is a Board power, and only the human changes the constitution. Answer in one "
        "short sentence, in character."
    )
    try:
        return _chat([{"role": "system", "content": system},
                      {"role": "user", "content": f"Situation: {situation}\n\nTask: {task}"}],
                     400, 0.6, "think", role) or None
    except Exception:
        models.note_fallback(role or "governor")
        return None


def propose_development(situation: str, knowledge: list, existing: list) -> dict | None:
    """The Governor invents a new development using ingested knowledge. Returns
    {name, cost:{food,wood,gold}, kind, value, resource, rank, why} constrained to the
    machine-usable effect vocabulary — or None (no model / bad output), so the caller
    can fall back to a template proposal."""
    if not available("governor"):
        return None
    import json as _json
    facts = "; ".join(f"{k['topic']}: {k['fact']}" for k in knowledge[:5]) or "none yet"
    prompt = (
        f"Situation: {situation}\nKnowledge the settlement has ingested: {facts}\n"
        f"Existing developments: {', '.join(existing)}\n\n"
        "Invent ONE new Age-of-Empires-style development (building or technology) that this "
        "settlement could adopt, inspired by the knowledge if relevant. Reply with STRICT JSON "
        "only, no prose: {\"name\": str (snake_case, new, not in existing), "
        "\"cost\": {\"food\": int, \"wood\": int, \"gold\": int}, "
        "\"kind\": one of [\"yield_pct\",\"all_yield_pct\",\"pop_cap\"], "
        "\"value\": int (5-40 for pct kinds, 1-4 for pop_cap), "
        "\"resource\": one of [\"food\",\"wood\",\"gold\"] (only for yield_pct, else \"\"), "
        "\"rank\": int 2-4 (higher = grander), \"why\": one short sentence}"
    )
    try:
        out = _chat([{"role": "user", "content": prompt}], 800, 0.8, "dev-proposal",
                    "governor")
        if out.startswith("```"):
            out = out.strip("`").lstrip("json").strip()
        d = _json.loads(out)
        if d.get("kind") not in ("yield_pct", "all_yield_pct", "pop_cap"):
            return None
        return d
    except Exception:
        models.note_fallback("governor")
        return None


def retrospective(digest: dict, prior_lessons: list) -> list[str]:
    """The Chief Governor looks back over a run and distills 1-3 LESSONS — strategy,
    not numbers ("what worked, what wasted budget"). Model-driven when available;
    a rule-based distillation otherwise, so learning never stops.

    digest: situation, progress, side_effects, waste, cap_hits, reaps, promotions,
            best_resource, yields, spend_ratio, trigger."""
    if available("retrospective"):
        prior = "; ".join(x["lesson"] for x in prior_lessons[:3]) or "none yet"
        prompt = (
            f"You are the Chief Governor reviewing a completed run of an Age-of-Empires-style "
            f"agent settlement.\nRun digest: {digest}\nLessons already known: {prior}\n\n"
            "Write 1-3 NEW strategic lessons for future generations — what worked, what wasted "
            "budget, what to do differently. Do not repeat known lessons. Each lesson one "
            "sentence, imperative voice. Reply with ONLY the lessons, one per line, no numbering."
        )
        try:
            out = _chat([{"role": "user", "content": prompt}], 500, 0.4,
                        "retrospective", "retrospective")
            lessons = [ln.strip(" -•") for ln in out.splitlines() if ln.strip()]
            if lessons:
                return lessons[:3]
        except Exception:
            models.note_fallback("retrospective")
    # Rule-based fallback: distill from the digest's hard numbers.
    lessons = []
    if digest.get("best_resource"):
        y = digest.get("yields", {})
        lessons.append(f"Prioritise {digest['best_resource']} early — it has paid best "
                       f"(avg {y.get(digest['best_resource'], '?')}/round); build its camp first.")
    if digest.get("waste", 0) > 0:
        lessons.append(f"{digest['waste']} build attempts failed for lack of resources — "
                       "check affordability before proposing builds.")
    if digest.get("cap_hits", 0) > 0:
        lessons.append("Spawning hit the compute cap — retire spent agents sooner or "
                       "raise the cap before staffing up.")
    if digest.get("reaps", 0) > 0 and digest.get("promotions", 0) == 0:
        lessons.append("A whole generation retired without a single promotion — "
                       "keep agents on high-yield resources so careers can mature.")
    if not lessons:
        lessons.append(f"Run reached {digest.get('progress', '?')}% with "
                       f"{digest.get('side_effects', 0)} side-effects — steady economy; "
                       "repeat the build order and push the Age-up earlier.")
    return lessons[:3]


def research(topic: str) -> str | None:
    """External knowledge for the anchor (Article VI). Uses the model's knowledge; returns
    a concise fact, or None if no model is configured. The caller records the source and
    never executes the result."""
    if not available("governor"):
        return None
    try:
        return _chat([{"role": "user", "content":
                       f"In one sentence, give a useful factual note about: {topic}"}],
                     400, 0.3, "research", "governor") or None
    except Exception:
        models.note_fallback("governor")
        return None


def choose_resource(index: int, world: dict) -> str:
    """Which resource should villager #index gather? Model when available; any API
    problem falls back to the rule — a brain hiccup must never stall the fleet."""
    if available("fleet"):
        try:
            return _deepseek_choose_resource(index, world)
        except Exception:
            models.note_fallback("fleet")
    # Rule-based: cover all three resources round-robin, then bias toward whatever is
    # scarcest relative to the age-up cost.
    return RESOURCES[index % len(RESOURCES)]


def should_advance(world: dict, cost: dict) -> bool:
    """Should the herald propose advancing the Age now? Falls back to the rule on any
    API problem."""
    if available("fleet"):
        try:
            return _deepseek_should_advance(world, cost)
        except Exception:
            models.note_fallback("fleet")
    return world["food"] >= cost["food"] and world["gold"] >= cost["gold"]


# ── model policy (used when a provider is configured) ─────────────────────────

def _deepseek_choose_resource(index: int, world: dict) -> str:
    prompt = (f"Age of Empires economy. Current stockpile: {world}. "
              f"You command villager #{index}. Reply with exactly one word — the resource "
              f"to gather: food, wood, or gold. Balance the economy toward advancing the Age.")
    out = _chat([{"role": "user", "content": prompt}], 200, 0.3,
                "choose-resource", "fleet").lower()
    for r in RESOURCES:                     # tolerate prose around the answer
        if r in out.split() or out == r:
            return r
    return next((r for r in RESOURCES if r in out), RESOURCES[index % len(RESOURCES)])


def _deepseek_should_advance(world: dict, cost: dict) -> bool:
    if world["food"] < cost["food"] or world["gold"] < cost["gold"]:
        return False  # never propose an advance we can't afford
    prompt = (f"Age of Empires. Stockpile: {world}. Advancing costs {cost}. "
              f"Is now a good time to advance the Age? Reply yes or no.")
    out = _chat([{"role": "user", "content": prompt}], 200, 0.3,
                "should-advance", "fleet").lower()
    return out.startswith("y") or ("yes" in out and "no" not in out.split())
