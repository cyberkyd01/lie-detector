#!/usr/bin/env python3
"""Lie Detector: camera -> native face reading -> deception indicators.

Usage:
    python main.py                 # webcam 0
    python main.py --camera 1      # another camera
    python main.py --video clip.mp4
    python main.py --calib 15 --speak

Keys:  SPACE open/close a question   C recalibrate   M toggle mesh
       S toggle speech               Q / ESC quit
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time

import cv2

from liedetector.analysis import DeceptionAnalyzer, Verdict
from liedetector.face_engine import DEFAULT_MODEL_PATH, FaceEngine
from liedetector.ui import compose

SPOKEN = {Verdict.TRUTHFUL: "That appears to be the truth.",
          Verdict.INCONCLUSIVE: "Inconclusive.",
          Verdict.DECEPTIVE: "Lie detected."}
SOUND = {Verdict.TRUTHFUL: "/System/Library/Sounds/Glass.aiff",
         Verdict.INCONCLUSIVE: "/System/Library/Sounds/Tink.aiff",
         Verdict.DECEPTIVE: "/System/Library/Sounds/Basso.aiff"}


def announce(verdict: Verdict, speak: bool) -> None:
    """Non-blocking sound / speech feedback (macOS tools if present)."""
    try:
        if speak and shutil.which("say"):
            subprocess.Popen(["say", SPOKEN[verdict]])
        elif shutil.which("afplay") and os.path.exists(SOUND[verdict]):
            subprocess.Popen(["afplay", SOUND[verdict]])
    except OSError:
        pass


def open_capture(args) -> cv2.VideoCapture:
    if args.video:
        cap = cv2.VideoCapture(args.video)
    elif sys.platform == "darwin":
        cap = cv2.VideoCapture(args.camera, cv2.CAP_AVFOUNDATION)
    else:
        cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        raise SystemExit(
            "Could not open the camera. On macOS grant camera access to your terminal "
            "app in System Settings > Privacy & Security > Camera, then run again.")
    if not args.video:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    return cap


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", type=int, default=0, help="camera index (default 0)")
    ap.add_argument("--video", help="analyse a video file instead of the camera")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--calib", type=float, default=10.0, help="calibration seconds (default 10)")
    ap.add_argument("--model", default=DEFAULT_MODEL_PATH)
    ap.add_argument("--delegate", choices=["auto", "cpu", "gpu"], default="auto")
    ap.add_argument("--speak", action="store_true", help="speak verdicts aloud (macOS)")
    ap.add_argument("--no-mesh", action="store_true", help="hide the landmark mesh")
    ap.add_argument("--no-mirror", action="store_true", help="do not mirror the camera image")
    ap.add_argument("--headless", action="store_true", help="no window; print results (testing)")
    ap.add_argument("--auto-question", type=float, default=0.0,
                    help="headless/testing: open a question after calibration for N seconds")
    args = ap.parse_args()

    engine = FaceEngine(args.model, args.delegate)
    analyzer = DeceptionAnalyzer(calibration_seconds=args.calib)
    cap = open_capture(args)
    show_mesh = not args.no_mesh
    speak = args.speak
    win = "Lie Detector"
    if not args.headless:
        cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE)

    t_start = time.monotonic()
    frame_idx = 0
    fps, fps_t, fps_n = 0.0, time.monotonic(), 0
    video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    auto_q_opened = False
    print(f"engine: {engine.delegate} delegate | calibration {args.calib:.0f}s | "
          f"{'video ' + args.video if args.video else 'camera ' + str(args.camera)}")

    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                if args.video:
                    break
                continue
            if not args.video and not args.no_mirror:
                frame = cv2.flip(frame, 1)
            t = frame_idx / video_fps if args.video else time.monotonic() - t_start
            frame_idx += 1

            reading = engine.read(frame, t)
            analyzer.update(reading, t)

            if args.auto_question > 0 and analyzer.baseline.complete:
                if not auto_q_opened:
                    analyzer.toggle_question(); auto_q_opened = True
                    print(f"[{t:6.2f}s] question {len(analyzer.history) + 1} opened")
                elif analyzer.question_start is not None and t - analyzer.question_start >= args.auto_question \
                        and analyzer.state.name == "QUESTION":
                    res = analyzer.toggle_question()
                    if res:
                        print(f"[{t:6.2f}s] verdict: {res.verdict.value} score={res.mean_score:.1f} "
                              f"peak={res.peak_score:.1f} cues={res.top_cues}")
                    auto_q_opened = False

            fps_n += 1
            now = time.monotonic()
            if now - fps_t >= 0.5:
                fps, fps_t, fps_n = fps_n / (now - fps_t), now, 0

            if args.headless:
                if frame_idx % 30 == 0:
                    print(f"[{t:6.2f}s] {analyzer.state.value:12s} face={'yes' if reading else 'no '} "
                          f"score={analyzer.score:5.1f} infer={engine.last_inference_ms:5.1f}ms")
                continue

            canvas = compose(frame, reading, analyzer, fps, engine.last_inference_ms, show_mesh, speak)
            cv2.imshow(win, canvas)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            elif key == ord(" "):
                res = analyzer.toggle_question()
                if res is not None:
                    print(f"Q{res.index}: {res.verdict.value}  score {res.mean_score:.0f} "
                          f"(peak {res.peak_score:.0f})  cues: {', '.join(res.top_cues) or '-'}")
                    announce(res.verdict, speak)
            elif key == ord("c"):
                analyzer.recalibrate()
            elif key == ord("m"):
                show_mesh = not show_mesh
            elif key == ord("s"):
                speak = not speak
            try:
                if cv2.getWindowProperty(win, cv2.WND_PROP_VISIBLE) < 1:
                    break
            except cv2.error:
                break
    finally:
        cap.release()
        engine.close()
        if not args.headless:
            cv2.destroyAllWindows()

    if analyzer.history:
        print("\nSession summary")
        for q in analyzer.history:
            print(f"  Q{q.index}: {q.verdict.value:12s} score {q.mean_score:5.1f}  cues: {', '.join(q.top_cues) or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
