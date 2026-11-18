"""Run from the Clearcam repository with python -m pestsentry."""

import argparse
import logging
import os

from .config import load_config


def main():
    parser = argparse.ArgumentParser(description="PestSentry CPU camera hub")
    parser.add_argument("--config", default="pestsentry/config.local.json")
    parser.add_argument("--check-config", action="store_true", help="validate the profile and exit")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
    except (OSError, ValueError, TypeError) as error:
        parser.error(str(error))
    if args.check_config:
        print(f"Valid config: {len(config['cameras'])} cameras, {config['model']['backend']} backend")
        return
    if config["hub"]["host"] not in ("127.0.0.1", "localhost", "::1") and not os.environ.get("PESTSENTRY_API_TOKEN"):
        parser.error("set PESTSENTRY_API_TOKEN before exposing the dashboard beyond localhost")
    from .hub import Hub
    from .web import create_app

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        hub = Hub(config)
    except (OSError, ValueError, RuntimeError) as error:
        parser.error(f"hub startup failed ({type(error).__name__}): check the configured model and dependencies")
    app = create_app(hub)
    if config["model"]["backend"] == "demo":
        logging.warning("DEMO detector enabled. It recognizes only the fake camera's green marker.")
    hub.start()
    try:
        app.run(host=config["hub"]["host"], port=config["hub"]["port"], threaded=True,
                debug=False, use_reloader=False)
    finally:
        hub.stop()


if __name__ == "__main__":
    main()
