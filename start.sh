#!/usr/bin/env bash
#
# start.sh — one-command launcher for the Sentinel aerial surveillance pipeline.
#
# Boots the full pipeline (ingest → enhance → detect → log) with sensible
# defaults. Creates a virtualenv, installs any missing dependencies, then runs
# `python -m sentinel` with flags derived from environment variables.
#
# Usage:
#   ./start.sh                       # full pipeline, auto-discover the drone stream
#   SOURCE=screen REGION=100,100,640,480 ./start.sh   # phone-mirror capture
#   URL=rtsp://192.168.1.1:554/live ./start.sh         # known stream URL
#   LOG=0 ./start.sh                 # run detection but don't write events.db
#   ENHANCE=0 DETECT=0 ./start.sh    # raw feed only
#
# Any extra arguments are passed straight through to `python -m sentinel`:
#   ./start.sh --log-level DEBUG
#
# Configuration (env vars, all optional):
#   SOURCE     auto | screen                 (default: auto)
#   URL        explicit stream URL to try first
#   REGION     left,top,width,height         (screen mode)
#   ENHANCE    1|0  enable enhancement       (default: 1)
#   DETECT     1|0  enable YOLO detection    (default: 1)
#   LOG        1|0  log detections to SQLite (default: 1; implies DETECT)
#   LOG_LEVEL  DEBUG|INFO|WARNING|ERROR      (default: INFO)
#   PYTHON     python interpreter            (default: python3)
#   VENV       virtualenv directory          (default: .venv)
#
set -euo pipefail

# --- Resolve repo root so the script works from any CWD --------------------
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

log() { printf '\033[36m[start]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[start] error:\033[0m %s\n' "$*" >&2; exit 1; }

# --- Optional local overrides ----------------------------------------------
# A gitignored .env can set any of the variables below without exporting them.
if [ -f .env ]; then
  log "loading .env"
  set -a; . ./.env; set +a
fi

PYTHON="${PYTHON:-python3}"
VENV="${VENV:-.venv}"
SOURCE="${SOURCE:-auto}"
ENHANCE="${ENHANCE:-1}"
DETECT="${DETECT:-1}"
LOG="${LOG:-1}"
REGION="${REGION:-}"
URL="${URL:-}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"

command -v "$PYTHON" >/dev/null 2>&1 || die "'$PYTHON' not found on PATH."

# --- Virtualenv ------------------------------------------------------------
if [ ! -d "$VENV" ]; then
  log "creating virtualenv at $VENV"
  "$PYTHON" -m venv "$VENV"
fi
# shellcheck disable=SC1091
. "$VENV/bin/activate"

# --- Dependencies (install only what's missing) ----------------------------
if ! python -c "import cv2, numpy, mss" >/dev/null 2>&1; then
  log "installing core dependencies (opencv, numpy, mss)…"
  pip install -q --upgrade pip
  pip install -q -r requirements.txt
fi

# Detection/logging need ultralytics (pulls in torch — large, one-time).
if { [ "$DETECT" = "1" ] || [ "$LOG" = "1" ]; } \
   && ! python -c "import ultralytics" >/dev/null 2>&1; then
  log "installing detection deps (ultralytics + torch — large, one-time)…"
  pip install -q ultralytics
fi

# --- Build the argument list -----------------------------------------------
args=()
case "$SOURCE" in
  auto)   : ;;                       # discovery + screen fallback (default)
  screen) args+=(--screen) ;;
  *) die "unknown SOURCE='$SOURCE' (expected 'auto' or 'screen')." ;;
esac

[ -n "$URL" ]    && args+=(--url "$URL")
[ -n "$REGION" ] && args+=(--region "$REGION")
[ "$ENHANCE" = "1" ] && args+=(--enhance)

# Ask the installed CLI which flags it actually supports, so the launcher works
# against any revision (e.g. --log only exists once the logging stage lands).
supports() { python -m sentinel --help 2>/dev/null | grep -q -- "$1"; }

# --log already implies --detect, so avoid passing both.
if [ "$LOG" = "1" ]; then
  if supports --log; then
    args+=(--log)
  else
    log "this build has no --log stage yet; falling back to --detect"
    [ "$DETECT" != "0" ] && supports --detect && args+=(--detect)
  fi
elif [ "$DETECT" = "1" ] && supports --detect; then
  args+=(--detect)
fi

args+=(--log-level "$LOG_LEVEL")

# Pass through any extra CLI args given to start.sh.
args+=("$@")

log "launching: python -m sentinel ${args[*]}"
exec python -m sentinel "${args[@]}"
