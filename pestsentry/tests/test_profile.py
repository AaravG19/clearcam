import json
import os
import tempfile
import unittest
from pathlib import Path

import pestsentry.runtime as runtime
from pestsentry.alerts import AlertState
from pestsentry.config import COCO_LABELS, load_config
from pestsentry.notify_sink import parse_alert, save_alert
from pestsentry.simulate import build_send_body, run_simulation

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "pestsentry" / "config.example.json"
FAKE = ROOT / "pestsentry" / "config.fake.json"


class FakeDB:
    def __init__(self):
        self.puts = []
        self.deleted = []

    def run_get(self, table, key=None, id=None):
        return {}

    def run_put(self, table, key, val=None, id=None, replace=True):
        self.puts.append((table, key, val, id))

    def run_delete(self, table, key, id=None):
        self.deleted.append((table, key, id))


class Box:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class ProfileTests(unittest.TestCase):
    def tearDown(self):
        runtime._CFG = None
        for key in (
            "PESTSENTRY_PROFILE",
            "PESTSENTRY_MIN_SPEED",
            "PESTSENTRY_MIN_CROP",
            "PESTSENTRY_BACKEND",
            "PESTSENTRY_DASHBOARD_PORT",
        ):
            os.environ.pop(key, None)

    def test_coco_has_eighty_names(self):
        self.assertEqual(len(COCO_LABELS), 80)
        self.assertEqual(COCO_LABELS[14], "bird")
        self.assertEqual(COCO_LABELS[15], "cat")
        self.assertEqual(COCO_LABELS[16], "dog")
        self.assertEqual(COCO_LABELS[64], "mouse")

    def test_example_is_five_placeholder_nodes(self):
        cfg = load_config(EXAMPLE)
        self.assertEqual(len(cfg["cameras"]), 5)
        self.assertEqual(cfg["alerts"]["class_ids"], [14, 15, 16])
        self.assertEqual(cfg["model"]["backend"], "clearcam")
        self.assertTrue(all(cam["url"].startswith("http://192.168.1.10") for cam in cfg["cameras"]))
        text = EXAMPLE.read_text()
        self.assertNotIn("password", text.lower())

    def test_mouse_on_coco_is_rejected(self):
        raw = json.loads(EXAMPLE.read_text())
        raw["alerts"]["classes"] = ["mouse"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(json.dumps(raw))
            with self.assertRaises(SystemExit) as raised:
                load_config(path)
        self.assertIn("computer mouse", str(raised.exception))

    def test_custom_labels_allow_a_rodent_mouse(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            labels = tmp / "labels.txt"
            labels.write_text("rat\nmouse\n")
            weights = tmp / "yolov8n-pest.pt"
            weights.write_bytes(b"not a real checkpoint")
            path = tmp / "custom.json"
            path.write_text(json.dumps({
                "model": {"backend": "ultralytics", "weights": "yolov8n-pest.pt", "labels_file": "labels.txt"},
                "alerts": {"classes": ["mouse", "rat"], "confidence": 0.5, "cooldown_seconds": 30},
                "cameras": [{"name": "node-1", "url": "http://127.0.0.1:81/stream"}],
            }))
            cfg = load_config(path)
        self.assertEqual(cfg["alerts"]["class_ids"], [1, 0])
        self.assertEqual(cfg["labels"], ["rat", "mouse"])

    def test_apply_profile_seeds_clearcam_records(self):
        os.environ["PESTSENTRY_PROFILE"] = str(FAKE)
        db = FakeDB()
        runtime.apply_profile(db, Box, Box)
        links = [item for item in db.puts if item[0] == "links"]
        self.assertEqual(len(links), 5)
        self.assertEqual(links[0][1], "node-1")
        self.assertTrue(links[0][2].endswith("/stream/node-1"))
        settings = next(item for item in db.puts if item[0] == "global_settings")
        self.assertFalse(settings[2].use_clip)
        self.assertFalse(settings[2].use_qwen)
        self.assertEqual(settings[2].model_size, "t")
        self.assertEqual(settings[2].model_res, 640)
        self.assertEqual(settings[2].server_url, "http://127.0.0.1:8091")
        alert = next(item for item in db.puts if item[0] == "alerts")
        self.assertEqual(alert[3], "pestsentry")
        self.assertEqual(alert[2].classes, {14, 15, 16})
        self.assertEqual(alert[2].window_seconds, 120)
        self.assertEqual(os.environ["PESTSENTRY_MIN_SPEED"], "0.0")
        self.assertEqual(os.environ["PESTSENTRY_MIN_CROP"], "24")
        classes, labels = runtime.override_detection({"0"})
        self.assertEqual(classes, {"14", "15", "16"})
        self.assertEqual(labels[16], "dog")
        self.assertIsNone(runtime.maybe_detector())

    def test_cooldown_and_class_filter(self):
        state = AlertState([16], confidence=0.45, cooldown_seconds=120)
        self.assertFalse(state.consider("node-1", 0, 0.99, 1, now=1000))  # person
        self.assertFalse(state.consider("node-1", 16, 0.2, 2, now=1000))  # low score
        self.assertTrue(state.consider("node-1", 16, 0.9, 3, now=1000))
        self.assertFalse(state.consider("node-1", 16, 0.9, 3, now=1010))  # same track
        self.assertFalse(state.consider("node-1", 16, 0.9, 4, now=1010))  # still in cooldown
        self.assertFalse(state.consider("node-1", 16, 0.9, 4, now=1130))  # that track was already seen
        self.assertTrue(state.consider("node-1", 16, 0.9, 5, now=1130))
        self.assertTrue(state.consider("node-2", 16, 0.9, 4, now=1010))  # other camera

    def test_clearcam_defaults_stay_in_source(self):
        text = (ROOT / "clearcam.py").read_text()
        self.assertIn('classes = {"0","1","2","7"} # person, bike, car, truck, bird (14)', text)
        self.assertIn('os.environ.get("PESTSENTRY_MIN_SPEED", "2.5")', text)
        self.assertIn('os.environ.get("PESTSENTRY_MIN_CROP", "100")', text)
        self.assertIn('os.environ.get("PESTSENTRY_DASHBOARD_PORT", "8080")', text)
        self.assertIn('os.environ.get("PESTSENTRY_BACKEND") == "ultralytics"', text)
        self.assertIn("https://github.com/roryclear/clearcam", text)

    def test_firmware_has_placeholders_only(self):
        folder = ROOT / "pestsentry" / "firmware"
        sketches = list(folder.rglob("*.ino"))
        self.assertEqual(len(sketches), 3)
        for sketch in sketches:
            text = sketch.read_text()
            if "WIFI_PASSWORD" in text:
                self.assertIn("YOUR_WIFI_PASSWORD", text)
                self.assertIn("YOUR_WIFI_SSID", text)
            self.assertNotIn("BEGIN PRIVATE", text)

    def test_alert_body_round_trip(self):
        image = b"\xff\xd8fake-jpeg\xff\xd9"
        body, content_type = build_send_body("PestSentry dog on node-1 (kitchen)", image, "node-1.jpg")
        parsed = parse_alert(body, content_type)
        self.assertEqual(parsed["text"], "PestSentry dog on node-1 (kitchen)")
        self.assertEqual(parsed["image"], image)
        with tempfile.TemporaryDirectory() as tmp:
            saved = save_alert(parsed, Path(tmp))
            self.assertIsNotNone(saved)
            self.assertEqual(saved.read_bytes(), image)


class SimulateTests(unittest.TestCase):
    def test_offline_simulation_posts_five_snapshots(self):
        from pestsentry.notify_sink import serve

        httpd = serve(0, "127.0.0.1")
        self.addCleanup(httpd.shutdown)
        self.addCleanup(httpd.server_close)
        port = httpd.server_address[1]
        raw = json.loads(FAKE.read_text())
        raw["hub"]["notify_url"] = f"http://127.0.0.1:{port}"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fake.json"
            path.write_text(json.dumps(raw))
            cfg = load_config(path)
            previous = os.getcwd()
            os.chdir(tmp)
            try:
                sent = run_simulation(cfg, offline=True, now=1_700_000_000)
            finally:
                os.chdir(previous)
        self.assertEqual(len(sent), 5)
        self.assertTrue(all("dog" in item["text"] for item in sent))
        self.assertTrue(all(item["bytes"] > 100 for item in sent))


if __name__ == "__main__":
    unittest.main()
