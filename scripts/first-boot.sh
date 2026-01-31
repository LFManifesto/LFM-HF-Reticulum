#!/bin/bash
# ReticulumHF First Boot Script
# Sets up WiFi AP and launcher portal
# User selects operating mode (HF or LFN) via web portal
#
# NOTE: Do NOT use 'set -e' - we need robust error handling

LOG="/var/log/reticulumhf-firstboot.log"
SETUP_COMPLETE="/etc/reticulumhf/.firstboot_complete"

# Ensure log directory exists
touch "$LOG" 2>/dev/null || LOG="/tmp/reticulumhf-firstboot.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# Check if already complete
if [ -f "$SETUP_COMPLETE" ]; then
    log "First boot already complete, skipping"
    exit 0
fi

log "============================================"
log "ReticulumHF First Boot Starting"
log "============================================"

mkdir -p /etc/reticulumhf || { log "ERROR: Cannot create /etc/reticulumhf"; exit 1; }

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
else
    log "  [WARN] RNS not found"
fi

if [ -x /home/pi/.local/bin/freedvtnc2 ]; then
    log "  [OK] freedvtnc2 installed"
else
    log "  [WARN] freedvtnc2 not found"
fi

if command -v rigctl &> /dev/null; then
    log "  [OK] hamlib (rigctl) installed"
else
    log "  [WARN] hamlib not found"
fi

if systemctl is-enabled i2pd &>/dev/null; then
    log "  [OK] i2pd installed"
else
    log "  [WARN] i2pd not installed"
fi

log ""
log "Setting up WiFi AP..."

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
    log "  [ERROR] First boot FAILED"
    exit 1
fi

# Bring up wlan0 with static IP
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
    systemctl enable hostapd 2>/dev/null || true
else
    log "  [ERROR] hostapd not running"
    journalctl -u hostapd --no-pager -n 10 >> "$LOG" 2>&1
fi

# Start dnsmasq
log "Starting dnsmasq..."
if [ -f /etc/dnsmasq.d/reticulumhf.conf ]; then
    systemctl start dnsmasq 2>&1
    sleep 2
else
    log "  [ERROR] /etc/dnsmasq.d/reticulumhf.conf not found"
fi

# Check dnsmasq status
DNSMASQ_RUNNING=0
if pgrep dnsmasq &>/dev/null; then
    log "  [OK] dnsmasq is running"
    DNSMASQ_RUNNING=1
    systemctl enable dnsmasq 2>/dev/null || true
else
    log "  [ERROR] dnsmasq not running"
    journalctl -u dnsmasq --no-pager -n 10 >> "$LOG" 2>&1
fi

# Start the launcher portal service
log "Starting launcher portal..."
systemctl enable reticulumhf-launcher 2>/dev/null || true
LAUNCHER_OUTPUT=$(systemctl start reticulumhf-launcher 2>&1)
LAUNCHER_EXIT=$?
log "  launcher service start exit code: $LAUNCHER_EXIT"

sleep 3

LAUNCHER_RUNNING=0
if systemctl is-active reticulumhf-launcher &>/dev/null; then
    log "  [OK] Launcher portal is running"
    LAUNCHER_RUNNING=1
    if ss -tlnp 2>/dev/null | grep -q ':80'; then
        log "  [OK] Launcher listening on port 80"
    else
        log "  [WARN] Nothing listening on port 80 yet"
    fi
else
    log "  [ERROR] Launcher portal failed"
    journalctl -u reticulumhf-launcher --no-pager -n 10 >> "$LOG" 2>&1
fi

# Mark first boot complete
touch "$SETUP_COMPLETE"

log ""
log "============================================"
log "  ReticulumHF First Boot Complete"
log "============================================"
log ""

if [ $HOSTAPD_RUNNING -eq 1 ] && [ $DNSMASQ_RUNNING -eq 1 ]; then
    log "  WiFi AP: ReticulumHF"
    log "  Password: reticulumhf"
    log "  Portal: http://192.168.4.1"
    log ""
    log "  Select operating mode:"
    log "    - HF Gateway: Radio over FreeDV"
    log "    - Internet Gateway: I2P to Lightfighter Network"
else
    log "  [ERROR] WiFi AP services not all running"
    log "  hostapd: $HOSTAPD_RUNNING, dnsmasq: $DNSMASQ_RUNNING"
fi

log ""
log "  Log file: $LOG"
log "============================================"

exit 0
