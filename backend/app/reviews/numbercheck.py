"""Number check for review text (docs/SPEC.md, Reviews).

Every number in the model's text must match a fact it was given (after
rounding). A number written to ``d`` decimal places matches a fact value
when the fact rounds to it at that precision, i.e. within half a unit of
the last written digit. Sign is ignored: a sign flip is a wording error,
not an invented number. Clock times like ``09:30`` are not numbers.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from decimal import Decimal

# Not preceded or followed by a digit, dot or colon: skips times ("10:00")
# and parts of longer numbers.
_SEGMENT = re.compile(r"(?<![\d:.])\d[\d,]*(?:\.\d+)?(?![\d:.])")

# Date-shaped text is masked before scanning: "2026-10-13", "5 – 9 Oct",
# "Tue 13 Oct", "Oct 13" are dates, not figures the check should verify.
_MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
_DAYS = "Mon|Tue|Wed|Thu|Fri|Sat|Sun"
_DATE_SHAPES = re.compile(
    rf"\d{{4}}-\d{{2}}-\d{{2}}"
    rf"|(?:(?:{_DAYS})\s+)?\d{{1,2}}\s*[–-]\s*\d{{1,2}}\s+(?:{_MONTHS})"
    rf"|(?:(?:{_DAYS})\s+)?\d{{1,2}}\s+(?:{_MONTHS})"
    rf"|(?:{_MONTHS})\s+\d{{1,2}}(?:,?\s*\d{{4}})?"
)


def _mask_dates(text: str) -> str:
    return _DATE_SHAPES.sub(" ", text)


def _precision(literal: str) -> int:
    return len(literal.rsplit(".", 1)[1]) if "." in literal else 0


def extract_numbers(text: str) -> list[Decimal]:
    """Every numeric literal in ``text``, commas and sign ignored."""
    out: list[Decimal] = []
    for match in _SEGMENT.finditer(_mask_dates(text)):
        out.append(Decimal(match.group(0).replace(",", "")))
    return out


def fact_values(node: object) -> Iterator[Decimal]:
    """All numeric leaves of a JSON-shaped facts object (bools excluded)."""
    if isinstance(node, bool):
        return
    if isinstance(node, int | float):
        yield Decimal(str(node))
        return
    if isinstance(node, Decimal):
        yield node
        return
    if isinstance(node, Mapping):
        for value in node.values():
            yield from fact_values(value)
        return
    if isinstance(node, list | tuple):
        for value in node:
            yield from fact_values(value)


def bad_numbers(text: str, facts: object) -> list[str]:
    """Numeric literals in ``text`` that match no given fact after rounding."""
    allowed = list(fact_values(facts))
    bad: list[str] = []
    for match in _SEGMENT.finditer(_mask_dates(text)):
        literal = match.group(0)
        n = Decimal(literal.replace(",", ""))
        tolerance = Decimal(1).scaleb(-_precision(literal)) / 2
        if not any(abs(abs(v) - n) <= tolerance for v in allowed):
            bad.append(literal)
    return bad
