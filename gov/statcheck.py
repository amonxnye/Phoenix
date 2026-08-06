"""The recomputation oracle (statcheck) — a paper's own arithmetic, redone from its own numbers.

This is the cheapest replication there is, and the only one that needs neither the raw
data nor a laboratory: take the counts a paper reports and re-derive the statistics it
reports from them. If the percentages don't match the counts, if the odds ratio isn't
what the 2x2 table gives, if the p-value doesn't follow from the test statistic, or if
the mean is not attainable at all from that sample size — the paper says so itself, in
its own numbers, and no model's opinion enters into it.

Every function here is deterministic, pure Python and dependency-free (no numpy, no
scipy — the distributions are implemented from their series, which is both auditable
and keeps the oracle installable anywhere the rest of Phoenix runs).

What it can settle:  internal consistency, recomputable statistics, impossible means.
What it cannot:      whether the study was run honestly, whether the sample is
                     representative, whether the effect is real. Those need the raw
                     data or a replication in the world, and the replication harness
                     says so by name rather than guessing (Article IX).
"""

import math

# ── distributions, from their series (so there is nothing to install) ─────────


def _gamma_upper_reg(s: float, x: float) -> float:
    """Regularized upper incomplete gamma Q(s,x) — the chi-square tail."""
    if x < 0 or s <= 0:
        return float("nan")
    if x == 0:
        return 1.0
    if x < s + 1:                                  # series for P(s,x), then Q = 1 - P
        term = 1.0 / s
        total = term
        n = 1
        while n < 500:
            term *= x / (s + n)
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
            n += 1
        return 1.0 - total * math.exp(-x + s * math.log(x) - math.lgamma(s))
    # continued fraction for Q(s,x) (Lentz's method)
    tiny = 1e-300
    b, c, d = x + 1.0 - s, 1.0 / tiny, 1.0 / (x + 1.0 - s)
    h = d
    for i in range(1, 500):
        an = -i * (i - s)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return h * math.exp(-x + s * math.log(x) - math.lgamma(s))


def _betacf(a: float, b: float, x: float) -> float:
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        c = 1.0 + aa / c
        if abs(d) < tiny:
            d = tiny
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        c = 1.0 + aa / c
        if abs(d) < tiny:
            d = tiny
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return h


def _betainc_reg(a: float, b: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                     + a * math.log(x) + b * math.log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1 - x) / b


def p_from_z(z: float) -> float:
    """Two-sided normal p-value."""
    return math.erfc(abs(z) / math.sqrt(2.0))


def p_from_chi2(chi2: float, df: int = 1) -> float:
    return _gamma_upper_reg(df / 2.0, chi2 / 2.0)


def p_from_t(t: float, df: float) -> float:
    """Two-sided p from a t statistic."""
    return _betainc_reg(df / 2.0, 0.5, df / (df + t * t))


# ── the checks a replication actually runs ────────────────────────────────────

def percentage(count: int, total: int, reported_pct: float, dp: int = 1) -> dict:
    """Does the reported percentage follow from the reported counts? The commonest
    reporting error there is, and free to catch."""
    if not total:
        return {"ok": False, "why": "total is zero"}
    exact = 100.0 * count / total
    tol = 0.5 * 10 ** (-dp) + 1e-9
    ok = abs(exact - reported_pct) <= tol
    return {"ok": ok, "recomputed": round(exact, dp + 2), "reported": reported_pct,
            "why": f"{count}/{total} = {exact:.{dp + 2}f}%" +
                   ("" if ok else f" — reported {reported_pct}%, off by "
                                  f"{abs(exact - reported_pct):.{dp + 2}f}")}


def two_by_two(a: int, b: int, c: int, d: int) -> dict:
    """The 2x2 table redone: a/b = outcome/no-outcome in the exposed arm, c/d in the
    control arm. Returns the odds ratio, risk ratio, Woolf CI, Wald p and the
    chi-square p — everything a trial abstract reports and rarely shows its working for.

    A zero cell gets the Haldane-Anscombe 0.5 correction, and the correction is named
    in the output: a reader must know when the number was rescued rather than computed."""
    corrected = 0 in (a, b, c, d)
    A, B, C, D = (a + 0.5, b + 0.5, c + 0.5, d + 0.5) if corrected else (a, b, c, d)
    n1, n0, n = a + b, c + d, a + b + c + d
    orr = (A * D) / (B * C)
    se = math.sqrt(1 / A + 1 / B + 1 / C + 1 / D)
    lo, hi = math.exp(math.log(orr) - 1.96 * se), math.exp(math.log(orr) + 1.96 * se)
    z = math.log(orr) / se
    out = {"or": round(orr, 3), "ci": [round(lo, 3), round(hi, 3)],
           "p_wald": round(p_from_z(z), 4), "z": round(z, 3),
           "haldane_corrected": corrected, "n": n}
    if n1 and n0:
        r1, r0 = a / n1, c / n0
        out["risk"] = [round(r1, 4), round(r0, 4)]
        out["rr"] = round(r1 / r0, 3) if r0 else None
    if n and n1 and n0 and (a + c) and (b + d):
        e = [(n1 * (a + c)) / n, (n1 * (b + d)) / n, (n0 * (a + c)) / n, (n0 * (b + d)) / n]
        chi2 = sum((o - x) ** 2 / x for o, x in zip((a, b, c, d), e) if x)
        out["chi2"] = round(chi2, 3)
        out["p_chi2"] = round(p_from_chi2(chi2, 1), 4)
    return out


def grim(mean: float, n: int, dp: int = 2, items: int = 1) -> dict:
    """GRIM: a mean of integer-valued measures over n subjects can only land on certain
    values. A reported mean that no possible total produces is not a rounding artefact —
    it is an impossible number, and it needs an explanation before anything built on it
    is believed."""
    if n <= 0:
        return {"ok": False, "why": "n must be positive"}
    tol = 0.5 * 10 ** (-dp) + 1e-9
    total = round(mean * n * items)
    best, nearest = None, None
    for t in (total - 1, total, total + 1):
        cand = t / (n * items)
        if best is None or abs(cand - mean) < abs(best - mean):
            best, nearest = cand, round(cand, dp + 3)
    ok = abs(best - mean) <= tol
    return {"ok": ok, "nearest_possible": nearest, "reported": mean,
            "why": f"with n={n}" + (f" and {items} items" if items > 1 else "") +
                   (f", {mean} is attainable" if ok else
                    f", the closest attainable mean is {nearest} — {mean} is not possible")}


def sd_bound(mean: float, sd: float, n: int, lo: float, hi: float) -> dict:
    """The largest standard deviation a bounded scale allows. A reported SD above it is
    impossible whatever the data were."""
    if n < 2 or not (lo <= mean <= hi):
        return {"ok": False, "why": f"mean {mean} is outside the scale [{lo}, {hi}]"
                if n >= 2 else "n must be at least 2"}
    max_sd = math.sqrt((mean - lo) * (hi - mean) * n / (n - 1))
    ok = sd <= max_sd + 1e-9
    return {"ok": ok, "max_possible": round(max_sd, 4), "reported": sd,
            "why": f"on [{lo}, {hi}] with mean {mean} and n={n}, SD cannot exceed "
                   f"{max_sd:.4f}" + ("" if ok else f" — {sd} is impossible")}


def ci_symmetry(point: float, lo: float, hi: float, ratio: bool = True) -> dict:
    """A ratio's confidence interval is symmetric in logs around the point estimate.
    A CI that is not tells you the point estimate and the interval did not come from
    the same model — worth knowing before either is quoted."""
    if lo <= 0 or hi <= 0 or point <= 0:
        return {"ok": False, "why": "ratios and their bounds must be positive"}
    if ratio:
        centre = math.sqrt(lo * hi)
        off = abs(math.log(centre) - math.log(point)) / max(abs(math.log(point)), 1e-9)
        ok = off <= 0.10
        return {"ok": ok, "implied_point": round(centre, 3), "reported": point,
                "why": f"the interval [{lo}, {hi}] is centred (in logs) on "
                       f"{centre:.3f} against a reported {point}" +
                       ("" if ok else " — they disagree by more than 10%")}
    centre = (lo + hi) / 2
    ok = abs(centre - point) <= 0.10 * max(abs(point), 1e-9)
    return {"ok": ok, "implied_point": round(centre, 3), "reported": point,
            "why": f"the interval midpoint is {centre:.3f} against a reported {point}"}


def agrees(recomputed: float, reported: float, rel_tol: float = 0.10) -> bool:
    """Two numbers that mean the same thing, allowing for the method choices a paper is
    entitled to make (exact vs Wald, adjusted vs crude). Beyond this, it is a divergence
    to be reported, not rounding to be waved through."""
    if reported == 0:
        return abs(recomputed) <= rel_tol
    return abs(recomputed - reported) / abs(reported) <= rel_tol
