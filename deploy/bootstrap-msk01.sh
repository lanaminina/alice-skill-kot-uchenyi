#!/usr/bin/env bash
# One-time (or idempotent) bootstrap of release layout + staging on msk01.
# Run as root on msk01. Does not print .env secrets.
set -euo pipefail

APP_ROOT=/opt/alice-skill-kot-uchenyi
EDGE_TMPL=/opt/chatbook-edge/Caddyfile.msk01.template
EDGE_SCRIPTS=/opt/chatbook-edge/scripts

mkdir -p "$APP_ROOT/releases" "$APP_ROOT/deploy"

# Preserve existing .env → prod.env / staging.env
if [[ -f "$APP_ROOT/.env" && ! -f "$APP_ROOT/prod.env" ]]; then
  cp -a "$APP_ROOT/.env" "$APP_ROOT/prod.env"
fi
if [[ ! -f "$APP_ROOT/prod.env" ]]; then
  echo "missing prod.env / .env" >&2
  exit 1
fi
if [[ ! -f "$APP_ROOT/staging.env" ]]; then
  # staging = same secrets, open limits for Lana testing
  grep -vE '^(FREE_LIMIT|SOCRATIC_MAX_STEPS)=' "$APP_ROOT/prod.env" >"$APP_ROOT/staging.env" || true
  {
    echo "FREE_LIMIT=999"
    echo "SOCRATIC_MAX_STEPS=0"
  } >>"$APP_ROOT/staging.env"
  chmod 600 "$APP_ROOT/staging.env"
fi
chmod 600 "$APP_ROOT/prod.env"

# Initial release from current tree files (if prod symlink missing)
if [[ ! -L "$APP_ROOT/prod" ]]; then
  INIT_ID="bootstrap-$(date -u +%Y%m%dT%H%M%SZ)"
  INIT="$APP_ROOT/releases/$INIT_ID"
  mkdir -p "$INIT/static"
  cp -a "$APP_ROOT/main.py" "$INIT/"
  [[ -f "$APP_ROOT/requirements.txt" ]] && cp -a "$APP_ROOT/requirements.txt" "$INIT/"
  [[ -d "$APP_ROOT/static" ]] && cp -a "$APP_ROOT/static/." "$INIT/static/"
  ln -sfn "$INIT" "$APP_ROOT/prod"
fi

if [[ ! -d "$APP_ROOT/.venv" ]]; then
  python3 -m venv "$APP_ROOT/.venv"
fi
# shellcheck disable=SC1091
source "$APP_ROOT/.venv/bin/activate"
pip install -q -r "$APP_ROOT/prod/requirements.txt"

install -m 0755 "$APP_ROOT/deploy/deploy.sh" "$APP_ROOT/deploy/deploy.sh" 2>/dev/null || true
install -m 0644 "$APP_ROOT/deploy/cat-kot-uchenyi.service" /etc/systemd/system/cat-kot-uchenyi.service
install -m 0644 "$APP_ROOT/deploy/cat-kot-uchenyi-staging.service" /etc/systemd/system/cat-kot-uchenyi-staging.service

# Deploy user (limited sudo)
if ! id kot-deploy >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /home/kot-deploy --shell /bin/bash kot-deploy
fi
install -m 0440 "$APP_ROOT/deploy/sudoers-kot-deploy" /etc/sudoers.d/kot-deploy
visudo -cf /etc/sudoers.d/kot-deploy
mkdir -p /home/kot-deploy/.ssh
chmod 700 /home/kot-deploy/.ssh
chown -R kot-deploy:kot-deploy /home/kot-deploy
# kot-deploy can write release tarballs to /tmp and needs group write on releases via sudo script

systemctl daemon-reload
systemctl enable cat-kot-uchenyi.service cat-kot-uchenyi-staging.service
systemctl restart cat-kot-uchenyi.service

# First staging release = copy of current prod tree
if [[ ! -L "$APP_ROOT/staging" ]]; then
  bash "$APP_ROOT/deploy/deploy.sh" staging "$(readlink -f "$APP_ROOT/prod")" "staging-bootstrap"
else
  systemctl restart cat-kot-uchenyi-staging.service
fi

# Caddy: prefer re-render from template if present
if [[ -f "$EDGE_TMPL" ]] && grep -q 'cat-staging.chatbook.pro' "$EDGE_TMPL"; then
  if [[ -x "$EDGE_SCRIPTS/render-caddyfile.sh" ]]; then
    RS6_TS_IP="${RS6_TS_IP:-100.112.170.16}"
    BRIDGE_UPSTREAM="${BRIDGE_UPSTREAM:-127.0.0.1:8765}"
    RS6_TS_IP="$RS6_TS_IP" BRIDGE_UPSTREAM="$BRIDGE_UPSTREAM" \
      bash "$EDGE_SCRIPTS/render-caddyfile.sh" "$EDGE_TMPL" >/etc/caddy/Caddyfile.new
    caddy validate --config /etc/caddy/Caddyfile.new
    mv /etc/caddy/Caddyfile.new /etc/caddy/Caddyfile
    systemctl reload caddy
  fi
elif ! grep -q 'cat-staging.chatbook.pro' /etc/caddy/Caddyfile; then
  # fallback patch live file
  python3 - <<'PY'
from pathlib import Path
p = Path("/etc/caddy/Caddyfile")
text = p.read_text(encoding="utf-8")
block = """
cat-staging.chatbook.pro {
	import no_h3
	reverse_proxy http://127.0.0.1:8021
}
"""
if "cat-staging.chatbook.pro" not in text:
    needle = "cat.chatbook.pro {"
    if needle in text:
        # insert after cat.chatbook.pro block
        idx = text.index(needle)
        end = text.index("}", idx)
        # find closing brace of block (simple: first } after open — fragile if nested)
        depth = 0
        i = idx
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
            i += 1
        text = text[: end + 1] + "\n" + block + text[end + 1 :]
    else:
        text += "\n" + block
    p.write_text(text, encoding="utf-8")
    print("patched live Caddyfile")
PY
  caddy validate --config /etc/caddy/Caddyfile
  systemctl reload caddy
fi

echo "bootstrap done"
systemctl is-active cat-kot-uchenyi.service cat-kot-uchenyi-staging.service
curl -sf -m 3 -X POST http://127.0.0.1:8020/webhook -H 'Content-Type: application/json' \
  -d '{"request":{"original_utterance":"ping"}}' | grep -q pong && echo prod_ok
curl -sf -m 3 -X POST http://127.0.0.1:8021/webhook -H 'Content-Type: application/json' \
  -d '{"request":{"original_utterance":"ping"}}' | grep -q pong && echo staging_ok
