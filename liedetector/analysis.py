"""Deception-indicator analysis engine.

Turns a stream of `FaceReading`s into a live 0-100 "deception score" and a
per-question verdict. The approach:

1. CALIBRATION: for the first N seconds the subject answers easy, truthful
   questions ("what is your name?"). We record a personal baseline (mean and
   spread) for every indicator. People differ a lot, so everything is
   measured relative to *this* person's own baseline.
2. ANALYSIS: each indicator is converted to a z-score against the baseline
   over a sliding window, clipped, weighted and squashed into 0-100.
3. QUESTION: while a question is "open" the score is accumulated; when it is
   closed the mean score becomes the verdict (TRUTHFUL / INCONCLUSIVE /
   DECEPTIVE).

Indicators (all classic "stress leakage" cues from the deception literature):
  blink_rate     abnormal blink frequency (both freezing and fluttering)
  gaze_aversion  eyes looking away from the camera
  lip_press      lip compression / rolling lips inward
  brow_tension   brows drawn down or inner brows pulled up
  head_motion    fidgeting: angular + positional head velocity
  micro_expr     short (40-500 ms) expression flashes that are quickly masked
  fake_smile     smiling with the mouth but not the eyes, or asymmetric smiles
  eye_arousal    eyes widened or squinted (autonomic arousal)

IMPORTANT: none of these cues are proof of lying. Even trained humans and
polygraphs are unreliable. Treat the output as a stress/discomfort meter and
a piece of entertainment, never as evidence.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from .face_engine import FaceReading

CONTINUOUS_WINDOW = 3.0     # seconds, for per-frame indicators
EVENT_WINDOW = 10.0         # seconds, for blink / micro-expression counts

INDICATORS = [
    "blink_rate", "gaze_aversion", "lip_press", "brow_tension",
    "head_motion", "micro_expr", "fake_smile", "eye_arousal",
]
LABELS = {
    "blink_rate": "Blink rate",
    "gaze_aversion": "Gaze aversion",
    "lip_press": "Lip press",
    "brow_tension": "Brow tension",
    "head_motion": "Head motion",
    "micro_expr": "Micro-expr.",
    "fake_smile": "Fake smile",
    "eye_arousal": "Eye arousal",
}
WEIGHTS = {
    "blink_rate": 0.15, "gaze_aversion": 0.15, "lip_press": 0.15,
    "brow_tension": 0.10, "head_motion": 0.10, "micro_expr": 0.15,
    "fake_smile": 0.10, "eye_arousal": 0.10,
}
# Lower bounds on baseline spread so a perfectly still calibration does not
# make every tiny twitch look enormous.
STD_FLOOR = {
    "gaze_aversion": 0.08, "lip_press": 0.04, "brow_tension": 0.04,
    "head_motion": 4.0, "fake_smile": 0.04, "eye_arousal": 0.04,
}
GAIN = 1.75            # overall sensitivity of the score
Z_SATURATION = 2.5     # z-score at which an indicator is "fully on"

# Blendshapes watched for micro-expressions.
MICRO_SHAPES = [
    "mouthSmileLeft", "mouthSmileRight", "mouthFrownLeft", "mouthFrownRight",
    "noseSneerLeft", "noseSneerRight", "browInnerUp", "browDownLeft",
    "browDownRight", "eyeWideLeft", "eyeWideRight", "mouthStretchLeft",
    "mouthStretchRight", "mouthPressLeft", "mouthPressRight", "jawOpen",
]
MICRO_MIN_S, MICRO_MAX_S = 0.04, 0.50
MICRO_ONSET, MICRO_OFFSET = 0.25, 0.10


class State(Enum):
    NO_FACE = "NO FACE"
    CALIBRATING = "CALIBRATING"
    READY = "READY"
    QUESTION = "ANALYZING"


class Verdict(Enum):
    TRUTHFUL = "TRUTHFUL"
    INCONCLUSIVE = "INCONCLUSIVE"
    DECEPTIVE = "DECEPTIVE"


@dataclass
class QuestionResult:
    index: int
    start: float
    end: float
    mean_score: float
    peak_score: float
    verdict: Verdict
    top_cues: list[str]


@dataclass
class FrameFeatures:
    t: float
    gaze: float
    lip_press: float
    brow_tension: float
    head_motion: float
    fake_smile: float
    eye_arousal: float


@dataclass
class Baseline:
    mean: dict[str, float] = field(default_factory=dict)
    std: dict[str, float] = field(default_factory=dict)
    blink_rate: float = 15.0        # blinks / minute
    micro_rate: float = 0.5         # micro-expressions per EVENT_WINDOW
    complete: bool = False


def _mean(v: float, w: float) -> float:
    return v if w == 0 else 0.5 * (v + w)


class BlinkDetector:
    """Hysteresis blink detector fusing blendshape + eye-aspect-ratio cues."""

    def __init__(self):
        self.closed = False
        self.events: deque[float] = deque()

    def update(self, r: FaceReading) -> bool:
        blink = 0.5 * (r.bs("eyeBlinkLeft") + r.bs("eyeBlinkRight"))
        ear = 0.5 * (r.ear_left + r.ear_right)
        closed_now = blink > 0.5 or ear < 0.16
        open_now = blink < 0.3 and ear > 0.19
        fired = False
        if not self.closed and closed_now:
            self.closed = True
        elif self.closed and open_now:
            self.closed = False
            self.events.append(r.timestamp)
            fired = True
        return fired

    def count_since(self, t: float) -> int:
        while self.events and self.events[0] < t:
            self.events.popleft()
        return len(self.events)


class MicroExpressionDetector:
    """Flags brief expression spikes (rise then fall in < 0.5 s)."""

    def __init__(self, tau: float = 2.0):
        self.tau = tau
        self.ema: dict[str, float] = {}
        self.active: dict[str, tuple[float, float]] = {}   # name -> (onset_t, peak)
        self.events: deque[tuple[float, str]] = deque()
        self.last_t: float | None = None
        self.last_label = ""

    def update(self, r: FaceReading) -> list[str]:
        dt = 0.0 if self.last_t is None else max(0.0, r.timestamp - self.last_t)
        self.last_t = r.timestamp
        alpha = 1.0 - math.exp(-dt / self.tau) if dt > 0 else 1.0
        fired: list[str] = []
        for name in MICRO_SHAPES:
            v = r.bs(name)
            base = self.ema.get(name, v)
            dev = v - base
            if name in self.active:
                onset, peak = self.active[name]
                if dev > MICRO_OFFSET:
                    self.active[name] = (onset, max(peak, dev))
                else:
                    dur = r.timestamp - onset
                    del self.active[name]
                    if MICRO_MIN_S <= dur <= MICRO_MAX_S:
                        self.events.append((r.timestamp, name))
                        fired.append(name)
                        self.last_label = name
            elif dev > MICRO_ONSET:
                self.active[name] = (r.timestamp, dev)
            # Only track the baseline while not in an active spike, so the
            # spike itself does not pull the baseline up.
            if name not in self.active:
                self.ema[name] = base + alpha * (v - base)
        return fired

    def count_since(self, t: float) -> int:
        while self.events and self.events[0][0] < t:
            self.events.popleft()
        return len(self.events)


class DeceptionAnalyzer:
    def __init__(self, calibration_seconds: float = 10.0):
        self.calibration_seconds = calibration_seconds
        self.state = State.NO_FACE
        self.baseline = Baseline()
        self.blinks = BlinkDetector()
        self.micro = MicroExpressionDetector()
        self.frames: deque[FrameFeatures] = deque()
        self.prev: FaceReading | None = None
        self.calib_start: float | None = None
        self.calib_frames: list[FrameFeatures] = []
        self.calib_blinks = 0
        self.calib_micro = 0
        self.score = 0.0             # smoothed, 0..100
        self.raw_score = 0.0
        self.indicators: dict[str, float] = {k: 0.0 for k in INDICATORS}
        self.zscores: dict[str, float] = {k: 0.0 for k in INDICATORS}
        self.question_scores: list[float] = []
        self.question_start: float | None = None
        self.question_cues: dict[str, float] = {k: 0.0 for k in INDICATORS}
        self.history: list[QuestionResult] = []
        self.last_verdict: QuestionResult | None = None
        self.face_lost_since: float | None = None
        self.last_t = 0.0

    # ------------------------------------------------------------- control
    def recalibrate(self) -> None:
        self.baseline = Baseline()
        self.calib_start = None
        self.calib_frames = []
        self.calib_blinks = 0
        self.calib_micro = 0
        self.frames.clear()
        self.score = 0.0
        self.question_scores = []
        self.question_start = None
        if self.state != State.NO_FACE:
            self.state = State.CALIBRATING

    def calibration_progress(self) -> float:
        if self.calib_start is None:
            return 0.0
        return min(1.0, (self.last_t - self.calib_start) / self.calibration_seconds)

    def toggle_question(self) -> QuestionResult | None:
        """SPACE: open a question, or close it and return the verdict."""
        if not self.baseline.complete:
            return None
        if self.state == State.QUESTION:
            return self._close_question()
        self.state = State.QUESTION
        self.question_start = self.last_t
        self.question_scores = []
        self.question_cues = {k: 0.0 for k in INDICATORS}
        return None

    def _close_question(self) -> QuestionResult | None:
        scores = self.question_scores[max(0, len(self.question_scores) // 6):]  # drop first ~0.5 s
        self.state = State.READY
        if not scores:
            return None
        mean = float(np.mean(scores))
        peak = float(np.max(scores))
        if mean >= 60:
            verdict = Verdict.DECEPTIVE
        elif mean >= 40:
            verdict = Verdict.INCONCLUSIVE
        else:
            verdict = Verdict.TRUTHFUL
        n = max(1, len(self.question_scores))
        cues = sorted(self.question_cues.items(), key=lambda kv: -kv[1])
        top = [LABELS[k] for k, v in cues[:3] if v / n > 0.25]
        res = QuestionResult(len(self.history) + 1, self.question_start or 0.0,
                             self.last_t, mean, peak, verdict, top)
        self.history.append(res)
        self.last_verdict = res
        return res

    # -------------------------------------------------------------- update
    def update(self, reading: FaceReading | None, t: float) -> None:
        self.last_t = t
        if reading is None:
            if self.face_lost_since is None:
                self.face_lost_since = t
            if t - self.face_lost_since > 0.5:
                self.prev = None
                if self.state == State.CALIBRATING:
                    self.recalibrate()
                self.state = State.NO_FACE
            return
        self.face_lost_since = None
        if self.state == State.NO_FACE:
            self.state = State.READY if self.baseline.complete else State.CALIBRATING
            self.prev = None

        blinked = self.blinks.update(reading)
        micro_fired = self.micro.update(reading)
        feat = self._features(reading)
        self.prev = reading
        self.frames.append(feat)
        while self.frames and self.frames[0].t < t - EVENT_WINDOW:
            self.frames.popleft()

        if self.state == State.CALIBRATING:
            if self.calib_start is None:
                self.calib_start = t
            self.calib_frames.append(feat)
            self.calib_blinks += int(blinked)
            self.calib_micro += len(micro_fired)
            if t - self.calib_start >= self.calibration_seconds:
                self._finish_calibration(t - self.calib_start)
            return

        self._compute_score(t)
        if self.state == State.QUESTION:
            self.question_scores.append(self.raw_score)
            for k, v in self.indicators.items():
                self.question_cues[k] += v

    # ------------------------------------------------------------ features
    def _features(self, r: FaceReading) -> FrameFeatures:
        gaze = max(
            _mean(r.bs("eyeLookOutLeft"), r.bs("eyeLookInRight")),
            _mean(r.bs("eyeLookInLeft"), r.bs("eyeLookOutRight")),
            _mean(r.bs("eyeLookUpLeft"), r.bs("eyeLookUpRight")),
            _mean(r.bs("eyeLookDownLeft"), r.bs("eyeLookDownRight")),
        )
        lip_press = max(_mean(r.bs("mouthPressLeft"), r.bs("mouthPressRight")),
                        _mean(r.bs("mouthRollUpper"), r.bs("mouthRollLower")))
        brow = max(_mean(r.bs("browDownLeft"), r.bs("browDownRight")), r.bs("browInnerUp"))

        head_motion = 0.0
        if self.prev is not None:
            dt = r.timestamp - self.prev.timestamp
            if 1e-3 < dt < 1.0:
                ang = math.sqrt((r.yaw - self.prev.yaw) ** 2 + (r.pitch - self.prev.pitch) ** 2
                                + (r.roll - self.prev.roll) ** 2) / dt
                trans = float(np.linalg.norm(r.translation - self.prev.translation)) / dt
                head_motion = ang + 2.0 * trans     # deg/s + cm/s (weighted)

        smile = _mean(r.bs("mouthSmileLeft"), r.bs("mouthSmileRight"))
        duchenne = _mean(r.bs("cheekSquintLeft"), r.bs("cheekSquintRight"))
        asym = abs(r.bs("mouthSmileLeft") - r.bs("mouthSmileRight"))
        fake_smile = max(0.0, smile - 1.5 * duchenne) + asym if smile > 0.2 else 0.0

        eye_arousal = max(_mean(r.bs("eyeWideLeft"), r.bs("eyeWideRight")),
                          _mean(r.bs("eyeSquintLeft"), r.bs("eyeSquintRight")))

        return FrameFeatures(r.timestamp, gaze, lip_press, brow, head_motion, fake_smile, eye_arousal)

    def _finish_calibration(self, duration: float) -> None:
        cols = {
            "gaze_aversion": [f.gaze for f in self.calib_frames],
            "lip_press": [f.lip_press for f in self.calib_frames],
            "brow_tension": [f.brow_tension for f in self.calib_frames],
            "head_motion": [f.head_motion for f in self.calib_frames],
            "fake_smile": [f.fake_smile for f in self.calib_frames],
            "eye_arousal": [f.eye_arousal for f in self.calib_frames],
        }
        b = Baseline()
        for k, v in cols.items():
            arr = np.asarray(v, dtype=np.float64)
            b.mean[k] = float(arr.mean()) if arr.size else 0.0
            b.std[k] = max(float(arr.std()) if arr.size else 0.0, STD_FLOOR[k])
        b.blink_rate = 60.0 * self.calib_blinks / max(duration, 1e-3)
        b.micro_rate = self.calib_micro * EVENT_WINDOW / max(duration, 1e-3)
        b.complete = True
        self.baseline = b
        self.state = State.READY

    # --------------------------------------------------------------- score
    def _compute_score(self, t: float) -> None:
        b = self.baseline
        recent = [f for f in self.frames if f.t >= t - CONTINUOUS_WINDOW]
        if not recent:
            return
        win = {
            "gaze_aversion": np.mean([f.gaze for f in recent]),
            "lip_press": np.mean([f.lip_press for f in recent]),
            "brow_tension": np.mean([f.brow_tension for f in recent]),
            "head_motion": np.mean([f.head_motion for f in recent]),
            "fake_smile": np.mean([f.fake_smile for f in recent]),
            "eye_arousal": np.mean([f.eye_arousal for f in recent]),
        }
        z: dict[str, float] = {}
        for k, v in win.items():
            z[k] = (float(v) - b.mean[k]) / b.std[k]

        # Event-based indicators over a longer window.
        span = min(EVENT_WINDOW, max(1.0, t - (self.frames[0].t if self.frames else t)))
        blink_rate = 60.0 * self.blinks.count_since(t - EVENT_WINDOW) / span
        z["blink_rate"] = (blink_rate - b.blink_rate) / max(0.5 * b.blink_rate, 6.0)
        micro = self.micro.count_since(t - EVENT_WINDOW) * (EVENT_WINDOW / span)
        z["micro_expr"] = (micro - b.micro_rate) / max(0.5 * b.micro_rate, 1.0)

        ind: dict[str, float] = {}
        for k in INDICATORS:
            zz = abs(z[k]) if k == "blink_rate" else z[k]      # blink freeze counts too
            ind[k] = float(np.clip(zz / Z_SATURATION, 0.0, 1.0))
        raw = sum(WEIGHTS[k] * ind[k] for k in INDICATORS) * GAIN
        self.raw_score = float(np.clip(raw, 0.0, 1.0) * 100.0)
        # Smooth the display score (~0.5 s time constant at 30 fps).
        self.score += 0.12 * (self.raw_score - self.score)
        self.indicators = ind
        self.zscores = z
        self._live_blink_rate = blink_rate

    @property
    def live_blink_rate(self) -> float:
        return getattr(self, "_live_blink_rate", 0.0)

    def live_verdict(self) -> Verdict:
        if self.score >= 60:
            return Verdict.DECEPTIVE
        if self.score >= 40:
            return Verdict.INCONCLUSIVE
        return Verdict.TRUTHFUL
