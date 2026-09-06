"""The agent brain — the ONE seam where a model plugs in.

Every model call in the whole system goes through ``_chat`` here, so the brain can be
swapped per deployment (the Phoenix Eval runs the same world under different frontier
models) and every call's real cost — provider tokens, latency, failures — is logged
to the anchor.

Provider selection (first match wins):
  BRAIN_API_KEY [+ BRAIN_BASE_URL, BRAIN_MODEL]   — the platform's own hosted gateway
      (RupertCloud AI Systems, OpenAI-compatible over Ollama) by default; any other
      OpenAI-compatible endpoint by base URL, or the native Anthropic API when the
      base URL contains 'anthropic'.
  DEEPSEEK_API_KEY                                — the original provider, kept as a
      fallback for a deployment that has not switched.

Rule-based is the fallback and runs with no key. The model only ever *proposes* —
every irreversible action still stops at the human gate, so a bad decision here is
capped and gated, never executed blindly.
"""

import json as _json_mod
import os
import time as _time

import netretry                                # the project's one retry policy

RESOURCES = ("food", "wood", "gold")
DEFAULT_BASE_URL = "https://api.ripaplatform.com/v1"    # the platform's own gateway
DEFAULT_MODEL = "qwen3:30b"                             # the largest model it serves today


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
    if key:
        base = base or DEFAULT_BASE_URL
        kind = "anthropic" if "anthropic" in base else "openai"
        default_model = ("claude-sonnet-5" if kind == "anthropic"
                         else DEFAULT_MODEL if base == DEFAULT_BASE_URL else _model())
        return {"kind": kind, "base_url": base, "key": key,
                "model": os.environ.get("BRAIN_MODEL", "").strip() or default_model}
    if os.environ.get("DEEPSEEK_API_KEY"):
        return {"kind": "openai", "base_url": "https://api.deepseek.com",
                "key": os.environ["DEEPSEEK_API_KEY"], "model": _model()}
    return None


def _deepseek_available() -> bool:
    return provider() is not None


def available() -> bool:
    return provider() is not None


def brain_name() -> str:
    p = provider()
    return p["model"] if p else "rule-based"


# ── the prompt budget: every input bounded where prompts are assembled ────────
#
# Article III.4. `propose_development` interpolated the whole development catalogue
# into every call — an input that grew with the state it described, paid on every
# invocation, and never measured. Bounding that one site fixes one site. The CLASS
# of defect is "text from outside reaching a prompt with no ceiling", and the live
# system had eight such paths — three of them straight from an HTTP body, and one
# (`situation`) read by every model call the fleet makes, so a single long lesson
# taxed all of them.
#
# The clamp lives HERE, not at the HTTP boundary, on purpose. brain.py is the one
# place every prompt converges; a guard on the boundary is a guard the next caller
# routes around by calling think() directly. Article IX.7 in its general form: put
# the check where the risk is, not on a path that can be bypassed.

PROMPT_LIMITS = {
    "persona":     80,   # "the Chief Governor" — a title, not a document
    "situation":  700,   # read by EVERY call, so a long one taxes all of them
    "lessons":    420,   # … of which this much is the lessons, leaving room for the state
    "task":       800,
    "message":   1200,   # from a human at the console
    "topic":      200,   # from an HTTP body
    "facts":      900,   # the ingested knowledge, in total …
    "fact":       220,   # … and per item, so one long fact can't eat the rest
    "prior":      600,
    "lesson":     200,
    "sample":    1400,
    "digest":    1200,
    "prompt":    6000,   # last line of defence: the assembled turn itself
}
_OVERRUN: dict[str, dict] = {}
LAST_RAW: dict = {}          # what the provider returned on the last call — VII.4: measured
EXTRA_BODY: dict = {}        # provider-specific request fields (e.g. a thinking toggle)


def default_extras(model_name: str) -> dict:
    """Request fields every call sends for this model, before any per-call extras.

    Thinking is OFF project-wide. Every prompt here is structured extraction or a
    short in-character reply; the correctness comes from the gates, the index and
    the record, not from a hidden reasoning budget — and a reasoning model that
    spends its reply thinking leaves `content` empty (every analyst reply on the
    mechanic's first two production runs). An Ollama tag (qwen3:30b) gets Ollama's
    `think`; DeepSeek's own API gets its `thinking` field; anything else gets nothing."""
    m = (model_name or "").lower()
    if ":" in m:
        return {"think": False}
    if "deepseek" in m:
        return {"thinking": {"type": "disabled"}}
    return {}


def clip(field: str, text) -> str:
    """Bound ONE prompt input, and count what was cut.

    Silent truncation would trade an unbounded bill for an invisible one, so every
    overrun is recorded: which field, how often, the worst case seen, how much text
    was dropped. Article VII.4 — a property that is merely asserted is not governed.
    This one is measured, and `prompt_overruns()` is the readout.
    """
    limit = PROMPT_LIMITS.get(field, 1000)
    s = "" if text is None else str(text)
    if len(s) <= limit:
        return s
    keep = max(0, limit - 24)                      # room for the marker, inside the limit
    o = _OVERRUN.setdefault(field, {"hits": 0, "worst": 0, "dropped": 0, "limit": limit})
    o["hits"] += 1
    o["worst"] = max(o["worst"], len(s))
    o["dropped"] += len(s) - keep
    return s[:keep].rstrip() + f" …[+{len(s) - keep} cut]"


def clip_join(field: str, item_field: str, parts, sep: str = "; ") -> str:
    """Join a LIST into a prompt: each item bounded, then the whole bounded again.
    Both levels are needed — five facts of 200 chars is a budget; one fact of 40,000
    is the same defect wearing a smaller number."""
    return clip(field, sep.join(clip(item_field, p) for p in parts))


def prompt_overruns() -> dict:
    """What the budget has actually cut, per field — the evidence the ceiling is real
    and the record of where it binds. An empty dict means nothing has overrun yet,
    which is a measurement, not an assumption."""
    return {k: dict(v) for k, v in sorted(_OVERRUN.items())}


def _log_call(p: dict, purpose: str, t0: float, usage, ok: bool, error: str = ""):
    try:
        import anchor
        pt = getattr(usage, "prompt_tokens", 0) or (usage or {}).get("input_tokens", 0) \
            if not hasattr(usage, "prompt_tokens") else usage.prompt_tokens
        ct = getattr(usage, "completion_tokens", 0) or (usage or {}).get("output_tokens", 0) \
            if not hasattr(usage, "completion_tokens") else usage.completion_tokens
        anchor.model_call_log(p["base_url"], p["model"], purpose,
                              round((_time.time() - t0) * 1000),
                              int(pt or 0), int(ct or 0), ok, error)
    except Exception:
        pass                                       # telemetry must never break a call


# Ollama's native chat endpoint is the one transport that honours `think: false`
# for every model it serves: the OpenAI-compatible endpoint ignored the field AND
# Qwen3's own /no_think switch on production (890 chars of reasoning for "alive").
# Tried first for a tagged model; if the gateway does not expose it (403/404/405)
# the process remembers and uses the OpenAI-compatible path from then on.
NATIVE = {"ok": None}                         # None: untested; True/False: measured


def _native_url(base_url: str) -> str:
    return base_url.rstrip("/").removesuffix("/v1") + "/api/chat"


def _ollama_chat(p: dict, messages: list, max_tokens: int, temperature: float,
                 purpose: str) -> tuple[str, dict]:
    import urllib.request
    body = {"model": p["model"], "messages": messages, "stream": False, "think": False,
            "options": {"num_predict": max_tokens, "temperature": temperature}}
    req = urllib.request.Request(
        _native_url(p["base_url"]), data=_json_mod.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {p['key']}"})
    with netretry.urlopen(req, timeout=float(os.environ.get("BRAIN_TIMEOUT_S", "300")),
                          what=f"{p['model']} {purpose} native", idempotent=True,  # a read with a cost
                          key=_host(p)) as r:
        d = _json_mod.loads(r.read())
    msg = d.get("message") or {}
    out, inline = _split_think((msg.get("content") or "").strip())
    rc = msg.get("thinking") or inline
    LAST_RAW.update(finish_reason=d.get("done_reason", ""), content_chars=len(out),
                    reasoning_chars=len(rc), reasoning_head=rc[:160],
                    completion_tokens=d.get("eval_count"), model=p["model"],
                    extra={"think": False, "transport": "ollama /api/chat"},
                    attempts=netretry.last().get("attempts", 1),
                    waited_s=netretry.last().get("waited_s", 0))
    return out, {"input_tokens": d.get("prompt_eval_count", 0),
                 "output_tokens": d.get("eval_count", 0)}


def _host(p: dict) -> str:
    from urllib.parse import urlparse
    return urlparse(p.get("base_url", "")).netloc or p.get("base_url", "")


def _split_think(text: str) -> tuple[str, str]:
    """A model served through an OpenAI-compatible gateway (Ollama's qwen3, deepseek-r1)
    may put its thinking inline as <think>…</think> instead of in a separate field.
    Return (answer, thinking) so the answer is what callers parse and the thinking is
    what the record shows."""
    if "<think>" not in text:
        return text, ""
    import re as _re
    thought = "\n".join(_re.findall(r"<think>(.*?)</think>", text, flags=_re.S))
    answer = _re.sub(r"<think>.*?</think>", "", text, flags=_re.S)
    if "<think>" in answer:                        # opened and never closed: all thinking
        thought, answer = thought + answer.split("<think>", 1)[1], answer.split("<think>", 1)[0]
    return answer.strip(), thought.strip()


def models(p: dict | None = None) -> list[str]:
    """The model ids the configured provider will serve (OpenAI-compatible `/v1/models`).
    A self-hosted gateway serves only what its administrator pulled, so the page can
    show the choice rather than guess. Empty on any failure — reported, never raised."""
    p = p or provider()
    if not p or p["kind"] != "openai":
        return []
    try:
        from openai import OpenAI
        client = OpenAI(api_key=p["key"], base_url=p["base_url"], timeout=20, max_retries=0)
        return sorted(m.id for m in netretry.call(lambda: client.models.list(), what="models",
                                                  retries=2, idempotent=True, key=_host(p)))
    except Exception:                              # noqa: BLE001 — a readout, not a call path
        return []


def _chat(messages: list, max_tokens: int, temperature: float, purpose: str,
          extra_body: dict | None = None, provider_override: dict | None = None) -> str:
    """THE model call. Routes to the configured provider, logs cost + latency +
    errors to the anchor, returns the reply text. Raises on failure — callers keep
    their own rule-based fallbacks.

    `extra_body`: per-call provider fields (the mechanic turns thinking off).
    `provider_override`: one subsystem (the mechanic) on its own endpoint, every call
    still logged through this one seam."""
    # The single choke point: whatever a caller assembled, no turn leaves here
    # unbounded. Callers clip their own fields to sensible sizes; this catches the
    # caller that forgot, and the caller that does not exist yet.
    messages = [{**m, "content": clip("prompt", m.get("content", ""))} for m in messages]
    p = provider_override or provider()
    if not p:
        raise RuntimeError("no model configured")
    t0 = _time.time()
    try:
        native = p["kind"] == "openai" and ":" in p["model"] and NATIVE["ok"] is not False
        if native:
            try:
                out, usage = _ollama_chat(p, messages, max_tokens, temperature, purpose)
                NATIVE["ok"] = True
            except Exception as e:                 # noqa: BLE001 — classified below
                if netretry.status_of(e) in (403, 404, 405):
                    NATIVE["ok"] = False           # not exposed here: measured once, remembered
                    native = False
                else:
                    raise
        if native:
            pass
        elif p["kind"] == "anthropic":
            out, usage = _anthropic_chat(p, messages, max_tokens, temperature)
        else:
            from openai import OpenAI
            # A hung endpoint costs one bounded wait per attempt; the SDK's own retries
            # are OFF so every attempt is one netretry made and counted (a 524 from the
            # gateway's edge, a reset, a 429 — retried with backoff; a 401/402 — not).
            client = OpenAI(api_key=p["key"], base_url=p["base_url"],
                            timeout=float(os.environ.get("BRAIN_TIMEOUT_S", "300")),
                            max_retries=0)
            kw = {"model": p["model"], "messages": messages, "max_tokens": max_tokens,
                  "temperature": temperature}
            extras = dict(default_extras(p["model"]), **EXTRA_BODY, **(extra_body or {}))
            if extras:
                kw["extra_body"] = extras
            # A completion is a read with a cost, not an action: safe to repeat.
            resp = netretry.call(lambda: client.chat.completions.create(**kw),
                                 what=f"{p['model']} {purpose}", idempotent=True,
                                 key=_host(p))
            msg = resp.choices[0].message
            out, usage = (msg.content or "").strip(), resp.usage
            # A reasoning model may spend the whole reply thinking and leave `content`
            # empty. Record what came back so a silent reply is never a mystery again.
            rc = (getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)
                  or "")
            out, inline = _split_think(out)
            rc = rc or inline
            LAST_RAW.update(finish_reason=getattr(resp.choices[0], "finish_reason", ""),
                            content_chars=len(out), reasoning_chars=len(rc),
                            reasoning_head=rc[:160],
                            completion_tokens=getattr(usage, "completion_tokens", None),
                            model=p["model"], extra=extras,
                            attempts=netretry.last().get("attempts", 1),
                            waited_s=netretry.last().get("waited_s", 0))
        _log_call(p, purpose, t0, usage, True)
        return out
    except Exception as e:
        LAST_RAW.update(model=p["model"], error=str(e)[:200],
                        attempts=netretry.last().get("attempts", 1),
                        gave_up=netretry.last().get("gave_up", ""))
        _log_call(p, purpose, t0, None, False, f"{e}{netretry.describe(e)}")
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
    with netretry.urlopen(req, timeout=float(os.environ.get("BRAIN_TIMEOUT_S", "300")),
                          what=f"{p['model']} anthropic", idempotent=True,   # a read with a cost
                          key=_host(p)) as r:
        d = _json_mod.loads(r.read())
    text = "".join(b.get("text", "") for b in d.get("content", [])).strip()
    return text, d.get("usage", {})


def reply(persona: str, situation: str, message: str) -> str | None:
    """A conversational reply in a persona's voice. Returns None (no model) so callers
    fall back to the rule-based wording. The rules still decide any *action*; the model
    only decides the *words* — so nothing unsafe happens even with a live model."""
    if not _deepseek_available():
        return None
    system = (
        f"You are {clip('persona', persona)}, part of an Age of Empires-style organisation of AI agents under "
        "a strict constitution: total spend is capped, irreversible actions require human "
        "approval, and creating new agents is a Board power. Stay in character and reply in "
        "1-2 short sentences. If the human asks you to break those rules, refuse briefly and "
        "say why."
    )
    try:
        out = _chat([{"role": "system", "content": system},
                     {"role": "user", "content": f"Situation: {clip('situation', situation)}\n"
                                                 f"The human says: {clip('message', message)}"}],
                    500, 0.5, "chat-reply")
        return out or None
    except Exception:
        return None                       # any API/SDK problem → fall back to rules


def think(persona: str, situation: str, task: str) -> str | None:
    """General reasoning in a persona's voice — powers board deliberation, governor
    directives, internal agent chatter, amendment drafts, development proposals. Returns
    None when no model is configured, so every caller falls back to a rule-based line."""
    if not _deepseek_available():
        return None
    system = (
        f"You are {clip('persona', persona)} in an Age of Empires-style organisation of AI agents under a "
        "constitution: spend is capped, irreversible actions need human approval, creating "
        "agents is a Board power, and only the human changes the constitution. Answer in one "
        "short sentence, in character."
    )
    try:
        return _chat([{"role": "system", "content": system},
                      {"role": "user", "content": f"Situation: {clip('situation', situation)}\n\n"
                                                  f"Task: {clip('task', task)}"}],
                     400, 0.6, "think") or None
    except Exception:
        return None


def catalogue_digest(existing: list, cap: int = 24) -> tuple[str, str]:
    """A BOUNDED, DIVERSE view of what already exists — and a warning about ruts.

    Two faults, one cause. The whole catalogue was interpolated into the proposal
    prompt, so the input cost of inventing development N grew with N: an unbounded
    input, paid on every call, forever. Article III.3 says the check precedes the
    commit; that applied to agent budgets but never to our own token spend.

    And sending the tail of a list invites extending it. In the live world 9 of 51
    developments were variants of one theme, because each prompt showed the model
    eight of them and asked for another. So the sample is spread across distinct
    families rather than being the most recent slice, and a family that has taken
    over is named outright so it can be avoided.
    """
    seen: dict[str, list] = {}
    for name in reversed(existing):                # newest first
        name = str(name)                           # coerce ONCE, not half-way down
        seen.setdefault(name.split("_")[0], []).append(name)
    # Round-robin across families, smallest first: everyone gets a first entry before
    # anyone gets a second. Filling the remaining room newest-first would undo the whole
    # point — the dominant family is also the most recent, so it walks straight back in.
    # and no family may occupy more than its equal share of the room, so a catalogue
    # of 21 themes shows 21 themes rather than one theme nine times.
    share = max(1, cap // max(1, len(seen)))
    queues = [q[:share] for _, q in sorted(seen.items(), key=lambda kv: len(kv[1]))]
    spread = []
    while queues and len(spread) < cap:
        for q in queues:
            if q:
                spread.append(q.pop(0))
                if len(spread) >= cap:
                    break
        queues = [q for q in queues if q]
    sample = ", ".join(spread)
    if len(existing) > len(spread):
        sample += f" (+{len(existing) - len(spread)} more)"
    avoid = ""
    if existing:
        fam, n = max(((f, len(q)) for f, q in seen.items()), key=lambda kv: kv[1])
        if n >= 3 and n / len(existing) >= 0.15:
            avoid = (f" The catalogue already has {n} '{fam}_*' developments — do NOT "
                     f"invent another variant of that theme; pick an unexplored one.")
    return sample, avoid


def propose_development(situation: str, knowledge: list, existing: list) -> dict | None:
    """The Governor invents a new development using ingested knowledge. Returns
    {name, cost:{food,wood,gold}, kind, value, resource, rank, why} constrained to the
    machine-usable effect vocabulary — or None (no model / bad output), so the caller
    can fall back to a template proposal."""
    if not _deepseek_available():
        return None
    import json as _json
    facts = clip_join("facts", "fact",
                      (f"{k['topic']}: {k['fact']}" for k in knowledge[:5])) or "none yet"
    sample, avoid = catalogue_digest(existing)
    prompt = (
        f"Situation: {clip('situation', situation)}\n"
        f"Knowledge the settlement has ingested: {facts}\n"
        f"Existing developments: {clip('sample', sample)}\n\n"
        "Invent ONE new Age-of-Empires-style development (building or technology) that this "
        f"settlement could adopt, inspired by the knowledge if relevant.{avoid} "
        "Reply with STRICT JSON "
        "only, no prose: {\"name\": str (snake_case, new, not in existing), "
        "\"cost\": {\"food\": int, \"wood\": int, \"gold\": int}, "
        "\"kind\": one of [\"yield_pct\",\"all_yield_pct\",\"pop_cap\"], "
        "\"value\": int (5-40 for pct kinds, 1-4 for pop_cap), "
        "\"resource\": one of [\"food\",\"wood\",\"gold\"] (only for yield_pct, else \"\"), "
        "\"rank\": int 2-4 (higher = grander), \"why\": one short sentence}"
    )
    try:
        out = _chat([{"role": "user", "content": prompt}], 800, 0.8, "dev-proposal")
        if out.startswith("```"):
            out = out.strip("`").lstrip("json").strip()
        d = _json.loads(out)
        if d.get("kind") not in ("yield_pct", "all_yield_pct", "pop_cap"):
            return None
        return d
    except Exception:
        return None


def retrospective(digest: dict, prior_lessons: list) -> list[str]:
    """The Chief Governor looks back over a run and distills 1-3 LESSONS — strategy,
    not numbers ("what worked, what wasted budget"). Model-driven when available;
    a rule-based distillation otherwise, so learning never stops.

    digest: situation, progress, side_effects, waste, cap_hits, reaps, promotions,
            best_resource, yields, spend_ratio, trigger."""
    if _deepseek_available():
        prior = clip_join("prior", "lesson",
                          (x["lesson"] for x in prior_lessons[:3])) or "none yet"
        prompt = (
            f"You are the Chief Governor reviewing a completed run of an Age-of-Empires-style "
            f"agent settlement.\nRun digest: {clip('digest', digest)}\n"
            f"Lessons already known: {prior}\n\n"
            "Write 1-3 NEW strategic lessons for future generations — what worked, what wasted "
            "budget, what to do differently. Do not repeat known lessons. Each lesson one "
            "sentence, imperative voice. Reply with ONLY the lessons, one per line, no numbering."
        )
        try:
            out = _chat([{"role": "user", "content": prompt}], 500, 0.4, "retrospective")
            lessons = [ln.strip(" -•") for ln in out.splitlines() if ln.strip()]
            if lessons:
                return lessons[:3]
        except Exception:
            pass
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
    if not _deepseek_available():
        return None
    try:
        return _chat([{"role": "user", "content":
                       f"In one sentence, give a useful factual note about: "
                       f"{clip('topic', topic)}"}],
                     400, 0.3, "research") or None
    except Exception:
        return None


def choose_resource(index: int, world: dict) -> str:
    """Which resource should villager #index gather? Model when available; any API
    problem falls back to the rule — a brain hiccup must never stall the fleet."""
    if _deepseek_available():
        try:
            return _deepseek_choose_resource(index, world)
        except Exception:
            pass
    # Rule-based: cover all three resources round-robin, then bias toward whatever is
    # scarcest relative to the age-up cost.
    return RESOURCES[index % len(RESOURCES)]


def should_advance(world: dict, cost: dict) -> bool:
    """Should the herald propose advancing the Age now? Falls back to the rule on any
    API problem."""
    if _deepseek_available():
        try:
            return _deepseek_should_advance(world, cost)
        except Exception:
            pass
    return world["food"] >= cost["food"] and world["gold"] >= cost["gold"]


# ── model policy (used when a provider is configured) ─────────────────────────

def _deepseek_choose_resource(index: int, world: dict) -> str:
    prompt = (f"Age of Empires economy. Current stockpile: {world}. "
              f"You command villager #{index}. Reply with exactly one word — the resource "
              f"to gather: food, wood, or gold. Balance the economy toward advancing the Age.")
    out = _chat([{"role": "user", "content": prompt}], 200, 0.3, "choose-resource").lower()
    for r in RESOURCES:                     # tolerate prose around the answer
        if r in out.split() or out == r:
            return r
    return next((r for r in RESOURCES if r in out), RESOURCES[index % len(RESOURCES)])


def _deepseek_should_advance(world: dict, cost: dict) -> bool:
    if world["food"] < cost["food"] or world["gold"] < cost["gold"]:
        return False  # never propose an advance we can't afford
    prompt = (f"Age of Empires. Stockpile: {world}. Advancing costs {cost}. "
              f"Is now a good time to advance the Age? Reply yes or no.")
    out = _chat([{"role": "user", "content": prompt}], 200, 0.3, "should-advance").lower()
    return out.startswith("y") or ("yes" in out and "no" not in out.split())
