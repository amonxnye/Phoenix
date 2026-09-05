"""The mechanic's outbound requests use the project's one retry policy (gov/netretry).

Borrowed, not rebuilt: the same backoff, the same classification of what is worth
retrying, the same readout of attempts — so a GitHub archive download, an OSV.dev
query and a model call all fail the same way and the record reads the same way.
"""

import os
import sys

_GOV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gov")
if _GOV not in sys.path:
    sys.path.insert(0, _GOV)

import netretry                                        # noqa: E402 — the policy lives there

call = netretry.call
urlopen = netretry.urlopen
describe = netretry.describe
LAST = netretry.LAST
