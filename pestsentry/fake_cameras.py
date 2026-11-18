"""Five local HTTP cameras with a moving green marker. No model or hardware needed."""

import argparse
import math
import time

import cv2
import numpy as np
from flask import Flask, Response, abort


def make_frame(camera_id, tick=None):
    tick = time.monotonic() if tick is None else tick
    image = np.full((240, 320, 3), 35, dtype=np.uint8)
    offset = int(25 * math.sin(tick / 8 + int(camera_id[-1])))
    cv2.rectangle(image, (110 + offset, 115), (160 + offset, 145), (0, 240, 0), -1)
    cv2.putText(image, camera_id + " - synthetic camera", (12, 25), cv2.FONT_HERSHEY_SIMPLEX,
                0.5, (230, 230, 230), 1)
    success, data = cv2.imencode(".jpg", image)
    if not success:
        raise RuntimeError("fake frame encoding failed")
    return data.tobytes()


def create_app():
    app = Flask(__name__)

    def validate(camera_id):
        if camera_id not in {f"node-{i}" for i in range(1, 6)}:
            abort(404)

    @app.get("/capture/<camera_id>")
    def capture(camera_id):
        validate(camera_id)
        return Response(make_frame(camera_id), mimetype="image/jpeg",
                        headers={"Cache-Control": "no-store"})

    @app.get("/stream/<camera_id>")
    def stream(camera_id):
        validate(camera_id)

        def frames():
            while True:
                frame = make_frame(camera_id)
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " +
                       str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")
                time.sleep(0.5)

        return Response(frames(), mimetype="multipart/x-mixed-replace; boundary=frame")

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8082)
    args = parser.parse_args()
    create_app().run(host="127.0.0.1", port=args.port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
