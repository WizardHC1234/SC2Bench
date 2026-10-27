"""Recognize a Codex account that cannot start another paid turn."""
from __future__ import annotations


_MARKERS = (
    "hityourusagelimit",
    "usagelimitreached",
    "usagelimitexceeded",
    "insufficientquota",
    "creditsdepleted",
    "ratelimitreached",
    "projectspendlimit",
)


def codex_quota_exhausted(text: object) -> bool:
    """True when Codex reports that the usage or credit allowance is gone."""
    compact = str(text or "").lower().replace("_", "").replace(" ", "")
    return any(marker in compact for marker in _MARKERS)
