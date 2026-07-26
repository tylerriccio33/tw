#!/usr/bin/env bash
# Runs godot and fails the target if either the exit code is nonzero OR
# stderr contains a "SCRIPT ERROR:"/"ERROR:" line - regardless of exit code.
#
# Why this exists: Godot exits 0 even after a script parse error or a runtime
# error that aborted mid-function (GDScript unwinds only the failing
# function, not the process). A stage that crashed halfway through then looks
# identical, from the shell's point of view, to one that finished cleanly.
# Every make target that runs godot should go through this instead of calling
# $(GODOT) directly.
set -u

GODOT="${GODOT:-godot}"
err_log="$(mktemp)"
trap 'rm -f "$err_log"' EXIT

# Route only stderr through the pipe (fd3 keeps stdout on its original
# target) so `tee` sees godot's stderr synchronously - no process
# substitution race between the child exiting and the log finishing a write.
exec 3>&1
"$GODOT" "$@" 2>&1 1>&3 3>&- | tee "$err_log" >&2
exit_code="${PIPESTATUS[0]}"
exec 3>&-

# WARNING: lines (e.g. deprecated-API notices) are expected noise and must
# not trip the gate; only real script/engine errors do.
if grep -qE '^(SCRIPT ERROR:|ERROR:)' "$err_log"; then
	echo "godot_gate: stderr contained a SCRIPT ERROR/ERROR line - failing" \
		"regardless of exit code ($exit_code)" >&2
	exit 1
fi

exit "$exit_code"
