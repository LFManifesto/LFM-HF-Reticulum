# ReticulumHF

**v0.3.0-alpha** - Reticulum over HF radio using FreeDV.

## Overview

Runs Reticulum over HF radio using FreeDV data modes. Provides encrypted peer-to-peer communication without internet infrastructure.

**Key Features:**
- Web-based setup wizard - no command line required
- Live RX audio level from modem
- Instant mode and volume changes (no service restart)
- FreeDV mode selection based on band conditions
- VOX mode support for audio-only interfaces
- Works with Sideband, MeshChat, NomadNet

**Tested Radios:**
- Xiegu G90 (Digirig Mobile)
- Yaesu FT-818 (Digirig Mobile)
- (tr)uSDX (Digirig Lite + VOX)

## Pre-built Image

Download the SD card image, flash it, boot, configure via web portal.

### Download

[reticulumhf-v0.3.0-alpha.img.xz](https://github.com/LFManifesto/ReticulumHF/releases)

### Quick Start

1. Flash to SD card using [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. Insert SD card, connect radio via Digirig, power on
3. Connect to WiFi: **ReticulumHF** (password: `reticulumhf`)
4. Open **http://192.168.4.1** in browser
5. Complete 6-step setup wizard
6. Connect Sideband to `192.168.4.1:4242`

### Setup Wizard (6 Steps)

1. **Select Radio** - Choose from supported radios (tested radios marked)
2. **Radio Settings** - Configure your radio with the displayed menu values
3. **Hardware Detection** - Automatically detects Digirig, serial port, audio device
4. **Test Connection** - Verify CAT and PTT (or check "Using VOX mode" for audio-only)
5. **Network Security** - Optional IFAC encryption for the gateway
6. **Start Gateway** - Launches services and redirects to status page

### Status Page Features

- **Live RX Level** - Real-time audio level from modem (updates every second)
  - Target: -15 to -3 dB for good signal
  - Below -35 dB indicates quiet/no signal
- **TX Volume Control** - Adjustable output volume (-20 to 0 dB)
  - Reduce if radio ALC meter is high or power fluctuates
- **Instant Mode Changes** - Switch FreeDV modes without service restart
- **FreeDV Mode Selection** - DATAC1 (fast), DATAC3 (balanced), DATAC4 (robust)
- **Service Controls** - Restart/view logs for rnsd, rigctld, freedvtnc2
- **Troubleshooting Guide** - Level adjustment tips and common fixes

### FreeDV Modes

| Mode | Bitrate | Min SNR | Use Case |
|------|---------|---------|----------|
| DATAC1 | 290 bps | 5 dB | Default - good conditions |
| DATAC3 | 124 bps | 0 dB | Moderate conditions |
| DATAC4 | 87 bps | -4 dB | Poor conditions, weak signals |

**IMPORTANT: All stations must use the same FreeDV mode to communicate. Coordinate with other operators before changing modes.**

### Network Configuration

| Setting | Value |
|---------|-------|
| WiFi SSID | ReticulumHF |
| WiFi Password | reticulumhf |
| Gateway IP | 192.168.4.1 |
| Web Portal | http://192.168.4.1 |
| RNS Gateway Port | 4242 (all clients) |
| SSH | pi / reticulumhf |

### Client Connections

All clients connect using **TCP Client Interface** on port **4242**.

**Sideband / Columba:**
Settings → Connectivity → Add Interface → TCP Client

| Setting | Value |
|---------|-------|
| Interface Type | TCP Client Interface |
| Address | 192.168.4.1 |
| Port | 4242 |

**MeshChat:**
Settings → Interfaces → Add → TCP Client Interface

| Setting | Value |
|---------|-------|
| Interface Type | TCP Client Interface |
| Target Host | 192.168.4.1 |
| Target Port | 4242 |

**Note:** If IFAC is configured on the gateway, clients must use matching IFAC Name and Passphrase.

### What's Included

- Raspberry Pi OS Bookworm Lite (64-bit)
- RNS (Reticulum Network Stack)
- freedvtnc2 (FreeDV modem)
- codec2 (built from source)
- Hamlib rigctld (CAT/PTT control)
- Web portal with setup wizard and status dashboard

---

## Verified Hardware

| Component | Model | Notes |
|-----------|-------|-------|
| Computer | Raspberry Pi 4 (4GB) | Recommended |
| Radio | Xiegu G90 | **TESTED** - Hamlib 3088, PTT via RTS |
| Radio | Yaesu FT-818 | **TESTED** - Hamlib 1020 (FT-817), PTT via RTS |
| Radio | (tr)uSDX | **TESTED** - VOX mode, Digirig Lite |
| Interface | Digirig Mobile | CAT + Audio (recommended) |
| Interface | Digirig Lite | Audio only - use VOX mode |
| Phone App | Sideband (Android/iOS) | Port 4242 |
| Desktop App | MeshChat | Port 8001, KISS framing |

### Radio-Specific Notes

**Xiegu G90:**
- Mode: USB-D (USB Digital)
- AGC: OFF
- AUX IN: 10, AUX OUT: 13
- Hamlib model: 3088, Baud: 19200

**Yaesu FT-818:**
- Mode: USB or DIG
- Use Hamlib model 1020 (FT-817), NOT 1041
- Baud: 4800
- Set appropriate menu items for DATA input

**(tr)uSDX:**
- Mode: USB
- Enable VOX (Menu 3.1: ON)
- Adjust Noise Gate (Menu 3.2) for reliable PTT
- Volume (Menu 1.1): 8-10
- TX Drive (Menu 3.3): 4
- Requires Digirig Lite or similar audio interface

---

## Manual Installation

For building from scratch on existing Raspberry Pi OS.

### Prerequisites

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git build-essential cmake python3 python3-pip python3-venv \
    portaudio19-dev alsa-utils libhamlib-utils libhamlib-dev pipx
pipx ensurepath
source ~/.bashrc
```

### Build Codec2

```bash
cd ~
git clone https://github.com/drowe67/codec2.git
cd codec2 && mkdir build_linux && cd build_linux
cmake .. && make && sudo make install && sudo ldconfig
```

### Install Reticulum Stack

```bash
pipx install rns
pipx install nomadnet
pipx install freedvtnc2
pipx runpip rns install numpy pyaudio scipy
```

### Configure Reticulum

Edit `~/.reticulum/config`:

```ini
[reticulum]
  enable_transport = no

[interfaces]
  [[Default Interface]]
    type = AutoInterface
    enabled = yes

  [[FreeDV HF]]
    type = TCPClientInterface
    enabled = yes
    target_host = 127.0.0.1
    target_port = 8001
    kiss_framing = yes
```

### Start Services

```bash
# Start rigctld (for CAT-controlled PTT)
rigctld -m 3088 -r /dev/ttyUSB0 -s 19200 -t 4532 -P RTS &

# Start freedvtnc2
freedvtnc2 --input-device 1 --output-device 1 --mode DATAC1 \
    --rigctld-port 4532 --kiss-tcp-port 8001 --kiss-tcp-address 0.0.0.0 \
    --ptt-on-delay-ms 300 --ptt-off-delay-ms 200 --output-volume 0

# For VOX mode (no CAT), use --rigctld-port 0
# Reduce --output-volume to -6 or lower if radio ALC activates (power fluctuates)
```

---

## Troubleshooting

### Hardware Check

```bash
lsusb                      # List USB devices
arecord -l                 # List audio devices
ls -la /dev/ttyUSB*        # List serial ports
```

### CAT/PTT Test

```bash
rigctl -m 3088 -r /dev/ttyUSB0 -s 19200 f    # Get frequency
rigctl -m 3088 -r /dev/ttyUSB0 -s 19200 T 1  # Key transmitter
rigctl -m 3088 -r /dev/ttyUSB0 -s 19200 T 0  # Unkey
```

### Common Issues

**Radio power fluctuates during TX (4-10W instead of steady 10W):**
- ALC is activating due to TX audio being too hot
- Reduce TX Audio Level on status page (try -8 to -12 dB)

**Audio in use by modem:**
- Status page shows "Audio in use by modem" - click to temporarily stop modem for level check
- Modem automatically restarts after 5 seconds

**No serial port detected:**
- Check USB cable connection
- Verify Digirig LED is lit
- Run `dmesg | tail` after plugging in

**Radio not keying:**
- Verify rigctld is running: `systemctl status rigctld`
- Test PTT manually: `rigctl -m 3088 -r /dev/ttyUSB0 -s 19200 T 1`
- For VOX: check radio VOX settings and audio levels

**Can't connect from phone:**
- Verify connected to ReticulumHF WiFi
- Check interface settings match (TCP Client, 192.168.4.1:4242)
- Verify IFAC settings match if configured

**Can't decode signals:**
- Verify both stations using same FreeDV mode
- Check audio levels: target -10 to -5 dB on signals
- Try DATAC4 for weak signal conditions

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Client (Sideband/MeshChat/NomadNet)                            │
│  TCP Client Interface → 192.168.4.1:4242                        │
└─────────────────────────────┬───────────────────────────────────┘
                              │ WiFi (TCP:4242)
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Raspberry Pi (ReticulumHF Gateway)                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │    rnsd     │──│ freedvtnc2  │──│  rigctld    │              │
│  │  (TCP:4242) │  │  (TCP:8001) │  │  (TCP:4532) │              │
│  │  boundary   │  │  FreeDV     │  │  CAT/PTT    │              │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
└─────────────────────────────┬───────────────────────────────────┘
                              │ USB (Audio + CAT)
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Digirig Mobile/Lite                                            │
└─────────────────────────────┬───────────────────────────────────┘
                              │ Audio + PTT
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  HF Radio (G90 / FT-818 / truSDX)                               │
└─────────────────────────────────────────────────────────────────┘
```

---

## References

- [Reticulum Manual](https://markqvist.github.io/Reticulum/manual/)
- [codec2 Data Modes](https://github.com/drowe67/codec2/blob/main/README_data.md)
- [freedvtnc2](https://github.com/xssfox/freedvtnc2)
- [Sideband](https://github.com/markqvist/Sideband)
- [MeshChat](https://github.com/liamcottle/meshtastic-meshchat)
- [Hamlib Supported Radios](https://github.com/Hamlib/Hamlib/wiki/Supported-Radios)

## License

MIT License - See LICENSE file

## Author

Light Fighter Manifesto L.L.C.
https://lightfightermanifesto.org
