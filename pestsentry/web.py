"""Local Flask dashboard, event search and optional gateway JPEG ingress."""

import base64
import hmac
import math
import os
from datetime import datetime, timezone

from flask import Flask, Response, abort, jsonify, render_template, request


def _timestamp(value):
    if not value:
        return None
    try:
        number = float(value)
    except ValueError:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        number = date.timestamp()
    if not math.isfinite(number):
        raise ValueError("invalid timestamp")
    return number


def create_app(hub, api_token=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = hub.config["hub"]["max_jpeg_bytes"]
    token = os.environ.get("PESTSENTRY_API_TOKEN", "") if api_token is None else api_token

    @app.before_request
    def authenticate():
        if not token:
            return None
        authorization = request.headers.get("Authorization", "")
        supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
        if authorization.startswith("Basic "):
            try:
                supplied = base64.b64decode(authorization[6:], validate=True).decode().split(":", 1)[1]
            except (ValueError, IndexError, UnicodeError):
                supplied = ""
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            return Response("Authentication required", 401,
                            {"WWW-Authenticate": 'Basic realm="PestSentry"'})
        return None

    @app.get("/")
    def dashboard():
        return render_template("dashboard.html", cameras=hub.camera_configs.values(),
                               classes=hub.config["alerts"]["classes"],
                               demo=hub.config["model"]["backend"] == "demo")

    @app.get("/api/status")
    def status():
        return jsonify(hub.status())

    @app.get("/api/events")
    def events():
        try:
            confidence = float(request.args.get("confidence", 0))
            if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError("confidence must be between 0 and 1")
            result = hub.store.search(camera=request.args.get("camera"), label=request.args.get("class"),
                                      since=_timestamp(request.args.get("since")),
                                      until=_timestamp(request.args.get("until")), confidence=confidence,
                                      query=request.args.get("q", "")[:200])
        except (ValueError, OverflowError):
            return jsonify(error="invalid confidence or date; dates use ISO 8601 UTC or Unix seconds"), 400
        for event in result:
            event["snapshot_url"] = f"/api/events/{event['id']}/snapshot.jpg"
        return jsonify(result)

    @app.get("/api/events/<event_id>/snapshot.jpg")
    def event_snapshot(event_id):
        data = hub.store.snapshot(event_id)
        if data is None:
            abort(404)
        return Response(data, mimetype="image/jpeg", headers={"Cache-Control": "private, no-store"})

    @app.get("/api/cameras/<camera_id>/snapshot.jpg")
    def camera_snapshot(camera_id):
        if camera_id not in hub.frames:
            abort(404)
        with hub.lock:
            data = hub.frames[camera_id]["jpeg"]
        if data is None:
            abort(503, "waiting for inference")
        return Response(data, mimetype="image/jpeg", headers={"Cache-Control": "private, no-store"})

    @app.post("/api/cameras/<camera_id>/frame")
    def push_frame(camera_id):
        if camera_id not in hub.camera_configs:
            abort(404)
        if hub.camera_configs[camera_id]["source"] != "push":
            return jsonify(error="camera is configured for snapshot polling"), 409
        if request.mimetype != "image/jpeg":
            return jsonify(error="send raw image/jpeg bytes"), 415
        try:
            hub.publish(camera_id, request.get_data())
        except ValueError as error:
            return jsonify(error=str(error)), 400
        return jsonify(accepted=True), 202

    return app
