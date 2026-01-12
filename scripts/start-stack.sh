#!/bin/bash
# ReticulumHF Stack Startup Script
# Starts rigctld, freedvtnc2, rnsd, and nomadnet in proper order

set -e

# Load configuration
source /etc/reticulumhf/config.env

echo "[ReticulumHF] Starting stack..."

# Start rigctld in background
echo "[ReticulumHF] Starting rigctld..."
eval "$RIGCTLD_CMD" &
RIGCTLD_PID=$!
echo "[ReticulumHF] rigctld started (PID: $RIGCTLD_PID)"

# Wait for rigctld to be ready
sleep 3

# Test rigctld is responding
if ! rigctl -m 2 f > /dev/null 2>&1; then
    echo "[ReticulumHF] Warning: rigctld may not be responding"
fi

# Start freedvtnc2 in background
echo "[ReticulumHF] Starting freedvtnc2..."
eval "$FREEDVTNC2_CMD" &
FREEDVTNC2_PID=$!
echo "[ReticulumHF] freedvtnc2 started (PID: $FREEDVTNC2_PID)"

# Wait for TNC to initialize
sleep 5

# Start rnsd (Reticulum daemon)
echo "[ReticulumHF] Starting rnsd..."
rnsd &
RNSD_PID=$!
echo "[ReticulumHF] rnsd started (PID: $RNSD_PID)"

# Wait for Reticulum to initialize
sleep 3

# Start NomadNet in headless mode
echo "[ReticulumHF] Starting NomadNet..."
nomadnet --daemon &
NOMADNET_PID=$!
echo "[ReticulumHF] NomadNet started (PID: $NOMADNET_PID)"

echo "[ReticulumHF] Stack started successfully"
echo "[ReticulumHF] PIDs: rigctld=$RIGCTLD_PID freedvtnc2=$FREEDVTNC2_PID rnsd=$RNSD_PID nomadnet=$NOMADNET_PID"

# Save PIDs for stop script
echo "$RIGCTLD_PID" > /run/reticulumhf/rigctld.pid
echo "$FREEDVTNC2_PID" > /run/reticulumhf/freedvtnc2.pid
echo "$RNSD_PID" > /run/reticulumhf/rnsd.pid
echo "$NOMADNET_PID" > /run/reticulumhf/nomadnet.pid

# Wait for any process to exit
wait -n

# If we get here, something crashed
echo "[ReticulumHF] A process exited unexpectedly"
exit 1
