"""Client order id format: sa-<instance>-<kind>-<rest> (PR 23 note).

The kind segment must stay parseable whether or not an instance id is
present — the breach marker drives the stop_breached exit reason.
"""

import pytest
from app.broker.client_id import client_order_id, order_instance, order_kind


def test_format_with_instance() -> None:
    cid = client_order_id("home", "breach", 42, "WMT", "abc123", 7)
    assert cid == "sa-home-breach-42-WMT-abc123-7"


def test_format_without_instance_is_the_old_shape() -> None:
    cid = client_order_id("", "exit", 42, "MSFT", "abc123", 7)
    assert cid == "sa-exit-42-MSFT-abc123-7"


def test_instance_is_stripped_to_one_segment() -> None:
    cid = client_order_id("vps-01", "stop", 1, "AAPL")
    assert cid == "sa-vps01-stop-1-AAPL"
    assert order_kind(cid) == "stop"


@pytest.mark.parametrize(
    ("cid", "kind"),
    [
        ("sa-breach-1-WMT-n-1", "breach"),
        ("sa-vps1-breach-1-WMT-n-1", "breach"),
        ("sa-entry-9-MSFT-n-1", "entry"),
        ("sa-home01-exit-9-MSFT-n-1", "exit"),
        ("sa-stop-9-MSFT-n-1", "stop"),
        ("sa-broker-external", None),
        ("other-exit-1-MSFT", None),
        (None, None),
        ("", None),
    ],
)
def test_order_kind_parses_with_and_without_instance(
    cid: str | None, kind: str | None
) -> None:
    assert order_kind(cid) == kind


def test_unknown_kind_rejected() -> None:
    with pytest.raises(ValueError):
        client_order_id("", "protect", 1, "MSFT")


def test_instance_clamped_to_ten_chars() -> None:
    """The instance is the only unbounded segment (Alpaca caps the id at
    48 chars); it is stripped and clamped."""
    cid = client_order_id("a-very-long-instance-name!!", "stop", 1, "X")
    assert cid == "sa-averylongi-stop-1-X"


def test_kind_word_instance_rejected() -> None:
    """SA_INSTANCE_ID='stop' would write sa-stop-<kind>-… ids that parse
    as the pre-instance shape — orders indistinguishable and foreign-
    instance detection off."""
    with pytest.raises(ValueError, match="reserved"):
        client_order_id("stop", "entry", 1, "MSFT")


def test_foreign_instance_stop_id_detected() -> None:
    """Foreign-instance detection works on a 'stop' kind too, not only
    on 'entry' ids."""
    assert order_instance("sa-vps2-stop-1-MSFT-n-1") == "vps2"
    assert order_kind("sa-vps2-stop-1-MSFT-n-1") == "stop"
    # Pre-instance ids have no instance segment.
    assert order_instance("sa-stop-1-MSFT-n-1") is None
