"""Exercise alerts without a camera and without the detection model.

Grabs one JPEG from each fake node (or draws one with ffmpeg) and pretends
the tracker saw a dog. Posts the snapshot to the notify sink the same way
clearcam does.

    python3 -m pestsentry notify-sink
    python3 -m pestsentry fake-cameras
    python3 -m pestsentry simulate --config pestsentry/config.fake.json
"""

from __future__ import annotations

import argparse
import time
import urllib.request
import uuid

from pestsentry.alerts import AlertState
from pestsentry.config import load_config
from pestsentry.jpegutil import encode_jpeg, rgb_frame


def fetch_jpeg(url: str, timeout: float = 5) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        data = resp.read()
    if not data.startswith(b"\xff\xd8"):
        raise RuntimeError(f"{url} did not return a JPEG")
    return data


def snapshot_url(stream_url: str) -> str:
    # http://host:port/stream/node-1 -> http://host:port/snapshot/node-1
    return stream_url.replace("/stream/", "/snapshot/", 1)


def build_send_body(text: str, image: bytes, filename: str) -> tuple[bytes, str]:
    """Match utils/helpers.py send_notif, including the image after the first closing boundary."""
    boundary = f"Boundary-{uuid.uuid4()}"
    lines = [
        f"--{boundary}",
        'Content-Disposition: form-data; name="text"',
        "",
        text,
        f"--{boundary}--",
        "",
    ]
    body = "\r\n".join(lines).encode()
    body += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="img"; filename="{filename}"\r\n'
        "Content-Type: image/jpeg\r\n\r\n"
    ).encode() + image + f"\r\n--{boundary}--\r\n".encode()
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


def post_alert(notify_url: str, text: str, image: bytes, filename: str) -> int:
    body, content_type = build_send_body(text, image, filename)
    req = urllib.request.Request(
        notify_url.rstrip("/") + "/send",
        data=body,
        headers={"Content-Type": content_type, "Content-Length": str(len(body))},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        resp.read()
        return resp.status


def run_simulation(cfg: dict, offline: bool = False, now: float | None = None) -> list[dict]:
    state = AlertState(
        cfg["alerts"]["class_ids"],
        cfg["alerts"]["confidence"],
        cfg["alerts"]["cooldown_seconds"],
    )
    # dog is the stand-in pest. A person box is included to prove the filter drops it.
    dog_id = cfg["labels"].index("dog") if "dog" in cfg["labels"] else cfg["alerts"]["class_ids"][0]
    person_id = cfg["labels"].index("person") if "person" in cfg["labels"] else -1
    stamp = time.time() if now is None else now
    sent = []
    for cam in cfg["cameras"]:
        if offline:
            image = encode_jpeg(rgb_frame(320, 240, 80, 60), 320, 240)
        else:
            image = fetch_jpeg(snapshot_url(cam["url"]))
        detections = [
            {"class_id": person_id, "score": 0.99, "track_id": 1},
            {"class_id": dog_id, "score": 0.20, "track_id": 2},  # under the confidence gate
            {"class_id": dog_id, "score": 0.91, "track_id": 3},
            {"class_id": dog_id, "score": 0.95, "track_id": 3},  # same track, one alert
        ]
        fired = False
        for det in detections:
            if state.consider(cam["name"], det["class_id"], det["score"], det["track_id"], stamp):
                fired = True
        if not fired:
            continue
        label = cfg["labels"][dog_id]
        text = f"PestSentry {label} on {cam['name']} ({cam['location'] or 'unassigned'})"
        if cfg["hub"]["notify_url"]:
            post_alert(cfg["hub"]["notify_url"], text, image, f"{cam['name']}.jpg")
        sent.append({"camera": cam["name"], "text": text, "bytes": len(image)})
    return sent


def main(argv=None):
    parser = argparse.ArgumentParser(description="Send a fake pest alert for each configured camera")
    parser.add_argument("--config", default="pestsentry/config.fake.json")
    parser.add_argument("--offline", action="store_true", help="draw JPEGs locally instead of fetching the fake cameras")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    sent = run_simulation(cfg, offline=args.offline)
    print(f"sent {len(sent)} alert(s)")
    for item in sent:
        print(f"  {item['text']} ({item['bytes']} bytes)")
    return 0 if sent else 1


if __name__ == "__main__":
    raise SystemExit(main())
