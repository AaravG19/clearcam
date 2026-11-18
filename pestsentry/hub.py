"""Bounded frame ingestion, Clearcam tracking, event storage and snapshot alerts."""

import logging
import os
import sqlite3
import threading
import time
import uuid
from http.client import HTTPException
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import cv2
import numpy as np

from ocsort_tracker.ocsort import KalmanBoxTracker, OCSort

from .detector import DemoDetector, OnnxDetector

LOG = logging.getLogger(__name__)


def decode_jpeg(data, max_bytes, max_pixels):
    """Check JPEG dimensions before allocating its decompressed image."""
    if not data or len(data) > max_bytes or data[:2] != b"\xff\xd8" or data[-2:] != b"\xff\xd9":
        raise ValueError("expected a complete JPEG within hub.max_jpeg_bytes")
    offset = 2
    dimensions = None
    while offset + 4 <= len(data):
        if data[offset] != 255:
            raise ValueError("invalid JPEG marker")
        while offset < len(data) and data[offset] == 255:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in (0xD9, 0xDA):
            break
        length = int.from_bytes(data[offset:offset + 2], "big")
        if length < 2 or offset + length > len(data):
            raise ValueError("truncated JPEG segment")
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if length < 8:
                raise ValueError("invalid JPEG dimensions")
            height = int.from_bytes(data[offset + 3:offset + 5], "big")
            width = int.from_bytes(data[offset + 5:offset + 7], "big")
            dimensions = width, height
            break
        offset += length
    if dimensions is None or min(dimensions) < 1 or dimensions[0] * dimensions[1] > max_pixels:
        raise ValueError("JPEG dimensions exceed hub.max_image_pixels or are missing")
    frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None or frame.shape[1::-1] != dimensions:
        raise ValueError("JPEG could not be decoded")
    return frame


class EventStore:
    def __init__(self, directory, max_events):
        Path(directory).mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(Path(directory) / "events.sqlite3", check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.max_events = max_events
        with self.connection:
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, camera_id TEXT, camera_name TEXT, label TEXT,
                    confidence REAL, track_key TEXT UNIQUE, captured_at REAL, created_at REAL,
                    notification_status TEXT, notification_attempts INTEGER DEFAULT 0,
                    notification_error TEXT DEFAULT '', notify_after REAL, notified_at REAL,
                    snapshot BLOB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_time ON events(created_at);
                CREATE TABLE IF NOT EXISTS cooldowns (
                    camera_id TEXT, label TEXT, reserved_at REAL,
                    PRIMARY KEY(camera_id, label)
                );
            """)

    def add(self, camera, label, score, track_key, captured_at, jpeg, cooldown, notifications, now=None):
        now = time.time() if now is None else now
        with self.lock, self.connection:
            if self.connection.execute("SELECT 1 FROM events WHERE track_key=?", (track_key,)).fetchone():
                return None
            row = self.connection.execute("SELECT reserved_at FROM cooldowns WHERE camera_id=? AND label=?",
                                          (camera["id"], label)).fetchone()
            status = "disabled"
            if notifications:
                status = "suppressed" if row and now - row[0] < cooldown else "pending"
            event_id = uuid.uuid4().hex
            self.connection.execute("""INSERT INTO events
                (id, camera_id, camera_name, label, confidence, track_key, captured_at,
                 created_at, notification_status, notify_after, snapshot)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event_id, camera["id"], camera["name"], label, float(score), track_key,
                 captured_at, now, status, now, jpeg))
            if status == "pending":
                self.connection.execute("INSERT OR REPLACE INTO cooldowns VALUES (?, ?, ?)",
                                        (camera["id"], label, now))
            self.connection.execute("""DELETE FROM events WHERE id IN
                (SELECT id FROM events ORDER BY created_at DESC, rowid DESC LIMIT -1 OFFSET ?)""",
                (self.max_events,))
            return event_id

    def search(self, camera=None, label=None, since=None, until=None, confidence=0, query="", limit=100):
        where = ["confidence >= ?"]
        values = [confidence]
        for column, value, operator in (("camera_id", camera, "="), ("label", label, "="),
                                        ("captured_at", since, ">="), ("captured_at", until, "<=")):
            if value is not None and value != "":
                where.append(f"{column} {operator} ?")
                values.append(value)
        if query:
            where.append("(instr(lower(label), lower(?)) > 0 OR instr(lower(camera_name), lower(?)) > 0)")
            values.extend([query, query])
        with self.lock:
            rows = self.connection.execute("""SELECT id, camera_id, camera_name, label,
                confidence, track_key, captured_at, created_at, notification_status,
                notification_attempts, notification_error, notified_at FROM events WHERE """ +
                " AND ".join(where) + " ORDER BY created_at DESC, rowid DESC LIMIT ?", values + [limit]).fetchall()
        return [dict(row) for row in rows]

    def snapshot(self, event_id):
        with self.lock:
            row = self.connection.execute("SELECT snapshot FROM events WHERE id=?", (event_id,)).fetchone()
        return bytes(row[0]) if row else None

    def due_notification(self):
        with self.lock:
            row = self.connection.execute("""SELECT id, camera_name, label, confidence,
                notification_attempts, snapshot FROM events WHERE notification_status='pending'
                AND notify_after <= ? ORDER BY created_at LIMIT 1""", (time.time(),)).fetchone()
        return dict(row) if row else None

    def finish_notification(self, event_id, attempts, error=None):
        status = "sent" if error is None else ("failed" if attempts >= 3 else "pending")
        now = time.time()
        with self.lock, self.connection:
            self.connection.execute("""UPDATE events SET notification_status=?, notification_attempts=?,
                notification_error=?, notify_after=?, notified_at=? WHERE id=?""",
                (status, attempts, error or "", now + 2 ** attempts,
                 now if status == "sent" else None, event_id))

    def close(self):
        with self.lock:
            self.connection.close()


class CameraTracker:
    """Keep Clearcam's tracker and confirmation history separate for every camera."""

    def __init__(self, config):
        self.max_age = config["max_age_frames"]
        count = KalmanBoxTracker.count
        self.tracker = OCSort(max_age=self.max_age, min_hits=0, iou_threshold=config["iou_threshold"])
        KalmanBoxTracker.count = count
        self.hits = {}
        self.emitted = set()

    def update(self, detections):
        tracks = self.tracker.update(detections, det_thresh=0)
        # Clearcam extends stationary tracks to 600 frames. Use the profile's
        # explicit age bound so a long-running small hub keeps bounded history.
        self.tracker.trackers = [track for track in self.tracker.trackers
                                 if track.time_since_update <= self.max_age]
        latest_scores = {}
        for track in self.tracker.trackers:
            history_size = self.max_age + self.tracker.delta_t + 2
            track.observations = dict(list(track.observations.items())[-history_size:])
            track.history_observations = track.history_observations[-history_size:]
            track.kf.history_obs = track.kf.history_obs[-history_size:]
            if track.kf.observed:
                track.kf.attr_saved = None
            latest_scores[track.id + 1] = (track.last_observation[4]
                                          if track.last_observation[4] >= 0 else track.score)
        for track in tracks:
            # Upstream's returned score otherwise retains its first observation.
            track.score = latest_scores[int(track.track_id)]
        active = {int(track.id) + 1 for track in self.tracker.trackers}
        current = {int(track.track_id) for track in tracks}
        self.hits = {track_id: self.hits.get(track_id, 0) + 1 for track_id in current}
        self.emitted.intersection_update(active)
        return tracks


class Hub:
    def __init__(self, config, detector=None, notify_url=None):
        self.config = config
        self.camera_configs = {camera["id"]: camera for camera in config["cameras"]}
        model = config["model"]
        classes = config["alerts"]["classes"]
        if detector is None:
            detector = (DemoDetector(model["labels"], classes) if model["backend"] == "demo"
                        else OnnxDetector(model, classes))
        self.detector = detector
        self.trackers = {camera_id: CameraTracker(config["tracking"]) for camera_id in self.camera_configs}
        self.frames = {camera_id: {"version": 0, "processed": 0, "frame": None,
                                  "received_at": 0, "captured_at": 0, "jpeg": None,
                                  "error": "waiting for a frame", "processed_at": None}
                       for camera_id in self.camera_configs}
        self.lock = threading.Lock()
        self.store = EventStore(config["hub"]["data_dir"], config["hub"]["max_events"])
        self.run_id = uuid.uuid4().hex[:12]
        self.stop_event = threading.Event()
        self.threads = []
        self.started_at = time.monotonic()
        self.notify_url = os.environ.get("PESTSENTRY_NTFY_URL", "") if notify_url is None else notify_url
        if self.notify_url:
            url = urlsplit(self.notify_url)
            if url.scheme not in ("http", "https") or not url.hostname:
                raise ValueError("PESTSENTRY_NTFY_URL must be an HTTP(S) topic URL")

    def publish(self, camera_id, data):
        settings = self.config["hub"]
        frame = decode_jpeg(data, settings["max_jpeg_bytes"], settings["max_image_pixels"])
        with self.lock:
            slot = self.frames[camera_id]
            slot.update(frame=frame, captured_at=time.time(), received_at=time.monotonic(), error="")
            slot["version"] += 1

    def capture_loop(self, camera):
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                request = Request(camera["url"], headers={"Accept": "image/jpeg", "Cache-Control": "no-cache"})
                with urlopen(request, timeout=camera["timeout_seconds"]) as response:
                    data = response.read(self.config["hub"]["max_jpeg_bytes"] + 1)
                self.publish(camera["id"], data)
            except (OSError, ValueError, URLError, HTTPException) as error:
                with self.lock:
                    self.frames[camera["id"]]["error"] = f"capture failed ({type(error).__name__})"
            self.stop_event.wait(max(0, camera["poll_seconds"] - (time.monotonic() - started)))

    def process_camera(self, camera_id):
        with self.lock:
            slot = self.frames[camera_id]
            if slot["version"] == slot["processed"]:
                return
            slot["processed"] = slot["version"]
            if time.monotonic() - slot["received_at"] > self.config["hub"]["max_frame_age_seconds"]:
                slot["error"] = "frame expired before inference"
                return
            frame, captured_at = slot["frame"], slot["captured_at"]
            previous_at = slot["processed_at"]
        if previous_at and captured_at - previous_at > self.config["hub"]["max_frame_age_seconds"]:
            self.trackers[camera_id].tracker.trackers.clear()
            self.trackers[camera_id].hits.clear()
            self.trackers[camera_id].emitted.clear()
        detections = self.detector.detect(frame)
        tracks = self.trackers[camera_id].update(detections)
        annotated = frame.copy()
        for track in tracks:
            x1, y1, x2, y2 = np.asarray(track.tlbr, dtype=int)
            label = self.config["model"]["labels"][int(track.class_id)]
            text = f"{label} #{int(track.track_id)} {track.score:.2f}"
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 220, 255), 2)
            cv2.putText(annotated, text, (max(0, x1), max(15, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45, (0, 220, 255), 1)
        encoded, jpeg = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not encoded:
            raise RuntimeError("snapshot encoding failed")
        jpeg = jpeg.tobytes()
        tracker = self.trackers[camera_id]
        alerts = self.config["alerts"]
        for track in tracks:
            track_id = int(track.track_id)
            if track_id in tracker.emitted or tracker.hits[track_id] < alerts["confirmation_frames"]:
                continue
            if track.score < alerts["confidence"]:
                continue
            label = self.config["model"]["labels"][int(track.class_id)]
            if label not in alerts["classes"]:
                continue
            key = f"{camera_id}:{self.run_id}:{track_id}"
            self.store.add(self.camera_configs[camera_id], label, track.score, key, captured_at,
                           jpeg, alerts["cooldown_seconds"], bool(self.notify_url))
            tracker.emitted.add(track_id)
        with self.lock:
            self.frames[camera_id].update(jpeg=jpeg, processed_at=captured_at)

    def inference_loop(self):
        while not self.stop_event.is_set():
            for camera_id in self.camera_configs:
                try:
                    self.process_camera(camera_id)
                except Exception as error:
                    LOG.error("Inference failed for %s (%s)", camera_id, type(error).__name__)
                    with self.lock:
                        self.frames[camera_id]["error"] = f"inference failed ({type(error).__name__})"
            self.stop_event.wait(0.05)

    def send_notification(self, event):
        title = f"PestSentry {event['camera_name']} {event['label']}"
        headers = {"Content-Type": "image/jpeg", "Filename": "pest.jpg",
                   "Title": title.encode("ascii", "replace").decode("ascii"),
                   "Message": f"Confidence {event['confidence']:.0%}", "Tags": "warning"}
        token = os.environ.get("PESTSENTRY_NTFY_TOKEN", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(self.notify_url, data=event["snapshot"], headers=headers, method="POST")
        with urlopen(request, timeout=5) as response:
            response.read(1024)

    def notify_once(self):
        if not self.notify_url:
            return False
        event = self.store.due_notification()
        if event is None:
            return False
        try:
            self.send_notification(event)
            self.store.finish_notification(event["id"], event["notification_attempts"] + 1)
        except (OSError, URLError, ValueError, HTTPException) as error:
            self.store.finish_notification(event["id"], event["notification_attempts"] + 1,
                                           f"upload failed ({type(error).__name__})")
        return True

    def notification_loop(self):
        while not self.stop_event.is_set():
            self.notify_once()
            self.stop_event.wait(0.5)

    def start(self):
        jobs = [(self.capture_loop, (camera,)) for camera in self.camera_configs.values()
                if camera["source"] == "snapshot"]
        jobs += [(self.inference_loop, ()), (self.notification_loop, ())]
        for target, args in jobs:
            thread = threading.Thread(target=target, args=args, daemon=True)
            thread.start()
            self.threads.append(thread)

    def stop(self):
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=35)
        if all(not thread.is_alive() for thread in self.threads):
            self.store.close()

    def status(self):
        with self.lock:
            cameras = [{"id": camera_id, "name": self.camera_configs[camera_id]["name"],
                        "last_frame_age_seconds": round(time.monotonic() - slot["received_at"], 1)
                        if slot["received_at"] else None,
                        "stale": bool(slot["received_at"] and time.monotonic() - slot["received_at"] >
                                      self.config["hub"]["max_frame_age_seconds"]),
                        "processed_at": slot["processed_at"], "error": slot["error"]}
                       for camera_id, slot in self.frames.items()]
        return {"uptime_seconds": round(time.monotonic() - self.started_at), "cameras": cameras,
                "model_backend": self.config["model"]["backend"],
                "notifications_enabled": bool(self.notify_url)}
