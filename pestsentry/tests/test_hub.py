"""Hardware-free checks for ingestion, YOLO decoding, tracking, search and alerts."""

import base64
import copy
import json
import logging
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import cv2
import numpy as np
from werkzeug.serving import make_server

from pestsentry.config import load_config
from pestsentry.detector import OnnxDetector
from pestsentry.fake_cameras import create_app as fake_app, make_frame
from pestsentry.hub import CameraTracker, EventStore, Hub, decode_jpeg
from pestsentry.web import create_app


class HubTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = load_config(Path(__file__).parents[1] / "config.demo.json")
        self.config["hub"]["data_dir"] = self.directory.name

    def make_hub(self, config=None, **kwargs):
        hub = Hub(config or self.config, notify_url=kwargs.pop("notify_url", ""), **kwargs)
        self.addCleanup(hub.stop)
        return hub

    def test_five_http_cameras_capture_infer_and_search(self):
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        server = make_server("127.0.0.1", 0, fake_app(), threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        for camera in self.config["cameras"]:
            camera["url"] = f"http://127.0.0.1:{server.server_port}/capture/{camera['id']}"
            camera["poll_seconds"] = 0.1
        hub = self.make_hub()
        hub.start()
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline and len(hub.store.search()) < 5:
            time.sleep(0.05)
        events = hub.store.search()
        self.assertEqual({row["camera_id"] for row in events}, {f"node-{i}" for i in range(1, 6)})
        self.assertEqual(len(events), 5)
        client = create_app(hub, api_token="").test_client()
        self.assertEqual(client.get("/").status_code, 200)
        filtered = client.get("/api/events?camera=node-2&class=demo-pest&confidence=0.9&q=demo").get_json()
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["camera_id"], "node-2")
        snapshot = client.get(filtered[0]["snapshot_url"])
        self.assertEqual(snapshot.mimetype, "image/jpeg")
        self.assertEqual(decode_jpeg(snapshot.data, 524288, 320 * 240).shape, (240, 320, 3))
        self.assertEqual(client.get("/api/cameras/node-1/snapshot.jpg").status_code, 200)
        self.assertEqual(client.get("/api/events?since=99999999999").get_json(), [])
        for query in ("confidence=nan", "since=nan", "until=bad", "confidence=2"):
            self.assertEqual(client.get("/api/events?" + query).status_code, 400)

    def test_one_event_per_track_and_confirmation(self):
        hub = self.make_hub()
        hub.publish("node-1", make_frame("node-1", 0))
        hub.process_camera("node-1")
        self.assertEqual(hub.store.search(), [])
        for _ in range(5):
            hub.publish("node-1", make_frame("node-1", 0))
            hub.process_camera("node-1")
        self.assertEqual(len(hub.store.search()), 1)
        self.assertEqual(hub.store.search()[0]["notification_status"], "disabled")

    def test_alert_threshold_uses_current_detection_score(self):
        class Detector:
            score = 0.3

            def detect(self, frame):
                return np.array([[10, 10, 50, 50, self.score, 0]], dtype=np.float32)

        detector = Detector()
        hub = self.make_hub(detector=detector)
        hub.publish("node-1", make_frame("node-1"))
        hub.process_camera("node-1")
        detector.score = 0.9
        hub.publish("node-1", make_frame("node-1"))
        hub.process_camera("node-1")
        events = hub.store.search()
        self.assertEqual(len(events), 1)
        self.assertAlmostEqual(events[0]["confidence"], 0.9, places=5)

    def test_expired_frame_is_skipped_and_stale_tracks_restart(self):
        hub = self.make_hub()
        jpeg = make_frame("node-1", 0)
        hub.publish("node-1", jpeg)
        hub.frames["node-1"]["received_at"] -= 20
        hub.process_camera("node-1")
        self.assertEqual(hub.store.search(), [])
        self.assertIn("expired", hub.frames["node-1"]["error"])
        for _ in range(2):
            hub.publish("node-1", jpeg)
            hub.process_camera("node-1")
        self.assertEqual(len(hub.store.search()), 1)
        hub.frames["node-1"]["processed_at"] -= 20
        for _ in range(2):
            hub.publish("node-1", jpeg)
            hub.process_camera("node-1")
        self.assertEqual(len(hub.store.search()), 2)

    def test_tracker_history_and_missed_track_expiry_are_bounded(self):
        tracker = CameraTracker(self.config["tracking"])
        detection = np.array([[10, 10, 50, 50, 0.9, 0]], dtype=np.float32)
        for _ in range(100):
            tracker.update(detection)
        tracked = tracker.tracker.trackers[0]
        self.assertLessEqual(len(tracked.observations), 10)
        self.assertLessEqual(len(tracked.history_observations), 10)
        self.assertLessEqual(len(tracked.kf.history_obs), 10)
        self.assertIsNone(tracked.kf.attr_saved)
        tracker.update(np.empty((0, 6)))
        tracker.update(detection)
        self.assertIsNone(tracker.tracker.trackers[0].kf.attr_saved)
        for _ in range(6):
            tracker.update(np.empty((0, 6)))
        self.assertEqual(len(tracker.tracker.trackers), 0)

    def test_constructing_another_tracker_preserves_ids(self):
        first = CameraTracker(self.config["tracking"])
        box = np.array([[10, 10, 50, 50, 0.9, 0]], dtype=np.float32)
        first_id = first.update(box)[0].track_id
        CameraTracker(self.config["tracking"])
        distant = np.array([[200, 200, 250, 250, 0.9, 0]], dtype=np.float32)
        second_id = first.update(distant)[0].track_id
        self.assertNotEqual(first_id, second_id)

    def test_push_ingress_authentication_and_jpeg_limits(self):
        self.config["cameras"][0]["source"] = "push"
        hub = self.make_hub()
        client = create_app(hub, api_token="test-token").test_client()
        endpoint = "/api/cameras/node-1/frame"
        self.assertEqual(client.get("/api/events").status_code, 401)
        headers = {"Authorization": "Bearer test-token"}
        self.assertEqual(client.post(endpoint, data=make_frame("node-1"),
                                     content_type="image/jpeg", headers=headers).status_code, 202)
        self.assertEqual(client.post(endpoint, data=b"wrong", content_type="image/jpeg",
                                     headers=headers).status_code, 400)
        self.assertEqual(client.post(endpoint, data=b"x", headers=headers).status_code, 415)
        self.assertEqual(client.post(endpoint, data=b"x" * 524289, content_type="image/jpeg",
                                     headers=headers).status_code, 413)
        self.assertEqual(client.post("/api/cameras/node-2/frame", data=make_frame("node-2"),
                                     content_type="image/jpeg", headers=headers).status_code, 409)
        basic = base64.b64encode(b"user:test-token").decode()
        self.assertEqual(client.get("/api/status", headers={"Authorization": "Basic " + basic}).status_code, 200)
        image = np.zeros((40, 40, 3), dtype=np.uint8)
        _, oversized = cv2.imencode(".jpg", image)
        with self.assertRaises(ValueError):
            decode_jpeg(oversized.tobytes(), 524288, 1000)
        with self.assertRaises(ValueError):
            decode_jpeg(make_frame("node-1")[:-100], 524288, 320 * 240)

    def test_capture_recovers_after_malformed_http_response(self):
        requests = []
        jpeg = make_frame("node-1", 0)

        class Camera(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(True)
                if len(requests) == 1:
                    self.wfile.write(b"INVALID HTTP STATUS\r\n\r\n")
                    self.close_connection = True
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(jpeg)))
                self.end_headers()
                self.wfile.write(jpeg)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Camera)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.config["cameras"] = [self.config["cameras"][0]]
        self.config["cameras"][0].update(url=f"http://127.0.0.1:{server.server_port}/capture", poll_seconds=0.1)
        hub = self.make_hub()
        hub.start()
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and not hub.store.search():
            time.sleep(0.05)
        self.assertGreaterEqual(len(requests), 3)
        self.assertEqual(len(hub.store.search()), 1)
        self.assertTrue(hub.threads[0].is_alive())

    def test_onnx_decoder_letterbox_class_filter_and_nms(self):
        detector = OnnxDetector.__new__(OnnxDetector)
        detector.config = {"confidence": 0.25, "iou_threshold": 0.45}
        detector.labels = ["rat", "person"]
        detector.class_ids = {0}
        # 640x320 source letterboxed into 320x320 has 80px top padding.
        output = np.array([[[160, 160, 100], [160, 160, 100], [40, 40, 20], [20, 20, 20],
                            [0.9, 0.8, 0.1], [0.01, 0.02, 0.9]]], dtype=np.float32)
        detections = detector.decode(output, 0.5, 0, 80, 640, 320)
        self.assertEqual(detections.shape, (1, 6))
        np.testing.assert_allclose(detections[0, :4], [280, 140, 360, 180])
        np.testing.assert_allclose(detector.decode(output.transpose(0, 2, 1), 0.5, 0, 80, 640, 320), detections)
        with self.assertRaises(ValueError):
            detector.decode(np.zeros((1, 8, 20)), 1, 0, 0, 320, 320)

    def test_notification_snapshot_attachment_and_retry_failure(self):
        uploads = []

        class Sink(BaseHTTPRequestHandler):
            def do_POST(self):
                uploads.append((self.headers, self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Sink)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        hub = self.make_hub(notify_url=f"http://127.0.0.1:{server.server_port}/test")
        event_id = hub.store.add(self.config["cameras"][0], "demo-pest", 0.9, "test-track", time.time(),
                                 make_frame("node-1"), 120, True)
        with patch.dict("os.environ", {"PESTSENTRY_NTFY_TOKEN": "test-secret"}):
            self.assertTrue(hub.notify_once())
        headers, body = uploads[0]
        self.assertEqual(headers["Authorization"], "Bearer test-secret")
        self.assertEqual(headers["Filename"], "pest.jpg")
        self.assertEqual(body[:2], b"\xff\xd8")
        self.assertEqual(hub.store.search()[0]["notification_status"], "sent")
        hub.store.add(self.config["cameras"][1], "demo-pest", 0.9, "failed-track", time.time(),
                      make_frame("node-2"), 120, True)
        with patch.object(hub, "send_notification", side_effect=IncompleteRead(b"short response")):
            for _ in range(3):
                with hub.store.connection:
                    hub.store.connection.execute("UPDATE events SET notify_after=0")
                self.assertTrue(hub.notify_once())
        failed = hub.store.search(camera="node-2")[0]
        self.assertEqual(failed["notification_status"], "failed")
        self.assertEqual(failed["notification_attempts"], 3)
        self.assertEqual(hub.store.snapshot(event_id)[:2], b"\xff\xd8")

    def test_cooldown_persists_and_event_retention_is_bounded(self):
        store = EventStore(self.directory.name, 2)
        camera = self.config["cameras"][0]
        jpeg = make_frame("node-1")
        first = store.add(camera, "demo-pest", 0.9, "first", 1, jpeg, 120, True, now=100)
        self.assertIsNotNone(first)
        self.assertIsNone(store.add(camera, "demo-pest", 0.9, "first", 1, jpeg, 120, True, now=101))
        store.close()
        store = EventStore(self.directory.name, 2)
        self.addCleanup(store.close)
        store.add(camera, "demo-pest", 0.9, "second", 2, jpeg, 120, True, now=110)
        self.assertEqual(store.search()[0]["notification_status"], "suppressed")
        store.add(camera, "demo-pest", 0.9, "third", 3, jpeg, 120, True, now=230)
        self.assertEqual(store.search()[0]["notification_status"], "pending")
        self.assertEqual(len(store.search()), 2)
        self.assertIsNone(store.snapshot(first))

    def test_config_validation_and_relative_paths(self):
        original = json.loads((Path(__file__).parents[1] / "config.example.json").read_text())
        config_path = Path(self.directory.name) / "test.json"
        config_path.write_text(json.dumps(original))
        loaded = load_config(config_path)
        self.assertEqual(loaded["model"]["path"], str(Path(self.directory.name) / "models" / "pest-yolov8n.onnx"))
        variants = []
        for section, field, bad in (("model", "input_size", 321), ("alerts", "classes", ["bird"]),
                                    ("alerts", "cooldown_seconds", float("nan")),
                                    ("alerts", "confirmation_frames", 1.5), ("hub", "port", True)):
            variant = copy.deepcopy(original)
            variant[section][field] = bad
            variants.append(variant)
        duplicate = copy.deepcopy(original)
        duplicate["cameras"][1]["id"] = "node-1"
        variants.append(duplicate)
        for variant in variants:
            config_path.write_text(json.dumps(variant))
            with self.assertRaises(ValueError):
                load_config(config_path)


if __name__ == "__main__":
    unittest.main()
