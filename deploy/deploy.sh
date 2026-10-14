#!/usr/bin/env bash
# Deploy Kot Uchenyi release to staging or prod on msk01.
# Usage: deploy.sh <staging|prod> <release_dir_or_tarball> [release_id]
# Does not print secrets. Auto-rolls back symlink + restart on failed healthcheck.
set -euo pipefail

ENV_NAME="${1:-}"
SRC="${2:-}"
RELEASE_ID="${3:-}"

APP_ROOT=/opt/alice-skill-kot-uchenyi
KEEP_RELEASES=5

usage() {
  echo "usage: $0 <staging|prod> <release_dir_or_tarball> [release_id]" >&2
  exit 2
}

[[ "$ENV_NAME" == "staging" || "$ENV_NAME" == "prod" ]] || usage
[[ -n "$SRC" ]] || usage

if [[ "$ENV_NAME" == "staging" ]]; then
  PORT=8021
  UNIT=cat-kot-uchenyi-staging.service
  LINK="$APP_ROOT/staging"
  ENV_FILE="$APP_ROOT/staging.env"
else
  PORT=8020
  UNIT=cat-kot-uchenyi.service
  LINK="$APP_ROOT/prod"
  ENV_FILE="$APP_ROOT/prod.env"
fi

RELEASES="$APP_ROOT/releases"
VENV="$APP_ROOT/.venv"
mkdir -p "$RELEASES"

if [[ -z "$RELEASE_ID" ]]; then
  RELEASE_ID="$(date -u +%Y%m%dT%H%M%SZ)-${RANDOM}"
fi
# sanitize
RELEASE_ID="$(echo "$RELEASE_ID" | tr -cd 'A-Za-z0-9._-')"
TARGET="$RELEASES/$RELEASE_ID"
TMP="$RELEASES/.tmp-$RELEASE_ID"

rm -rf "$TMP" "$TARGET"
mkdir -p "$TMP"

if [[ -f "$SRC" ]]; then
  tar -xzf "$SRC" -C "$TMP"
  # tolerate tarball with a single top-level dir
  if [[ "$(find "$TMP" -mindepth 1 -maxdepth 1 | wc -l)" -eq 1 ]]; then
    ONLY="$(find "$TMP" -mindepth 1 -maxdepth 1)"
    if [[ -d "$ONLY" ]]; then
      mv "$ONLY"/* "$TMP"/ 2>/dev/null || true
      mv "$ONLY"/.[!.]* "$TMP"/ 2>/dev/null || true
      rmdir "$ONLY" 2>/dev/null || true
    fi
  fi
elif [[ -d "$SRC" ]]; then
  cp -a "$SRC"/. "$TMP"/
else
  echo "source not found: $SRC" >&2
  exit 1
fi

[[ -f "$TMP/main.py" ]] || { echo "main.py missing in release" >&2; exit 1; }
mv "$TMP" "$TARGET"

if [[ ! -d "$VENV" ]]; then
  python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
if [[ -f "$TARGET/requirements.txt" ]]; then
  pip install -q -r "$TARGET/requirements.txt"
fi

PREV=""
if [[ -L "$LINK" ]]; then
  PREV="$(readlink -f "$LINK" || true)"
fi

ln -sfn "$TARGET" "$LINK"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE" >&2
  exit 1
fi

systemctl restart "$UNIT"
sleep 2

health_ok=0
for _ in $(seq 1 15); do
  if curl -sf -m 3 -X POST "http://127.0.0.1:${PORT}/webhook" \
    -H 'Content-Type: application/json' \
    -d '{"request":{"original_utterance":"ping"},"session":{"session_id":"deploy-health","new":false},"version":"1.0"}' \
    | grep -q '"text":"pong"'; then
    health_ok=1
    break
  fi
  sleep 1
done

if [[ "$health_ok" -ne 1 ]]; then
  echo "HEALTHCHECK FAILED for $ENV_NAME ($RELEASE_ID) — rolling back" >&2
  if [[ -n "$PREV" && -d "$PREV" ]]; then
    ln -sfn "$PREV" "$LINK"
    systemctl restart "$UNIT"
    sleep 2
  fi
  exit 1
fi

echo "deployed $ENV_NAME -> $RELEASE_ID (ok)"

# prune old releases (keep current staging+prod targets + KEEP_RELEASES newest)
mapfile -t ALL < <(ls -1dt "$RELEASES"/* 2>/dev/null | grep -v '/\.tmp-' || true)
KEEP_PATHS=()
[[ -L "$APP_ROOT/staging" ]] && KEEP_PATHS+=("$(readlink -f "$APP_ROOT/staging")")
[[ -L "$APP_ROOT/prod" ]] && KEEP_PATHS+=("$(readlink -f "$APP_ROOT/prod")")
count=0
for r in "${ALL[@]}"; do
  skip=0
  for k in "${KEEP_PATHS[@]}"; do
    [[ "$r" == "$k" ]] && skip=1 && break
  done
  if [[ "$skip" -eq 1 ]]; then
    continue
  fi
  count=$((count + 1))
  if [[ "$count" -gt "$KEEP_RELEASES" ]]; then
    rm -rf "$r"
  fi
done
