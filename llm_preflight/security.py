from __future__ import annotations

import urllib.parse
import urllib.error
import urllib.request
from ipaddress import ip_address
import socket


def require_http_url(url: str, *, resolve_host: bool = True) -> None:
    """Reject local-file and ambiguous URLs before urllib handles them."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL must not contain embedded credentials")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must use http or https with a host")
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


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Fail closed: provider calls must not cross an unvalidated redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise urllib.error.HTTPError(
            req.full_url,
            code,
            "redirects are not allowed for provider requests",
            headers,
            fp,
        )


def open_public_url(request: urllib.request.Request, timeout: float):
    """Open a validated public URL without allowing redirect-based SSRF."""
    require_http_url(request.full_url)
    opener = urllib.request.build_opener(NoRedirectHandler())
    return opener.open(request, timeout=timeout)
