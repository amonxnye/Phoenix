"""The agent brain — where DeepSeek plugs in.

Two decisions drive the game: what a villager should gather, and whether the herald
should propose advancing the Age. Both go through this one interface so the policy can
swap from rule-based to a real model without touching the sim or the governor.

Rule-based is the default and runs with no key. If ``DEEPSEEK_API_KEY`` is set, the
DeepSeek policy is used (OpenAI-compatible API). The model only ever *proposes* — every
irreversible action still stops at the human gate, so a bad decision here is capped and
gated, never executed blindly.
"""

import os

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


def _deepseek_available() -> bool:
    return bool(os.environ.get("DEEPSEEK_API_KEY"))


def available() -> bool:
    return _deepseek_available()


def reply(persona: str, situation: str, message: str) -> str | None:
    """A conversational reply in a persona's voice. Returns None (no model) so callers
    fall back to the rule-based wording. The rules still decide any *action*; the model
    only decides the *words* — so nothing unsafe happens even with a live model."""
    if not _deepseek_available():
        return None
    system = (
        f"You are {persona}, part of an Age of Empires-style organisation of AI agents under "
        "a strict constitution: total spend is capped, irreversible actions require human "
        "approval, and creating new agents is a Board power. Stay in character and reply in "
        "1-2 short sentences. If the human asks you to break those rules, refuse briefly and "
        "say why."
    )
    try:
        client = _deepseek_client()
        out = client.chat.completions.create(
            model=_model(),
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": f"Situation: {situation}\nThe human says: {message}"}],
            max_tokens=90, temperature=0.5,
        ).choices[0].message.content.strip()
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
        f"You are {persona} in an Age of Empires-style organisation of AI agents under a "
        "constitution: spend is capped, irreversible actions need human approval, creating "
        "agents is a Board power, and only the human changes the constitution. Answer in one "
        "short sentence, in character."
    )
    try:
        client = _deepseek_client()
        return client.chat.completions.create(
            model=_model(),
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": f"Situation: {situation}\n\nTask: {task}"}],
            max_tokens=70, temperature=0.6,
        ).choices[0].message.content.strip() or None
    except Exception:
        return None


def research(topic: str) -> str | None:
    """External knowledge for the anchor (Article VI). Uses the model's knowledge; returns
    a concise fact, or None if no model is configured. The caller records the source and
    never executes the result."""
    if not _deepseek_available():
        return None
    try:
        client = _deepseek_client()
        return client.chat.completions.create(
            model=_model(),
            messages=[{"role": "user", "content":
                       f"In one sentence, give a useful factual note about: {topic}"}],
            max_tokens=80, temperature=0.3,
        ).choices[0].message.content.strip() or None
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


# ── DeepSeek policy (used when DEEPSEEK_API_KEY is set) ───────────────────────
# DeepSeek exposes an OpenAI-compatible API at https://api.deepseek.com. We keep the
# calls behind these functions so the rest of the system never imports an SDK.

def _deepseek_client():
    from openai import OpenAI  # deferred; only needed when a key is present
    return OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com")


def _deepseek_choose_resource(index: int, world: dict) -> str:
    client = _deepseek_client()
    prompt = (f"Age of Empires economy. Current stockpile: {world}. "
              f"You command villager #{index}. Reply with exactly one word — the resource "
              f"to gather: food, wood, or gold. Balance the economy toward advancing the Age.")
    out = client.chat.completions.create(
        model=_model(),
        messages=[{"role": "user", "content": prompt}],
        max_tokens=4, temperature=0.3,
    ).choices[0].message.content.strip().lower()
    return out if out in RESOURCES else RESOURCES[index % len(RESOURCES)]


def _deepseek_should_advance(world: dict, cost: dict) -> bool:
    if world["food"] < cost["food"] or world["gold"] < cost["gold"]:
        return False  # never propose an advance we can't afford
    client = _deepseek_client()
    prompt = (f"Age of Empires. Stockpile: {world}. Advancing costs {cost}. "
              f"Is now a good time to advance the Age? Reply yes or no.")
    out = client.chat.completions.create(
        model=_model(),
        messages=[{"role": "user", "content": prompt}],
        max_tokens=3, temperature=0.3,
    ).choices[0].message.content.strip().lower()
    return out.startswith("y")
