#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Author: Mark Bingham
# This script starts the demo.
# Runs one instance per YAML config in ./configs
# ============================================================

CONFIG_DIR="$(pwd)/configs"
PYTHON_APP="$(pwd)/hospital_federator_demo.py"
PID_DIR="pids"
HOST="127.0.0.1"
PORT_BASE=8000

mkdir -p pids

if [[ ! -d "$CONFIG_DIR" ]]; then
  echo "ERROR: Config directory not found: $CONFIG_DIR"
  exit 1
fi

if [[ ! -f "$PYTHON_APP" ]]; then
  echo "ERROR: Python app not found: $PYTHON_APP"
  exit 1
fi

echo "Launching peers from configs in: $CONFIG_DIR"
echo

shopt -s nullglob
CONFIGS=("$CONFIG_DIR"/peer*.yaml)
shopt -u nullglob

if [[ ${#CONFIGS[@]} -eq 0 ]]; then
  echo "ERROR: No peer*.yaml files found in $CONFIG_DIR"
  exit 1
fi

for cfg in "${CONFIGS[@]}"; do
  fname="$(basename "$cfg")"
  peer_id="${fname%.yaml}"

  # Extract numeric suffix: peer1 -> 1
  if [[ "$peer_id" =~ ^peer([0-9]+)$ ]]; then
    idx="${BASH_REMATCH[1]}"
  else
    echo "Skipping unrecognised config name: $fname"
    continue
  fi

  port=$((PORT_BASE + idx - 1))

  echo "Starting $peer_id"
  echo "  Config : $cfg"
  echo "  URL    : https://$HOST:$port"
  echo

  pid_file="$PID_DIR/$peer_id.pid"

  python "$PYTHON_APP" \
    --config "$cfg" \
    --peer-id "$peer_id" \
    --listen-host "$HOST" \
    --listen-port "$port" \
    > "logs/$peer_id.log" 2>&1 &
    
    pid=$!
    echo "$pid" > "$pid_file"
done

echo "------------------------------------------------------------"
echo "All peers started."
echo
echo "Logs:"
for cfg in "${CONFIGS[@]}"; do
  peer_id="$(basename "$cfg" .yaml)"
  echo "  logs/$peer_id.log"
done
echo
echo "To stop all peers:"
echo "  ./kill_all.sh"
echo "Alternatively:"
echo "  pkill -f hospital_federator_demo.py"
echo "============================================================"

