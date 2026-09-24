"""On-screen overlay: face mesh, score gauge, indicator bars, verdicts."""
from __future__ import annotations

import cv2
import numpy as np

from .analysis import DeceptionAnalyzer, INDICATORS, LABELS, State, Verdict
from .face_engine import FaceReading

FONT = cv2.FONT_HERSHEY_SIMPLEX
WHITE = (255, 255, 255)
GREY = (170, 170, 170)
DARK = (25, 25, 25)
GREEN = (80, 200, 80)
AMBER = (0, 190, 255)
RED = (60, 60, 230)
CYAN = (230, 200, 80)

VERDICT_COLOR = {Verdict.TRUTHFUL: GREEN, Verdict.INCONCLUSIVE: AMBER, Verdict.DECEPTIVE: RED}
PANEL_W = 300
MIN_H = 660          # panel needs this much height; shorter frames are padded


def _score_color(score: float):
    if score >= 60:
        return RED
    if score >= 40:
        return AMBER
    return GREEN


def _text(img, s, org, scale=0.55, color=WHITE, thick=1):
    # Drop shadow at the same thickness: OpenCV 5 changes glyph advance with
    # thickness, so a thicker outline pass would leak past the text.
    x, y = org
    cv2.putText(img, s, (x + 1, y + 1), FONT, scale, (0, 0, 0), thick, cv2.LINE_AA)
    cv2.putText(img, s, org, FONT, scale, color, thick, cv2.LINE_AA)


def _bar(img, x, y, w, h, frac, color, bg=(60, 60, 60)):
    cv2.rectangle(img, (x, y), (x + w, y + h), bg, -1)
    cv2.rectangle(img, (x, y), (x + int(w * max(0.0, min(1.0, frac))), y + h), color, -1)
    cv2.rectangle(img, (x, y), (x + w, y + h), (110, 110, 110), 1)


def draw_face(frame: np.ndarray, r: FaceReading | None, show_mesh: bool) -> None:
    if r is None:
        return
    x, y, w, h = r.bbox
    cv2.rectangle(frame, (x, y), (x + w, y + h), CYAN, 1)
    if show_mesh:
        pts = r.landmarks_px.astype(np.int32)
        for px, py in pts[::2]:
            cv2.circle(frame, (int(px), int(py)), 1, (0, 255, 160), -1)


def compose(frame: np.ndarray, r: FaceReading | None, an: DeceptionAnalyzer,
            fps: float, infer_ms: float, show_mesh: bool, speak: bool) -> np.ndarray:
    """Return a new image: camera view + right-hand analysis panel."""
    draw_face(frame, r, show_mesh)
    fh, w = frame.shape[:2]
    h = max(fh, MIN_H)
    canvas = np.zeros((h, w + PANEL_W, 3), dtype=np.uint8)
    canvas[:] = DARK
    canvas[:fh, :w] = frame
    p = canvas[:, w:]

    # ------------------------------------------------------------ header
    _text(p, "LIE DETECTOR", (14, 32), 0.8, WHITE, 2)
    _text(p, "on-device facial reading engine", (14, 52), 0.42, GREY)
    _text(p, f"{fps:4.1f} fps  |  infer {infer_ms:4.1f} ms", (14, 70), 0.42, GREY)

    # ------------------------------------------------------------- state
    y = 100
    state = an.state
    color = {State.NO_FACE: GREY, State.CALIBRATING: CYAN, State.READY: WHITE,
             State.QUESTION: AMBER}[state]
    _text(p, state.value, (14, y), 0.75, color, 2)
    if state == State.CALIBRATING:
        _bar(p, 14, y + 12, PANEL_W - 28, 12, an.calibration_progress(), CYAN)
        _text(p, "Look at the camera, answer a simple", (14, y + 42), 0.42, GREY)
        _text(p, "question truthfully (your name, the day)", (14, y + 58), 0.42, GREY)
    elif state == State.NO_FACE:
        _text(p, "No face detected. Face the camera.", (14, y + 24), 0.45, GREY)
    elif state == State.QUESTION and an.question_start is not None:
        _text(p, f"question {len(an.history) + 1}  |  {an.last_t - an.question_start:4.1f} s",
              (14, y + 24), 0.45, AMBER)
    else:
        _text(p, "Press SPACE to open a question", (14, y + 24), 0.45, GREY)

    # ------------------------------------------------------------- gauge
    y = 185
    ready = an.baseline.complete and state in (State.READY, State.QUESTION)
    score = an.score if ready else 0.0
    _text(p, "DECEPTION SCORE", (14, y), 0.5, WHITE)
    _text(p, f"{score:3.0f}", (PANEL_W - 70, y + 2), 0.9, _score_color(score), 2)
    _bar(p, 14, y + 10, PANEL_W - 28, 22, score / 100.0, _score_color(score))
    # thresholds
    for frac in (0.4, 0.6):
        x = 14 + int((PANEL_W - 28) * frac)
        cv2.line(p, (x, y + 10), (x, y + 32), WHITE, 1)
    if ready:
        lv = an.live_verdict()
        _text(p, lv.value, (14, y + 54), 0.6, VERDICT_COLOR[lv], 2)

    # -------------------------------------------------------- indicators
    y = 270
    _text(p, "CUES vs. your baseline", (14, y), 0.48, WHITE)
    for i, k in enumerate(INDICATORS):
        yy = y + 20 + i * 24
        v = an.indicators[k] if ready else 0.0
        _text(p, LABELS[k], (14, yy + 11), 0.42, GREY)
        _bar(p, 118, yy, PANEL_W - 132, 13, v, _score_color(v * 100))
    yy = y + 20 + len(INDICATORS) * 24
    if ready:
        _text(p, f"blinks/min {an.live_blink_rate:4.1f}  (base {an.baseline.blink_rate:4.1f})",
              (14, yy + 8), 0.4, GREY)
        if an.micro.last_label:
            _text(p, f"last micro-expr: {an.micro.last_label}", (14, yy + 24), 0.4, GREY)

    # ------------------------------------------------------------ history
    y = h - 118
    _text(p, "VERDICTS", (14, y), 0.48, WHITE)
    for i, q in enumerate(an.history[-4:]):
        _text(p, f"Q{q.index}: {q.verdict.value:12s} {q.mean_score:3.0f}",
              (14, y + 20 + i * 18), 0.45, VERDICT_COLOR[q.verdict])

    # --------------------------------------------------------------- keys
    _text(p, "SPACE question  C recalibrate  M mesh", (14, h - 26), 0.38, GREY)
    _text(p, f"S speech [{'on' if speak else 'off'}]  Q quit", (14, h - 10), 0.38, GREY)

    # ---------------------------------------------------- verdict banner
    if an.last_verdict is not None and an.last_t - an.last_verdict.end < 4.0:
        v = an.last_verdict
        label = {Verdict.TRUTHFUL: "TRUTH", Verdict.INCONCLUSIVE: "INCONCLUSIVE",
                 Verdict.DECEPTIVE: "LIE DETECTED"}[v.verdict]
        col = VERDICT_COLOR[v.verdict]
        overlay = canvas.copy()
        cv2.rectangle(overlay, (0, fh // 2 - 60), (w, fh // 2 + 60), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.55, canvas, 0.45, 0, canvas)
        (tw, _), _ = cv2.getTextSize(label, FONT, 1.6, 4)
        _text(canvas, label, ((w - tw) // 2, fh // 2 + 5), 1.6, col, 4)
        sub = f"score {v.mean_score:.0f}" + (f"  |  {', '.join(v.top_cues)}" if v.top_cues else "")
        (sw, _), _ = cv2.getTextSize(sub, FONT, 0.55, 1)
        _text(canvas, sub, ((w - sw) // 2, fh // 2 + 40), 0.55, WHITE, 1)
    return canvas
