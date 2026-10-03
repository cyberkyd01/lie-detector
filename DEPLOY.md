# Deploy Lie Detector (browser version) on a VPS (over SSH)

This guide puts the **browser version** of Lie Detector on your server at `https://your-domain`. The page and its WebAssembly face engine are static files served by nginx. All face reading happens **inside each visitor's browser**, so no video ever reaches the server. Updates are one command and roll back automatically. Everything it creates is kept separate from other sites on the same server.

The Python desktop app (`main.py` / `run.sh`) is a different thing: it needs a local webcam and a screen. It's covered in **[INSTALL.md](INSTALL.md)**.

> Target: **Ubuntu 22.04 / 24.04 LTS or Debian 12**, x86_64 or arm64. You log in as a sudo user or as root. Every block uses `sudo`, which also works as root. On minimal Debian logged in as root, run `apt-get install -y sudo` first.

> **HTTPS is required for the live camera.** Browsers only allow camera access (`getUserMedia`) on secure origins: `https://` pages or `http://localhost`. On a plain `http://<server-ip>:8113` page (no-domain mode) the **Start camera** button shows "Camera unavailable here". Uploaded video files and the sample clip still work. To use the camera without a domain, use the SSH tunnel in step 11.

## At a glance

| | |
|---|---|
| Install directory | `/opt/lie-detector`. Releases are in `releases/`, and nginx serves `/opt/lie-detector/current/web`. |
| Config | `/etc/lie-detector/deploy.conf` |
| System user | `lie-detector` (owns the files, no login shell). nginx only reads them. |
| Service | **No app process.** nginx serves static files, and a deploy just switches a symlink and reloads nginx. |
| Ports | `127.0.0.1:3113`: a loopback-only nginx listener for health checks and SSH-tunnel access. `127.0.0.1:3213`: used for about 3 s during each build by `serve.sh`'s preview server. Public: 80/443, or `8113` in no-domain mode. |
| Public URL | `https://<DOMAIN>` (camera works), or `http://<server-ip>:8113` (no camera) |
| Runtime | **None on the server.** No Node and no Python packages. The engine (MediaPipe FaceLandmarker, WebAssembly) runs in the browser. |
| Database | None |
| Never touches | the system Python/Node, other nginx sites, `nginx.conf`, the `default` site, firewall policy, other services |

## Requirements

- A VPS running Ubuntu 22.04/24.04 or Debian 12 with **1 vCPU, 256 MB RAM (512 MB comfortable) and 500 MB free disk**. Each release is about 27 MB and three are kept.
- **A domain or subdomain** (e.g. `lie.example.com`) with a DNS **A record** pointing at the server's public IPv4, plus an email for Let's Encrypt. This is optional, but without it the hosted page can't use the camera.
- Access to `https://github.com/cyberkyd01/lie-detector` so you can add a deploy key, because the repo is private.
- Visitors need a current Chrome, Edge, Firefox or Safari with WebAssembly and a camera. Each visitor downloads about 16 MB the first time (about 7.5 MB with gzip). The page loads its fonts from Google Fonts.

## 1. Connect and set your variables

From your computer:

```bash
ssh your-user@your-server-ip
```

On the server, edit the values if needed (usually only `DOMAIN` and `EMAIL`), then paste the block. **This file is the only place you edit settings.** Every later block reads it.

```bash
sudo mkdir -p /etc/lie-detector
sudo tee /etc/lie-detector/deploy.conf >/dev/null <<'EOF'
# lie-detector deployment settings - the ONLY place you edit values.
APP=lie-detector
APP_USER=lie-detector
APP_HOME=/opt/lie-detector
REPO_URL=git@github.com:cyberkyd01/lie-detector.git
BRANCH=main
PORT=3113           # loopback-only nginx listener (health checks, SSH tunnel). No app process.
PREVIEW_PORT=3213   # serve.sh's throwaway preview server during each build (127.0.0.1, ~3 s)
PUBLIC_PORT=8113    # used only when DOMAIN is empty (no-domain mode: NO camera)
DOMAIN=             # e.g. lie.example.com - strongly recommended (camera needs HTTPS)
EMAIL=              # for Let's Encrypt notices - required when DOMAIN is set
EOF
sudo chmod 644 /etc/lie-detector/deploy.conf
cat /etc/lie-detector/deploy.conf
```

To change a value later, run `sudo nano /etc/lie-detector/deploy.conf` and re-run step 9. If the repo were public you could use `REPO_URL=https://github.com/cyberkyd01/lie-detector.git` and skip step 6.

## 2. Preflight checks

This block changes nothing. It prints `OK`, `WARN` or `STOP` lines.

```bash
sudo bash -uo pipefail <<'EOF'
source /etc/lie-detector/deploy.conf
FAIL=0
ok()   { echo "OK    $*"; }
warn() { echo "WARN  $*"; }
stop() { echo "STOP  $*"; FAIL=1; }
ours() { [ -e "/etc/nginx/sites-enabled/$APP" ] || [ -e "/etc/nginx/conf.d/$APP.conf" ]; }
. /etc/os-release
case "$ID:$VERSION_ID" in
  ubuntu:22.04|ubuntu:24.04|debian:12) ok "OS: $PRETTY_NAME" ;;
  *) warn "OS: $PRETTY_NAME is not Ubuntu 22.04/24.04 or Debian 12 (may still work)" ;;
esac
case "$(uname -m)" in
  x86_64|aarch64) ok "CPU: $(uname -m)" ;;
  *) warn "CPU $(uname -m) is untested (static files only, so it will probably work)" ;;
esac
MEM_MB=$(awk '/^MemTotal:/ {print int($2/1024)}' /proc/meminfo)
if [ "$MEM_MB" -ge 230 ]; then ok "RAM: ${MEM_MB} MB"; else stop "RAM: ${MEM_MB} MB (need 256 MB)"; fi
DISK_MB=$(df -Pm /opt | awk 'NR==2 {print $4}')
if [ "$DISK_MB" -ge 500 ]; then ok "Free disk on /opt: ${DISK_MB} MB"; else stop "Free disk on /opt: ${DISK_MB} MB (need 500 MB)"; fi
if ss -ltnH "sport = :$PORT" | grep -q .; then
  if ours && ss -ltnpH "sport = :$PORT" | grep -q nginx; then ok "Port $PORT is used by this site's nginx listener (already installed)"
  else stop "Port $PORT is in use: $(ss -ltnpH "sport = :$PORT" | head -1). Set PORT= in /etc/$APP/deploy.conf to a free port."; fi
else ok "Port $PORT is free"; fi
if ss -ltnH "sport = :$PREVIEW_PORT" | grep -q .; then warn "Port $PREVIEW_PORT is in use - harmless (only serve.sh's preview server would fail), but you can set PREVIEW_PORT= to a free port"
else ok "Port $PREVIEW_PORT is free"; fi
if [ -z "$DOMAIN" ]; then
  if ss -ltnH "sport = :$PUBLIC_PORT" | grep -q . && ! ours; then
    stop "Public port $PUBLIC_PORT is in use. Set PUBLIC_PORT= in /etc/$APP/deploy.conf to a free port."
  else warn "No-domain mode: http://<server-ip>:$PUBLIC_PORT - the live camera will NOT work there (needs HTTPS)"; fi
fi
WEB=$(ss -ltnpH '( sport = :80 or sport = :443 )' | grep -oE 'users:\(\("[^"]+"' | cut -d'"' -f2 | sort -u | tr '\n' ' ' || true)
case "$WEB" in
  "")       ok "Ports 80/443: free (nginx will be installed)" ;;
  "nginx ") ok "Ports 80/443: nginx (this guide only adds its own site)" ;;
  *)        stop "Ports 80/443 are used by: $WEB- skip nginx and use the Appendix (Caddy/Apache) in step 9" ;;
esac
if [ -n "$DOMAIN" ]; then
  if command -v curl >/dev/null; then
    SERVER_IP=$(curl -4fsS --max-time 10 https://api.ipify.org || true)
    DNS_IPS=$(getent ahostsv4 "$DOMAIN" | awk '{print $1}' | sort -u | tr '\n' ' ')
    if [ -n "$SERVER_IP" ] && echo " $DNS_IPS" | grep -q " $SERVER_IP "; then ok "DNS: $DOMAIN -> $SERVER_IP"
    else stop "DNS: $DOMAIN resolves to '${DNS_IPS:-nothing}' but this server is '${SERVER_IP:-unknown}'. Fix the A record and wait."; fi
  else warn "curl is not installed yet - re-run this block after step 3 for the DNS check"; fi
fi
[ "$FAIL" -eq 0 ] && echo "==> All checks passed." || echo "==> Fix the STOP lines above, then re-run this block."
EOF
```

You should see `OK` lines ending with `==> All checks passed.` In no-domain mode there's also one `WARN` about the camera.

## 3. Install system packages

This only adds packages. nginx is installed only when ports 80/443 are free or already belong to nginx.

```bash
sudo bash -euo pipefail <<'EOF'
source /etc/lie-detector/deploy.conf
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl git openssh-client iproute2
WEB=$(ss -ltnpH '( sport = :80 or sport = :443 )' | grep -oE 'users:\(\("[^"]+"' | cut -d'"' -f2 | sort -u | tr '\n' ' ' || true)
if [ -z "$WEB" ] || [ "$WEB" = "nginx " ]; then
  if ! command -v nginx >/dev/null; then apt-get install -y --no-install-recommends nginx; fi
  nginx -v
else
  echo "Ports 80/443 belong to: $WEB- nginx NOT installed. Use the Appendix in step 9."
fi
EOF
```

You should see `nginx version: nginx/1.x` at the end.

## 4. Create the app user and folders

nginx must be able to read the files, so the folders are world-readable (`755`). Only the SSH key folder is private.

```bash
sudo bash -euo pipefail <<'EOF'
source /etc/lie-detector/deploy.conf
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --user-group --home-dir "$APP_HOME" --shell /usr/sbin/nologin "$APP_USER"
install -d -m 755 -o "$APP_USER" -g "$APP_USER" "$APP_HOME" "$APP_HOME/releases"
install -d -m 700 -o "$APP_USER" -g "$APP_USER" "$APP_HOME/.ssh"
install -d -m 755 -o root -g root "$APP_HOME/bin" "/etc/$APP"
id "$APP_USER"
ls -la "$APP_HOME"
EOF
```

You should see `uid=...(lie-detector)` and the folders `bin releases .ssh`.

## 5. Install the runtime

**There's nothing to install.** What the server hosts is `web/`: one HTML page plus the MediaPipe WebAssembly runtime (`web/wasm/`), the face model (`web/models/face_landmarker.wasm`), the JS bundle and a sample clip. All of these are committed to the repo. The "build" just wraps `web/app.html` into a full `web/index.html` page, which is what `./serve.sh` does with a few `printf` lines. The repo's Python dependencies (OpenCV, MediaPipe, NumPy in `requirements.txt`) belong to the desktop app and are **not** needed here.

`build_web.py` is also not used. It makes a single 9 MB `web/dist/index.html` with everything base64-embedded, which is meant for hosts that can't serve the side files. nginx serves them directly, which is smaller, cacheable and doesn't depend on a CDN.

## 6. Give the server read access to the repo

```bash
sudo bash -euo pipefail <<'EOF'
source /etc/lie-detector/deploy.conf
KEY="$APP_HOME/.ssh/id_ed25519"
[ -f "$KEY" ] || sudo -u "$APP_USER" ssh-keygen -q -t ed25519 -N "" -C "$APP@$(hostname)" -f "$KEY"
touch "$APP_HOME/.ssh/known_hosts"
ssh-keygen -F github.com -f "$APP_HOME/.ssh/known_hosts" >/dev/null || ssh-keyscan -t ed25519 github.com 2>/dev/null >> "$APP_HOME/.ssh/known_hosts"
chown -R "$APP_USER:$APP_USER" "$APP_HOME/.ssh"
chmod 700 "$APP_HOME/.ssh"; chmod 600 "$APP_HOME/.ssh/known_hosts"
echo; echo "=== Copy the whole next line into GitHub ==="
cat "$KEY.pub"
EOF
```

1. Open **https://github.com/cyberkyd01/lie-detector/settings/keys/new**.
2. Title: `lie-detector server`. Key: paste the `ssh-ed25519 ...` line. **Leave "Allow write access" unticked.**
3. Click **Add key**, then test it:

```bash
sudo -u lie-detector -H ssh -T git@github.com
```

You should see `Hi cyberkyd01/lie-detector! You've successfully authenticated, but GitHub does not provide shell access.`

## 7. Configure the app

**Nothing to configure.** The page reads no environment variables and has no secrets or server-side settings. Scoring weights and thresholds are code constants (`WEIGHTS`, `GAIN` in `liedetector/analysis.py` for the desktop app, and the matching block in `web/app.html` for the browser). To change them, commit the change and run `deploy.sh`.

What a visitor's browser loads:

| From | What |
|---|---|
| your server | `index.html`, `vision_bundle.mjs`, `wasm/*.js` + `wasm/*.wasm` (SIMD and no-SIMD builds), `models/face_landmarker.wasm`, `sample/synthetic_face.mp4` |
| fonts.googleapis.com | the page fonts |
| cdn.jsdelivr.net | **only as a fallback** if a local engine file fails to load, which happens when the MIME types are wrong. Step 12 checks them. |

## 8. Database

None. Camera frames and video files are analysed in the browser and never uploaded. The server only keeps normal nginx access logs (IP, time, path) in `/var/log/nginx/lie-detector.access.log`.

## 9. Serve it with nginx (+ HTTPS)

There's no app service, so nginx takes that role. This block adds **only** this app's site, with two parts:

- a **loopback listener** on `127.0.0.1:3113`. `deploy.sh` health-checks through it, and you can open it through an SSH tunnel with the camera working (step 11).
- the **public site**: `DOMAIN` on port 80, or port 8113 when `DOMAIN` is empty.

Both serve `/opt/lie-detector/current/web`. They set the MIME types browsers require for ES modules (`.mjs` → `text/javascript`) and WebAssembly (`.wasm` → `application/wasm`), which older nginx `mime.types` files lack. They also gzip the engine and send `Cache-Control: no-cache`, so each deploy shows up straight away. Repeat visits get a quick `304`, and after a deploy the files are downloaded once more.

The block opens the port in ufw if ufw is active. In domain mode it checks DNS and gets a Let's Encrypt certificate, with HTTP redirected to HTTPS. You can re-run it at any time: it rewrites the site and then re-applies the existing certificate. Until the first deploy (step 10) the site answers `404`, which is expected.

```bash
sudo bash -euo pipefail <<'EOF'
source /etc/lie-detector/deploy.conf
command -v nginx >/dev/null || { echo "STOP: nginx is not installed (another web server owns 80/443?). Use the Appendix."; exit 1; }
if [ -n "$DOMAIN" ] && [ -z "$EMAIL" ]; then echo "STOP: set EMAIL= in /etc/$APP/deploy.conf (needed for Let's Encrypt)."; exit 1; fi

STATIC="$(cat <<BLOCK
    root $APP_HOME/current/web;
    index index.html;

    gzip on;
    gzip_types text/css text/javascript application/javascript application/wasm application/json image/svg+xml;
    gzip_min_length 1024;

    # Revalidate on every visit so a new deploy shows at once (repeat visits get a quick 304).
    add_header Cache-Control "no-cache" always;
    add_header X-Content-Type-Options "nosniff" always;

    location / {
        try_files \$uri \$uri/ =404;
    }
    # Browsers only run ES modules / stream-compile WebAssembly with these exact types.
    location ~* \.mjs\$ {
        types { text/javascript mjs; }
    }
    location ~* \.wasm\$ {
        types { application/wasm wasm; }
    }
BLOCK
)"
if [ -n "$DOMAIN" ]; then LPORT=80; NAME_LINE="server_name $DOMAIN;"; else LPORT="$PUBLIC_PORT"; NAME_LINE="# no domain: http://<server-ip>:$PUBLIC_PORT (camera blocked: not HTTPS)"; fi
LISTEN_LINES="listen $LPORT;"
if [ -f /proc/net/if_inet6 ]; then LISTEN_LINES="$LISTEN_LINES
    listen [::]:$LPORT;"; fi
if [ -d /etc/nginx/sites-available ]; then CONF="/etc/nginx/sites-available/$APP"; LINK="/etc/nginx/sites-enabled/$APP"
else CONF="/etc/nginx/conf.d/$APP.conf"; LINK=""; fi
cat > "$CONF" <<NGINX
# $APP - written by DEPLOY.md step 9. Re-run that step instead of editing by hand.

# Loopback only: deploy.sh health checks + SSH-tunnel access (http://localhost counts as secure for the camera).
server {
    listen 127.0.0.1:$PORT;
    access_log off;
    error_log /var/log/nginx/$APP.error.log;
$STATIC
}

# Public site.
server {
    $LISTEN_LINES
    $NAME_LINE

    access_log /var/log/nginx/$APP.access.log;
    error_log  /var/log/nginx/$APP.error.log;
$STATIC
}
NGINX
if [ -n "$LINK" ]; then ln -sfn "$CONF" "$LINK"; fi
nginx -t
systemctl reload nginx || systemctl start nginx

# --- firewall: only if ufw is already active (never enables it) ---
if command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then
  if [ -n "$DOMAIN" ]; then ufw allow 80/tcp; ufw allow 443/tcp; else ufw allow "$PUBLIC_PORT/tcp"; fi
fi

# --- HTTPS (domain mode) ---
if [ -n "$DOMAIN" ]; then
  SERVER_IP="$(curl -4fsS --max-time 10 https://api.ipify.org || true)"
  if ! getent ahostsv4 "$DOMAIN" | awk '{print $1}' | grep -qx "${SERVER_IP:-none}"; then
    echo "STOP: $DOMAIN does not resolve to this server (${SERVER_IP:-unknown}) yet. Fix the DNS A record, wait, then re-run this block."
    exit 1
  fi
  if ! command -v certbot >/dev/null || { ! dpkg -s python3-certbot-nginx >/dev/null 2>&1 && [ ! -e /snap/bin/certbot ]; }; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends certbot python3-certbot-nginx
  fi
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect --keep-until-expiring
  echo "==> Site: https://$DOMAIN"
else
  echo "==> Site: http://$(curl -4fsS --max-time 10 https://api.ipify.org || echo '<server-ip>'):$PUBLIC_PORT  (no camera without HTTPS - see step 11)"
fi
EOF
```

You should see `syntax is ok`, `test is successful`, and in domain mode `Successfully deployed certificate`. If your VPS provider has its own firewall (security groups), allow TCP 80+443 (domain) or 8113 (no-domain) there as well.

## 10. Create the deploy script and deploy

`/opt/lie-detector/bin/deploy.sh` handles the first install and every update in the same way:

1. It fetches the branch and unpacks it into a new release folder.
2. It runs the repo's own `serve.sh` to build `web/index.html`. `serve.sh` writes that file first and then starts a local preview server, which the script stops after 3 seconds.
3. It checks that the engine files are present.
4. It switches `current`, runs `nginx -t` and reloads nginx.
5. It health-checks through nginx: the page must be the new release, and `.wasm`/`.mjs` must have the right MIME types. If the check fails it **rolls back automatically**.

It keeps the last 3 releases.

```bash
sudo bash -euo pipefail <<'EOF'
source /etc/lie-detector/deploy.conf
cat > "$APP_HOME/bin/deploy.sh" <<'DEPLOY'
#!/usr/bin/env bash
# Deploys the latest commit of $BRANCH. First install and every update. Safe to re-run.
set -Eeuo pipefail
source /etc/lie-detector/deploy.conf
[ "$(id -u)" -eq 0 ] || { echo "Run with: sudo $0"; exit 1; }

RELEASES="$APP_HOME/releases"; REPO="$APP_HOME/repo"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
as_app() { sudo -u "$APP_USER" -H env HOME="$APP_HOME" PATH="$PATH" bash -euo pipefail -c "$1"; }
BASE="http://127.0.0.1:$PORT"                 # loopback nginx listener from step 9
ENGINE_FILES=(index.html vision_bundle.mjs wasm/vision_wasm_internal.js wasm/vision_wasm_internal.wasm
              wasm/vision_wasm_nosimd_internal.js wasm/vision_wasm_nosimd_internal.wasm models/face_landmarker.wasm)

echo "==> Fetching $BRANCH"
if [ ! -d "$REPO/.git" ]; then as_app "git clone --quiet '$REPO_URL' '$REPO'"; fi
as_app "cd '$REPO' && git fetch --quiet --prune origin '$BRANCH' && git reset --quiet --hard 'origin/$BRANCH'"
SHA="$(as_app "cd '$REPO' && git rev-parse --short HEAD")"
NEW="$RELEASES/$(date +%Y%m%d%H%M%S)-$SHA"
as_app "mkdir -p '$NEW' && cd '$REPO' && git archive HEAD | tar -x -C '$NEW'"

echo "==> Building web/index.html with serve.sh"
# serve.sh writes web/index.html, then starts a preview server on 127.0.0.1:PREVIEW_PORT;
# timeout stops that server after 3 s. Its exit code is irrelevant - the files are checked below.
as_app "cd '$NEW' && timeout 3 bash ./serve.sh '$PREVIEW_PORT' >/dev/null 2>&1 || true"
for f in "${ENGINE_FILES[@]}"; do
  [ -s "$NEW/web/$f" ] || { echo "!! web/$f is missing - not switching" >&2; rm -rf "$NEW"; exit 1; }
done
grep -qi '^<!doctype html>' "$NEW/web/index.html" || { echo "!! web/index.html was not built - not switching" >&2; rm -rf "$NEW"; exit 1; }
chmod -R a+rX "$NEW"

echo "==> Switching to $NEW"
PREV="$(readlink -f "$APP_HOME/current" 2>/dev/null || true)"
ln -sfn "$NEW" "$APP_HOME/current.new" && mv -Tf "$APP_HOME/current.new" "$APP_HOME/current"

if ! command -v nginx >/dev/null || { [ ! -e "/etc/nginx/sites-enabled/$APP" ] && [ ! -e "/etc/nginx/conf.d/$APP.conf" ]; }; then
  echo "==> Deployed $SHA (no nginx site for $APP found - skipped the web check; see step 9 or the Appendix)"
  ls -1dt "$RELEASES"/*/ | tail -n +4 | xargs -r rm -rf
  exit 0
fi
if nginx -t 2>/dev/null; then systemctl reload nginx; else echo "!! nginx -t failed (another site?) - not reloading" >&2; fi

web_ok() {   # the new page is served, and the engine files have the MIME types browsers require
  local body wasm mjs
  body="$(curl -fs --max-time 5 "$BASE/")" || return 1
  [ "$body" = "$(cat "$NEW/web/index.html")" ] || return 1
  wasm="$(curl -fsI --max-time 5 -o /dev/null -w '%{content_type}' "$BASE/models/face_landmarker.wasm")" || return 1
  mjs="$(curl -fsI --max-time 5 -o /dev/null -w '%{content_type}' "$BASE/vision_bundle.mjs")" || return 1
  [[ "$wasm" == application/wasm* && "$mjs" == text/javascript* ]]
}
echo "==> Health check through nginx ($BASE)"
for i in $(seq 1 10); do
  if web_ok; then
    echo "==> Deployed $SHA"
    ls -1dt "$RELEASES"/*/ | tail -n +4 | xargs -r rm -rf   # keep last 3
    exit 0
  fi
  sleep 2
done
echo "!! Health check failed - rolling back" >&2
tail -n 20 "/var/log/nginx/$APP.error.log" >&2 2>/dev/null || true
if [ -n "$PREV" ] && [ -d "$PREV" ]; then
  ln -sfn "$PREV" "$APP_HOME/current.new" && mv -Tf "$APP_HOME/current.new" "$APP_HOME/current"
  systemctl reload nginx || true
  echo "!! Rolled back to $PREV" >&2
fi
exit 1
DEPLOY
chown root:root "$APP_HOME/bin/deploy.sh"
chmod 750 "$APP_HOME/bin/deploy.sh"
echo "Wrote $APP_HOME/bin/deploy.sh"
EOF
```

Deploy for the first time:

```bash
sudo /opt/lie-detector/bin/deploy.sh
```

You should see `==> Deployed <commit>` within about 20 seconds.

## 11. Camera access: HTTPS, no-domain mode and the SSH tunnel

| How you open it | Camera | Video file / sample clip |
|---|---|---|
| `https://<DOMAIN>` (step 9 with a domain) | **works**: the browser asks for permission | works |
| `http://<server-ip>:8113` (no-domain mode) | **blocked by the browser**: shows "Camera unavailable here" | works |
| `http://localhost:3113` through an SSH tunnel | **works**, because `localhost` counts as secure | works |

The SSH tunnel works in both modes. Run it **on your computer** and leave it running while you use the page:

```bash
ssh -N -L 3113:127.0.0.1:3113 your-user@your-server-ip
```

Then open `http://localhost:3113` in your browser on that computer. If you changed `PORT`, use that number in both places.

The camera is also blocked when the page is embedded in an `<iframe>` from another site, unless that iframe has `allow="camera"`.

## 12. Verify

On the server, check through the loopback listener:

```bash
source /etc/lie-detector/deploy.conf
curl -fsS "http://127.0.0.1:$PORT/" | grep -o '<title>Lie Detector</title>'
for f in vision_bundle.mjs wasm/vision_wasm_internal.js wasm/vision_wasm_internal.wasm models/face_landmarker.wasm sample/synthetic_face.mp4; do
  curl -fsSI -o /dev/null -w "%{http_code}  %{content_type}  $f\n" "http://127.0.0.1:$PORT/$f"
done
```

You should see `<title>Lie Detector</title>`, then `200` for every file. The types should be `text/javascript` for `.mjs`, `application/javascript` or `text/javascript` for `.js`, `application/wasm` for both `.wasm` files and `video/mp4` for the clip.

Check the public site:

```bash
source /etc/lie-detector/deploy.conf
if [ -n "$DOMAIN" ]; then
  curl -sSI "https://$DOMAIN/" | head -1
  curl -sSI "http://$DOMAIN/" | grep -i '^location'
else
  curl -sSI "http://$(curl -4fsS https://api.ipify.org):$PUBLIC_PORT/" | head -1
fi
```

You should see `HTTP/1.1 200 OK`, and in domain mode a `Location: https://...` redirect for plain HTTP.

In a browser:

1. Open `https://<DOMAIN>`, or the SSH-tunnel URL. The small status line at the bottom of the right-hand panel should say **`engine ready (local files, GPU)`** or `(local files, CPU)`. If it says `CDN ...`, the local files didn't load (see Troubleshooting).
2. Click **Try the sample clip**, or open `https://<DOMAIN>/#sample`, to watch it analyse the bundled clip.
3. Click **Start camera** and allow access. Face the camera for the 10 s calibration, then use **Open a question** (or the SPACE key) as described in the README.

## Updating

```bash
sudo /opt/lie-detector/bin/deploy.sh
```

Visitors get the new version on their next page load.

## Rolling back

`deploy.sh` rolls back on its own when the health check fails. To go back manually to the most recent other release:

```bash
sudo bash -euo pipefail <<'EOF'
source /etc/lie-detector/deploy.conf
cd "$APP_HOME/releases"
ls -1dt */
CUR="$(basename "$(readlink -f "$APP_HOME/current")")"
PREV="$(ls -1dt */ | sed 's#/$##' | grep -vx "$CUR" | head -1 || true)"
[ -n "$PREV" ] || { echo "No other release to roll back to."; exit 1; }
ln -sfn "$APP_HOME/releases/$PREV" "$APP_HOME/current.new" && mv -Tf "$APP_HOME/current.new" "$APP_HOME/current"
nginx -t && systemctl reload nginx
echo "Now serving $PREV"
EOF
```

## Logs, status, restart

There's no app process and no app log. Engine errors happen in the visitor's browser: open the developer console, and the page also shows the error text. On the server:

```bash
sudo tail -f /var/log/nginx/lie-detector.access.log /var/log/nginx/lie-detector.error.log
readlink -f /opt/lie-detector/current
sudo systemctl status nginx --no-pager
sudo nginx -t && sudo systemctl reload nginx
```

## Backups and restore

Nothing on the server needs backing up. The site is rebuilt from git by `deploy.sh`, and there's no user data. To rebuild on a new server, copy `/etc/lie-detector/deploy.conf` (or retype it in step 1) and follow the guide again. The nginx site is regenerated by step 9, and certbot issues a fresh certificate.

```bash
sudo cp /etc/lie-detector/deploy.conf ~/lie-detector-deploy.conf
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "Camera unavailable here" | The page isn't on HTTPS or localhost, camera permission was denied, or it's inside an iframe without `allow="camera"` | Use `https://DOMAIN` or the SSH tunnel (step 11). Reset the site's camera permission in the browser (padlock icon). On macOS, also allow the browser in System Settings → Privacy & Security → Camera. |
| "Engine failed to load" with `file fetch probes: ... 404` | First deploy not done, or `current` missing | `sudo /opt/lie-detector/bin/deploy.sh` and check `readlink -f /opt/lie-detector/current` |
| Status shows `CDN ...` instead of `local files` | Wrong MIME types for `.mjs`/`.wasm` (site file edited or replaced) | Re-run step 9, then check the types with step 12 |
| `403 Forbidden` | nginx can't read `/opt/lie-detector` | `sudo chmod 755 /opt/lie-detector /opt/lie-detector/releases`, then re-run `deploy.sh` (it runs `chmod -R a+rX` on each release) |
| Preflight: port 3113 in use | Another app uses it | Set `PORT=` to a free port in `deploy.conf` and re-run step 9 |
| `deploy.sh`: `web/index.html was not built` | `serve.sh` changed or failed | Run `cd /opt/lie-detector/repo && sudo -u lie-detector bash ./serve.sh 3213` to see the error, then Ctrl+C |
| `deploy.sh`: health check failed, rolled back | nginx not serving this site on `127.0.0.1:3113` | `sudo nginx -t`, then `curl -sI http://127.0.0.1:3113/` and re-run step 9 |
| `nginx -t` fails | Error in another site, or both `conf.d/lie-detector.conf` and `sites-enabled/lie-detector` exist | Read the file and line it names. Keep only one lie-detector site file. |
| certbot: `DNS problem` / `Timeout during connect` | A record wrong, or port 80 blocked by a provider firewall | `getent ahostsv4 your-domain` must show the server IP. Open 80/443 in the provider panel, then re-run step 9. |
| `Permission denied (publickey)` on deploy | Deploy key missing on GitHub | Redo step 6 and check `sudo -u lie-detector -H ssh -T git@github.com` |
| Slow first load on mobile | About 7.5 MB of engine (gzipped) on the first visit | Normal. Later visits revalidate with `304` and are quick. |
| Low frame rate, or `CPU` instead of `GPU` | The visitor's device has no WebGL2 GPU path | Expected on weak devices. Results still work, just slower. |

## Uninstall

This removes only what this guide created: the nginx site and logs, the certificate, the ufw port rule from no-domain mode, `/opt/lie-detector`, `/etc/lie-detector` and the user. Packages stay installed. Also delete the deploy key on GitHub (repo → Settings → Deploy keys).

```bash
sudo bash -uo pipefail <<'EOF'
source /etc/lie-detector/deploy.conf || exit 1
rm -f "/etc/nginx/sites-enabled/$APP" "/etc/nginx/sites-available/$APP" "/etc/nginx/conf.d/$APP.conf"
if command -v nginx >/dev/null; then nginx -t && systemctl reload nginx; fi
rm -f /var/log/nginx/"$APP".access.log* /var/log/nginx/"$APP".error.log*
if [ -n "$DOMAIN" ] && command -v certbot >/dev/null; then certbot delete --cert-name "$DOMAIN" --non-interactive; fi
if [ -z "$DOMAIN" ] && command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then ufw delete allow "$PUBLIC_PORT/tcp"; fi
userdel "$APP_USER" 2>/dev/null
rm -rf "${APP_HOME:?}" "/etc/${APP:?}"
echo "lie-detector removed. Installed packages were left in place."
EOF
```

## Appendix: Caddy / Apache instead of nginx

Use this if step 2 found another web server on ports 80/443. Do steps 1–8 and the deploy-script part of step 10, then use one of these in place of step 9. `deploy.sh` notices there's no nginx site and skips its web check, so check the site in your browser after each deploy. Both servers already send the right MIME types for `.wasm` and `.mjs` (Apache needs the two `AddType` lines).

**Caddy** (automatic HTTPS). Add this to your Caddyfile, then run `sudo systemctl reload caddy`:

```
lie.example.com {
    root * /opt/lie-detector/current/web
    encode gzip
    header Cache-Control "no-cache"
    file_server
}
```

**Apache**: create `/etc/apache2/sites-available/lie-detector.conf`. Then run `sudo a2enmod headers && sudo a2ensite lie-detector && sudo apache2ctl configtest && sudo systemctl reload apache2`, and finally `sudo certbot --apache -d lie.example.com` (package `python3-certbot-apache`):

```
<VirtualHost *:80>
    ServerName lie.example.com
    DocumentRoot /opt/lie-detector/current/web
    <Directory /opt/lie-detector/current/web>
        Options FollowSymLinks
        AllowOverride None
        Require all granted
    </Directory>
    AddType application/wasm .wasm
    AddType text/javascript .mjs
    Header set Cache-Control "no-cache"
</VirtualHost>
```
