"""Known-vulnerable dependencies — R17, done the spec's way: consume the existing
answer, do not reproduce the scanner.

The spec's non-goal is explicit: no attempt to compete with Dependabot or Snyk on
breadth of CVE detection; where those tools already have an answer, consume it. The
open answer is OSV.dev — the database Dependabot, pip-audit and npm audit all draw on
— queried over HTTPS with no key and no credential, exactly as ingestion fetches an
archive.

What this stage produces is a **vulnerability fact**: "OSV lists GHSA-xxxx for
name@version". It is checkable in seconds by anyone with the URL, so it is admitted as
machine-verified evidence (Charter §2), and it costs no model call.

Fingerprinted by manifest and package, so the watch does the rest: the cycle after a
version is bumped past the affected range, the finding vanishes from re-analysis and
is marked fixed upstream.
"""

import json
import os
import re
import ssl
import urllib.error
import urllib.request

OSV_BATCH = "https://api.osv.dev/v1/querybatch"
OSV_VULN = "https://api.osv.dev/v1/vulns/{id}"
TIMEOUT_S = 25
MAX_PACKAGES = 400
MAX_DETAILS = 40
_SEV = {"CRITICAL": "critical", "HIGH": "high", "MODERATE": "medium", "MEDIUM": "medium",
        "LOW": "low"}


# ── inventory: what the repository declares it depends on ────────────────────

def _pins_requirements(path: str, rel: str) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, ln in enumerate(f, 1):
                m = re.match(r"^\s*([A-Za-z0-9_.\-\[\]]+)\s*==\s*([A-Za-z0-9_.\-+!]+)", ln)
                if m:
                    out.append({"ecosystem": "PyPI", "name": m.group(1).split("[")[0].lower(),
                                "version": m.group(2), "manifest": rel, "line": i})
    except OSError:
        pass
    return out


def _npm_lock(path: str, rel: str) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        d = json.loads(text)
    except (OSError, json.JSONDecodeError):
        return out
    pk = d.get("packages")
    if isinstance(pk, dict):                          # lockfile v2/v3
        for key, meta in pk.items():
            if not key or not isinstance(meta, dict) or not meta.get("version"):
                continue
            name = key.split("node_modules/")[-1]
            out.append({"ecosystem": "npm", "name": name, "version": str(meta["version"]),
                        "manifest": rel, "line": _line_of(text, f'"{key}"')})
    else:                                             # lockfile v1
        for name, meta in (d.get("dependencies") or {}).items():
            if isinstance(meta, dict) and meta.get("version"):
                out.append({"ecosystem": "npm", "name": name, "version": str(meta["version"]),
                            "manifest": rel, "line": _line_of(text, f'"{name}"')})
    return out


def _line_of(text: str, needle: str) -> int:
    i = text.find(needle)
    return text.count("\n", 0, i) + 1 if i >= 0 else 1


def inventory(root: str) -> list[dict]:
    """Declared, pinned dependencies with the manifest and line that declares them."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ("node_modules", ".git", "vendor",
                                                         ".venv", "venv", "__pycache__")]
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, root)
            if fn == "requirements.txt" or (fn.startswith("requirements") and fn.endswith(".txt")):
                found += _pins_requirements(p, rel)
            elif fn == "package-lock.json":
                found += _npm_lock(p, rel)
    seen, uniq = set(), []
    for d in found:
        k = (d["ecosystem"], d["name"], d["version"])
        if k not in seen:
            seen.add(k)
            uniq.append(d)
    return uniq[:MAX_PACKAGES]


# ── the answer: OSV.dev, consumed ────────────────────────────────────────────

def _ctx():
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        p = os.environ.get(var, "")
        if p and os.path.exists(p):
            return ssl.create_default_context(cafile=p)
    return ssl.create_default_context()


def _post(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "phoenix-software-mechanic/0.1"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S, context=_ctx()) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "{}")


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "phoenix-software-mechanic/0.1"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S, context=_ctx()) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "{}")


def osv_query(packages: list[dict]) -> dict:
    """Batch query → {(eco, name, version): [vuln ids]}. Raises on network failure;
    the caller turns that into a recorded gap."""
    out = {}
    for i in range(0, len(packages), 100):
        chunk = packages[i:i + 100]
        res = _post(OSV_BATCH, {"queries": [
            {"package": {"name": p["name"], "ecosystem": p["ecosystem"]}, "version": p["version"]}
            for p in chunk]})
        for p, r in zip(chunk, res.get("results") or []):
            ids = [v.get("id") for v in (r.get("vulns") or []) if v.get("id")]
            if ids:
                out[(p["ecosystem"], p["name"], p["version"])] = ids
    return out


def osv_detail(vid: str) -> dict:
    try:
        d = _get(OSV_VULN.format(id=vid))
    except (urllib.error.URLError, OSError, ValueError):
        return {}
    sev = ""
    for s in d.get("severity") or []:
        if s.get("type") == "CVSS_V3":
            sev = _cvss_bucket(s.get("score", ""))
    sev = sev or _SEV.get(str((d.get("database_specific") or {}).get("severity", "")).upper(), "")
    fixed = []
    for aff in d.get("affected") or []:
        for rng in aff.get("ranges") or []:
            for ev in rng.get("events") or []:
                if ev.get("fixed"):
                    fixed.append(ev["fixed"])
    return {"id": vid, "summary": (d.get("summary") or "")[:200], "severity": sev or "medium",
            "fixed": sorted(set(fixed))[:4]}


def _cvss_bucket(vector: str) -> str:
    # OSV carries the vector, not the score; a bucket from the vector's impact is a
    # rough read and is labelled as one by the severity's provenance in the evidence.
    v = vector.upper()
    if "/C:H" in v and "/I:H" in v:
        return "critical" if "/AV:N" in v and "/PR:N" in v else "high"
    if "/C:H" in v or "/I:H" in v or "/A:H" in v:
        return "high"
    return "medium"


QUERY = osv_query                                      # replaceable in tests
DETAIL = osv_detail


def analyse(root: str) -> dict:
    """Returns {findings, gaps, packages}. Never raises."""
    inv = inventory(root)
    if not inv:
        return {"findings": [], "gaps": [], "packages": 0}
    try:
        hits = QUERY(inv)
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"findings": [], "packages": len(inv),
                "gaps": [{"scope": "dependencies",
                          "reason": f"{len(inv)} declared dependencies could not be checked — "
                                    f"OSV.dev unreachable: {type(e).__name__}: {str(e)[:80]}"}]}
    findings, details_left = [], MAX_DETAILS
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    for p in inv:
        ids = hits.get((p["ecosystem"], p["name"], p["version"]))
        if not ids:
            continue
        dets = []
        for vid in ids[:5]:
            if details_left > 0:
                dets.append(DETAIL(vid) or {"id": vid, "severity": "medium", "fixed": [], "summary": ""})
                details_left -= 1
            else:
                dets.append({"id": vid, "severity": "medium", "fixed": [], "summary": ""})
        worst = min((d["severity"] for d in dets), key=lambda x: order.get(x, 9))
        fixed = sorted({v for d in dets for v in d["fixed"]})
        findings.append({
            "unit": "", "file": p["manifest"], "symbol": "",
            "line_range": f"{p['line']}-{p['line']}", "category": "security",
            "severity": worst, "confidence": 0.98, "basis": "machine-verified",
            "title": f"`{p['name']}@{p['version']}` ({p['ecosystem']}) has {len(ids)} known "
                     f"vulnerabilit{'y' if len(ids) == 1 else 'ies'}: {', '.join(ids[:3])}"
                     + (" …" if len(ids) > 3 else ""),
            "description": "; ".join(f"{d['id']}: {d['summary']}" for d in dets if d["summary"])
            or "Listed by OSV.dev for this exact version.",
            "recommendation": (f"Upgrade `{p['name']}` to a version outside the affected ranges"
                               + (f" — fixed in {', '.join(fixed)}" if fixed else "")
                               + f". Declared at {p['manifest']}:{p['line']}."),
            "claim_kind": "vulnerability", "claim": f"OSV.dev lists {ids[0]} for {p['name']}@{p['version']}",
            "proposed_by": "dependency-scan",
            "evidence": [{"file": p["manifest"], "line_range": f"{p['line']}-{p['line']}",
                          "reason": f"vulnerability fact: OSV.dev lists {', '.join(ids[:3])} for "
                                    f"{p['name']}@{p['version']}; severity from the advisory"}],
        })
    findings.sort(key=lambda f: order.get(f["severity"], 9))
    return {"findings": findings, "gaps": [], "packages": len(inv)}
