"""The one CSV writer (docs/SPEC.md → Security hardening → CSV cells
can't be formulas, item 085).

Every export goes through :func:`writer`. A text cell that starts with
``=``, ``+``, ``-``, ``@``, a tab or a carriage return gets a leading
``'`` so a spreadsheet shows it as text instead of running it as a
formula. Numbers (``int``, ``Decimal``, ``float``) are written as
numbers and are not touched — a negative number stays ``-49.30``.
"""

from __future__ import annotations

import csv
from typing import IO, Any

_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> Any:
    """Prefix ``'`` on a formula-looking string; pass everything else."""
    if isinstance(value, str) and value.startswith(_FORMULA_STARTS):
        return "'" + value
    return value


class _SafeWriter:
    """``csv.writer`` with :func:`safe_cell` applied to every cell."""

    def __init__(self, buf: IO[str], **kwargs: Any) -> None:
        self._writer = csv.writer(buf, **kwargs)

    def writerow(self, row: Any) -> Any:
        return self._writer.writerow([safe_cell(cell) for cell in row])

    def writerows(self, rows: Any) -> Any:
        return self._writer.writerows(
            [[safe_cell(cell) for cell in row] for row in rows]
        )


def writer(buf: IO[str], **kwargs: Any) -> _SafeWriter:
    return _SafeWriter(buf, **kwargs)
