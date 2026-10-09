"""Stream address parsing and validation."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit, urlunsplit

DEFAULT_PORT = 8080
DEFAULT_PATH = "/video"

_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?$"
)
_UNSUPPORTED_SCHEMES = {
    "rtsp": "RTSP streams are not supported yet; use the app's MJPEG/HTTP URL instead.",
    "rtmp": "RTMP streams are not supported; use the app's MJPEG/HTTP URL instead.",
    "ws": "WebSocket streams are not supported; use the app's MJPEG/HTTP URL instead.",
    "wss": "WebSocket streams are not supported; use the app's MJPEG/HTTP URL instead.",
}


class InvalidStreamUrl(ValueError):
    """Raised when the user-supplied stream address cannot be used."""


@dataclass(frozen=True)
class StreamTarget:
    """A validated stream endpoint. ``url`` never contains credentials."""

    url: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None

    @property
    def has_credentials(self) -> bool:
        return bool(self.username)

    @property
    def is_local_address(self) -> bool:
        """True when the host is a private, loopback or link-local IP, or a .local name."""
        try:
            ip = ipaddress.ip_address(self.host)
        except ValueError:
            return self.host.lower().endswith(".local") or self.host.lower() == "localhost"
        return ip.is_private or ip.is_loopback or ip.is_link_local

    def __repr__(self) -> str:
        auth = ", credentials=<redacted>" if self.has_credentials else ""
        return f"StreamTarget(url={self.url!r}{auth})"


def parse_stream_url(
    text: str,
    username: str | None = None,
    password: str | None = None,
    *,
    default_port: int = DEFAULT_PORT,
    default_path: str = DEFAULT_PATH,
) -> StreamTarget:
    """Validate and normalise a stream address.

    Accepts a full URL (``http://192.168.1.25:8080/video``) or a bare host / ``host:port``,
    in which case ``default_port`` and ``default_path`` are filled in. Credentials may be
    given in the URL or separately; separate values take precedence.
    """
    raw = (text or "").strip()
    if not raw:
        raise InvalidStreamUrl("Enter the stream URL or IP address shown by the phone app.")
    if any(ch.isspace() for ch in raw):
        raise InvalidStreamUrl("The stream address must not contain spaces.")

    bare = "://" not in raw
    if bare:
        raw = "http://" + raw

    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise InvalidStreamUrl(f"Invalid stream address: {exc}.") from None

    scheme = parts.scheme.lower()
    if scheme in _UNSUPPORTED_SCHEMES:
        raise InvalidStreamUrl(_UNSUPPORTED_SCHEMES[scheme])
    if scheme not in ("http", "https"):
        raise InvalidStreamUrl(f"Unsupported scheme '{parts.scheme}'. Use http:// or https://.")

    host = parts.hostname or ""
    if not host:
        raise InvalidStreamUrl("The stream address is missing a host or IP address.")
    if not _is_valid_host(host):
        raise InvalidStreamUrl(f"'{host}' is not a valid IP address or host name.")

    if port is None:
        port = default_port if bare else (443 if scheme == "https" else 80)
    if not 1 <= port <= 65535:
        raise InvalidStreamUrl("The port must be between 1 and 65535.")

    path = parts.path
    if not path or path == "/":
        if bare:
            path = default_path
        elif not path:
            path = "/"

    user = username if username else (unquote(parts.username) if parts.username else None)
    pwd = password if username else (unquote(parts.password) if parts.password else None)
    if user is None and password:
        raise InvalidStreamUrl("A password was given without a username.")

    netloc_host = f"[{host}]" if ":" in host else host
    url = urlunsplit((scheme, f"{netloc_host}:{port}", path, parts.query, ""))
    return StreamTarget(url=url, host=host, port=port, username=user or None, password=pwd or None)


def _is_valid_host(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if re.fullmatch(r"[0-9.]+", host):
        return False  # looks like a malformed IPv4 address, e.g. 192.168.1
    return bool(_HOSTNAME_RE.match(host))
