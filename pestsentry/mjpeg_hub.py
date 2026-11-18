# PestSentry hub integration for clearcam. Copyright (C) 2026 Aarav Gaudani. Licensed under GPL-3.0-or-later; see ../LICENSE.md (or ../../LICENSE.md for firmware).
import argparse
import json
import sys
import threading
import time
from email.parser import BytesHeaderParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import cv2
import numpy as np


def jpeg(image, quality):
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return encoded.tobytes()


def canvas(label, gray=0, x=None):
    image = np.full((480, 640, 3), gray, dtype=np.uint8)
    if x is not None:
        cv2.rectangle(image, (x, 180), (x + 80, 260), (45, 180, 240), -1)
    cv2.putText(image, label, (15, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                (255, 255, 255), 2, cv2.LINE_AA)
    return image


class Frames:
    def __init__(self, cfg):
        self.quality = cfg["jpeg_quality"]
        self.interval = 1 / cfg["fps"]
        if self.interval <= 0:
            raise ValueError("fps must be positive")
        self.ids = tuple(node["id"] for node in cfg["nodes"])
        self.lock = threading.Lock()
        self.latest = {i: jpeg(canvas(f"NO SIGNAL cam_{i}"), self.quality) for i in self.ids}

    def update(self, frames):
        with self.lock:
            self.latest.update(frames)

    def read(self, node):
        with self.lock:
            return self.latest[node]


def handler(frames, stop):
    routes = {f"/cam/{i}": i for i in frames.ids}

    class Stream(BaseHTTPRequestHandler):
        def do_GET(self):
            node = routes.get(self.path)
            if node is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            try:
                while not stop.is_set():
                    data = frames.read(node)
                    header = f"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {len(data)}\r\n\r\n"
                    self.wfile.write(header.encode("ascii") + data + b"\r\n")
                    stop.wait(frames.interval)
            except ConnectionError:
                pass

    return Stream


def serial_source(frames, stop, settings):
    import serial

    buffer = bytearray()
    with serial.Serial(settings["port"], settings["baud"], timeout=0.25) as port:
        while not stop.is_set():
            buffer.extend(port.read(min(8192, port.in_waiting or 1)))
            while len(buffer) >= 9:
                start = buffer.find(b"PSN1")
                if start < 0:
                    del buffer[:-3]
                    break
                del buffer[:start]
                if len(buffer) < 9:
                    break
                node = buffer[4]
                size = int.from_bytes(buffer[5:9], "big")
                if node not in frames.ids or not 4 <= size <= 60 * 1024:
                    del buffer[:1]
                    continue
                if len(buffer) < 9 + size:
                    break
                data = bytes(buffer[9:9 + size])
                if data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9"):
                    frames.update({node: data})
                    del buffer[:9 + size]
                else:
                    del buffer[:1]


def produce(args, cfg, frames, stop, ready):
    if args.source == "serial":
        serial_source(frames, stop, cfg["serial"])
        return
    cap = None
    try:
        if args.source == "video":
            device = int(args.video) if args.video.isdecimal() else args.video
            cap = cv2.VideoCapture(device)
            if not cap.isOpened():
                raise ValueError(f"Cannot open video {device}")
        while not stop.is_set():
            tick = time.monotonic()
            if cap is None:
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                batch = {}
                for node in frames.ids:
                    x = int(tick * 100 + node * 101) % 560
                    batch[node] = jpeg(canvas(f"cam_{node} {ts}", 95, x), frames.quality)
            else:
                ok, image = cap.read()
                if not ok and isinstance(device, str):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ok, image = cap.read()
                if not ok:
                    raise ValueError("Cannot read a video frame")
                batch = dict.fromkeys(frames.ids, jpeg(image, frames.quality))
            frames.update(batch)
            ready.set()
            stop.wait(max(0, tick + frames.interval - time.monotonic()))
    finally:
        if cap is not None:
            cap.release()


def probe(port, node):
    with urlopen(f"http://127.0.0.1:{port}/cam/{node}", timeout=5) as response:
        if (response.headers.get_param("boundary") != "frame"
                or response.readline() != b"--frame\r\n"):
            raise ValueError("Missing multipart boundary")
        headers = bytearray()
        while (line := response.readline(1024)) != b"\r\n":
            if not line or len(headers) > 4096:
                raise ValueError("Invalid part headers")
            headers.extend(line)
        part = BytesHeaderParser().parsebytes(headers)
        if part.get_content_type() != "image/jpeg":
            raise ValueError("Expected JPEG part")
        data = response.read(int(part["Content-Length"]))
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.shape[:2] != (480, 640):
            raise ValueError("Expected a decodable 640x480 JPEG")


def main():
    parser = argparse.ArgumentParser(description="Serve a latest-frame MJPEG stream per node")
    parser.add_argument("--source", choices=("serial", "video", "synthetic"), default="serial")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--video", default="0")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    path = Path(__file__).with_name("pestsentry.config.json")
    if not path.exists():
        path = path.with_name("pestsentry.config.example.json")
    cfg = json.loads((args.config or path).read_text(encoding="utf-8"))
    frames = Frames(cfg)
    stop, ready = threading.Event(), threading.Event()
    addr = (cfg["hub"]["host"], cfg["hub"]["port"])
    if args.selftest:
        args.source, addr = "synthetic", ("127.0.0.1", 0)
    with ThreadingHTTPServer(addr, handler(frames, stop)) as server:
        serving = threading.Thread(target=server.serve_forever)
        serving.start()
        try:
            if args.selftest:
                source = threading.Thread(target=produce, args=(args, cfg, frames, stop, ready))
                source.start()
                try:
                    if not ready.wait(5):
                        raise ValueError("Synthetic source timed out")
                    probe(server.server_port, frames.ids[0])
                finally:
                    stop.set()
                    source.join()
                print("SELFTEST OK")
            else:
                produce(args, cfg, frames, stop, ready)
        finally:
            stop.set()
            server.shutdown()
            serving.join()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        sys.exit(f"Hub failed: {exc}")
