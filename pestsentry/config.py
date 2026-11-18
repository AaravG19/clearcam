"""Load a PestSentry hub config. No third-party dependencies."""

from __future__ import annotations

import json
from pathlib import Path

# COCO 80, same order as darknet's coco.names. "mouse" here is a computer mouse.
COCO_LABELS = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
    "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
    "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors",
    "teddy bear", "hair drier", "toothbrush",
)

BACKENDS = ("clearcam", "ultralytics")


def load_label_file(path: Path) -> list[str]:
    names = [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.strip().startswith("#")]
    if not names:
        raise ValueError(f"label file {path} is empty")
    if len(names) != len(set(names)):
        raise ValueError(f"label file {path} has duplicate names")
    return names


def load_config(path: str | Path) -> dict:
    path = Path(path).resolve()
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        raise SystemExit(f"config not found: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise SystemExit("config must be a JSON object")

    hub = raw.get("hub") or {}
    model = raw.get("model") or {}
    alerts = raw.get("alerts") or {}
    cameras = raw.get("cameras")
    if not isinstance(cameras, list) or not cameras:
        raise SystemExit("config needs a non-empty cameras list")

    backend = model.get("backend", "clearcam")
    if backend not in BACKENDS:
        raise SystemExit(f"model.backend must be one of {BACKENDS}, got {backend!r}")

    try:
        res = int(model.get("res", 640))
        confidence = float(alerts.get("confidence", 0.45))
        cooldown = float(alerts.get("cooldown_seconds", 120))
        min_speed = float(alerts.get("min_speed", 0))
        min_crop = int(alerts.get("min_crop", 24))
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"numeric config field is invalid: {exc}") from exc
    if res < 32 or res % 32 != 0:
        raise SystemExit("model.res must be a positive multiple of 32")
    if not 0 < confidence <= 1:
        raise SystemExit("alerts.confidence must be between 0 and 1")
    if cooldown < 0:
        raise SystemExit("alerts.cooldown_seconds must be >= 0")
    if min_crop < 1:
        raise SystemExit("alerts.min_crop must be >= 1")

    labels_file = model.get("labels_file") or ""
    weights = model.get("weights") or ""
    if backend == "ultralytics":
        if not weights:
            raise SystemExit("ultralytics backend needs model.weights set to a .pt file")
        if not labels_file:
            raise SystemExit("ultralytics backend needs model.labels_file")
        label_names = load_label_file(_resolve(path, labels_file))
        weights = str(_resolve(path, weights))
    else:
        if labels_file:
            raise SystemExit("labels_file is only used with the ultralytics backend; clearcam uses COCO labels")
        label_names = list(COCO_LABELS)

    class_names = alerts.get("classes")
    if not isinstance(class_names, list) or not class_names:
        raise SystemExit("alerts.classes must list at least one class name")
    class_ids = []
    for name in class_names:
        if name not in label_names:
            raise SystemExit(f"unknown class {name!r}. Labels are: {', '.join(label_names)}")
        if label_names == list(COCO_LABELS) and name == "mouse":
            raise SystemExit(
                "COCO class 'mouse' is a computer mouse, not a rodent. "
                "Use the ultralytics backend with a pest label file for rats and mice."
            )
        class_ids.append(label_names.index(name))

    seen = set()
    cleaned = []
    for cam in cameras:
        if not isinstance(cam, dict) or not cam.get("name") or not cam.get("url"):
            raise SystemExit("each camera needs a name and a url")
        name = str(cam["name"])
        if name in seen:
            raise SystemExit(f"duplicate camera name {name!r}")
        seen.add(name)
        cleaned.append({
            "name": name,
            "url": str(cam["url"]),
            "location": str(cam.get("location") or ""),
        })

    notify_url = str(hub.get("notify_url") or "").rstrip("/")
    return {
        "path": str(path),
        "hub": {
            "notify_url": notify_url,
            "ntfy_topic": str(hub.get("ntfy_topic") or ""),
            "dashboard_port": int(hub.get("dashboard_port") or 8080),
        },
        "model": {
            "backend": backend,
            "size": str(model.get("size") or "t"),
            "res": res,
            "weights": weights,
            "labels_file": str(labels_file),
        },
        "alerts": {
            "confidence": confidence,
            "cooldown_seconds": cooldown,
            "min_speed": min_speed,
            "min_crop": min_crop,
            "classes": [str(n) for n in class_names],
            "class_ids": class_ids,
        },
        "labels": label_names,
        "cameras": cleaned,
    }


def _resolve(config_path: Path, value: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = config_path.parent / candidate
    return candidate.resolve()
