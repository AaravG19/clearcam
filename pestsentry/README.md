<!--
PestSentry hub integration for clearcam.
Copyright (C) 2026 Aarav Gaudani.
Licensed under GPL-3.0-or-later; see ../LICENSE.md.
clearcam is by Rory Clear (https://github.com/roryclear/clearcam), GPL-3.0.
-->

# PestSentry hub (clearcam profile)

This folder turns **clearcam** into the software hub for **PestSentry**: five
ESP32-CAM nodes that watch for pests and send their frames, over ESP-NOW, to one
hub board (an Arduino UNO Q). clearcam already does the hard parts — object
detection, per-camera tracking, event recording, a web dashboard, and phone
alerts with snapshots. PestSentry adds only three things clearcam does not have:

1. a way to get ESP-NOW camera frames into clearcam (`mjpeg_hub.py`),
2. a one-shot setup that registers the 5 cameras and their alert rules
   (`setup_cameras.py`), and
3. the node and gateway firmware (`firmware/`).

Nothing in the upstream clearcam code is changed. Everything here is additive
and lives under `pestsentry/`, so `python3 clearcam.py` still works exactly as
before.

## How the pieces fit

```
 [ESP32-CAM node 1] --\
 [ESP32-CAM node 2] ---\                                     Linux side of the
 [ESP32-CAM node 3] ----+-- ESP-NOW --> [ESP32 gateway] --USB/UART--> Arduino UNO Q
 [ESP32-CAM node 4] ---/                (reassembles         |
 [ESP32-CAM node 5] --/                  JPEG frames)        |
                                                             v
                                        mjpeg_hub.py  (serves /cam/1 .. /cam/5
                                             |          as local MJPEG streams)
                                             v
                                        clearcam.py  (YOLOv9 detect + OC-SORT
                                             |          track + record + notify)
                                             v
                                   dashboard :8080  +  phone alert w/ snapshot
```

The nodes never join WiFi. ESP-NOW is a connectionless radio link, so the nodes
stay low-power and do not need an access point. The gateway is a plain ESP32
wired to the UNO Q over USB; it reassembles each JPEG and writes it to the
serial port. `mjpeg_hub.py` reads that serial stream and re-serves each node as
a normal MJPEG HTTP stream on `127.0.0.1`, which is a source clearcam's ffmpeg
input can read like any other camera.

## The detection model — read this first

clearcam's detector is **not** a drop-in model file. It is a YOLOv9 network
hand-built in [tinygrad](https://github.com/tinygrad/tinygrad) (`detection/yolov9.py`)
with a fixed **COCO-80** detection head, and it loads COCO weights. You cannot
simply point it at a `yolov8n.pt` pest model.

So PestSentry ships with a working default that needs **no training**, plus a
documented path to a real pest model:

* **Default (works today): a COCO class filter.** COCO has no rodents or
  insects, but it does have `person` (0), `bird` (14), `cat` (15) and `dog`
  (16). PestSentry's default alert fires only on those four classes. Treat
  `bird/cat/dog` as **stand-ins** for "an animal is in frame" during bring-up,
  and `person` as an intruder/operator check. This is honest about what COCO can
  and cannot see — it will not detect a rat, mouse, squirrel, raccoon or insect.
* **Real pest detection (needs work + data): swap the model.** See
  [Swapping in a real pest model](#swapping-in-a-real-pest-model) below. This
  needs a trained model and a weight conversion, which in turn needs a labelled
  pest dataset — that is future work, not something this folder can fake.

The class list, confidence threshold and alert cooldown are all config values
(see below), so moving from the proxy classes to a real model later changes
config, not code.

## Quick start — no hardware needed

You can run the whole pipeline on a laptop with zero ESP32 boards.

```bash
# 1. clearcam's own deps (from the repo root), plus this folder's deps
pip install -r requirements.txt
pip install -r pestsentry/requirements.txt    # opencv + pyserial

# 2. copy the example config and (optionally) edit it
cp pestsentry/pestsentry.config.example.json pestsentry/pestsentry.config.json

# 3. start clearcam (dashboard on http://localhost:8080)
python3 clearcam.py

# 4. in a second terminal, serve 5 fake cameras.
#    synthetic = generated moving frames, needs nothing:
python3 pestsentry/mjpeg_hub.py --source synthetic
#    or play a real clip through all 5 endpoints (better for seeing detections,
#    use any clip with people/animals in it):
python3 pestsentry/mjpeg_hub.py --source video --video /path/to/clip.mp4

# 5. in a third terminal, register the cameras + alerts in clearcam
python3 pestsentry/setup_cameras.py
```

Open `http://localhost:8080` and you will see `cam_1` … `cam_5`. With a clip
that contains a person or an animal, clearcam will draw boxes, track objects and
(if notifications are configured) fire a PestSentry alert.

> Tip: clearcam also ships a public test feed in its own README
> (`https://webcam.elcat.kg/Too-Ashu_Tunnel_North/index.m3u8`). Pointing a
> camera's `src` at that is the quickest way to confirm detection itself works,
> independent of the PestSentry bridge.

Run the built-in self-checks any time:

```bash
python3 pestsentry/mjpeg_hub.py --selftest       # serves one synthetic frame and decodes it
python3 pestsentry/setup_cameras.py --selftest   # dry-runs the 5 camera + 5 alert URLs
```

## Quick start — with hardware

1. **Flash the firmware** (Arduino IDE or `arduino-cli`):
   * `firmware/espnow_node/espnow_node.ino` onto each ESP32-CAM. Set a unique
     `NODE_ID` (1–5) per board, set `GATEWAY_MAC` to your gateway's MAC, and set
     `WIFI_CHANNEL` the same on every board.
   * `firmware/espnow_gateway/espnow_gateway.ino` onto the gateway ESP32. Use the
     same `WIFI_CHANNEL`.
2. **Wire the gateway to the UNO Q** over USB (it shows up as a serial port).
   Put that port in `pestsentry.config.json` (`serial.port`), e.g. `/dev/ttyUSB0`
   on the UNO Q, `COMx` on Windows.
3. **Run the bridge and setup on the UNO Q (or your hub):**
   ```bash
   python3 pestsentry/mjpeg_hub.py --source serial
   python3 pestsentry/setup_cameras.py
   python3 clearcam.py
   ```

## Configuration

Copy `pestsentry.config.example.json` to `pestsentry.config.json` (the real file
is git-ignored). Fields:

| Field | Meaning |
|---|---|
| `nodes` | 5 entries `{id, name, src?}`. `src` is optional; if absent the hub URL `http://<hub.host>:<hub.port>/cam/<id>` is used. |
| `serial.port` / `serial.baud` | Serial port of the gateway ESP32 and its baud (default 921600). Serial source only. |
| `hub.host` / `hub.port` | Where `mjpeg_hub.py` serves the MJPEG streams (default `127.0.0.1:8090`). |
| `fps` | Frames per second each MJPEG endpoint emits. Keep low (5) for a low-power pest monitor. |
| `jpeg_quality` | JPEG quality for re-encoded frames (synthetic/video sources). |
| `clearcam.host` / `clearcam.port` | Where clearcam's HTTP API listens (default `127.0.0.1:8080`). |
| `pest_classes` | COCO class ids that raise an alert. Default `[0,14,15,16]` = person, bird, cat, dog. |
| `confidence_threshold_percent` | Minimum confidence, as a whole-number percent (clearcam divides by 100). Default `35` = 0.35. |
| `cooldown_seconds` | Rolling window; with `max=1` this is the minimum gap between alerts per camera. Default `60`. |

### Alert rules

`setup_cameras.py` creates one alert per camera through clearcam's existing API:

* **Class filter** — only `pest_classes` raise an alert.
* **Confidence threshold** — `confidence_threshold_percent`.
* **Cooldown** — `cooldown_seconds` with `max=1`: at most one notification per
  camera per window.
* **Schedule** — all day, every day by default. Change the `sched` in
  `setup_cameras.py` for day/night windows.

You can also change any of this live in the clearcam web UI per camera; the
script just seeds sensible PestSentry defaults.

### Phone alerts with snapshots

clearcam sends a notification (with a snapshot image) to whatever
`server_url` is set in its settings. The simplest self-hosted route is the
Pushover relay clearcam already includes at `utils/sample_server.py` — run it,
then set clearcam's notification URL to it in the web UI. **Do not commit your
Pushover (or any) token** — it goes in that server's own environment, not in
this repo.

## Wire formats

Two framings are involved; both are defined in the firmware and `mjpeg_hub.py`
and must stay in sync.

**Node → gateway (ESP-NOW, ≤250-byte packets).** A JPEG is split into chunks,
each prefixed with: magic, `node_id`, `frame_seq` (u8), `chunk_index` (u16),
`chunk_count` (u16), `payload_len` (u8), then the payload.

**Gateway → hub (serial).** One reassembled JPEG per frame: `b"PSN1"` magic +
`node_id` (1 byte) + length (4 bytes, big-endian) + the JPEG bytes. The hub
resynchronises by scanning for the magic, so a dropped byte only costs one frame.

## Wiring notes

* **ESP32-CAM (AI-Thinker):** use the standard AI-Thinker pin map (in the node
  sketch). These boards draw heavy current spikes on capture — power them from a
  solid 5V/≥500 mA supply, not a flaky USB port, or captures brown out.
* **PIR (optional):** a PIR motion sensor on `PIR_PIN` lets a node capture only
  on motion, which suits a battery pest monitor far better than a fixed interval.
* **Gateway → UNO Q:** plain USB. The gateway must be on the **same WiFi
  channel** as the nodes (ESP-NOW peers must share a channel).

## Can the Arduino UNO Q run this?

Short answer: **it can run the hub and gateway comfortably, but it is not a
realistic box for real-time detection on 5 cameras.** Run detection elsewhere.

The UNO Q's Linux side is a Qualcomm **QRB2210**: a quad-core Cortex-A53 at
2.0 GHz, an Adreno 702 GPU, and (on the 2 GB model) 2 GB LPDDR4. clearcam's
detector is a tinygrad YOLOv9 run at 960 px by default. On a quad A53 **CPU**,
that is **well under 1 fps per camera** (estimated from the class of hardware —
**not** benchmarked on this board), and 2 GB RAM is tight once ffmpeg's HLS
muxers for several cameras, tinygrad, and the OS are all resident. Five cameras
of real-time detection on-device is not happening.

What *is* realistic:

* **Recommended split.** The UNO Q runs the **ESP-NOW gateway host + `mjpeg_hub.py`
  + the dashboard/recording**, and a **laptop or mini-PC runs `clearcam.py`**
  (detection) by reading the UNO Q's MJPEG streams over the LAN (point each
  camera `src` at `http://<uno-q-ip>:8090/cam/<id>`). Any x86 laptop works; a
  Jetson Orin Nano or a Raspberry Pi 5 + Hailo-8L are tidy dedicated options.
* **On-device demo.** For a single camera at reduced resolution (e.g.
  `model_res` 320–416) and a low, motion-triggered frame rate, the UNO Q can do
  a live demo. The Adreno 702 exposes OpenCL 2.0, so tinygrad's GPU backend
  (`DEV=GPU`/`CL`) *might* lift this, but that path is unproven on this board and
  the first-run compile is slow — do not count on it for the build.

Either way the ESP-NOW side (nodes + gateway + bridge) is unchanged; only where
`clearcam.py` runs moves.

## Swapping in a real pest model

When you have a trained pest model, two things must change and both live in
**your fork** (they touch upstream behaviour, so keep them out of a PR to the
original clearcam):

1. **Weights + architecture.** clearcam builds YOLOv9 in tinygrad with a COCO-80
   head. A real pest model must match that architecture and class count and be
   converted to clearcam's safetensors layout, *or* `detection/yolov9.py` must be
   taught to load your weights. This is the significant piece and needs the
   labelled pest dataset first.
2. **Class names.** clearcam fetches COCO names at `clearcam.py:1444`:
   ```python
   class_labels = fetch('https://.../coco.names').read_text().split("\n")
   ```
   Point that at your own class list, e.g.:
   ```python
   import os
   _cf = os.environ.get("PESTSENTRY_CLASSES")
   class_labels = (Path(_cf).read_text().split("\n") if _cf
                   else fetch('https://.../coco.names').read_text().split("\n"))
   ```
   Then set `pest_classes` in the config to the ids in your new list and re-run
   `setup_cameras.py`.

## What still needs hardware to verify

The no-hardware path (synthetic/video → clearcam) is self-tested here. These
items can only be confirmed on real boards and are **not** verified:

- [ ] ESP-NOW chunking/reassembly end-to-end (node → gateway → serial).
- [ ] Serial throughput for 5 nodes at the chosen fps and JPEG size.
- [ ] ffmpeg in clearcam ingesting the hub's MJPEG without stalls.
- [ ] Real detection quality on actual pest footage (and the model swap).
- [ ] UNO Q thermals / RAM headroom under sustained load.

## License & attribution

PestSentry integration © 2026 Aarav Gaudani, **GPL-3.0-or-later**, matching
clearcam. clearcam is by Rory Clear — see `../LICENSE.md` and
<https://github.com/roryclear/clearcam>.
