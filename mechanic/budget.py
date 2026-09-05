"""Cost, bounded before it is spent — Charter §7, R12.

The spec's pre-flight gate cannot work as written: review runs at the strong tier
per finding, and the number of findings is unknown until the panel has run. So there
are two gates. The first projects the panel from the unit manifest before any model
call; the second re-projects review once the candidate count is known, and can halt
the run between stages with the panel's work already recorded. A run that halts says
which gate stopped it and what it would have cost.

Prices are assumptions and are labelled as such in the record. They are per million
tokens; tokens are estimated at four characters each.
"""

PRICE = {                                          # $ per 1M tokens: (input, output)
    "cheap": (0.14, 0.28),                         # DeepSeek-class
    "strong": (3.00, 15.00),                       # Sonnet-class
}
DEFAULT_CENTS = 1500                               # the spec's median target: $15
_ASSUMED = dict(PRICE)
PRICED_FOR = "assumed tiers"


def calibrate(model_name: str, base_url: str = "") -> str:
    """One provider serves both tiers today. When it is a cheap-class model, charging
    review at Sonnet prices would halt runs that cost a fraction of the projection —
    the ceiling is meant to bind on money, not on a table. The record names which
    table priced the run.

    A self-hosted model (an Ollama tag such as qwen3:30b, or MECHANIC_PRICE=self-hosted)
    is metered at 0¢: the ceiling is a money ceiling and there is no bill. What bounds
    such a run is structural — the turn ceiling, the per-unit candidate cap, the halt
    limit — and the record says so rather than pretending a price."""
    global PRICED_FOR
    import os
    m = (model_name or "").lower()
    forced = os.environ.get("MECHANIC_PRICE", "").strip().lower()
    if forced == "self-hosted" or (not forced and ":" in m):
        PRICE["strong"] = PRICE["cheap"] = (0.0, 0.0)
        PRICED_FOR = (f"self-hosted ({model_name}): metered at 0¢ — the money ceiling "
                      "does not bind; the structural bounds do")
    elif forced in ("cheap", "strong"):
        PRICE["strong"] = PRICE["cheap"] = _ASSUMED[forced]
        PRICED_FOR = f"forced {forced} tier ({model_name})"
    elif any(k in m for k in ("deepseek", "flash", "mini", "haiku", "small")):
        PRICE["strong"] = PRICE["cheap"] = _ASSUMED["cheap"]
        PRICED_FOR = f"single cheap-class provider ({model_name})"
    else:
        PRICE.update(_ASSUMED)
        PRICED_FOR = "assumed tiers"
    return PRICED_FOR
PANEL_OUT_TOKENS = 1500                            # per analyst call, projected (reasoning included)
REVIEW_IN_TOKENS = 2500                            # candidate + context + facts
REVIEW_OUT_TOKENS = 800
GOVERNOR_OUT_TOKENS = 1500


class OverBudget(Exception):
    def __init__(self, stage: str, projected: int, budget: int):
        super().__init__(f"{stage}: projected {projected}¢ exceeds the {budget}¢ budget")
        self.stage, self.projected, self.budget = stage, projected, budget


def _cents(tier: str, in_tok: int, out_tok: int) -> float:
    pi, po = PRICE[tier]
    return (in_tok * pi + out_tok * po) / 1_000_000 * 100


class Budget:
    def __init__(self, cents: int):
        self.cents = int(cents)
        self.spent = 0.0
        self.by_stage: dict[str, float] = {}
        self.tokens: dict[str, list] = {}         # stage → [in, out]

    def charge(self, stage: str, tier: str, in_tok: int, out_tok: int) -> None:
        c = _cents(tier, max(0, in_tok), out_tok)  # out_tok may be a negative settlement
        self.spent += c
        self.by_stage[stage] = self.by_stage.get(stage, 0.0) + c
        t = self.tokens.setdefault(stage, [0, 0])
        t[0] += max(0, in_tok)
        t[1] += out_tok

    def spent_cents(self) -> int:
        return int(round(self.spent))

    # ── the two gates ────────────────────────────────────────────────────────
    def gate(self, stage: str, projected_cents: float) -> None:
        """Halt BEFORE spending: raises OverBudget when what is already spent plus
        what this stage would cost exceeds the ceiling."""
        if self.spent + projected_cents > self.cents:
            raise OverBudget(stage, int(round(self.spent + projected_cents)), self.cents)

    @staticmethod
    def project_panel(contexts: list[str], roles: int) -> float:
        in_tok = sum(len(c) // 4 + 300 for c in contexts) * roles
        return _cents("cheap", in_tok, PANEL_OUT_TOKENS * len(contexts) * roles)

    @staticmethod
    def project_review(n_candidates: int) -> float:
        return _cents("strong", REVIEW_IN_TOKENS * n_candidates,
                      REVIEW_OUT_TOKENS * n_candidates)

    @staticmethod
    def project_governor(n_upheld: int) -> float:
        return _cents("strong", 400 * n_upheld + 600, GOVERNOR_OUT_TOKENS)

    def record(self) -> dict:
        return {"budget_cents": self.cents, "spent_cents": self.spent_cents(),
                "by_stage": {k: round(v, 2) for k, v in self.by_stage.items()},
                "tokens": dict(self.tokens),
                "priced_for": PRICED_FOR,
                "note": "tokens estimated at 4 chars each; prices are assumptions"}
