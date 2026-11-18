"""Apply a PestSentry profile to a running clearcam process.

Imported by clearcam.py. Stays idle unless PESTSENTRY_PROFILE is set, so
`python3 clearcam.py` keeps the upstream defaults.
"""

from __future__ import annotations

import os
import sys

from pestsentry.config import load_config

_CFG = None


def _profile_path():
    return os.environ.get("PESTSENTRY_PROFILE")


def current_config():
    global _CFG
    path = _profile_path()
    if not path:
        return None
    if _CFG is None or _CFG.get("path") != os.path.abspath(path):
        _CFG = load_config(path)
    return _CFG


def apply_profile(database, alert_cls, settings_cls):
    """Write cameras, alert rules, and hub settings into clearcam's database."""
    cfg = current_config()
    if cfg is None:
        return None

    os.environ["PESTSENTRY_MIN_SPEED"] = str(cfg["alerts"]["min_speed"])
    os.environ["PESTSENTRY_MIN_CROP"] = str(cfg["alerts"]["min_crop"])
    os.environ["PESTSENTRY_BACKEND"] = cfg["model"]["backend"]
    os.environ["PESTSENTRY_DASHBOARD_PORT"] = str(cfg["hub"]["dashboard_port"])

    existing = database.run_get("global_settings", "all")
    user_id = None
    key = None
    if existing not in ({}, None) and hasattr(existing, "userID"):
        user_id = existing.userID
        key = existing.key

    notify = cfg["hub"]["notify_url"] or "https://clearcam.org"
    settings = settings_cls(
        use_clip=False,
        use_face=False,
        model_size=cfg["model"]["size"],
        model_res=cfg["model"]["res"],
        userID=user_id,
        key=key,
        use_qwen=False,
        qwen_size=2,
        qwen_prompt="What pest was detected? Write one short sentence.",
        server_url=notify,
    )
    database.run_put("global_settings", "all", settings)

    class_ids = set(cfg["alerts"]["class_ids"])
    for cam in cfg["cameras"]:
        database.run_put("links", cam["name"], cam["url"])
        database.run_put("settings", cam["name"], {
            "threshold": cfg["alerts"]["confidence"],
            "is_notif": True,
        })
        database.run_delete("alerts", cam["name"])
        alert = alert_cls(
            window_seconds=cfg["alerts"]["cooldown_seconds"],
            max=1,
            classes=class_ids,
            cam_name=cam["name"],
            desc=None,
            threshold=cfg["alerts"]["confidence"],
        )
        # id must not be "1": clearcam returns a bare value for that id, and the
        # alert loop expects a dict of id -> counter.
        database.run_put("alerts", cam["name"], alert, id="pestsentry")

    print(
        f"PestSentry profile {cfg['path']}: {len(cfg['cameras'])} cameras, "
        f"classes {cfg['alerts']['classes']}, backend {cfg['model']['backend']}"
    )
    return cfg


def override_detection(default_classes):
    """Return (class id strings, label list) for the active profile."""
    cfg = current_config()
    if cfg is None:
        raise RuntimeError("override_detection called without PESTSENTRY_PROFILE")
    classes = {str(i) for i in cfg["alerts"]["class_ids"]}
    if not classes:
        return default_classes, list(cfg["labels"])
    return classes, list(cfg["labels"])


def maybe_detector():
    """Build a swapped-in detector, or None to keep clearcam's YOLOv9."""
    cfg = current_config()
    if cfg is None or cfg["model"]["backend"] == "clearcam":
        return None
    if cfg["model"]["backend"] == "ultralytics":
        from pestsentry.detector import UltralyticsDetector
        return UltralyticsDetector(cfg["model"]["weights"], cfg["labels"])
    print(f"unknown PestSentry backend {cfg['model']['backend']}", file=sys.stderr)
    raise SystemExit(1)
