"""Build a few JPEG frames with ffmpeg. Clearcam already requires ffmpeg."""

from __future__ import annotations

import shutil
import subprocess


def require_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("ffmpeg is required (clearcam needs it too)")
    return path


def rgb_frame(width: int, height: int, blob_x: int, blob_y: int, blob: int = 36) -> bytes:
    raw = bytearray(width * height * 3)
    # Dark floor, a light moving blob. Enough for a stream test, not a real animal.
    for y in range(height):
        row = y * width * 3
        shade = 18 + (y % 8)
        for x in range(width):
            i = row + x * 3
            raw[i] = shade
            raw[i + 1] = shade
            raw[i + 2] = shade + 6
    x0 = max(0, blob_x)
    y0 = max(0, blob_y)
    x1 = min(width, blob_x + blob)
    y1 = min(height, blob_y + blob)
    for y in range(y0, y1):
        row = y * width * 3
        for x in range(x0, x1):
            i = row + x * 3
            raw[i] = 40
            raw[i + 1] = 180
            raw[i + 2] = 70
    return bytes(raw)


def encode_jpeg(rgb: bytes, width: int, height: int) -> bytes:
    cmd = [
        require_ffmpeg(), "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}",
        "-i", "pipe:0",
        "-frames:v", "1",
        "-f", "image2", "-c:v", "mjpeg", "-q:v", "6",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, input=rgb, capture_output=True, check=True)
    if not proc.stdout.startswith(b"\xff\xd8"):
        raise RuntimeError("ffmpeg did not return a JPEG")
    return proc.stdout


def sample_frames(count: int = 8, width: int = 320, height: int = 240) -> list[bytes]:
    frames = []
    for i in range(count):
        x = 20 + i * ((width - 80) // max(1, count - 1))
        y = 40 + (i % 3) * 30
        frames.append(encode_jpeg(rgb_frame(width, height, x, y), width, height))
    return frames
