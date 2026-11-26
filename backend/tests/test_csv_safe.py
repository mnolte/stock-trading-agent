"""Item 085 (S10): one CSV writer — no exported cell can run as a
formula. Numbers are written as numbers and are not touched."""

import csv
import io
from decimal import Decimal
from pathlib import Path

import pytest
from app import csv_safe
from app.csv_safe import safe_cell, writer


def _dump(row: list[object]) -> str:
    buf = io.StringIO()
    writer(buf).writerow(row)
    return buf.getvalue().replace("\r\n", "\n")


def _cell(text: str) -> str:
    """The first cell as a spreadsheet would read it."""
    return next(csv.reader(io.StringIO(_dump([text]))))[0]


@pytest.mark.parametrize(
    "text",
    [
        '=HYPERLINK("http://evil.example")',
        "+1+1",
        "-1R",
        "@SUM(A1)",
        "\t=1+1",
        "\r=1+1",
    ],
)
def test_formula_strings_get_a_leading_quote(text: str) -> None:
    assert safe_cell(text) == "'" + text
    assert _cell(text) == "'" + text


def test_negative_number_string_is_quoted() -> None:
    assert _cell("-1R") == "'-1R"
    assert _dump(["-1R"]) == "'-1R\n"


def test_numbers_stay_numbers() -> None:
    assert _dump([Decimal("-49.30")]) == "-49.30\n"
    assert _dump([-49.3]) == "-49.3\n"
    assert _dump([-5]) == "-5\n"
    assert _dump([Decimal("0.00001")]) == "0.00001\n"


def test_plain_strings_untouched() -> None:
    assert _dump(["AAPL", "enter", "weekly"]) == "AAPL,enter,weekly\n"
    assert _dump([None]) == '""\n'  # csv.writer renders None as an empty quoted cell
    assert safe_cell(Decimal("1.5")) == Decimal("1.5")


def test_writerows_applies_safe_cell() -> None:
    buf = io.StringIO()
    w = writer(buf)
    w.writerows([["=x"], [Decimal("2.5")]])
    assert buf.getvalue().replace("\r\n", "\n") == "'=x\n2.5\n"


def test_no_csv_writer_outside_csv_safe() -> None:
    """Every export goes through app/csv_safe.py — a raw csv.writer
    anywhere else in app/ fails this walk."""
    app_dir = Path(csv_safe.__file__).resolve().parent
    offenders = [
        str(path.relative_to(app_dir))
        for path in app_dir.rglob("*.py")
        if path.name != "csv_safe.py" and "csv.writer" in path.read_text()
    ]
    assert offenders == []
