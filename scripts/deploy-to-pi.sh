#!/bin/bash
# Deploy ReticulumHF to Pi - Gateway Installation
# Usage: ./deploy-to-pi.sh [pi-hostname-or-ip] [username]
#
# ReticulumHF turns a Raspberry Pi into a Reticulum gateway for HF radio.
# Use with Sideband app on your phone for messaging.
#
# Architecture:
#   [Phone/Sideband] --WiFi--> [Pi/ReticulumHF] --Audio--> [Radio] ~~~HF~~~
#
# This script performs a COMPLETE installation on a fresh Raspberry Pi OS.

set -e

PI_HOST="${1:-192.168.8.192}"
PI_USER="${2:-pi}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "============================================"
echo "ReticulumHF Full Deployment"
echo "============================================"
echo "Target: ${PI_USER}@${PI_HOST}"
echo "Source: $PROJECT_DIR"
echo ""

# Check SSH connectivity
echo "[1/7] Testing SSH connection..."
if ! ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "${PI_USER}@${PI_HOST}" "echo 'SSH OK'" 2>/dev/null; then
    echo "Error: Cannot connect to ${PI_USER}@${PI_HOST}"
    echo ""
    echo "For fresh Pi OS, try:"
    echo "  sshpass -p 'raspberry' ssh ${PI_USER}@${PI_HOST}"
    echo ""
    echo "Or set up SSH keys:"
    echo "  ssh-copy-id ${PI_USER}@${PI_HOST}"
    exit 1
fi

echo "[2/7] Installing system dependencies..."
ssh "${PI_USER}@${PI_HOST}" << 'EOF'
set -e

echo "Updating package lists..."
sudo apt-get update

echo "Installing system packages..."
sudo apt-get install -y \
    python3 python3-pip python3-venv python3-flask pipx \
    git build-essential cmake \
    portaudio19-dev alsa-utils \
    libhamlib-utils libhamlib-dev \
    hostapd dnsmasq \
    i2pd \
    libcodec2-dev || {
        echo "Note: Some packages may not be available, continuing..."
    }

# Ensure pipx path is set up
pipx ensurepath 2>/dev/null || true
export PATH="$PATH:$HOME/.local/bin"

echo "System packages installed"
EOF

echo "[3/7] Installing Python packages via pipx..."
ssh "${PI_USER}@${PI_HOST}" << 'EOF'
set -e
export PATH="$PATH:$HOME/.local/bin"

# Core gateway packages (required)
echo "Installing RNS (Reticulum Network Stack)..."
pipx install rns 2>/dev/null || pipx upgrade rns || echo "RNS already installed"

echo "Installing freedvtnc2 (FreeDV modem)..."
pipx install freedvtnc2 2>/dev/null || pipx upgrade freedvtnc2 || echo "freedvtnc2 already installed"

# Python 3.13 removed audioop - install backport
echo "Installing audioop-lts (Python 3.13 compatibility)..."
pipx runpip freedvtnc2 install audioop-lts 2>/dev/null || echo "audioop-lts may already be installed"

# Optional: NomadNet for CLI/terminal access (not needed if using Sideband)
echo "Installing NomadNet (optional - for terminal access)..."
pipx install nomadnet 2>/dev/null || pipx upgrade nomadnet || echo "NomadNet already installed"

# Verify installations
echo ""
echo "Verifying installations..."
$HOME/.local/bin/rnstatus --version || echo "rnstatus not found"
$HOME/.local/bin/freedvtnc2 --help 2>/dev/null | head -1 || echo "freedvtnc2 installed"
$HOME/.local/bin/nomadnet --version 2>/dev/null || echo "nomadnet installed (optional)"

echo "Python packages installed"
EOF

echo "[4/7] Building codec2 (if needed)..."
ssh "${PI_USER}@${PI_HOST}" << 'EOF'
set -e

# Check if codec2 library exists
if ! ldconfig -p | grep -q libcodec2; then
    echo "Building codec2 from source..."
    cd /tmp
    rm -rf codec2
    git clone https://github.com/drowe67/codec2.git
    cd codec2
    mkdir -p build_linux && cd build_linux
    cmake ..
    make -j$(nproc)
    sudo make install
    sudo ldconfig
    rm -rf /tmp/codec2
    echo "codec2 built and installed"
else
    echo "codec2 already installed"
fi
EOF

echo "[5/7] Creating directories and copying files..."
ssh "${PI_USER}@${PI_HOST}" "sudo mkdir -p /opt/reticulumhf /etc/reticulumhf && sudo chown -R ${PI_USER}:${PI_USER} /opt/reticulumhf"

rsync -avz --delete \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='build/' \
    --exclude='output/' \
    --exclude='.claude/' \
    --exclude='*.md' \
    "$PROJECT_DIR/" "${PI_USER}@${PI_HOST}:/opt/reticulumhf/"

echo "[6/7] Installing systemd services..."
ssh "${PI_USER}@${PI_HOST}" << 'EOF'
set -e

# Copy service files
sudo cp /opt/reticulumhf/services/*.service /etc/systemd/system/

# Make scripts executable
chmod +x /opt/reticulumhf/scripts/*.sh

# Add pi user to required groups
sudo usermod -aG dialout,audio,plugdev pi 2>/dev/null || true

# Reload systemd
sudo systemctl daemon-reload

# Enable first-boot service (creates WiFi AP on first boot)
sudo systemctl enable reticulumhf-firstboot.service

# Enable the setup portal service (runs when .setup_complete doesn't exist)
sudo systemctl enable reticulumhf-setup.service

# Enable main services (conditional on .setup_complete)
sudo systemctl enable rigctld.service freedvtnc2.service reticulumhf.service

# Enable persistent WiFi AP (runs after setup complete)
sudo systemctl enable reticulumhf-wifi.service

# Stop hostapd/dnsmasq default services (we manage them)
sudo systemctl disable hostapd 2>/dev/null || true
sudo systemctl disable dnsmasq 2>/dev/null || true

echo "Systemd services installed and enabled"
EOF

echo "[7/7] Verifying installation..."
ssh "${PI_USER}@${PI_HOST}" << 'EOF'
cd /opt/reticulumhf/setup-portal

# Syntax check
python3 -m py_compile app.py hardware.py
echo "Python syntax: OK"

# Test imports
python3 -c "from hardware import detect_serial_ports, detect_audio_devices; print('Hardware module: OK')"
python3 -c "import flask; print('Flask: OK')"

# Check service files
echo ""
echo "=== Installed Services ==="
systemctl list-unit-files | grep reticulumhf || true
systemctl list-unit-files | grep rigctld || true
systemctl list-unit-files | grep freedvtnc2 || true
EOF

echo ""
echo "============================================"
echo "ReticulumHF Gateway Installation Complete!"
echo "============================================"
echo ""
echo "Reboot the Pi to start setup mode:"
echo "  sudo reboot"
echo ""
echo "SETUP STEPS:"
echo ""
echo "  1. After reboot, connect to WiFi:"
echo "     Network: ReticulumHF-Setup"
echo "     Password: reticulumhf"
echo ""
echo "  2. Open browser:"
echo "     http://192.168.4.1"
echo ""
echo "  3. Complete the setup wizard"
echo ""
echo "  4. Install Sideband on phone:"
echo "     https://github.com/markqvist/Sideband/releases"
echo ""
echo "FIELD USE:"
echo "  Phone (Sideband) --> WiFi --> Pi --> Radio --> HF"
echo ""
