# Lie Detector

A webcam "lie detector" that runs entirely on your machine. It opens the
camera, reads the face with an on-device landmark + expression engine, and
scores each answer for classic deception "leakage" cues.

> **Honesty note.** No facial-expression method can reliably tell truth from
> lies, and this tool is no exception. It measures *stress and discomfort
> relative to your own baseline*. Use it for fun, demos and experiments, never
> as evidence about a real person.

## Quick start

```bash
./setup.sh        # venv + dependencies + downloads the 3.7 MB face model
./run.sh          # opens the camera
```

macOS asks for camera permission the first time. If the window never appears,
grant your terminal app access in **System Settings > Privacy & Security > Camera**.

## Browser version

The same engine and scoring also run in the browser, on-device via WebAssembly:

```bash
./serve.sh        # then open http://localhost:8765 for a live camera
```

Append `#sample` to the URL to auto-play the bundled sample clip. The page in
`web/app.html` is also published as a Claude artifact; hosted pages cannot use
the camera, so there it analyzes uploaded clips or the sample.

## How to use it

1. **Calibrate (10 s).** Face the camera and answer easy, truthful questions
   ("What's your name?", "What day is it?"). The engine learns your personal
   baseline for every cue.
2. **Press SPACE** to open a question, ask it, let the subject answer, and press
   **SPACE** again to close it. A banner shows `TRUTH`, `INCONCLUSIVE` or
   `LIE DETECTED` with the score and the strongest cues.
3. Repeat. The right-hand panel shows the live 0-100 score, each cue against
   baseline, blink rate and the last micro-expression.

| Key | Action |
|-----|--------|
| SPACE | open / close a question |
| C | recalibrate baseline |
| M | toggle the landmark mesh |
| S | toggle spoken verdicts (macOS `say`) |
| Q / ESC | quit |

Options: `--camera 1`, `--video clip.mp4`, `--calib 15`, `--speak`, `--no-mesh`,
`--delegate cpu|gpu`, `--headless` (print-only, for testing).

## Architecture

```
main.py                     camera loop, keys, verdict announcements
liedetector/face_engine.py  native facial reading: 478 landmarks, 52 blendshapes,
                            head pose, eye-aspect-ratio, mouth geometry (MediaPipe
                            FaceLandmarker, on-device, no network)
liedetector/analysis.py     deception engine: baseline calibration, blink and
                            micro-expression detectors, z-scored cues, score, verdicts
liedetector/ui.py           overlay panel and banners (OpenCV drawing)
```

### Cues and weights

| Cue | What it measures | Weight |
|-----|------------------|--------|
| Blink rate | blinks/min far above **or** below baseline | 0.15 |
| Gaze aversion | eyes looking away from the camera | 0.15 |
| Lip press | lips compressed or rolled inward | 0.15 |
| Micro-expressions | 40-500 ms expression flashes that get masked | 0.15 |
| Brow tension | brows down or inner brows raised | 0.10 |
| Head motion | fidgeting: angular + positional head velocity | 0.10 |
| Fake smile | mouth smiles without the eyes, or asymmetric | 0.10 |
| Eye arousal | eyes widened or squinted | 0.10 |

Each cue becomes a z-score against the calibration baseline over a sliding
window (3 s for continuous cues, 10 s for blink/micro-expression counts), is
clipped at z = 2.5, weighted, summed and scaled to 0-100. A question's verdict
is the mean score while it was open: < 40 truthful, 40-60 inconclusive,
>= 60 deceptive. Tune `WEIGHTS`, `GAIN` and the thresholds in
`liedetector/analysis.py`.

## Requirements

Python 3.10+, a webcam. Tested on macOS (Apple Silicon) with Python 3.13,
OpenCV 5 and MediaPipe 1.0. On macOS the engine uses the GPU (Metal) delegate
because MediaPipe's CPU graph aborts there; on Linux/Windows it defaults to CPU.
