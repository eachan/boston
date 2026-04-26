from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

from boston.models import FighterVisibility, ScoringCandidate


LOGGER = logging.getLogger(__name__)


class VisionAnalyzer:
    """Camera analyzer with fighter visibility checks and lightweight strike detection."""

    def __init__(
        self,
        camera_index: int,
        frame_width: int,
        frame_height: int,
        fps: int,
        detection_interval_seconds: float,
        min_color_area: int,
    ) -> None:
        self.camera_index = camera_index
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.fps = fps
        self.detection_interval_seconds = detection_interval_seconds
        self.min_color_area = min_color_area

        self._running = False
        self._available = False
        self._match_running = False
        self._last_visibility = FighterVisibility(False, False)
        self._candidate_queue: queue.Queue[ScoringCandidate] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._video_capture: cv2.VideoCapture | None = None
        self._last_attack_ts = 0.0
        self._hailo_model_path: str | None = None

        self._white_history: deque[tuple[float, tuple[int, int], int]] = deque(maxlen=10)
        self._blue_history: deque[tuple[float, tuple[int, int], int]] = deque(maxlen=10)

    @property
    def hailo_model_path(self) -> str | None:
        return self._hailo_model_path

    def set_hailo_model_path(self, model_path: str | None) -> None:
        self._hailo_model_path = model_path

    @property
    def running(self) -> bool:
        return self._running

    @property
    def available(self) -> bool:
        return self._available

    def set_match_running(self, running: bool) -> None:
        self._match_running = running

    def start(self) -> None:
        if self._running:
            return

        cap = cv2.VideoCapture(self.camera_index)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.frame_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.frame_height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)

        if not cap.isOpened():
            LOGGER.error("Camera %s failed to open", self.camera_index)
            self._available = False
            return

        if self._hailo_model_path:
            LOGGER.info("Hailo model selected for vision pipeline: %s", self._hailo_model_path)

        self._video_capture = cap
        self._available = True
        self._running = True
        self._thread = threading.Thread(target=self._worker, daemon=True, name="boston-vision")
        self._thread.start()
        LOGGER.info("Vision analyzer started")

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        if self._video_capture is not None:
            self._video_capture.release()
            self._video_capture = None
        self._available = False

    def get_visibility(self) -> FighterVisibility:
        with self._lock:
            return FighterVisibility(
                white_visible=self._last_visibility.white_visible,
                blue_visible=self._last_visibility.blue_visible,
                white_area=self._last_visibility.white_area,
                blue_area=self._last_visibility.blue_area,
            )

    def pop_candidates(self, limit: int = 8) -> list[ScoringCandidate]:
        candidates: list[ScoringCandidate] = []
        for _ in range(limit):
            try:
                candidates.append(self._candidate_queue.get_nowait())
            except queue.Empty:
                break
        return candidates

    def _worker(self) -> None:
        while self._running and self._video_capture is not None:
            ok, frame = self._video_capture.read()
            if not ok:
                LOGGER.warning("Camera frame read failed")
                time.sleep(0.1)
                continue

            analyzed = self._analyze_frame(frame)
            with self._lock:
                self._last_visibility = analyzed["visibility"]

            if self._match_running and analyzed["visibility"].both_visible:
                candidate = self._infer_scoring_candidate(analyzed)
                if candidate:
                    self._candidate_queue.put(candidate)

            time.sleep(self.detection_interval_seconds)

    def _analyze_frame(self, frame: np.ndarray) -> dict[str, object]:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        white_mask = cv2.inRange(hsv, np.array([0, 0, 180]), np.array([180, 60, 255]))
        blue_mask = cv2.inRange(hsv, np.array([90, 60, 40]), np.array([140, 255, 255]))

        white_area, white_center = self._largest_blob(white_mask)
        blue_area, blue_center = self._largest_blob(blue_mask)

        visibility = FighterVisibility(
            white_visible=white_area >= self.min_color_area,
            blue_visible=blue_area >= self.min_color_area,
            white_area=white_area,
            blue_area=blue_area,
        )

        now = time.time()
        self._white_history.append((now, white_center, white_area))
        self._blue_history.append((now, blue_center, blue_area))

        return {
            "visibility": visibility,
            "white_center": white_center,
            "blue_center": blue_center,
            "white_area": white_area,
            "blue_area": blue_area,
            "timestamp": now,
        }

    def _infer_scoring_candidate(self, analyzed: dict[str, object]) -> ScoringCandidate | None:
        now = float(analyzed["timestamp"])
        if now - self._last_attack_ts < 1.25:
            return None

        white_center = analyzed["white_center"]
        blue_center = analyzed["blue_center"]
        if not isinstance(white_center, tuple) or not isinstance(blue_center, tuple):
            return None

        white_speed = self._estimate_speed(self._white_history)
        blue_speed = self._estimate_speed(self._blue_history)

        attacker = "white" if white_speed > blue_speed else "blue"
        target = "blue" if attacker == "white" else "white"

        dominant_speed = max(white_speed, blue_speed)
        if dominant_speed < 90:
            return None

        attacker_center = white_center if attacker == "white" else blue_center
        target_center = blue_center if attacker == "white" else white_center

        dx = float(target_center[0] - attacker_center[0])
        dy = float(target_center[1] - attacker_center[1])
        distance = max(1.0, np.hypot(dx, dy))

        closeness = max(0.0, 1.0 - (distance / max(200.0, self.frame_width * 0.5)))
        timing = min(1.0, dominant_speed / 350.0)
        quality = min(1.0, 0.35 + 0.65 * ((timing + closeness) / 2.0))
        control = min(1.0, 0.4 + 0.6 * closeness)

        technique = self._technique_from_geometry(attacker_center, target_center, dominant_speed)
        self._last_attack_ts = now
        return ScoringCandidate(
            attacker=attacker,
            target=target,
            technique=technique,
            quality=float(quality),
            timing=float(timing),
            distance=float(closeness),
            control=float(control),
            source="vision_heuristic",
            metadata={
                "dominant_speed": dominant_speed,
                "pixel_distance": distance,
            },
        )

    @staticmethod
    def _technique_from_geometry(attacker: tuple[int, int], target: tuple[int, int], speed: float) -> str:
        y_delta = attacker[1] - target[1]
        if y_delta < -70 and speed > 135:
            return "head_kick"
        if y_delta < 30 and speed > 110:
            return "body_kick"
        return "punch"

    @staticmethod
    def _estimate_speed(history: deque[tuple[float, tuple[int, int], int]]) -> float:
        if len(history) < 2:
            return 0.0
        (t1, p1, _), (t2, p2, _) = history[-2], history[-1]
        dt = max(1e-6, t2 - t1)
        dx = float(p2[0] - p1[0])
        dy = float(p2[1] - p1[1])
        return float(np.hypot(dx, dy) / dt)

    @staticmethod
    def _largest_blob(mask: np.ndarray) -> tuple[int, tuple[int, int]]:
        kernel = np.ones((5, 5), np.uint8)
        clean = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        contours, _ = cv2.findContours(clean, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return 0, (0, 0)
        contour = max(contours, key=cv2.contourArea)
        area = int(cv2.contourArea(contour))
        moments = cv2.moments(contour)
        if moments["m00"] == 0:
            return area, (0, 0)
        center = (int(moments["m10"] / moments["m00"]), int(moments["m01"] / moments["m00"]))
        return area, center


def select_best_hailo_model(model_dir: str, preferred_models: list[str]) -> str | None:
    base = Path(model_dir)
    if not base.exists() or not base.is_dir():
        return None

    available = {p.name: str(p) for p in base.glob("*.hef") if p.is_file()}
    for name in preferred_models:
        if name in available:
            return available[name]

    if available:
        first = sorted(available.values())[0]
        return first
    return None
