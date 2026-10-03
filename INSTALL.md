# Install Lie Detector (desktop app) on macOS, Windows or Linux

The desktop app (`main.py`) opens your webcam in a window, reads your face with an on-device MediaPipe engine and scores each answer for deception cues. It runs entirely on your computer and nothing is uploaded. It's for fun and demos only; see the honesty note in the [README](README.md).

To **host the browser version** on a server for other people, use **[DEPLOY.md](DEPLOY.md)** instead.

## At a glance

| | |
|---|---|
| What you install | The Python app from this repo, run from source. There's no installer, and the repo has no GitHub Releases. |
| Where | The folder you clone into (e.g. `~/lie-detector`). Dependencies go into `.venv` inside it (about 370 MB), and the face model goes to `models/face_landmarker.task` (3.7 MB). |
| Python | 3.10 or newer. Tested on macOS Apple Silicon with Python 3.14, MediaPipe 1.0.1 and OpenCV 5.0. |
| Dependencies | `opencv-python`, `mediapipe`, `numpy` (`requirements.txt`). pip adds what MediaPipe needs. |
| Admin rights | Only to install Python/Git if you don't have them. On some Linux systems you may also need them to join the `video` group. |
| Network | Only during install. The app itself works offline. |
| Never touches | System-wide Python packages (everything goes into `.venv`). It changes no system settings apart from the camera permission you grant. |

## Requirements

- A webcam, built-in or USB.
- One of:
  - **macOS** on Apple Silicon. macOS 13+ gets the current OpenCV build; older versions fall back to older builds (untested). **Intel Macs** are untested: the last MediaPipe release for them is 0.10.21, which needs Python 3.9–3.12.
  - **Windows 10/11 64-bit.** On Windows on ARM, use the **x64** Python, because OpenCV publishes no native ARM64 wheel.
  - **Linux** desktop, x86_64 or arm64, with glibc 2.28+ and Python 3.10+, e.g. Ubuntu 22.04+ or Debian 12+. You need a graphical session (X11, or Wayland with XWayland, which is the default on Ubuntu/Fedora).
- About 500 MB free disk.
- Access to the private repo `cyberkyd01/lie-detector`.

## Get the code

With the GitHub CLI (if you're already signed in with `gh auth login`):

```bash
gh repo clone cyberkyd01/lie-detector ~/lie-detector
```

Or with plain git. It asks you to sign in to GitHub because the repo is private:

```bash
git clone https://github.com/cyberkyd01/lie-detector.git ~/lie-detector
```

Or open the repo on GitHub, click **Code → Download ZIP** and unzip it to a folder called `lie-detector` in your home folder. If you use the ZIP and `./setup.sh` says `Permission denied`, run `bash setup.sh` and `bash run.sh` instead.

Windows commands are in the Windows section below.

## Install on macOS

1. **Check Python.** You need 3.10 or newer:

   ```bash
   python3 --version
   ```

   If it says `command not found` or a version below 3.10, install Python from <https://www.python.org/downloads/macos/> or with `brew install python`, then open a new Terminal window. Apple's built-in `python3` is 3.9 on many macOS versions, which is too old.

2. **Install** (virtualenv, dependencies, face model). This takes about a minute:

   ```bash
   cd ~/lie-detector
   ./setup.sh
   ```

   You should see `Setup complete. Run:  ./run.sh`.

3. **Run:**

   ```bash
   cd ~/lie-detector
   ./run.sh
   ```

   The first time, macOS asks whether your terminal app (Terminal, iTerm or VS Code) may use the camera. Click **OK**. If the window never appears or you see `Could not open the camera`, open **System Settings → Privacy & Security → Camera**, switch on your terminal app, **quit and reopen the terminal**, and run `./run.sh` again.

If `python3` points at an old Python but you have a newer one installed, create the virtualenv with it first; `setup.sh` reuses an existing `.venv`. For example, with Homebrew:

```bash
cd ~/lie-detector
rm -rf .venv
/opt/homebrew/bin/python3 -m venv .venv
./setup.sh
```

## Install on Linux (Ubuntu / Debian)

1. **System packages.** These are Python with venv support, plus the OpenGL and GLib libraries the OpenCV window needs. Desktop installs usually have the libraries already.

   ```bash
   sudo apt-get update
   sudo apt-get install -y --no-install-recommends python3 python3-venv curl git libgl1 libglib2.0-0
   python3 --version
   ```

   You need `Python 3.10` or newer: Ubuntu 22.04 ships 3.10, Debian 12 ships 3.11 and Ubuntu 24.04 ships 3.12. On other distributions, install the equivalent packages (Fedora: `sudo dnf install python3 git curl mesa-libGL`).

2. **Install and run:**

   ```bash
   cd ~/lie-detector
   ./setup.sh
   ./run.sh
   ```

3. **Camera access.** If you see `Could not open the camera`, check that a camera device exists and whether your user can open it:

   ```bash
   ls -l /dev/video*
   groups
   ```

   Desktop sessions usually grant camera access to the logged-in user automatically. If the device is `crw-rw---- root video` and `video` isn't in your groups, add yourself. This needs admin rights and changes group membership for your user only:

   ```bash
   sudo usermod -aG video "$USER"
   ```

   Then log out and back in. Also close other apps that might be using the camera (video calls). If you have several cameras, try `./run.sh --camera 1`.

## Install on Windows (PowerShell)

1. **Install Python and Git** if you don't have them. This uses the built-in `winget`. On Windows on ARM, add `--architecture x64` to the Python line.

   ```powershell
   winget install -e --id Python.Python.3.13
   winget install -e --id Git.Git
   ```

   If `winget` isn't available, install Python from <https://www.python.org/downloads/windows/> (keep **py launcher** selected) and Git from <https://git-scm.com/download/win>. **Close PowerShell and open a new window** so `py` and `git` are on the PATH. Check them:

   ```powershell
   py -3 --version
   git --version
   ```

2. **Get the code and install.** This does the same as `setup.sh`. There's no need to activate the virtualenv or change PowerShell's execution policy, because every command calls `.venv\Scripts\python.exe` directly.

   ```powershell
   cd $HOME
   git clone https://github.com/cyberkyd01/lie-detector.git
   cd lie-detector
   py -3 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install --upgrade pip
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   New-Item -ItemType Directory -Force models | Out-Null
   $ProgressPreference = 'SilentlyContinue'   # makes the download much faster in Windows PowerShell
   Invoke-WebRequest -Uri "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task" -OutFile models\face_landmarker.task
   (Get-Item models\face_landmarker.task).Length
   ```

   The last line should print `3758596`. `git clone` opens a GitHub sign-in window the first time, because the repo is private.

3. **Run:**

   ```powershell
   cd $HOME\lie-detector
   .\.venv\Scripts\python.exe main.py
   ```

   If you see `Could not open the camera`, open **Settings → Privacy & security → Camera** and switch on **Camera access**, **Let apps access your camera** and **Let desktop apps access your camera**. Close other apps that use the camera, then run it again.

## First run and configuration

1. **Calibrate (10 s).** Face the camera and answer easy, truthful questions. The engine learns your personal baseline.
2. **Press SPACE** to open a question, let the person answer, and press **SPACE** again to close it. A banner shows `TRUTH`, `INCONCLUSIVE` or `LIE DETECTED`.
3. Other keys: **C** recalibrates, **M** toggles the mesh, **S** toggles spoken verdicts and **Q**/**ESC** quits. Spoken verdicts and sounds use macOS's `say`/`afplay` and are silently skipped on Windows and Linux.

Options (add them after `./run.sh`, or after `main.py` on Windows):

| Option | Effect |
|---|---|
| `--camera 1` | use another camera (default `0`) |
| `--video clip.mp4` | analyse a video file instead of the camera |
| `--calib 15` | calibration seconds (default 10) |
| `--width 640 --height 480` | lower camera resolution (default 1280×720), which helps on slow machines |
| `--speak` | start with spoken verdicts on (macOS) |
| `--no-mesh`, `--no-mirror` | hide the face mesh, or don't mirror the image |
| `--delegate cpu\|gpu` | engine backend. The default `auto` uses GPU on macOS (its CPU path aborts there) and CPU elsewhere. |
| `--headless` | no window; print readings (testing) |

**Check the install without a camera.** This analyses the bundled sample clip and prints a session summary:

```bash
cd ~/lie-detector
./.venv/bin/python main.py --headless --video web/sample/synthetic_face.mp4 --calib 2 --auto-question 1
```

```powershell
cd $HOME\lie-detector
.\.venv\Scripts\python.exe main.py --headless --video web\sample\synthetic_face.mp4 --calib 2 --auto-question 1
```

You should see lines like `[ 10.97s] ANALYZING face=yes score= 16.3 ...` and a `Session summary` with `Q1: TRUTHFUL ...`.

**Browser version on your own computer (macOS/Linux):** run `./serve.sh` and open <http://localhost:8765>. The camera works there because `localhost` counts as a secure origin. Press Ctrl+C to stop.

The scoring weights and thresholds are constants (`WEIGHTS`, `GAIN`) in `liedetector/analysis.py`.

## Updating

macOS / Linux:

```bash
cd ~/lie-detector
git pull
./setup.sh
```

`setup.sh` reuses `.venv`, installs any new or changed dependencies and keeps the downloaded model.

Windows:

```powershell
cd $HOME\lie-detector
git pull
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If dependencies get into a bad state, delete `.venv` and install again.

## Uninstall

Everything the app installed is inside its folder: the code, `.venv` and the model. Delete the folder to remove it.

```bash
rm -rf ~/lie-detector
```

```powershell
Remove-Item -Recurse -Force $HOME\lie-detector
```

Optional clean-up:

- Turn the camera permission off again for your terminal app (macOS: System Settings → Privacy & Security → Camera).
- If you installed Python or Git only for this, uninstall them as usual (Windows: `winget uninstall -e --id Python.Python.3.13`).
- On Linux, if you joined the `video` group only for this, run `sudo gpasswd -d "$USER" video`.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `python3: command not found` or Python below 3.10 | Python missing or too old | Install Python 3.10+ (see your OS section). On macOS, create `.venv` with the newer Python as shown there. |
| `No matching distribution found for mediapipe` (or opencv) | No wheel for this Python/OS: Intel Mac with Python 3.13+, Windows ARM64 Python, 32-bit Python, or Linux with glibc < 2.28 | Intel Mac: use Python 3.12. Windows: install the 64-bit **x64** Python. Linux: use Ubuntu 22.04+ / Debian 12+. |
| `ImportError: libGL.so.1` (Linux) | OpenGL library missing | `sudo apt-get install -y libgl1 libglib2.0-0` |
| `Could not open the camera` | Camera permission off, camera in use by another app, or wrong index | Grant permission (see your OS section), close video-call apps, try `--camera 1` |
| macOS: no window, nothing happens | Terminal app has no camera permission | System Settings → Privacy & Security → Camera → enable it, then quit and reopen the terminal |
| macOS: crash or abort with `--delegate cpu` | MediaPipe's CPU path aborts on macOS | Leave `--delegate` at its default (GPU on macOS) |
| Windows/Linux: error with `--delegate gpu` | GPU delegate unavailable | Use the default (CPU) |
| `Face model not found at .../models/face_landmarker.task` | Setup didn't finish | Re-run `./setup.sh` (Windows: the `Invoke-WebRequest` line) |
| Engine error loading the model, or the model file is much smaller than 3,758,596 bytes | `setup.sh` downloads with `curl -sL`, which saves an HTTP error page without complaining | Delete `models/face_landmarker.task` and re-run setup. Offline alternative: the repo already contains the same file, so copy `web/models/face_landmarker.wasm` to `models/face_landmarker.task` (identical SHA-256). |
| `./setup.sh: Permission denied` | Executable bit lost (ZIP download) | `bash setup.sh` and `bash run.sh` |
| Low frame rate | Large camera frames on a slow CPU | `--width 640 --height 480` |
| Linux on Wayland: window doesn't open | XWayland missing (OpenCV's window uses X11) | Use a session with XWayland (the default on Ubuntu/Fedora GNOME), or an X11 session |
