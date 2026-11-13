"""Outbound-URL validation (docs/SPEC.md → Security hardening → Stored
credentials): user-set endpoints must be ``https://`` and must not
resolve to a loopback or private address. The same check runs again at
every use — a name that was public when saved can point somewhere else
later (item 085)."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UrlRejected(Exception):
    """The URL fails the https-and-public rule; message is 422-safe."""


def _check_address(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address, *, allow_private: bool
) -> None:
    """Only ``is_private`` is relaxed by ``allow_private`` — the flag
    exists for a private SMTP relay. Loopback, link-local, multicast,
    reserved and unspecified are refused even then: this host's own
    services and the cloud metadata address are never a relay."""
    if (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise UrlRejected("must not point at a private address")
    if not allow_private and ip.is_private:
        raise UrlRejected("must not point at a private address")


def resolve_public(
    host: str,
    resolver: object = None,
    *,
    allow_private: bool = False,
    missing_ok: bool = False,
) -> list[str]:
    """The checked addresses for ``host`` — the list a caller connects
    to, so the lookup that was checked is the lookup that is used.

    ``resolver`` is injectable so tests never touch the network — same
    signature as ``socket.getaddrinfo``. ``allow_private`` is the SMTP
    escape hatch (``security.allow_private_smtp``): private addresses
    pass, loopback and the other never-relay ranges do not.
    ``missing_ok`` is for callers whose connection fails on its own
    when the name does not resolve (SMTP): an unresolvable name has no
    address to refuse, so it returns ``[]`` instead of raising.
    """
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        _check_address(literal, allow_private=allow_private)
        return [str(literal)]
    resolve = resolver or socket.getaddrinfo
    try:
        infos = resolve(host, None)  # type: ignore[operator]
    except Exception:  # noqa: BLE001 — a failed lookup is never "public"
        if missing_ok:
            return []
        raise UrlRejected("does not resolve to a public address") from None
    addresses: list[str] = []
    for info in infos:
        raw = info[4][0] if isinstance(info, tuple) else str(info)
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            continue
        _check_address(ip, allow_private=allow_private)
        text = str(ip)
        if text not in addresses:
            addresses.append(text)
    if not addresses and not missing_ok:
        raise UrlRejected("does not resolve to a public address")
    return addresses


def validate_public_https_url(url: str, resolver: object = None) -> str:
    """Return the normalized URL or raise ``UrlRejected``.

    ``resolver`` is injectable so tests never touch the network —
    same signature as ``socket.getaddrinfo``.
    """
    text = (url or "").strip()
    parsed = urlparse(text)
    if parsed.scheme != "https":
        raise UrlRejected("must be an https:// URL")
    host = parsed.hostname or ""
    if not host:
        raise UrlRejected("has no host")
    if parsed.username or parsed.password:
        raise UrlRejected("must not contain credentials")
    try:
        ipaddress.ip_address(host)
        literal = True
    except ValueError:
        literal = False
    if not literal and (
        host.lower() in {"localhost", "localhost.localdomain"}
        or host.lower().endswith((".local", ".internal", ".lan", ".home"))
    ):
        raise UrlRejected("must not point at a private address") from None
    resolve_public(host, resolver=resolver)
    return parsed.geturl().rstrip("/")
