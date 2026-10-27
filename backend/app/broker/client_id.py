"""The one client order id format: ``sa-<instance>-<kind>-<rest>``.

``kind`` is the order's role in the agent — entry, stop, target, exit or
breach (a breached-stop market sale; legs and exit reasons derive from
it). The instance segment identifies the agent copy on a shared paper
account (PR 23, SA_INSTANCE_ID); it is optional and always a single
dashless segment, so ids written before instances existed
(``sa-<kind>-<rest>``) keep parsing.

Parse with :func:`order_kind` — never with ``startswith`` on the whole
string: an instance id would shift every segment by one.
"""

from __future__ import annotations

import re

KINDS = frozenset({"entry", "stop", "target", "exit", "breach"})

_INSTANCE_SAFE = re.compile(r"[^a-zA-Z0-9]")


def client_order_id(instance: str, kind: str, *parts: object) -> str:
    """Build an id: ``sa-<instance>-<kind>-<parts>``.

    With an empty ``instance`` the segment is dropped
    (``sa-<kind>-<parts>``) — the pre-instance shape. ``instance`` is
    stripped to ``[a-zA-Z0-9]`` so the kind always sits at segment 2.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown client order id kind: {kind!r}")
    inst = _INSTANCE_SAFE.sub("", str(instance))
    segments = ["sa", inst, kind] if inst else ["sa", kind]
    segments.extend(str(p) for p in parts)
    return "-".join(segments)


def order_kind(client_order_id: str | None) -> str | None:
    """The kind segment of a client id, with or without an instance.

    Looks at segment 1 (no instance) then segment 2 (instance present);
    anything else is not an agent order.
    """
    if not client_order_id:
        return None
    parts = client_order_id.split("-")
    if len(parts) < 3 or parts[0] != "sa":
        return None
    for index in (1, 2):
        if index < len(parts) and parts[index] in KINDS:
            return parts[index]
    return None


def order_instance(client_order_id: str | None) -> str | None:
    """The instance segment of an ``sa-<instance>-<kind>-…`` id, or None.

    ``sa-<kind>-…`` (pre-instance format) has no instance; an id that is
    not an agent id at all returns None too.
    """
    if not client_order_id:
        return None
    parts = client_order_id.split("-")
    if len(parts) < 3 or parts[0] != "sa":
        return None
    if parts[1] in KINDS:
        return None  # pre-instance shape: sa-<kind>-<rest>
    if parts[2] in KINDS:
        return parts[1]
    return None
