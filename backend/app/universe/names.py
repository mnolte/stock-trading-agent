"""Company names for headline ``about_symbol`` (docs/SPEC.md, Fixes of
3 November).

``universe_pool.name`` holds Alpaca's asset name cleaned of corporate
suffixes ("Inc", "Corp", "(The)", "Class A", …), so a headline can match
the name people actually write. ``decide.v4.pool_names`` reads it —
kept there so the decide package never loads this package's service
(the shadow-isolation rule).
"""

from __future__ import annotations

import re

# "(The)", "(Delaware)" and friends never carry the name.
_PAREN = re.compile(r"\s*\([^)]*\)\s*")
# "Class A", "Class B common stock" style tails.
_CLASS = re.compile(r"[,\s]+class\s+[a-z]\.?\s*$", re.IGNORECASE)
_SHARES = re.compile(
    r"[,\s]+(common stock|ordinary shares|common shares|"
    r"american depositary shares)\.?\s*$",
    re.IGNORECASE,
)
_TRAIL_WORD = re.compile(r"[,\s]+([A-Za-z.&']+)\.?\s*$")
_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company",
    "ltd", "limited", "llc", "llp", "lp", "plc", "nv", "sa",
    "holdings", "holding", "group", "&", "and",
}
_LEADING_THE = re.compile(r"^the\s+", re.IGNORECASE)


def clean_company_name(raw: str | None) -> str | None:
    """Alpaca's asset name minus the corporate dressing — what a
    headline would call the company. ``None`` stays ``None``; a name
    made only of suffixes keeps its raw form rather than emptying out."""
    if raw is None:
        return None
    name = re.sub(r"\s+", " ", _PAREN.sub(" ", raw)).strip(" ,.")
    while True:
        new = _CLASS.sub("", name)
        new = _SHARES.sub("", new)
        word = _TRAIL_WORD.search(new)
        if word and word.group(1).replace(".", "").lower() in _SUFFIXES:
            new = new[: word.start()]
        new = new.strip(" ,.&")
        if new == name:
            break
        name = new
    name = _LEADING_THE.sub("", name).strip(" ,.")
    return name or raw.strip(" ,.")
