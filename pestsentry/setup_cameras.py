# PestSentry hub integration for clearcam. Copyright (C) 2026 Aarav Gaudani. Licensed under GPL-3.0-or-later; see ../LICENSE.md (or ../../LICENSE.md for firmware).
import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


def main():
    parser = argparse.ArgumentParser(description="Register cameras and rolling alert windows")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--clearcam-host", default="127.0.0.1")
    parser.add_argument("--clearcam-port", type=int, default=8080)
    parser.add_argument("--hub-host")
    parser.add_argument("--hub-port", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    path = Path(__file__).with_name("pestsentry.config.json")
    if args.selftest or not path.exists():
        path = path.with_name("pestsentry.config.example.json")
    cfg = json.loads((args.config or path).read_text(encoding="utf-8"))
    hub = cfg["hub"]
    origin = f"http://{args.clearcam_host}:{args.clearcam_port}"
    stream = f"http://{args.hub_host or hub['host']}:{args.hub_port or hub['port']}"
    counts = {"add_camera": 0, "edit_alert": 0}
    failed = False
    for node in cfg["nodes"]:
        camera = dict(cam_name=node["name"], src=node.get("src", f"{stream}/cam/{node['id']}"))
        alert = dict(cam_name=node["name"], window=cfg["cooldown_seconds"], max=1,
                     class_ids=",".join(map(str, cfg["pest_classes"])),
                     threshold=cfg["confidence_threshold_percent"],
                     sched=json.dumps(cfg.get("sched", [[0, 86400]] * 7), separators=(",", ":")))
        for action, query in (("add_camera", camera), ("edit_alert", alert)):
            url = f"{origin}/{action}?{urlencode(query)}"
            counts[action] += 1
            if args.dry_run or args.selftest:
                print(url)
                continue
            try:
                with urlopen(url, timeout=10) as response:
                    print(url, response.status)
            except (HTTPError, URLError, TimeoutError) as exc:
                print(url, getattr(exc, "code", str(exc)), file=sys.stderr)
                failed = True
                break
    if args.selftest:
        if counts != {"add_camera": 5, "edit_alert": 5}:
            sys.exit(f"SELFTEST FAILED: {counts}")
        print("SELFTEST OK")
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
