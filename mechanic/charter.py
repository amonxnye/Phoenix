"""Which rules governed a run — version for people, digest for proof (Charter §8).

Same discipline as the Constitution's Article X, kept separate because the product's
rules and the research settlement's rules are different documents that will diverge.
"""

import hashlib
import os
import re

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "CHARTER.md")
UNVERSIONED = "unversioned"


def text() -> str:
    try:
        with open(PATH, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def charter() -> dict:
    t = text()
    digest = hashlib.sha256(t.encode("utf-8")).hexdigest()[:12] if t else "0" * 12
    m = re.search(r"^Version:\s*([0-9][\w.\-+]*)\s*$", t, re.M)
    version = m.group(1) if m else UNVERSIONED
    return {"version": version, "digest": digest, "stamp": f"{version}+{digest}",
            "bytes": len(t)}


def sections() -> list[str]:
    """The section headings — so a check can prove every rule names its enforcer."""
    return re.findall(r"^## \d+\. (.+)$", text(), re.M)
