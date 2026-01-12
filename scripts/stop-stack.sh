#!/bin/bash
# ReticulumHF Stack Stop Script
# Gracefully stops all ReticulumHF services

echo "[ReticulumHF] Stopping stack..."

# Stop in reverse order
for service in nomadnet rnsd freedvtnc2 rigctld; do
    pidfile="/run/reticulumhf/${service}.pid"
    if [ -f "$pidfile" ]; then
        pid=$(cat "$pidfile")
        if kill -0 "$pid" 2>/dev/null; then
            echo "[ReticulumHF] Stopping $service (PID: $pid)..."
            kill "$pid"
            sleep 1
        fi
        rm -f "$pidfile"
    fi
done

# Kill any remaining processes by name
pkill -f "nomadnet" 2>/dev/null || true
pkill -f "rnsd" 2>/dev/null || true
pkill -f "freedvtnc2" 2>/dev/null || true
pkill -f "rigctld" 2>/dev/null || true

echo "[ReticulumHF] Stack stopped"
