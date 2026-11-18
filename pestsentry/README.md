# PestSentry hub

Five ESP32-CAM nodes provide JPEG snapshots over a private 2.4 GHz Wi-Fi network. The Linux hub runs one CPU detector, tracks sightings independently for each camera, and stores searchable events and snapshots. A phone subscribed to the configured ntfy topic can receive a snapshot when an alert qualifies.

## Run a five-camera demo

Use Python 3.11 or 3.12. Run the commands from the repository root. A laptop can run both processes. The demo uses moving green markers to exercise transport, tracking, search and alerts without downloading model weights.

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r pestsentry/requirements.txt
python -m pestsentry.fake_cameras
```

Open a second terminal in the same directory and activate the same environment.

```sh
python -m pestsentry --config pestsentry/config.demo.json
```

Run the regression suite with `python -m unittest discover -s pestsentry/tests -v`. Visit <http://127.0.0.1:8081>, wait for a sighting, filter by camera and open the event snapshot. On Windows use `.venv\Scripts\activate` in Command Prompt. To view the dashboard from another computer, keep the default loopback binding and forward its port using `ssh -L 8081:127.0.0.1:8081 user@hub`.

## Prepare a real detector on a laptop

A pretrained starting point is Ultralytics YOLOv8n Open Images V7. Its 601 classes distinguish the animal Mouse from Computer mouse and include Raccoon and Squirrel. The exporter reads the class order directly from the checkpoint and enables these three animal classes. The standard COCO checkpoint uses “mouse” for a computer peripheral. For rats, cockroaches or another target, use a detection checkpoint trained and evaluated for that class. [Model and dataset](https://docs.ultralytics.com/datasets/detect/open-images-v7/) · [Exporter](https://docs.ultralytics.com/modes/export/)

In a separate laptop Python environment, run the following from the repository root. The example downloads the official weights directly. Use only a trusted checkpoint for export.

```sh
python -m pip install ultralytics==8.3.253 onnx
mkdir -p pestsentry/models
curl -fL https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n-oiv7.pt -o pestsentry/models/yolov8n-oiv7.pt
python pestsentry/export_model.py pestsentry/models/yolov8n-oiv7.pt
```

The reference checkpoint's SHA-256 is `3851dfbf39ed2a076b1f39215cc22dba64eb5646f282bf964703785b0bed6a41`. The exporter produces a fixed 320 × 320, batch-one FP32 ONNX model and `pestsentry/config.local.json`. Copy both to the corresponding paths on the hub. The laptop alone needs the training/export stack. To use custom weights, pass their path and e.g. `--classes rat mouse cockroach`; each class must exist in those weights. For a pre-exported YOLOv8 detector, copy `config.example.json`, set the ONNX path and input size, enter **all** model labels in training order and select the alert subset. Raw detection output must contain four box coordinates and one score per class. Model and data licensing is separate from Clearcam; weights and training data are downloaded independently.

## Connect five cameras

The supplied sketch targets the **AI Thinker ESP32-CAM with OV2640 and PSRAM**. Check the silkscreen and pin map before flashing a different board. In the Arduino IDE select the Espressif ESP32 board package (3.x) and AI Thinker ESP32-CAM. Open `firmware/esp32_cam_mjpeg/esp32_cam_mjpeg.ino`. Copy `wifi.example.h` in that sketch folder to `wifi.local.h` and edit the local copy. Use the same sketch for all five nodes.

For a bare module, power the module's 5V/GND pins from a regulated supply rated for the module; use a USB-UART adapter with **3.3 V logic**. With power off, wire adapter TX to U0R, RX to U0T, and GND to GND. Ground GPIO0 for flashing, reset and upload. Disconnect GPIO0 from ground and reset to boot the camera. With a matching USB programmer base, use its boot/reset controls. Give each node its own adequate power supply; the cameras need no signal wires to the UNO Q.

Read each address from its 115200-baud serial console, reserve it in DHCP, and enter the five URLs in your local config, for example `http://192.168.1.101:81/capture`. Open each URL and check the view before starting detection. The sketch also serves MJPEG at `/stream`; close that preview before polling because each sketch serves one client at a time. Keep camera HTTP endpoints on the private LAN.

Activate the hub environment, then run:

```sh
python -m pestsentry --config pestsentry/config.local.json
```

The project drawing specifies ESP-NOW. That radio protocol carries small messages (250 bytes on v1, up to 1470 on v2), so a JPEG typically spans many packets. A gateway implementation would need another compatible ESP32, a shared radio channel, per-node frame and chunk IDs, and a timeout for incomplete frames. It could expose one HTTP snapshot URL per node after reassembly. The supplied Wi-Fi firmware avoids that extra board and protocol during the five-week build. [Espressif protocol details](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/network/esp_now.html)

For a bridge that pushes completed JPEGs, set that camera's `source` to `push` and POST the raw body with `Content-Type: image/jpeg` to `/api/cameras/node-1/frame` (substitute the node ID). HTTP 202 means accepted for processing. For access beyond loopback the hub requires `PESTSENTRY_API_TOKEN`; send it as a bearer token through a protected connection. The bridge must discard incomplete JPEGs. The supplied sketch serves direct Wi-Fi snapshots. Its build and flash still need verification on an AI Thinker board.

## Alerts and phone setup

Set `PESTSENTRY_NTFY_URL` in the hub process environment to your full ntfy topic URL and subscribe to that topic in the phone app. For a protected topic, set `PESTSENTRY_NTFY_TOKEN` to a publisher access token. Use a protected HTTPS topic when sending deployment photos. Keep both values outside version control. Start the hub again after changing the environment. Run the demo with a test topic first and check that the phone displays the correct camera name and JPEG. [ntfy attachments](https://docs.ntfy.sh/publish/#attachments)

`model.confidence` determines which detections enter tracking. `alerts.confidence`, `confirmation_frames` and `cooldown_seconds` determine when a sighting qualifies for an alert. Tune using footage from each mounting position, including empty frames and small animals moving in low light. A 320-pixel input can lose small or distant targets; move the camera closer or evaluate a larger export on the laptop.

The example polls each camera every two seconds, requires two confirmed frames and a 0.60 alert score, and applies a 120-second phone cooldown per camera and class. Short crossings can fall between polls. Search the most recent events at `/api/events?camera=node-1&class=Mouse`; `confidence`, `since` and `until` narrow the result. The default store retains up to 1,000 events with JPEG snapshots.

## UNO Q feasibility and fallback

Run Python on the QRB2210 Debian side. The STM32 runs sketches and plays no role in this inference path. The CPU profile avoids continuous recording, CLIP, face embeddings and the language model. The 2 GB board is a candidate for intermittent snapshots. Five-camera throughput, temperature and usable memory require a board measurement. [Arduino UNO Q hardware](https://docs.arduino.cc/hardware/uno-q/)

1. Boot headless, confirm `uname -m` reports `aarch64`, and install the profile dependencies in a venv. Verify `python -c "import cv2, flask, onnxruntime; print(onnxruntime.get_available_providers())"` lists the CPU provider.
2. Start with one camera in the config. Time a crossing from entry into view to the phone alert. Record the hub process's resident memory with `ps -o pid,rss,%cpu,cmd -p PID`, remaining memory with `free -h`, and swap activity with `vmstat 1`.
3. Repeat with all five nodes for at least 30 minutes, including simultaneous crossings and unplugging/reconnecting a node. Log missed crossings, false alerts, missing snapshots, alert latency, memory pressure and board temperature. Compare the achieved sampling interval to the time a pest stays in view.
4. If coverage or latency is inadequate, or ARM wheels are unavailable for the installed Python, create a fresh venv from the profile requirements on a Linux laptop on the same LAN, then copy the ONNX file and local config there. The same camera URLs and firmware work there. Do the same trial before relying on alerts.

## How this relates to Clearcam

Rory Clear's upstream NVR accepts RTSP over TCP, FFmpeg-readable HTTP feeds such as HLS, and local videos. Its live path copies the source codec into fMP4/HLS segments and decodes frames from that playlist. Codec compatibility matters for recording and browser playback. Clearcam uses tinygrad's YOLOv9 implementation (default tiny, resolution 960) and one OC-SORT tracker per camera. It stores clips and object images, supports zone/class/count rules, and can post events to a configurable notification endpoint or its premium service. Optional CLIP embeddings enable text and image similarity searches; optional face embeddings and Qwen3-VL summaries add model and compute costs. Upstream documents Python 3.11+, FFmpeg and Mac/Linux, with a Windows FFmpeg caveat. The root requirements pin tinygrad, NumPy and OpenCV. [Upstream](https://github.com/roryclear/clearcam)

PestSentry reuses the repository's OC-SORT implementation and supplies a separate detector, camera input and Flask dashboard. Its event search uses saved metadata. Run `python clearcam.py` for the original NVR and full search features. The node sketch derives from the earlier PestSentry proposal in this fork and adds single-frame capture and a local credentials header. Source is distributed under the repository's GPL-3.0 license. [License](../LICENSE.md) · [Earlier sketch](https://github.com/AaravG19/clearcam/blob/bb3c32a59318b8ade0168a749f230666765216fc/pestsentry/firmware/esp32_cam_mjpeg/esp32_cam_mjpeg.ino)

## Laptop validation

On a laptop, the 320-pixel export above correctly labeled a squirrel at 0.678 confidence (Open Images validation image `0022bffa9abfb554`). On the first 12 distinct Mouse-labeled images in the validation box CSV, it returned **zero correct Mouse detections at 0.25**, including one Squirrel prediction at 0.472. This small check establishes that the adapter runs and that these weights/settings are a poor starting point for reliable mouse alerts. Collect labeled clips at the intended distance, angle and lighting; evaluate missed crossings as well as false alerts before deployment. Use pest-specific weights if the reference model misses the target. [Validation annotations](https://storage.googleapis.com/openimages/v5/validation-annotations-bbox.csv) · [Positive squirrel frame](https://open-images-dataset.s3.amazonaws.com/validation/0022bffa9abfb554.jpg)

In an end-to-end laptop check, five local HTTP camera paths served the same labeled squirrel frame resized to 320 × 240. All five produced separate searchable events at 0.671 confidence and delivered five annotated JPEGs to a local notification receiver. This check used the real exported weights. Phone delivery and five physical cameras still require bench tests.
