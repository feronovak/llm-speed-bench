from __future__ import annotations

import urllib.parse
from ipaddress import ip_address
import socket


def require_http_url(url: str, *, resolve_host: bool = True) -> None:
    """Reject local-file and ambiguous URLs before urllib handles them."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"URL must use http or https with a host: {url!r}")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL must not contain embedded credentials")
    try:
        address = ip_address(parsed.hostname)
    except ValueError:
        if not resolve_host:
            return
        try:
            addresses = {
                item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port)
            }
        except OSError as exc:
            raise ValueError(
                f"URL host could not be resolved: {parsed.hostname!r}"
            ) from exc
        if not addresses or any(
            not ip_address(address).is_global for address in addresses
        ):
            raise ValueError("URL must use a public host")
        return
    if not address.is_global:
        raise ValueError("URL must use a public host")
