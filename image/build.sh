#!/bin/bash
# ReticulumHF Pi Image Build Script
# Creates a custom Raspberry Pi OS Bookworm image with ReticulumHF pre-installed
#
# WiFi AP: Uses hostapd + dnsmasq (proven stable solution)
# Base OS: Raspberry Pi OS Bookworm Lite (64-bit)
#
# Usage: ./build.sh [base-image.img]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$PROJECT_DIR/build"
OUTPUT_DIR="$PROJECT_DIR/output"
MOUNT_DIR="$BUILD_DIR/mnt"
LOOP_DEV=""

cleanup() {
    echo "Cleaning up..."
    sudo fuser -k "$MOUNT_DIR" 2>/dev/null || true
    sleep 1
    if [ -f "$MOUNT_DIR/etc/ld.so.preload.bak" ]; then
        sudo mv "$MOUNT_DIR/etc/ld.so.preload.bak" "$MOUNT_DIR/etc/ld.so.preload" 2>/dev/null || true
    fi
    sudo umount "$MOUNT_DIR/dev/pts" 2>/dev/null || true
    sudo umount "$MOUNT_DIR/dev" 2>/dev/null || true
    sudo umount "$MOUNT_DIR/proc" 2>/dev/null || true
    sudo umount "$MOUNT_DIR/sys" 2>/dev/null || true
    sudo umount "$MOUNT_DIR/boot/firmware" 2>/dev/null || true
    sudo umount "$MOUNT_DIR" 2>/dev/null || true
    if [ -n "$LOOP_DEV" ]; then
        sudo losetup -d "$LOOP_DEV" 2>/dev/null || true
    fi
}

trap cleanup EXIT

BASE_IMAGE="${1:-bookworm-lite.img}"
OUTPUT_IMAGE="reticulumhf-$(date +%Y%m%d).img"

echo "============================================"
echo "ReticulumHF Pi Image Builder"
echo "============================================"
echo "Base image: $BASE_IMAGE"
echo "Output: $OUTPUT_IMAGE"
echo "Approach: Bookworm + hostapd/dnsmasq"
echo ""

mkdir -p "$BUILD_DIR" "$OUTPUT_DIR"

if [ ! -f "$BASE_IMAGE" ]; then
    echo "Error: Base image not found: $BASE_IMAGE"
    echo ""
    echo "Download Raspberry Pi OS Lite Bookworm (64-bit) from:"
    echo "https://www.raspberrypi.com/software/operating-systems/"
    exit 1
fi

echo "[1/10] Copying base image..."
cp "$BASE_IMAGE" "$BUILD_DIR/$OUTPUT_IMAGE"

echo "[2/10] Expanding image (adding 3GB for software)..."
dd if=/dev/zero bs=1M count=3072 >> "$BUILD_DIR/$OUTPUT_IMAGE"

echo "[3/10] Setting up loop device..."
LOOP_DEV=$(sudo losetup -fP --show "$BUILD_DIR/$OUTPUT_IMAGE")
echo "Loop device: $LOOP_DEV"

echo "[4/10] Resizing partition..."
sudo parted -s "$LOOP_DEV" resizepart 2 100%
sudo e2fsck -fy "${LOOP_DEV}p2" 2>/dev/null || echo "Warning: e2fsck returned non-zero"
sudo resize2fs -f "${LOOP_DEV}p2"

echo "[5/10] Mounting filesystems..."
mkdir -p "$MOUNT_DIR"
sudo mount "${LOOP_DEV}p2" "$MOUNT_DIR"
sudo mkdir -p "$MOUNT_DIR/boot/firmware"
sudo mount "${LOOP_DEV}p1" "$MOUNT_DIR/boot/firmware"

sudo mount --bind /dev "$MOUNT_DIR/dev"
sudo mount --bind /dev/pts "$MOUNT_DIR/dev/pts"
sudo mount -t proc proc "$MOUNT_DIR/proc"
sudo mount -t sysfs sysfs "$MOUNT_DIR/sys"

echo "[6/10] Preparing chroot environment..."

if [ -f /usr/bin/qemu-aarch64-static ]; then
    sudo cp /usr/bin/qemu-aarch64-static "$MOUNT_DIR/usr/bin/"
fi

sudo cp /etc/resolv.conf "$MOUNT_DIR/etc/resolv.conf"

if [ -f "$MOUNT_DIR/etc/ld.so.preload" ]; then
    sudo cp "$MOUNT_DIR/etc/ld.so.preload" "$MOUNT_DIR/etc/ld.so.preload.bak"
    sudo sed -i 's/^/#DISABLED /g' "$MOUNT_DIR/etc/ld.so.preload"
fi

echo "[7/10] Installing ReticulumHF files..."

sudo mkdir -p "$MOUNT_DIR/opt/reticulumhf"
sudo cp -r "$PROJECT_DIR/setup-portal" "$MOUNT_DIR/opt/reticulumhf/"
sudo cp -r "$PROJECT_DIR/configs" "$MOUNT_DIR/opt/reticulumhf/"
sudo cp -r "$PROJECT_DIR/scripts" "$MOUNT_DIR/opt/reticulumhf/"
sudo cp -r "$PROJECT_DIR/beacon" "$MOUNT_DIR/opt/reticulumhf/"
sudo cp -r "$PROJECT_DIR/docs" "$MOUNT_DIR/opt/reticulumhf/"

sudo cp "$PROJECT_DIR/services/"*.service "$MOUNT_DIR/etc/systemd/system/"

# Install ALSA configuration for USB audio (fixes freedvtnc2 "Unknown PCM" errors)
sudo cp "$PROJECT_DIR/configs/asound.conf" "$MOUNT_DIR/etc/asound.conf"

sudo ln -sf /etc/systemd/system/reticulumhf-firstboot.service \
    "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/reticulumhf-firstboot.service"

# Enable rnsd service (will be started by first-boot, then auto-start on subsequent boots)
sudo ln -sf /etc/systemd/system/reticulumhf-rnsd.service \
    "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/reticulumhf-rnsd.service"

# Enable portal service
sudo ln -sf /etc/systemd/system/reticulumhf-portal.service \
    "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/reticulumhf-portal.service"

# Enable wlan0 static IP service (runs before hostapd)
sudo ln -sf /etc/systemd/system/reticulumhf-wlan.service \
    "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/reticulumhf-wlan.service"

# Install hostapd rfkill override (unblock WiFi before starting hostapd)
sudo mkdir -p "$MOUNT_DIR/etc/systemd/system/hostapd.service.d"
sudo cp "$PROJECT_DIR/configs/hostapd-rfkill.conf" "$MOUNT_DIR/etc/systemd/system/hostapd.service.d/rfkill.conf"

echo "[8/10] Running installation in chroot..."

cat << 'CHROOT_SCRIPT' | sudo tee "$MOUNT_DIR/tmp/install.sh"
#!/bin/bash
set -e

echo "============================================"
echo "ReticulumHF Chroot Installation"
echo "============================================"

# Set hostname
echo "reticulumhf" > /etc/hostname
sed -i 's/127.0.1.1.*/127.0.1.1\treticulumhf/' /etc/hosts

# Create pi user
echo "[1/9] Creating pi user..."
if ! id pi &>/dev/null; then
    useradd -m -s /bin/bash -G sudo,dialout,audio,video,plugdev,netdev,gpio,i2c,spi pi
fi
echo "pi:reticulumhf" | chpasswd
mkdir -p /home/pi/.config
touch /home/pi/.config/user-dirs.locale
chown -R pi:pi /home/pi/.config

# Enable SSH
echo "[2/9] Enabling SSH..."
systemctl enable ssh

# Disable first-boot wizard
echo "[3/9] Disabling first-boot wizard..."
systemctl disable userconfig 2>/dev/null || true
systemctl disable piwiz 2>/dev/null || true
systemctl mask userconfig 2>/dev/null || true
systemctl mask piwiz 2>/dev/null || true
apt-get remove -y piwiz userconf-pi 2>/dev/null || true
mkdir -p /etc/skel/.config
touch /etc/skel/.config/user-dirs.locale

# Install system packages
echo "[4/9] Installing system packages..."
apt-get update
apt-get install -y \
    python3 python3-pip python3-venv python3-dev python3-flask pipx \
    git build-essential cmake \
    portaudio19-dev alsa-utils \
    libhamlib-utils libhamlib-dev \
    hostapd dnsmasq \
    iptables \
    fake-hwclock

# Configure fake-hwclock to preserve time across reboots
# This fixes the "wrong date on first boot" issue for Pi without RTC
echo "[4.1/9] Configuring fake-hwclock..."
systemctl enable fake-hwclock

# Configure hostapd (WiFi AP)
echo "[5/9] Configuring hostapd..."
cat > /etc/hostapd/hostapd.conf << 'HOSTAPD'
interface=wlan0
driver=nl80211
ssid=ReticulumHF
hw_mode=g
channel=7
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase=reticulumhf
wpa_key_mgmt=WPA-PSK
wpa_pairwise=TKIP
rsn_pairwise=CCMP
HOSTAPD

# Point hostapd to config
sed -i 's|^#DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd 2>/dev/null || true
echo 'DAEMON_CONF="/etc/hostapd/hostapd.conf"' >> /etc/default/hostapd

# Configure dnsmasq (DHCP + DNS for AP)
echo "[6/9] Configuring dnsmasq..."
cat > /etc/dnsmasq.d/reticulumhf.conf << 'DNSMASQ'
interface=wlan0
dhcp-range=192.168.4.2,192.168.4.20,255.255.255.0,24h
address=/reticulumhf.local/192.168.4.1
DNSMASQ

# Configure static IP for wlan0
cat >> /etc/dhcpcd.conf << 'DHCPCD'

# ReticulumHF WiFi AP static IP
interface wlan0
static ip_address=192.168.4.1/24
nohook wpa_supplicant
DHCPCD

# Enable IP forwarding (for internet sharing later if connected via ethernet)
echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf

# Tell NetworkManager to not manage wlan0 (we use hostapd for AP mode)
mkdir -p /etc/NetworkManager/conf.d
cat > /etc/NetworkManager/conf.d/unmanaged-wlan0.conf << 'NMCONF'
[keyfile]
unmanaged-devices=interface-name:wlan0
NMCONF

# Disable hostapd and dnsmasq from auto-starting
# First-boot script will start them after verifying everything
systemctl disable hostapd
systemctl disable dnsmasq

# Build codec2 from source
echo "[7/9] Building codec2 from source..."
cd /tmp
rm -rf codec2
git clone https://github.com/drowe67/codec2.git
cd codec2
mkdir -p build_linux && cd build_linux
cmake .. -DCMAKE_BUILD_TYPE=Release
make -j$(nproc)
make install
ldconfig
cd /
rm -rf /tmp/codec2
echo "codec2 installed:"
ldconfig -p | grep codec2

# Install Python packages for pi user
echo "[8/9] Installing Python packages for pi user..."
export HOME=/home/pi
mkdir -p /home/pi/.local/bin
chown -R pi:pi /home/pi

sudo -u pi bash -c 'export PATH="$PATH:/home/pi/.local/bin"; pipx install rns'
sudo -u pi bash -c 'export PATH="$PATH:/home/pi/.local/bin"; pipx install nomadnet'
# Install freedvtnc2-lfm fork with command interface (port 8002)
sudo -u pi bash -c 'export PATH="$PATH:/home/pi/.local/bin"; pipx install git+https://github.com/LFManifesto/freedvtnc2.git'
sudo -u pi bash -c 'export PATH="$PATH:/home/pi/.local/bin"; pipx runpip freedvtnc2 install audioop-lts' || true

# Set permissions
echo "[9/9] Setting permissions..."
chown -R pi:pi /opt/reticulumhf
chmod +x /opt/reticulumhf/scripts/*.sh

# Clean up
apt-get clean
rm -rf /var/lib/apt/lists/*

echo ""
echo "============================================"
echo "Chroot installation complete!"
echo "============================================"
CHROOT_SCRIPT

sudo chmod +x "$MOUNT_DIR/tmp/install.sh"
sudo chroot "$MOUNT_DIR" /tmp/install.sh

sudo rm -f "$MOUNT_DIR/tmp/install.sh"
sudo rm -f "$MOUNT_DIR/usr/bin/qemu-aarch64-static"

echo "[9/10] Restoring ld.so.preload and unmounting..."

if [ -f "$MOUNT_DIR/etc/ld.so.preload.bak" ]; then
    sudo mv "$MOUNT_DIR/etc/ld.so.preload.bak" "$MOUNT_DIR/etc/ld.so.preload"
fi

sudo umount "$MOUNT_DIR/dev/pts" 2>/dev/null || true
sudo umount "$MOUNT_DIR/dev" 2>/dev/null || true
sudo umount "$MOUNT_DIR/proc" 2>/dev/null || true
sudo umount "$MOUNT_DIR/sys" 2>/dev/null || true
sudo umount "$MOUNT_DIR/boot/firmware" 2>/dev/null || true
sudo umount "$MOUNT_DIR" 2>/dev/null || true
sudo losetup -d "$LOOP_DEV" 2>/dev/null || true
LOOP_DEV=""

echo "[10/10] Compressing image..."
mv "$BUILD_DIR/$OUTPUT_IMAGE" "$OUTPUT_DIR/"
cd "$OUTPUT_DIR"
xz -T0 "$OUTPUT_IMAGE"

echo ""
echo "============================================"
echo "Build complete!"
echo "============================================"
echo "Output: $OUTPUT_DIR/${OUTPUT_IMAGE}.xz"
echo ""
echo "Image contains:"
echo "  - Hostname: reticulumhf"
echo "  - User: pi / Password: reticulumhf"
echo "  - SSH: enabled"
echo "  - WiFi AP: ReticulumHF (hostapd + dnsmasq)"
echo "  - codec2, RNS, NomadNet, freedvtnc2: pre-installed"
echo ""
echo "Flash to SD card with Raspberry Pi Imager"
echo ""
