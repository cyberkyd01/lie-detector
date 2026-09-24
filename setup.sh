#!/usr/bin/env bash
# One-time setup: virtualenv, dependencies and the on-device face model.
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt
mkdir -p models
if [ ! -f models/face_landmarker.task ]; then
  echo "Downloading face landmarker model..."
  curl -sL -o models/face_landmarker.task \
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
fi
echo "Setup complete. Run:  ./run.sh"
