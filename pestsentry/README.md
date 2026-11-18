# PestSentry

PestSentry is a personal pest-detection build: five ESP32-CAM nodes, a hub that runs object detection and tracking, a dashboard, and phone alerts with a snapshot.

The hub software is [clearcam](https://github.com/roryclear/clearcam) by Rory Clear, licensed under GPL-3.0 (see `../LICENSE.md`). This folder is a profile on top of that project. It does not replace `python3 clearcam.py`. Running clearcam with no `PESTSENTRY_PROFILE` keeps the original cameras, COCO classes, and notification settings.

Aarav Gaudani's GitHub account (`AaravG19`) has no separate PestSentry repository. The profile lives here, in the clearcam fork.

## What clearcam already does

Clearcam is a Python NVR. For each camera URL it:

1. Opens the stream with ffmpeg. RTSP is detected (`rtsp://`, TCP). Anything else ffmpeg can read is accepted, including an HTTP MJPEG stream and a local video file (`.mp4`, `.mov`, and the other extensions in `VideoCapture.init_cam`). HLS playlists work too.
2. Records the stream as fMP4 HLS under `data/cameras/<name>/streams/` and pulls raw frames back out for detection.
3. Runs **YOLOv9** (sizes `t`, `s`, `m`, `c`, `e`) in [tinygrad](https://github.com/tinygrad/tinygrad). Weights come from `roryclear/yolov9` on Hugging Face. The head is the 80 COCO classes. The default size in the web UI is the tiny model at 960 px. `DEV=AMD` or `DEV=NV` selects a GPU; otherwise it uses the CPU.
4. Tracks each camera with its own **OC-SORT** tracker (`ocsort_tracker/`). A box has to show up on more than one frame, and by default it has to be moving, before it counts.
5. Saves a JPEG crop when a track is new, and can embed those crops with CLIP for text search. Face recognition (AdaFace) and a Qwen3-VL summary are optional and off unless you turn them on.
6. Serves a dashboard on port 8080 (live view, events, search, zones, alert rules).
7. POSTs an alert, with a JPEG when the destination is not clearcam.org, to `{server_url}/send`. `utils/sample_server.py` shows a Pushover forwarder. The iOS and Android apps are a separate premium path and are not required.

Needs: Linux or macOS, ffmpeg, Python 3.11+, and the packages in `../requirements.txt` (tinygrad, numpy, opencv). The first run downloads model weights.

## How PestSentry uses it

```
ESP32-CAM 1..5  --Wi-Fi MJPEG-->  hub (clearcam + this profile)
       or                              |  YOLOv9-t, one OC-SORT tracker per camera
ESP32-CAM 1..5  --ESP-NOW-->  ESP32 gateway --Wi-Fi MJPEG-->  hub
                                      |
                                      +--> dashboard :8080
                                      +--> alert sink :8091 (snapshot on disk, optional ntfy)
```

The profile, when you start it with `python3 -m pestsentry run`, writes five camera URLs, a confidence threshold, and a cooldown into clearcam's database, then starts clearcam. It also:

- watches **bird, cat, and dog** (COCO ids 14, 15, 16) instead of person / bicycle / car / truck
- lowers the "must be moving" filter so a sitting animal still counts (`PESTSENTRY_MIN_SPEED`, default in this profile is 0; clearcam's own default stays 2.5)
- keeps crops smaller than 100 px, which matters at QVGA (`PESTSENTRY_MIN_CROP`)
- turns CLIP, face recognition, and Qwen off
- loads YOLOv9-t at 640 px instead of 960
- points notifications at a local sink that stores the snapshot

Each camera keeps its own tracker. That part is clearcam, unchanged.

### COCO is not a rodent model

COCO's class named `mouse` is a computer mouse. The config loader refuses it on the clearcam backend. Rats, mice, squirrels, and insects need a checkpoint trained on them. Point `model.backend` at `ultralytics` and set `model.weights` plus `model.labels_file` (see `labels.pest.example.txt`). That path returns boxes in the same shape OC-SORT already consumes. It is optional and is not the default.

## Will this run on the UNO Q 2GB?

The 2 GB UNO Q is a reasonable hub **if you stay on the default profile**. It is a tight fit, and this tree has not been booted on that board.

| Piece | On the UNO Q |
| --- | --- |
| CPU | Qualcomm QRB2210, four Cortex-A53 cores at 2.0 GHz, Debian, aarch64. Clearcam's tinygrad build runs on that CPU. |
| GPU | Adreno 702 is on the chip. Clearcam only has `DEV=AMD` and `DEV=NV` switches. There is no Adreno path, so detection is CPU-only. |
| RAM | 2 GB LPDDR4, shared with Debian. YOLOv9-t at 640 px, one frame at a time (clearcam's loop is already serial), QVGA input, CLIP/Qwen/face off. That is the profile. |
| Radio | Wi-Fi 5 and Bluetooth 5.1 on the Qualcomm side. **No ESP-NOW.** ESP-NOW has to end on an ESP32. |
| Disk | 16 GB eMMC. HLS recordings from five cameras will fill it. The dashboard's storage cap still applies. |
| MCU | STM32U585 is not used by this software. Detection runs on the Linux side. |

What does not fit: Qwen3-VL, CLIP ViT-L, the face model, YOLOv9-m or larger, and the Ultralytics/PyTorch backend. Five full-HD RTSP cameras with two ffmpeg processes each can also push a 2 GB board into swap. Use QVGA JPEG from the ESP32-CAM sketches.

If the board runs out of memory, run the same command on a laptop on the same Wi-Fi and point the camera URLs at the laptop. Nothing else in the profile changes. A used mini PC with 8 GB is the comfortable fallback; a laptop is enough to finish the software weeks before the board is on the bench.

## Try it with no hardware

From the repo root, with ffmpeg installed:

```bash
python3 -m pestsentry check --config pestsentry/config.example.json
python3 -m pestsentry notify-sink          # terminal 1, snapshots in data/pestsentry_alerts/
python3 -m pestsentry fake-cameras         # terminal 2, five MJPEG streams on port 8090
python3 -m pestsentry simulate --config pestsentry/config.fake.json
```

`simulate` fetches one JPEG from each fake node, drops a person detection and a low-score detection, and posts one "dog" alert per camera. Add `--offline` to draw the JPEGs locally instead of using the fake cameras.

`config.fake.json` points clearcam at those streams. A real model run still needs the Python packages:

```bash
pip install -r requirements.txt
python3 -m pestsentry run --config pestsentry/config.fake.json
```

Open `http://127.0.0.1:8080`. The fake blobs are not animals, so YOLOv9 will usually stay quiet. To watch the tracker on real footage, set one camera URL to a local file such as `test/videos/MOT16-03.mp4` and temporarily add `"person"` to `alerts.classes`. Put the file path back, and the original classes, before you call it a pest test.

`python3 -m unittest discover -s pestsentry/tests` covers the config, the cooldown, the alert snapshot, and the fake streams. It does not download YOLOv9.

## Hub setup

1. Install ffmpeg and Python 3.11 or newer. On the UNO Q that is the Debian side (`apt install ffmpeg python3 python3-pip`).
2. `pip install -r requirements.txt` from the repo root. The first detection run downloads YOLOv9-t.
3. Copy `pestsentry/config.example.json` to `pestsentry/config.local.json` and edit the five URLs. That filename is gitignored.
4. Start the sink, then the hub:

```bash
python3 -m pestsentry notify-sink
python3 -m pestsentry run --config pestsentry/config.local.json
```

5. Phone alerts are the JPEG stored by the sink, plus whatever you forward. `hub.ntfy_topic` is an [ntfy.sh](https://ntfy.sh) topic name with no token. Leave it empty if you do not want that. For Pushover, keep using `utils/sample_server.py` and put the tokens in that process's environment, not in this repo. Set `hub.notify_url` to that server.
6. Search stays available. Turn CLIP on from the clearcam settings only on a machine that has the RAM. The profile forces it off on each launch so a 2 GB board does not load it by accident. Launching through `pestsentry run` rewrites camera URLs and alert rules from the config.

Wi-Fi name and password belong in the firmware you flash, or in the board's own network config. They are not read by the hub.

## Nodes and wiring

Two firmware options. Both end in an MJPEG URL clearcam already understands.

### A. Wi-Fi MJPEG (use this)

`firmware/esp32_cam_mjpeg/esp32_cam_mjpeg.ino` on each AI-Thinker ESP32-CAM.

- Board package: Arduino-ESP32 3.x, board "AI Thinker ESP32-CAM".
- Replace `YOUR_WIFI_SSID` and `YOUR_WIFI_PASSWORD` locally. Do not commit them.
- To flash: connect GPIO0 to GND, reset, upload, then disconnect GPIO0 and reset again. GPIO0 is the camera clock after boot, and it is also the download strap.
- Power the **5V** pin. The camera brownouts on the 3.3V pin. Budget at least 500 mA per board.
- Serial log prints `http://<ip>:81/stream`. Put that URL in the config. No cable to the UNO Q. The hub and the nodes join the same Wi-Fi.
- QVGA, JPEG, about 5 fps.

### B. ESP-NOW, then Wi-Fi

This matches the original "nodes talk ESP-NOW" plan. The UNO Q cannot be the ESP-NOW peer.

- `firmware/esp32_cam_espnow/esp32_cam_espnow.ino` on nodes 1–5. Set `CAMERA_ID` and `GATEWAY_MAC`. Set `WIFI_CHANNEL` to the channel the gateway joined.
- `firmware/espnow_gateway/espnow_gateway.ino` on a sixth ESP32. It joins Wi-Fi (placeholders only), prints its MAC and channel, reassembles JPEG chunks (200 byte payload, under the 250 byte ESP-NOW limit), and serves `/stream/node-1` through `/stream/node-5` on port 81.
- One frame every two seconds per node, staggered. ESP-NOW is a shared 2.4 GHz link; five QVGA streams at video rate do not fit. A pest walking through a room does not need 30 fps. Expect dropped chunks when the radios are far apart.
- The gateway must be in radio range of the nodes and on the same Wi-Fi LAN as the hub.

Common ground is only required if you wire a serial cable. For either sketch above, power and radio are enough.

## Swapping the model

| Goal | Config |
| --- | --- |
| Smallest model that fits the board | `"backend": "clearcam"`, `"size": "t"`, `"res": 640`. `s` / `m` / `c` exist and get heavier. `res` must be a multiple of 32. |
| Different COCO animals | Edit `alerts.classes`. Names are resolved to ids. `mouse` is rejected. |
| A real pest/rodent YOLOv8n | `"backend": "ultralytics"`, `weights` set to your `.pt`, `labels_file` set to a text file in that model's class order. `labels.pest.example.txt` is the shape, not a trained model. Install `ultralytics` yourself. Do this on a laptop, not the 2 GB board. |

No pest weights are shipped. Training or downloading a rodent checkpoint is still open.

## What still needs hardware

- Flash five ESP32-CAM boards and confirm each `/stream` URL opens in a browser.
- If you use ESP-NOW: flash the gateway, copy its MAC and channel into the nodes, and check that a JPEG shows up.
- Put the hub on the UNO Q Debian side and watch `free -h` while all five streams are connected. If the OOM killer fires, move the hub process to a laptop.
- Choose a phone path (ntfy topic, or the Pushover sample) and trigger one real detection.
- A rodent checkpoint, if bird/cat/dog is not the target you actually care about.
- Mounts, lighting, and power for the nodes. Those are bench work, not software.

## License

clearcam is GPL-3.0. Modifications in `clearcam.py` are marked and only run when `PESTSENTRY_PROFILE` is set. This folder is part of the same program and is covered by the same license. Attribution: Rory Clear, https://github.com/roryclear/clearcam
