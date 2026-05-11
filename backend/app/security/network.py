"""SSRF guard for outbound HTTP from worker tasks.

Worker code that fetches URLs supplied (directly or transitively) by
external data — e.g. competitor discovery, link-following crawlers —
must call :func:`assert_public_url` before issuing the request so we
do not accidentally hit internal services, cloud metadata endpoints,
or loopback.
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse

ALLOWED_SCHEMES = {"http", "https"}

# Reserved / private ranges that must never be reachable from worker
# code. 169.254.0.0/16 covers AWS / GCP / Azure instance metadata
# (169.254.169.254) — the most dangerous SSRF target on cloud hosts.
PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local incl. cloud metadata
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


class SSRFBlocked(ValueError):
    """Raised when a URL targets a private/reserved/loopback host."""


def assert_public_url(url: str) -> None:
    """Raise :class:`SSRFBlocked` if ``url`` would target a private host.

    Resolves DNS at check time so a public hostname that resolves to
    a private IP (e.g. rebinding) is also rejected. Caller should still
    use a short timeout — DNS pinning between check and connect is out
    of scope here.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise SSRFBlocked(f"scheme {parsed.scheme!r} not allowed")
    host = parsed.hostname
    if not host:
        raise SSRFBlocked("URL has no host")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SSRFBlocked(f"DNS resolution failed: {exc}") from exc
    for _family, _type, _proto, _canon, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        for net in PRIVATE_NETWORKS:
            if ip in net:
                raise SSRFBlocked(
                    f"host {host} resolves to private/reserved range ({ip})"
                )


# ── Redirect-safe opener ───────────────────────────────────────────────


class _SSRFSafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-validate every redirect Location through assert_public_url.

    Without this, a malicious server can answer the first GET (which we
    already validated) with `302 Location: http://169.254.169.254/...`
    and `urllib.request.urlopen` would happily follow into the cloud
    metadata endpoint. Validating each hop closes the loop.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        try:
            assert_public_url(newurl)
        except SSRFBlocked as exc:
            raise urllib.error.HTTPError(
                newurl, code, f"redirect blocked: {exc}", headers, fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# Cap on hops — generous for legitimate sites, tight against redirect-
# loop probing. Standard urllib default is 10; 3 is plenty for normal
# www→canonical→trailing-slash chains.
MAX_REDIRECTS = 3


def safe_urlopen(url: str, *, timeout: float = 10.0, headers: dict | None = None):
    """Drop-in replacement for ``urllib.request.urlopen`` with SSRF guard.

    Validates the initial URL and every redirect target via
    :func:`assert_public_url`. Caps redirects at :data:`MAX_REDIRECTS`.
    Returns the response object — caller is responsible for ``.read()``
    and closing (use ``with``).
    """
    assert_public_url(url)

    handler = _SSRFSafeRedirectHandler()
    handler.max_repeats = MAX_REDIRECTS
    handler.max_redirections = MAX_REDIRECTS
    opener = urllib.request.build_opener(handler)

    req = urllib.request.Request(url)
    if headers:
        for key, value in headers.items():
            req.add_header(key, value)

    return opener.open(req, timeout=timeout)
