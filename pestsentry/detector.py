"""Optional YOLOv8n backend.

Clearcam's built-in detector is YOLOv9 with a fixed 80-class head, so a
custom pest checkpoint cannot be loaded into it. This wrapper runs an
Ultralytics .pt file (YOLOv8n or similar) and returns the Nx6 array
clearcam's tracker already expects: x1, y1, x2, y2, score, class id.

Ultralytics pulls in PyTorch. That runtime is a poor fit for a 2 GB board,
so the PestSentry profile defaults to clearcam's own small model.
"""

from __future__ import annotations

import numpy as np


class UltralyticsDetector:
    def __init__(self, weights: str, labels: list[str]):
        if not weights:
            raise RuntimeError("model.weights must point at a YOLOv8n .pt file")
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError(
                "The ultralytics backend needs `pip install ultralytics`. "
                "On the UNO Q, leave model.backend as 'clearcam'."
            ) from exc
        self.labels = list(labels)
        self.model = YOLO(weights)

    def detect(self, frame_bgr):
        result = self.model.predict(frame_bgr, verbose=False)[0]
        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            return np.zeros((0, 6), dtype=np.float32)
        xyxy = boxes.xyxy.cpu().numpy()
        conf = boxes.conf.cpu().numpy()
        cls = boxes.cls.cpu().numpy()
        return np.column_stack([xyxy, conf, cls]).astype(np.float32)
