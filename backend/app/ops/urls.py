"""Outbound-URL validation (docs/SPEC.md → Security hardening → Stored
credentials): user-set endpoints must be ``https://`` and must not
resolve to a loopback or private address."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UrlRejected(Exception):
    """The URL fails the https-and-public rule; message is 422-safe."""


def _resolves_public(host: str, resolver: object = None) -> bool:
    resolve = resolver or socket.getaddrinfo
    try:
        infos = resolve(host, None)  # type: ignore[operator]
    except Exception:  # noqa: BLE001 — a failed lookup is never "public"
        return False
    for info in infos:
        raw = info[4][0] if isinstance(info, tuple) else str(info)
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    return True


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
        ip = ipaddress.ip_address(host)
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise UrlRejected("must not point at a private address")
    except ValueError:
        if host.lower() in {"localhost", "localhost.localdomain"} or host.lower().endswith(
            (".local", ".internal", ".lan", ".home")
        ):
            raise UrlRejected("must not point at a private address") from None
        if not _resolves_public(host, resolver):
            raise UrlRejected(
                "does not resolve to a public address"
            ) from None
    return parsed.geturl().rstrip("/")
