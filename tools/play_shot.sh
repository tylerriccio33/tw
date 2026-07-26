#!/bin/bash
# Launches `make play` in a real (non-128x128) window, waits for it to come
# up, takes an OS-level screenshot of just that window via `screencapture`,
# and kills the process. macOS only - relies on System Events to resolve the
# window id, since screencapture needs -l<id> to target a single window
# instead of the whole screen.
set -euo pipefail

GODOT="${GODOT:-godot}"
RESOLUTION="${RESOLUTION:-1280x800}"
# campaign.tscn now builds the full 3D world (terrain/forest/city/road)
# synchronously before first paint, so it needs longer to settle than the
# bare 2D scene this used to be.
SETTLE_SECONDS="${SETTLE_SECONDS:-12}"
OUT_DIR="shots/play"
OUT_FILE="${OUT_DIR}/play.png"

mkdir -p "$OUT_DIR"

"$GODOT" res://campaign/campaign.tscn --resolution "$RESOLUTION" &
PID=$!
trap 'kill "$PID" 2>/dev/null || true' EXIT

sleep "$SETTLE_SECONDS"

# System Events can't return an AX window id for Godot's SDL-backed window
# (-1728 on every attempt), but position/size work fine, so crop by bounds
# via `screencapture -R` instead of targeting a window id with `-l`.
BOUNDS=$(osascript <<'EOF'
tell application "System Events"
    repeat with proc in application processes
        if (name of proc contains "godot") or (name of proc contains "Godot") then
            try
                set {px, py} to position of window 1 of proc
                set {sw, sh} to size of window 1 of proc
                return (px as string) & "," & (py as string) & "," & (sw as string) & "," & (sh as string)
            end try
        end if
    end repeat
end tell
return ""
EOF
)

if [ -z "$BOUNDS" ]; then
    echo "error: could not find a Godot window to screenshot" >&2
    exit 1
fi

screencapture -x -o -R"$BOUNDS" "$OUT_FILE"
echo "wrote $OUT_FILE"
