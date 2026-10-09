"""MJPEG-over-HTTP client.

The parser is independent of the network layer so it can be tested with raw bytes. Frames are
yielded as JPEG bytes; decoding happens in the capture worker.
"""

from __future__ import annotations

import logging
import re
import socket
import threading
from collections.abc import Iterator

import requests
import urllib3
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from babycue_camera import __version__
from babycue_camera.config import StreamTarget
from babycue_camera.stream.errors import (
    StreamAuthError,
    StreamConnectError,
    StreamEnded,
    StreamError,
    StreamHttpError,
    StreamProtocolError,
    StreamTimeout,
)

log = logging.getLogger(__name__)

SOI = b"\xff\xd8"
EOI = b"\xff\xd9"
DEFAULT_MAX_FRAME_BYTES = 16 * 1024 * 1024
_MAX_HEADER_BYTES = 16 * 1024
_BOUNDARY_RE = re.compile(r'boundary\s*=\s*"?([^";,]+)"?', re.IGNORECASE)
_CONTENT_LENGTH_RE = re.compile(rb"^content-length\s*:\s*(\d+)\s*$", re.IGNORECASE | re.MULTILINE)


def boundary_from_content_type(content_type: str) -> str | None:
    match = _BOUNDARY_RE.search(content_type or "")
    return match.group(1).strip() if match else None


class MjpegParser:
    """Incremental parser for ``multipart/x-mixed-replace`` JPEG streams.

    Uses each part's ``Content-Length`` when present, otherwise the next boundary. Without a
    boundary it falls back to scanning for JPEG start/end markers.
    """

    def __init__(self, boundary: str | None, max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES):
        token = (boundary or "").strip().lstrip("-")
        # Servers disagree on whether the leading "--" belongs to the boundary, so match the
        # token without dashes; it appears in the delimiter line either way.
        self._token = token.encode("latin-1") if token else None
        self._max_frame_bytes = max_frame_bytes
        self._buf = bytearray()
        self._body_start: int | None = None
        self._content_length: int | None = None

    def feed(self, data: bytes) -> list[bytes]:
        self._buf += data
        frames: list[bytes] = []
        if self._token is None:
            self._scan_markers(frames)
        else:
            while self._step(frames):
                pass
        return frames

    def _step(self, frames: list[bytes]) -> bool:
        assert self._token is not None
        buf, token = self._buf, self._token

        if self._body_start is None:
            idx = buf.find(token)
            if idx < 0:
                keep = len(token)
                if len(buf) > keep:
                    del buf[: len(buf) - keep]
                return False
            line_end = buf.find(b"\n", idx + len(token))
            if line_end < 0:
                self._check_header_size(len(buf) - idx)
                return False
            if buf.startswith(SOI, line_end + 1):
                header_end, body_start = line_end + 1, line_end + 1
            else:
                header_end, body_start = _find_blank_line(buf, line_end + 1)
                if header_end < 0:
                    self._check_header_size(len(buf) - idx)
                    return False
            headers = bytes(buf[line_end + 1 : header_end])
            match = _CONTENT_LENGTH_RE.search(headers)
            self._content_length = int(match.group(1)) if match else None
            if self._content_length is not None and self._content_length > self._max_frame_bytes:
                raise StreamProtocolError("The stream sent a frame larger than the allowed maximum.")
            del buf[:body_start]
            self._body_start = 0
            return True

        if self._content_length is not None:
            if len(buf) < self._content_length:
                return False
            body = bytes(buf[: self._content_length])
            del buf[: self._content_length]
        else:
            idx = buf.find(token)
            if idx < 0:
                if len(buf) > self._max_frame_bytes:
                    raise StreamProtocolError("No frame boundary found; the stream may be corrupt.")
                return False
            body = bytes(buf[:idx]).rstrip(b"\r\n-")
            del buf[:idx]
        self._body_start = None
        self._content_length = None
        if body:
            frames.append(body)
        return True

    def _scan_markers(self, frames: list[bytes]) -> None:
        buf = self._buf
        while True:
            start = buf.find(SOI)
            if start < 0:
                del buf[: max(0, len(buf) - 1)]
                return
            end = buf.find(EOI, start + 2)
            if end < 0:
                if start:
                    del buf[:start]
                if len(buf) > self._max_frame_bytes:
                    raise StreamProtocolError("No JPEG end marker found; the stream may be corrupt.")
                return
            frames.append(bytes(buf[start : end + 2]))
            del buf[: end + 2]

    @staticmethod
    def _check_header_size(size: int) -> None:
        if size > _MAX_HEADER_BYTES:
            raise StreamProtocolError("Malformed multipart headers in the stream.")


def _find_blank_line(buf: bytearray, start: int) -> tuple[int, int]:
    crlf = buf.find(b"\r\n\r\n", start)
    lf = buf.find(b"\n\n", start)
    # A part may also have no headers at all: the delimiter line is followed by a blank line.
    if buf.startswith(b"\r\n", start):
        return start, start + 2
    if buf.startswith(b"\n", start):
        return start, start + 1
    candidates = [(crlf, crlf + 4) if crlf >= 0 else None, (lf, lf + 2) if lf >= 0 else None]
    found = [c for c in candidates if c is not None]
    return min(found) if found else (-1, -1)


class MjpegStream:
    """Reads JPEG frames from an MJPEG-over-HTTP endpoint.

    ``abort()`` may be called from another thread to unblock a pending read.
    """

    def __init__(
        self,
        target: StreamTarget,
        *,
        connect_timeout: float = 4.0,
        read_timeout: float = 5.0,
        chunk_size: int = 64 * 1024,
        verify_tls: bool = True,
    ):
        self.target = target
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.chunk_size = chunk_size
        self.verify_tls = verify_tls
        self.content_type: str | None = None
        self._session: requests.Session | None = None
        self._response: requests.Response | None = None
        self._parser: MjpegParser | None = None
        self._lock = threading.Lock()
        self._aborted = False

    def open(self) -> None:
        session = requests.Session()
        # Never route the camera feed through a system/environment proxy.
        session.trust_env = False
        session.headers.update(
            {"User-Agent": f"BabyCue-Camera/{__version__}", "Accept": "multipart/x-mixed-replace, image/jpeg;q=0.5"}
        )
        with self._lock:
            if self._aborted:
                session.close()
                raise StreamEnded("Stream was closed.")
            self._session = session

        auth = HTTPBasicAuth(self.target.username, self.target.password or "") if self.target.has_credentials else None
        response = self._get(session, auth)
        if response.status_code == 401 and auth is not None:
            challenge = response.headers.get("WWW-Authenticate", "").lower()
            if challenge.startswith("digest"):
                response.close()
                response = self._get(session, HTTPDigestAuth(self.target.username, self.target.password or ""))
        with self._lock:
            self._response = response
        self._check_response(response)

    def _get(self, session: requests.Session, auth) -> requests.Response:
        try:
            return session.get(
                self.target.url,
                stream=True,
                timeout=(self.connect_timeout, self.read_timeout),
                auth=auth,
                verify=self.verify_tls,
            )
        except requests.exceptions.SSLError:
            raise StreamConnectError(
                "TLS/HTTPS certificate check failed. Phone camera apps usually use self-signed certificates; "
                "use the app's http:// URL on a trusted network."
            ) from None
        except requests.exceptions.ConnectTimeout:
            raise StreamTimeout(
                f"{self.target.host}:{self.target.port} did not respond within {self.connect_timeout:g} s. "
                "Check the IP address, that both devices are on the same network, and that the router "
                "does not isolate Wi-Fi clients."
            ) from None
        except requests.exceptions.ReadTimeout:
            raise StreamTimeout(
                f"The phone accepted the connection but sent nothing within {self.read_timeout:g} s."
            ) from None
        except requests.exceptions.ConnectionError as exc:
            raise StreamConnectError(_describe_connection_error(exc, self.target)) from None
        except requests.exceptions.RequestException as exc:
            raise StreamConnectError(f"Could not connect: {exc}") from None

    def _check_response(self, response: requests.Response) -> None:
        status = response.status_code
        if status in (401, 403):
            if self.target.has_credentials:
                raise StreamAuthError("The phone rejected the username or password.", status)
            raise StreamAuthError("The stream requires a username and password (set in the phone app).", status)
        if status == 404:
            raise StreamHttpError(
                "Stream endpoint not found (HTTP 404). Check the path shown by the phone app, e.g. /video.", status
            )
        if status >= 400:
            raise StreamHttpError(f"The phone app returned HTTP {status} {response.reason or ''}".strip() + ".", status)
        if 300 <= status < 400:
            raise StreamProtocolError(f"The address redirects (HTTP {status}); enter the final stream URL directly.")

        content_type = response.headers.get("Content-Type", "")
        self.content_type = content_type
        lowered = content_type.lower()
        if lowered.startswith("multipart/x-mixed-replace") or lowered.startswith("multipart/mixed"):
            self._parser = MjpegParser(boundary_from_content_type(content_type))
            return
        if lowered.startswith("image/jpeg"):
            raise StreamProtocolError(
                "This URL returns a single JPEG snapshot, not a live video stream. "
                "Use the app's MJPEG video URL instead (for IP Webcam: /video)."
            )
        if lowered.startswith("text/html"):
            raise StreamProtocolError(
                "This URL returns a web page, not an MJPEG stream. Add the video path shown by the phone app "
                "(for IP Webcam: /video)."
            )
        raise StreamProtocolError(
            f"Unsupported stream type '{content_type or 'unknown'}'. Only MJPEG over HTTP is supported."
        )

    def frames(self) -> Iterator[bytes]:
        response, parser = self._response, self._parser
        if response is None or parser is None:
            raise StreamError("Stream is not open.")
        raw = response.raw
        while True:
            try:
                data = raw.read1(self.chunk_size)
            except (urllib3.exceptions.ReadTimeoutError, TimeoutError):
                raise StreamTimeout(f"No data received for {self.read_timeout:g} s.") from None
            except (urllib3.exceptions.HTTPError, OSError, ValueError, AttributeError) as exc:
                if self._aborted:
                    raise StreamEnded("Stream was closed.") from None
                raise StreamConnectError(f"Connection to the phone was interrupted ({type(exc).__name__}).") from None
            if not data:
                if self._aborted:
                    raise StreamEnded("Stream was closed.")
                raise StreamEnded("The phone closed the stream.")
            yield from parser.feed(data)

    def abort(self) -> None:
        """Interrupt a blocked read from another thread and release the connection."""
        with self._lock:
            self._aborted = True
            response = self._response
        if response is not None:
            _shutdown_response_socket(response)

    def close(self) -> None:
        self.abort()
        with self._lock:
            response, session = self._response, self._session
            self._response = self._session = None
        if response is not None:
            try:
                response.close()
            except Exception:  # noqa: BLE001 - best effort cleanup
                log.debug("Error closing response", exc_info=True)
        if session is not None:
            session.close()
        self._parser = None


def _shutdown_response_socket(response: requests.Response) -> None:
    """Shut down the TCP connection under ``response`` so a blocked read returns immediately."""
    connection = getattr(response.raw, "_connection", None)
    sock = getattr(connection, "sock", None)
    if isinstance(sock, socket.socket):
        try:
            sock.shutdown(socket.SHUT_RDWR)
            return
        except OSError:
            pass
    # http.client detaches the socket from the connection and keeps only a file object, so
    # shut down through a duplicate of its descriptor (shutdown applies to the connection).
    # socket.fromfd duplicates the handle on both POSIX and Windows.
    try:
        fileno = response.raw._fp.fileno()  # type: ignore[union-attr]
    except (AttributeError, OSError, ValueError):
        return
    try:
        with socket.fromfd(fileno, socket.AF_INET, socket.SOCK_STREAM) as dup:
            dup.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def _describe_connection_error(exc: Exception, target: StreamTarget) -> str:
    text = str(exc).lower()
    where = f"{target.host}:{target.port}"
    if "refused" in text:
        return (
            f"Connection refused by {where}. The phone is reachable but nothing is listening on that port: "
            "make sure streaming is started in the phone app and the port is correct."
        )
    if "name or service not known" in text or "getaddrinfo" in text or "nodename" in text or "resolve" in text:
        return f"Could not resolve host '{target.host}'. Use the phone's IP address."
    if "unreachable" in text or "no route" in text:
        return f"{where} is unreachable. Check that the PC and phone are on the same network."
    return f"Could not connect to {where}. Check the address, Wi-Fi network and firewall."
