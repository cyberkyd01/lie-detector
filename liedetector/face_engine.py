"""Native, on-device face detection and facial reading engine.

Everything runs locally on this machine. No frames or data leave the device.

The engine wraps MediaPipe's FaceLandmarker task (478 3-D landmarks,
52 ARKit-style expression blendshapes and a head transformation matrix)
and turns each camera frame into a `FaceReading`, which the analysis
engine consumes.
"""
from __future__ import annotations

import math
import os
import sys
import time
from dataclasses import dataclass

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions, vision

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MODEL_PATH = os.path.join(PROJECT_ROOT, "models", "face_landmarker.task")

# Landmark indices (MediaPipe face mesh topology).
LEFT_EYE = [33, 160, 158, 133, 153, 144]
RIGHT_EYE = [362, 385, 387, 263, 373, 380]
MOUTH_TOP, MOUTH_BOTTOM = 13, 14
MOUTH_LEFT, MOUTH_RIGHT = 61, 291
NOSE_TIP = 1
CHIN = 152
FOREHEAD = 10


@dataclass
class FaceReading:
    """One frame's worth of facial measurements."""

    timestamp: float                 # seconds, monotonic
    landmarks_px: np.ndarray         # (478, 2) pixel coordinates
    landmarks_norm: np.ndarray       # (478, 3) normalised x, y, z
    blendshapes: dict[str, float]    # 52 expression coefficients, 0..1
    yaw: float                       # degrees
    pitch: float                     # degrees
    roll: float                      # degrees
    translation: np.ndarray          # (3,) head position, cm
    bbox: tuple[int, int, int, int]  # x, y, w, h in pixels
    ear_left: float                  # eye aspect ratio (geometric blink cue)
    ear_right: float
    mouth_open: float                # normalised lip gap
    mouth_width: float               # normalised mouth width

    def bs(self, name: str) -> float:
        return self.blendshapes.get(name, 0.0)


def _euler_from_matrix(m: np.ndarray) -> tuple[float, float, float]:
    """Rotation matrix -> (yaw, pitch, roll) in degrees."""
    r = m[:3, :3]
    sy = math.sqrt(r[0, 0] ** 2 + r[1, 0] ** 2)
    if sy > 1e-6:
        pitch = math.atan2(r[2, 1], r[2, 2])
        yaw = math.atan2(-r[2, 0], sy)
        roll = math.atan2(r[1, 0], r[0, 0])
    else:
        pitch = math.atan2(-r[1, 2], r[1, 1])
        yaw = math.atan2(-r[2, 0], sy)
        roll = 0.0
    return math.degrees(yaw), math.degrees(pitch), math.degrees(roll)


def _eye_aspect_ratio(pts: np.ndarray) -> float:
    """Classic EAR: (|p2-p6| + |p3-p5|) / (2 |p1-p4|)."""
    a = np.linalg.norm(pts[1] - pts[5])
    b = np.linalg.norm(pts[2] - pts[4])
    c = np.linalg.norm(pts[0] - pts[3])
    return float((a + b) / (2.0 * c + 1e-6))


class FaceEngine:
    """Reads faces from BGR frames. Create once, call `read()` per frame."""

    def __init__(self, model_path: str = DEFAULT_MODEL_PATH, delegate: str = "auto",
                 num_faces: int = 1):
        if not os.path.exists(model_path):
            raise FileNotFoundError(
                f"Face model not found at {model_path}. "
                "Run ./setup.sh (or see README) to download it.")

        if delegate == "auto":
            # On macOS the CPU graph aborts inside MediaPipe's Metal helper,
            # while the GPU delegate works. Elsewhere CPU is the safe default.
            delegate = "gpu" if sys.platform == "darwin" else "cpu"
        self.delegate = delegate
        dlg = BaseOptions.Delegate.GPU if delegate == "gpu" else BaseOptions.Delegate.CPU

        options = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=model_path, delegate=dlg),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=num_faces,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(options)
        self._t0 = time.monotonic()
        self._last_ts_ms = -1
        self.last_inference_ms = 0.0

    def close(self) -> None:
        self._landmarker.close()

    # ------------------------------------------------------------------
    def read(self, frame_bgr: np.ndarray, timestamp: float | None = None) -> FaceReading | None:
        """Analyse one frame. Returns None when no face is visible."""
        if timestamp is None:
            timestamp = time.monotonic() - self._t0
        ts_ms = int(timestamp * 1000)
        if ts_ms <= self._last_ts_ms:          # MediaPipe needs strictly increasing stamps
            ts_ms = self._last_ts_ms + 1
        self._last_ts_ms = ts_ms

        # The GPU path only accepts RGBA on macOS; RGBA works on CPU too.
        rgba = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGBA)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGBA, data=rgba)

        t = time.perf_counter()
        result = self._landmarker.detect_for_video(mp_image, ts_ms)
        self.last_inference_ms = (time.perf_counter() - t) * 1000.0

        if not result.face_landmarks:
            return None

        h, w = frame_bgr.shape[:2]
        lm = result.face_landmarks[0]
        norm = np.array([[p.x, p.y, p.z] for p in lm], dtype=np.float32)
        px = np.empty((len(lm), 2), dtype=np.float32)
        px[:, 0] = norm[:, 0] * w
        px[:, 1] = norm[:, 1] * h

        blend = {}
        if result.face_blendshapes:
            blend = {c.category_name: float(c.score) for c in result.face_blendshapes[0]}

        yaw = pitch = roll = 0.0
        translation = np.zeros(3, dtype=np.float32)
        if result.facial_transformation_matrixes:
            m = np.asarray(result.facial_transformation_matrixes[0], dtype=np.float64)
            yaw, pitch, roll = _euler_from_matrix(m)
            translation = m[:3, 3].astype(np.float32)

        x0, y0 = px.min(axis=0)
        x1, y1 = px.max(axis=0)
        bbox = (int(x0), int(y0), int(x1 - x0), int(y1 - y0))

        ear_l = _eye_aspect_ratio(norm[LEFT_EYE, :2])
        ear_r = _eye_aspect_ratio(norm[RIGHT_EYE, :2])
        face_h = float(np.linalg.norm(norm[FOREHEAD, :2] - norm[CHIN, :2])) + 1e-6
        mouth_open = float(np.linalg.norm(norm[MOUTH_TOP, :2] - norm[MOUTH_BOTTOM, :2])) / face_h
        mouth_width = float(np.linalg.norm(norm[MOUTH_LEFT, :2] - norm[MOUTH_RIGHT, :2])) / face_h

        return FaceReading(
            timestamp=timestamp,
            landmarks_px=px,
            landmarks_norm=norm,
            blendshapes=blend,
            yaw=yaw, pitch=pitch, roll=roll,
            translation=translation,
            bbox=bbox,
            ear_left=ear_l, ear_right=ear_r,
            mouth_open=mouth_open, mouth_width=mouth_width,
        )
