"""Alert decisions shared by the hardware-free simulator.

The live hub uses clearcam's RollingClassCounter with the same cooldown,
confidence, and class list. This object mirrors that rule so it can be
tested without loading the detection model:

- ignore classes that are not on the pest list
- ignore boxes under the confidence threshold
- count a track id once
- send at most one phone alert per camera per cooldown window
"""

from __future__ import annotations


class AlertState:
    def __init__(self, class_ids, confidence: float, cooldown_seconds: float):
        self.class_ids = set(int(c) for c in class_ids)
        self.confidence = float(confidence)
        self.cooldown_seconds = float(cooldown_seconds)
        self._seen = {}
        self._last = {}

    def consider(self, camera: str, class_id: int, score: float, track_id: int, now: float) -> bool:
        if int(class_id) not in self.class_ids:
            return False
        if float(score) < self.confidence:
            return False
        seen = self._seen.setdefault(camera, set())
        if int(track_id) in seen:
            return False
        seen.add(int(track_id))
        last = self._last.get(camera, float("-inf"))
        if now - last < self.cooldown_seconds:
            return False
        self._last[camera] = now
        return True
