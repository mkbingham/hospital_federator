#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Author: Mark Bingham
#
# This script:
#   - Attempts to kill via PID
#   - If that fails, attempts by pattern match (this will kill orphans)
# ============================================================================

PID_DIR="pids"

echo "* Stopping Hospital Federator peers..."

if [[ -d "$PID_DIR" ]] && ls "$PID_DIR"/*.pid >/dev/null 2>&1; then
    echo "* Found PID files in $PID_DIR"

    for pidfile in "$PID_DIR"/*.pid; do
        pid=$(cat "$pidfile")

        if kill -0 "$pid" 2>/dev/null; then
            echo "* Sending SIGTERM to PID $pid ($(basename "$pidfile"))"
            kill "$pid"

            # Give it a moment to exit cleanly
            sleep 1

            if kill -0 "$pid" 2>/dev/null; then
                echo "! PID $pid still running, sending SIGKILL"
                kill -9 "$pid" || true
            fi
        else
            echo "! PID $pid not running (stale pidfile)"
        fi

        rm -f "$pidfile"
    done

    echo "* All peers stopped via PID files"
    exit 0
fi

echo "! No PID files found — falling back to process-name kill"
echo "! This may kill ALL hospital_federator_demo.py instances"

pkill -f "hospital_federator_demo.py --config" || true

echo "Kill attempt complete"

