#!/usr/bin/env python3
"""Build web/dist/index.html: the browser app with the face engine embedded.

Hosted pages (the Claude artifact) may not be able to fetch files published
next to the page, so this build inlines everything the engine needs:
  - the wasm runtime (gzip + base64, inflated in the browser)
  - the face model (gzip + base64)
  - the sample clip (base64 data URI)
The loader script itself is fetched from a CDN the host allows.
"""
import base64, gzip, os, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
MARK = "/*__EMBEDDED_ASSETS__*/"

def b64gz(path):
    with open(path, "rb") as f:
        return base64.b64encode(gzip.compress(f.read(), 9)).decode()

def b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

def main():
    src = open(os.path.join(WEB, "app.html"), encoding="utf-8").read()
    if MARK not in src:
        sys.exit("marker not found in web/app.html")
    blob = ("const EMBEDDED = {\n"
            f'  wasm_gz: "{b64gz(os.path.join(WEB, "wasm", "vision_wasm_internal.wasm"))}",\n'
            f'  model_gz: "{b64gz(os.path.join(WEB, "models", "face_landmarker.wasm"))}",\n'
            f'  sample: "data:video/mp4;base64,{b64(os.path.join(WEB, "sample", "synthetic_face.mp4"))}",\n'
            "};")
    out = src.replace(MARK, blob, 1)
    os.makedirs(os.path.join(WEB, "dist"), exist_ok=True)
    dst = os.path.join(WEB, "dist", "index.html")
    open(dst, "w", encoding="utf-8").write(out)
    print(f"wrote {dst}: {os.path.getsize(dst)/1e6:.2f} MB")

if __name__ == "__main__":
    main()
