"""Serve one MJPEG stream per PestSentry node, with no ESP32 attached.

ffmpeg must be on PATH. Frames are a moving blob, so this checks the
pipeline (connect, decode, alert) rather than the pest model.

    python3 -m pestsentry fake-cameras --port 8090
    # clearcam / the profile then uses http://127.0.0.1:8090/stream/node-1
"""

from __future__ import annotations

import argparse
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pestsentry.jpegutil import sample_frames

FRAMES: list[bytes] = []
NAMES = [f"node-{i}" for i in range(1, 6)]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def _node(self):
        parts = self.path.split("?", 1)[0].strip("/").split("/")
        if len(parts) == 2 and parts[0] in ("stream", "snapshot") and parts[1] in NAMES:
            return parts[0], parts[1]
        return None, None

    def do_GET(self):
        kind, name = self._node()
        if self.path.split("?", 1)[0] in ("/", "/index.html"):
            body = _index().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if kind == "snapshot":
            jpg = _frame_for(name)
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpg)))
            self.end_headers()
            self.wfile.write(jpg)
            return
        if kind == "stream":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                i = NAMES.index(name)
                while True:
                    jpg = FRAMES[(i + int(time.time() * 2)) % len(FRAMES)]
                    header = (
                        b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                        + str(len(jpg)).encode()
                        + b"\r\n\r\n"
                    )
                    self.wfile.write(header)
                    self.wfile.write(jpg)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                    time.sleep(0.2)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                return
            return
        self.send_error(404)


def _frame_for(name: str) -> bytes:
    return FRAMES[NAMES.index(name) % len(FRAMES)]


def _index() -> str:
    items = "\n".join(
        f"<li><a href='/stream/{name}'>{name} stream</a> "
        f"(<a href='/snapshot/{name}'>jpeg</a>)</li>"
        for name in NAMES
    )
    return (
        "<!doctype html><title>PestSentry fake cameras</title>"
        "<h1>Fake ESP32-CAM nodes</h1>"
        f"<ul>{items}</ul>"
        "<p>Point the PestSentry profile at these URLs to test without hardware.</p>"
    )


def serve(port: int = 8090, host: str = "127.0.0.1"):
    global FRAMES
    if not FRAMES:
        FRAMES = sample_frames()
    httpd = ThreadingHTTPServer((host, port), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def main(argv=None):
    parser = argparse.ArgumentParser(description="MJPEG stand-in for five ESP32-CAM nodes")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args(argv)
    httpd = serve(args.port, args.host)
    bound = httpd.server_address
    print(f"Fake cameras at http://{bound[0]}:{bound[1]}/")
    for name in NAMES:
        print(f"  http://127.0.0.1:{bound[1]}/stream/{name}")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
