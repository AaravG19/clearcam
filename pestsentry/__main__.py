"""PestSentry command line.

    python3 -m pestsentry check --config pestsentry/config.example.json
    python3 -m pestsentry fake-cameras
    python3 -m pestsentry notify-sink
    python3 -m pestsentry simulate --config pestsentry/config.fake.json --offline
    python3 -m pestsentry run --config pestsentry/config.fake.json
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from pestsentry.config import load_config

REPO = Path(__file__).resolve().parent.parent


def _cmd_check(args) -> int:
    cfg = load_config(args.config)
    alerts = cfg["alerts"]
    print(f"config: {cfg['path']}")
    print(f"backend: {cfg['model']['backend']} size={cfg['model']['size']} res={cfg['model']['res']}")
    if cfg["model"]["weights"]:
        print(f"weights: {cfg['model']['weights']}")
    print(
        "alerts: "
        + ", ".join(f"{name}={cid}" for name, cid in zip(alerts["classes"], alerts["class_ids"]))
    )
    print(
        f"confidence {alerts['confidence']}  cooldown {alerts['cooldown_seconds']}s  "
        f"min_speed {alerts['min_speed']}  min_crop {alerts['min_crop']}"
    )
    print(f"notify: {cfg['hub']['notify_url'] or '(clearcam default, no local snapshot sink)'}")
    for cam in cfg["cameras"]:
        place = f" ({cam['location']})" if cam["location"] else ""
        print(f"  {cam['name']}{place}: {cam['url']}")
    return 0


def _cmd_run(args) -> int:
    cfg_path = Path(args.config).resolve()
    load_config(cfg_path)  # fail before replacing this process
    os.chdir(REPO)
    os.environ["PESTSENTRY_PROFILE"] = str(cfg_path)
    clearcam = REPO / "clearcam.py"
    os.execv(sys.executable, [sys.executable, str(clearcam), *args.rest])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="pestsentry", description="PestSentry profile for clearcam")
    sub = parser.add_subparsers(dest="cmd", required=True)

    check = sub.add_parser("check", help="validate a config and print the camera plan")
    check.add_argument("--config", default="pestsentry/config.example.json")
    check.set_defaults(func=_cmd_check)

    run = sub.add_parser("run", help="apply the profile and start clearcam")
    run.add_argument("--config", default="pestsentry/config.example.json")
    run.add_argument("rest", nargs=argparse.REMAINDER)
    run.set_defaults(func=_cmd_run)

    fake = sub.add_parser("fake-cameras", help="serve five MJPEG streams with no hardware")
    fake.add_argument("--host", default="0.0.0.0")
    fake.add_argument("--port", type=int, default=8090)

    sink = sub.add_parser("notify-sink", help="save alert snapshots posted by clearcam")
    sink.add_argument("--host", default="0.0.0.0")
    sink.add_argument("--port", type=int, default=8091)
    sink.add_argument("--topic", default="")

    sim = sub.add_parser("simulate", help="post one practice alert per camera")
    sim.add_argument("--config", default="pestsentry/config.fake.json")
    sim.add_argument("--offline", action="store_true")

    args = parser.parse_args(argv)
    if args.cmd == "fake-cameras":
        from pestsentry.fake_cameras import main as fake_main
        fake_main(["--host", args.host, "--port", str(args.port)])
        return 0
    if args.cmd == "notify-sink":
        from pestsentry.notify_sink import main as sink_main
        sink_main(["--host", args.host, "--port", str(args.port), "--topic", args.topic])
        return 0
    if args.cmd == "simulate":
        from pestsentry.simulate import main as sim_main
        extra = ["--config", args.config]
        if args.offline:
            extra.append("--offline")
        return sim_main(extra)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
