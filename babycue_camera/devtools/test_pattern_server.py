"""Local MJPEG test server that emits a clearly labelled synthetic test pattern.

Used by the automated tests and for trying the viewer without a phone. It is never started by
the viewer itself, and every frame is stamped "SYNTHETIC TEST PATTERN" so it cannot be mistaken
for a real camera feed.

    python -m babycue_camera.devtools.test_pattern_server --port 8081
"""

from __future__ import annotations

import argparse
import base64
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

BOUNDARY = "babycueframe"


def render_pattern(
    index: int,
    width: int = 640,
    height: int = 480,
    *,
    brightness: float = 1.0,
    blur: int = 0,
) -> np.ndarray:
    y, x = np.mgrid[0:height, 0:width]
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[..., 0] = (x * 255 // max(1, width - 1)).astype(np.uint8)
    image[..., 1] = (y * 255 // max(1, height - 1)).astype(np.uint8)
    image[..., 2] = 96
    checker = ((x // 40 + y // 40) % 2).astype(bool)
    image[checker] = image[checker] // 2
    offset = (index * 8) % max(1, width - 80)
    cv2.rectangle(image, (offset, height // 2 - 40), (offset + 80, height // 2 + 40), (255, 255, 255), -1)
    cv2.putText(image, "SYNTHETIC TEST PATTERN", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
    cv2.putText(image, f"frame {index}", (20, height - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    if blur > 0:
        k = blur * 2 + 1
        image = cv2.GaussianBlur(image, (k, k), 0)
    if brightness != 1.0:
        image = np.clip(image.astype(np.float32) * brightness, 0, 255).astype(np.uint8)
    return image


class TestPatternServer:
    __test__ = False  # not a pytest test class

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        *,
        fps: float = 15.0,
        width: int = 640,
        height: int = 480,
        path: str = "/video",
        username: str | None = None,
        password: str | None = None,
    ):
        self.host = host
        self.requested_port = port
        self.fps = fps
        self.width = width
        self.height = height
        self.path = path
        self.username = username
        self.password = password
        self.brightness = 1.0
        self.blur = 0
        self.paused = threading.Event()
        self._generation = 0
        self._lock = threading.Lock()
        self._clients: set[socket.socket] = set()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = port

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def url(self) -> str:
        return self.base_url + self.path

    @property
    def active_clients(self) -> int:
        with self._lock:
            return len(self._clients)

    def start(self) -> TestPatternServer:
        handler = _make_handler(self)
        server = ThreadingHTTPServer((self.host, self.requested_port or self.port), handler)
        server.daemon_threads = True
        self._server = server
        self.port = server.server_address[1]
        self._thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        self.drop_connections()

    def pause(self) -> None:
        """Keep connections open but stop sending frames (simulates a frozen source)."""
        self.paused.set()

    def resume(self) -> None:
        self.paused.clear()

    def drop_connections(self) -> None:
        with self._lock:
            self._generation += 1
            clients = list(self._clients)
        for sock in clients:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def __enter__(self) -> TestPatternServer:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # internal
    def _register(self, sock: socket.socket) -> int:
        with self._lock:
            self._clients.add(sock)
            return self._generation

    def _unregister(self, sock: socket.socket) -> None:
        with self._lock:
            self._clients.discard(sock)

    def _generation_now(self) -> int:
        with self._lock:
            return self._generation

    def _authorized(self, header: str | None) -> bool:
        if not self.username:
            return True
        expected = base64.b64encode(f"{self.username}:{self.password or ''}".encode()).decode()
        return header == f"Basic {expected}"


def _make_handler(owner: TestPatternServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, format, *args):  # noqa: A002 - silence default logging
            pass

        def do_GET(self):  # noqa: N802
            if not owner._authorized(self.headers.get("Authorization")):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="test"')
                self.end_headers()
                return
            if self.path == owner.path:
                self._stream(valid=True)
            elif self.path == "/garbage":
                self._stream(valid=False)
            elif self.path == "/shot.jpg":
                data = _jpeg(render_pattern(0, owner.width, owner.height))
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif self.path == "/":
                body = b"<html><body>Test pattern server</body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)

        def _stream(self, valid: bool):
            self.send_response(200)
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            generation = owner._register(self.connection)
            index = 0
            try:
                while owner._server is not None and owner._generation_now() == generation:
                    if owner.paused.is_set():
                        time.sleep(0.02)
                        continue
                    if valid:
                        frame = render_pattern(
                            index, owner.width, owner.height, brightness=owner.brightness, blur=owner.blur
                        )
                        data = _jpeg(frame)
                    else:
                        data = b"this is not a jpeg" * 10
                    header = (
                        f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(data)}\r\n\r\n"
                    ).encode()
                    self.wfile.write(header + data + b"\r\n")
                    self.wfile.flush()
                    index += 1
                    time.sleep(1.0 / owner.fps)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                owner._unregister(self.connection)

    return Handler


def _jpeg(image: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return buf.tobytes()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (use 0.0.0.0 to test across the LAN)")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--fps", type=float, default=15.0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--brightness", type=float, default=1.0, help="Multiply pixel values (e.g. 0.15 = dark)")
    args = parser.parse_args()
    server = TestPatternServer(args.host, args.port, fps=args.fps, width=args.width, height=args.height)
    server.brightness = args.brightness
    server.start()
    print(f"Serving synthetic MJPEG test pattern at {server.url}  (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
