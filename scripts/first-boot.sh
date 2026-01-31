#!/bin/bash
# ReticulumHF First Boot Script
# Verifies pre-installed software and activates WiFi AP using hostapd/dnsmasq
# All software is pre-installed in the image - NO compilation needed
#
# NOTE: Do NOT use 'set -e' - we need robust error handling

LOG="/var/log/reticulumhf-firstboot.log"
SETUP_COMPLETE="/etc/reticulumhf/.setup_complete"

# Ensure log directory exists
touch "$LOG" 2>/dev/null || LOG="/tmp/reticulumhf-firstboot.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# Check if already complete
if [ -f "$SETUP_COMPLETE" ]; then
    log "Setup already complete, skipping first-boot"
    exit 0
fi

log "============================================"
log "ReticulumHF First Boot Starting"
log "============================================"

mkdir -p /run/reticulumhf || log "WARN: Failed to create /run/reticulumhf"
mkdir -p /etc/reticulumhf || { log "ERROR: Cannot create /etc/reticulumhf"; exit 1; }
mkdir -p /etc/reticulumhf/backups || log "WARN: Failed to create backups directory"

# Backup original configuration files for recovery
log "Creating configuration backups..."
if [ -f /etc/hostapd/hostapd.conf ]; then
    cp /etc/hostapd/hostapd.conf /etc/reticulumhf/backups/hostapd.conf.default 2>/dev/null
    log "  [OK] hostapd.conf backed up"
fi
if [ -f /etc/dnsmasq.d/reticulumhf.conf ]; then
    cp /etc/dnsmasq.d/reticulumhf.conf /etc/reticulumhf/backups/dnsmasq.conf.default 2>/dev/null
    log "  [OK] dnsmasq.conf backed up"
fi
if [ -f /etc/dhcpcd.conf ]; then
    cp /etc/dhcpcd.conf /etc/reticulumhf/backups/dhcpcd.conf.default 2>/dev/null
    log "  [OK] dhcpcd.conf backed up"
fi

# Give system time to fully initialize
log "Waiting for system initialization..."
sleep 5

# Verify pre-installed software
log "Verifying pre-installed software..."

if ldconfig -p 2>/dev/null | grep -q libcodec2; then
    log "  [OK] codec2 library found"
else
    log "  [WARN] codec2 not found"
fi

if [ -x /home/pi/.local/bin/rnstatus ]; then
    log "  [OK] RNS installed"
elif [ -d /home/pi/.local/pipx/venvs/rns ]; then
    log "  [WARN] RNS venv exists but symlink missing"
else
    log "  [WARN] RNS not found"
fi

if [ -x /home/pi/.local/bin/freedvtnc2 ]; then
    log "  [OK] freedvtnc2 installed"
elif [ -d /home/pi/.local/pipx/venvs/freedvtnc2 ]; then
    log "  [WARN] freedvtnc2 venv exists but symlink missing"
else
    log "  [WARN] freedvtnc2 not found"
fi

if command -v rigctl &> /dev/null; then
    log "  [OK] hamlib (rigctl) installed"
else
    log "  [WARN] hamlib not found"
fi

log ""
log "Setting up WiFi AP with hostapd..."

# Unblock WiFi
log "Unblocking WiFi..."
rfkill unblock wlan 2>&1 || log "  rfkill not needed or failed"

# Wait for wlan0
log "Waiting for wlan0 interface..."
WLAN_FOUND=0
for i in {1..30}; do
    if ip link show wlan0 &>/dev/null; then
        log "  wlan0 found after $i seconds"
        WLAN_FOUND=1
        break
    fi
    sleep 1
done

if [ "$WLAN_FOUND" -eq 0 ]; then
    log "  [ERROR] wlan0 not found after 30 seconds"
    INTERFACES=$(ip link show 2>/dev/null | grep -E '^[0-9]+:' | cut -d: -f2 | tr -d ' ')
    log "  Available interfaces: $INTERFACES"
    log "  [ERROR] First boot FAILED - do NOT mark as complete"
    log "  To retry: delete /etc/reticulumhf/.setup_complete and reboot"
    # DO NOT mark setup_complete on failure - this prevents recovery
    exit 1
fi

# Bring up wlan0 with static IP (dhcpcd should handle this)
log "Bringing up wlan0..."
ip link set wlan0 up 2>&1 || log "  Could not bring up wlan0"
sleep 2

# Verify static IP is assigned
IP_ADDR=$(ip addr show wlan0 2>/dev/null | grep 'inet ' | awk '{print $2}')
if [ -n "$IP_ADDR" ]; then
    log "  wlan0 IP: $IP_ADDR"
else
    log "  Manually assigning 192.168.4.1/24..."
    ip addr add 192.168.4.1/24 dev wlan0 2>&1 || log "  Could not assign IP (may already exist)"
fi

# Start hostapd
log "Starting hostapd..."
if [ -f /etc/hostapd/hostapd.conf ]; then
    systemctl unmask hostapd 2>/dev/null || true
    HOSTAPD_OUTPUT=$(systemctl start hostapd 2>&1)
    HOSTAPD_EXIT=$?
    log "  hostapd start exit code: $HOSTAPD_EXIT"
    if [ $HOSTAPD_EXIT -ne 0 ]; then
        log "  hostapd error: $HOSTAPD_OUTPUT"
        log "  Trying hostapd directly..."
        hostapd -B /etc/hostapd/hostapd.conf 2>&1 || log "  Direct hostapd also failed"
    fi
    sleep 2
else
    log "  [ERROR] /etc/hostapd/hostapd.conf not found"
fi

# Check hostapd status
HOSTAPD_RUNNING=0
if pgrep hostapd &>/dev/null; then
    log "  [OK] hostapd is running"
    HOSTAPD_RUNNING=1
    # Enable for persistence after reboot
    systemctl enable hostapd 2>/dev/null || true
    log "  [OK] hostapd enabled for boot"
else
    log "  [ERROR] hostapd not running"
    journalctl -u hostapd --no-pager -n 10 >> "$LOG" 2>&1
fi

# Start dnsmasq
log "Starting dnsmasq..."
if [ -f /etc/dnsmasq.d/reticulumhf.conf ]; then
    systemctl start dnsmasq 2>&1
    DNSMASQ_EXIT=$?
    log "  dnsmasq start exit code: $DNSMASQ_EXIT"
    sleep 2
else
    log "  [ERROR] /etc/dnsmasq.d/reticulumhf.conf not found"
fi

# Check dnsmasq status
DNSMASQ_RUNNING=0
if pgrep dnsmasq &>/dev/null; then
    log "  [OK] dnsmasq is running"
    DNSMASQ_RUNNING=1
    # Enable for persistence after reboot
    systemctl enable dnsmasq 2>/dev/null || true
    log "  [OK] dnsmasq enabled for boot"
else
    log "  [ERROR] dnsmasq not running"
    journalctl -u dnsmasq --no-pager -n 10 >> "$LOG" 2>&1
fi

# Configure RNS for TCP bridge
log "Configuring RNS..."
RNS_CONFIG_DIR="/home/pi/.reticulum"
RNS_CONFIG_SRC="/opt/reticulumhf/configs/rns-config"

if [ -f "$RNS_CONFIG_SRC" ]; then
    mkdir -p "$RNS_CONFIG_DIR"
    cp "$RNS_CONFIG_SRC" "$RNS_CONFIG_DIR/config"
    chown -R pi:pi "$RNS_CONFIG_DIR"
    chmod 700 "$RNS_CONFIG_DIR"
    chmod 600 "$RNS_CONFIG_DIR/config"
    log "  [OK] RNS config installed"
else
    log "  [WARN] RNS config not found at $RNS_CONFIG_SRC"
fi

# Start i2pd and wait for it to reseed
log "Starting i2pd (I2P transport)..."
systemctl enable i2pd 2>/dev/null || true
systemctl start i2pd 2>&1
sleep 2

# Wait for i2pd to reseed (check for >5 routers)
log "Waiting for i2pd to reseed from I2P network..."
I2PD_READY=0
for i in {1..60}; do
    ROUTERS=$(curl -s "http://127.0.0.1:7070/" 2>/dev/null | grep -oP 'Routers:</b> \K[0-9]+' || echo "0")
    if [ "$ROUTERS" -gt 5 ]; then
        log "  [OK] i2pd reseeded with $ROUTERS routers after ${i}s"
        I2PD_READY=1
        break
    fi
    if [ $((i % 10)) -eq 0 ]; then
        log "  Still waiting for i2pd reseed... ($ROUTERS routers, ${i}s)"
    fi
    sleep 1
done

if [ $I2PD_READY -eq 0 ]; then
    log "  [WARN] i2pd reseed timeout - I2P may take longer to connect"
fi

# Restart RNS daemon to load new config
log "Starting RNS daemon..."
systemctl enable reticulumhf-rnsd 2>/dev/null || true
# Use restart (not start) to ensure config is reloaded
RNSD_OUTPUT=$(systemctl restart reticulumhf-rnsd 2>&1)
RNSD_EXIT=$?
log "  rnsd restart exit code: $RNSD_EXIT"
sleep 5

RNSD_RUNNING=0
if pgrep -f "rnsd" &>/dev/null; then
    log "  [OK] rnsd is running"
    RNSD_RUNNING=1
    # Check if TCP port is listening
    if ss -tlnp 2>/dev/null | grep -q ':4242'; then
        log "  [OK] RNS TCP server listening on port 4242"
    else
        log "  [WARN] RNS TCP port 4242 not yet listening"
    fi
else
    log "  [ERROR] rnsd not running"
    journalctl -u reticulumhf-rnsd --no-pager -n 10 >> "$LOG" 2>&1
fi

# Start the setup portal service
log "Starting setup portal service..."
systemctl enable reticulumhf-portal 2>/dev/null || true
PORTAL_OUTPUT=$(systemctl start reticulumhf-portal 2>&1)
PORTAL_EXIT=$?
log "  portal service start exit code: $PORTAL_EXIT"

sleep 3

PORTAL_RUNNING=0
if systemctl is-active reticulumhf-portal &>/dev/null; then
    log "  [OK] Setup portal service is running"
    PORTAL_RUNNING=1
    if ss -tlnp 2>/dev/null | grep -q ':80'; then
        log "  [OK] Setup portal listening on port 80"
    else
        log "  [WARN] Nothing listening on port 80 yet"
    fi
else
    log "  [ERROR] Setup portal service failed"
    journalctl -u reticulumhf-portal --no-pager -n 10 >> "$LOG" 2>&1
fi

# NOTE: Do NOT mark setup_complete here - wizard does this after radio config
# The .setup_complete flag is created by /api/complete-setup in app.py

log ""
log "============================================"
log "  ReticulumHF First Boot Complete"
log "============================================"
log ""

if [ $HOSTAPD_RUNNING -eq 1 ] && [ $DNSMASQ_RUNNING -eq 1 ]; then
    log "  WiFi AP: ReticulumHF-Setup"
    log "  Password: reticulumhf"
    log "  Portal: http://192.168.4.1"
else
    log "  [ERROR] WiFi AP services not all running"
    log "  hostapd: $HOSTAPD_RUNNING, dnsmasq: $DNSMASQ_RUNNING"
fi

if [ $RNSD_RUNNING -eq 1 ]; then
    log "  RNS TCP: 192.168.4.1:4242 (for Sideband)"
else
    log "  [ERROR] RNS daemon not running"
fi

log ""
log "  Log file: $LOG"
log "============================================"

exit 0
