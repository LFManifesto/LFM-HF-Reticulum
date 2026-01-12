#!/bin/bash
# ReticulumHF WiFi Access Point Manager
# Creates and manages a persistent WiFi AP for Sideband connections

set -e

AP_SSID="${RETICULUMHF_AP_SSID:-ReticulumHF}"
AP_PASS="${RETICULUMHF_AP_PASS:-}"  # Empty = open network
AP_CHANNEL="${RETICULUMHF_AP_CHANNEL:-7}"
AP_IP="192.168.4.1"

ACTION="${1:-start}"

configure_hostapd() {
    local use_password="$1"

    if [ -n "$AP_PASS" ] && [ "$use_password" = "true" ]; then
        # WPA2 secured network
        cat > /etc/hostapd/hostapd.conf << EOF
interface=wlan0
driver=nl80211
ssid=$AP_SSID
hw_mode=g
channel=$AP_CHANNEL
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase=$AP_PASS
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
EOF
    else
        # Open network
        cat > /etc/hostapd/hostapd.conf << EOF
interface=wlan0
driver=nl80211
ssid=$AP_SSID
hw_mode=g
channel=$AP_CHANNEL
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=0
EOF
    fi
}

configure_dnsmasq() {
    cat > /etc/dnsmasq.d/reticulumhf.conf << EOF
interface=wlan0
dhcp-range=192.168.4.10,192.168.4.50,255.255.255.0,24h
# Don't redirect DNS after setup (no captive portal)
EOF
}

configure_network() {
    # Configure static IP for wlan0
    if ! grep -q "interface wlan0" /etc/dhcpcd.conf; then
        cat >> /etc/dhcpcd.conf << EOF

# ReticulumHF WiFi AP
interface wlan0
static ip_address=${AP_IP}/24
nohook wpa_supplicant
EOF
    fi
}

start_ap() {
    echo "[ReticulumHF] Starting WiFi AP: $AP_SSID"

    # Stop any existing wpa_supplicant on wlan0
    wpa_cli -i wlan0 terminate 2>/dev/null || true

    # Configure and start services
    configure_hostapd "true"
    configure_dnsmasq
    configure_network

    # Restart networking
    systemctl restart dhcpcd
    sleep 2

    # Unmask and start hostapd
    systemctl unmask hostapd
    systemctl restart hostapd

    # Restart dnsmasq
    systemctl restart dnsmasq

    echo "[ReticulumHF] WiFi AP '$AP_SSID' started on $AP_IP"
}

stop_ap() {
    echo "[ReticulumHF] Stopping WiFi AP"
    systemctl stop hostapd || true
    systemctl stop dnsmasq || true
}

status_ap() {
    echo "=== WiFi AP Status ==="
    echo "SSID: $AP_SSID"
    echo "IP: $AP_IP"
    echo ""
    systemctl status hostapd --no-pager || true
    echo ""
    echo "=== Connected Clients ==="
    cat /var/lib/misc/dnsmasq.leases 2>/dev/null || echo "No leases found"
}

case "$ACTION" in
    start)
        start_ap
        ;;
    stop)
        stop_ap
        ;;
    restart)
        stop_ap
        sleep 2
        start_ap
        ;;
    status)
        status_ap
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac
