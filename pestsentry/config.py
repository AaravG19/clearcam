"""Read the hub profile without importing an inference runtime."""

import json
import math
import re
from pathlib import Path
from urllib.parse import urlsplit


def _number(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def load_config(path):
    path = Path(path).resolve()
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("config must be a JSON object")
    defaults = {
        "hub": {"host": "127.0.0.1", "port": 8081, "data_dir": "data",
                "max_frame_age_seconds": 10, "max_jpeg_bytes": 524288,
                "max_image_pixels": 1280 * 720, "max_events": 1000},
        "model": {"backend": "onnx", "path": "models/pest-yolov8n.onnx",
                  "input_size": 320, "threads": 2, "confidence": 0.25,
                  "iou_threshold": 0.45, "labels": []},
        "alerts": {"classes": [], "confidence": 0.6, "cooldown_seconds": 120,
                   "confirmation_frames": 2},
        "tracking": {"max_age_frames": 5, "iou_threshold": 0.2},
    }
    for section, values in defaults.items():
        supplied = config.get(section, {})
        if not isinstance(supplied, dict):
            raise ValueError(f"{section} must be an object")
        unknown = set(supplied) - set(values)
        if unknown:
            raise ValueError(f"unknown {section} fields: {sorted(unknown)}")
        config[section] = {**values, **supplied}
    numeric_fields = (
        ("hub", "port", 1, 65535), ("hub", "max_frame_age_seconds", 0.1, 3600),
        ("hub", "max_jpeg_bytes", 1024, 2 * 1024 * 1024),
        ("hub", "max_image_pixels", 1024, 4096 * 4096), ("hub", "max_events", 1, 100000),
        ("model", "input_size", 32, 1280), ("model", "threads", 1, 16),
        ("model", "confidence", 0.01, 1), ("model", "iou_threshold", 0.01, 1),
        ("alerts", "confidence", 0.01, 1), ("alerts", "cooldown_seconds", 0, 86400),
        ("alerts", "confirmation_frames", 1, 100),
        ("tracking", "max_age_frames", 1, 600), ("tracking", "iou_threshold", 0.01, 1),
    )
    for section, field, low, high in numeric_fields:
        _number(config[section][field], f"{section}.{field}", low, high)
    for section, field in (("hub", "port"), ("hub", "max_jpeg_bytes"),
                           ("hub", "max_image_pixels"), ("hub", "max_events"),
                           ("model", "input_size"), ("model", "threads"),
                           ("alerts", "confirmation_frames"), ("tracking", "max_age_frames")):
        if not isinstance(config[section][field], int):
            raise ValueError(f"{section}.{field} must be an integer")
    model = config["model"]
    if model["backend"] not in ("onnx", "demo"):
        raise ValueError("model.backend must be onnx or demo")
    if model["input_size"] % 32:
        raise ValueError("model.input_size must be a multiple of 32")
    labels = model["labels"]
    if not isinstance(labels, list) or not labels or not all(isinstance(x, str) and x for x in labels):
        raise ValueError("model.labels must contain the model's ordered class names")
    if len(set(labels)) != len(labels):
        raise ValueError("model.labels must be unique")
    classes = config["alerts"]["classes"]
    if not isinstance(classes, list) or not classes or any(x not in labels for x in classes):
        raise ValueError("alerts.classes must contain names from model.labels")
    if config["alerts"]["confidence"] < model["confidence"]:
        raise ValueError("alerts.confidence must be at least model.confidence")
    cameras = config.get("cameras")
    if not isinstance(cameras, list) or not cameras:
        raise ValueError("cameras must contain at least one camera")
    seen = set()
    for camera in cameras:
        if not isinstance(camera, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", camera.get("id", "")):
            raise ValueError("camera.id must use 1-40 letters, digits, underscores or hyphens")
        if camera["id"] in seen:
            raise ValueError(f"duplicate camera id {camera['id']}")
        seen.add(camera["id"])
        camera.setdefault("name", camera["id"])
        camera.setdefault("source", "snapshot")
        camera.setdefault("poll_seconds", 2)
        camera.setdefault("timeout_seconds", 3)
        _number(camera["poll_seconds"], "camera.poll_seconds", 0.1, 3600)
        _number(camera["timeout_seconds"], "camera.timeout_seconds", 0.1, 30)
        if camera["source"] not in ("snapshot", "push"):
            raise ValueError("camera.source must be snapshot or push")
        if camera["source"] == "snapshot":
            url = urlsplit(camera.get("url", ""))
            if url.scheme not in ("http", "https") or not url.hostname:
                raise ValueError("snapshot cameras need an HTTP(S) url")
    for section, field in (("hub", "data_dir"), ("model", "path")):
        value = config[section][field]
        if not isinstance(value, str) or not value:
            raise ValueError(f"{section}.{field} must be a path")
        config[section][field] = str((path.parent / value).resolve())
    return config
