#!/usr/bin/env bash
# Serve the browser version locally. The live camera works at http://localhost:8765
cd "$(dirname "$0")/web"
PORT="${1:-8765}"
{ printf '<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
  printf '<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}img{max-width:100%%}[hidden]{display:none!important}</style></head><body>\n'
  cat app.html; printf '\n</body></html>\n'; } > index.html
echo "Lie Detector (browser) at  http://localhost:$PORT   (Ctrl+C to stop)"
exec python3 -m http.server "$PORT" --bind 127.0.0.1
