"""Local phone-alert stand-in.

clearcam POSTs multipart form data to {notify_url}/send, including a JPEG
when the host is not clearcam.org. This sink saves that JPEG and prints
the message. If hub.ntfy_topic is set, it also publishes the text to
ntfy.sh/<topic>. Leave the topic empty until you choose one.

    python3 -m pestsentry notify-sink --port 8091
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

OUT_DIR = Path("data/pestsentry_alerts")
TOPIC = ""


def parse_alert(body: bytes, content_type: str) -> dict:
    """Accept clearcam's /send body. The image part is appended after an early closing boundary."""
    text = None
    body_text = None
    image = None
    filename = "alert.jpg"
    marker = "boundary="
    if marker not in content_type:
        return {"text": body.decode("utf-8", "replace"), "body_text": None, "image": None, "filename": filename}
    boundary = content_type.split(marker, 1)[1].strip().strip('"')
    for part in body.split(b"--" + boundary.encode()):
        if not part or part in (b"--\r\n", b"--", b"\r\n"):
            continue
        part = part.lstrip(b"\r\n")
        split_at = part.find(b"\r\n\r\n")
        if split_at < 0:
            continue
        header = part[:split_at].decode("utf-8", "replace")
        data = part[split_at + 4:]
        if data.endswith(b"\r\n"):
            data = data[:-2]
        if data.endswith(b"--"):
            data = data[:-2]
        if 'name="text"' in header:
            text = data.decode("utf-8", "replace")
        elif 'name="body_text"' in header:
            body_text = data.decode("utf-8", "replace")
        elif 'name="img"' in header:
            image = data
            if 'filename="' in header:
                filename = header.split('filename="', 1)[1].split('"', 1)[0] or filename
    return {"text": text, "body_text": body_text, "image": image, "filename": filename}


def save_alert(parsed: dict, out_dir: Path = OUT_DIR) -> Path | None:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    saved = None
    if parsed.get("image"):
        saved = out_dir / f"{stamp}-{Path(parsed['filename']).name}"
        saved.write_bytes(parsed["image"])
    record = {
        "text": parsed.get("text"),
        "body_text": parsed.get("body_text"),
        "image": str(saved) if saved else None,
    }
    (out_dir / f"{stamp}.json").write_text(json.dumps(record))
    print(f"alert: {record['text']} image={record['image']}")
    _forward_ntfy(record["text"])
    return saved


def _forward_ntfy(text: str | None):
    if not TOPIC or not text:
        return
    # Topic is a config value the user chooses. Do not put secrets in the path.
    req = urllib.request.Request(
        f"https://ntfy.sh/{TOPIC}",
        data=text.encode(),
        headers={"Title": "PestSentry"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as exc:
        print(f"ntfy forward failed: {exc}")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length)
        if self.path.split("?", 1)[0] != "/send":
            self.send_error(404)
            return
        parsed = parse_alert(body, self.headers.get("Content-Type", ""))
        save_alert(parsed)
        payload = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def serve(port: int = 8091, host: str = "127.0.0.1", topic: str = ""):
    global TOPIC
    TOPIC = topic
    httpd = ThreadingHTTPServer((host, port), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def main(argv=None):
    parser = argparse.ArgumentParser(description="Save PestSentry alert snapshots")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--topic", default="", help="optional ntfy.sh topic, no token")
    args = parser.parse_args(argv)
    httpd = serve(args.port, args.host, args.topic)
    bound = httpd.server_address
    print(f"Alert sink at http://{bound[0]}:{bound[1]}/send")
    print(f"Snapshots go in {OUT_DIR.resolve()}")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
