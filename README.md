# ReticulumHF

Reticulum over HF radio using FreeDV.

## Overview

Runs Reticulum over HF radio using FreeDV DATAC1 mode for modulation. Provides encrypted peer-to-peer communication without internet infrastructure.

Two installation methods:
- **Pre-built Image** - Flash and go. Includes web-based setup wizard and TCP bridge for Sideband.
- **Manual Installation** - Build everything yourself on existing Pi.

## Pre-built Image

Download the SD card image, flash it, boot, configure via web portal.

### Download

[reticulumhf-20260126.img.xz](https://github.com/LFManifesto/ReticulumHF/releases/download/v2.0/reticulumhf-20260126.img.xz)

### Setup

1. Flash to SD card using [Raspberry Pi Imager](https://www.raspberrypi.com/software/) - select "Use custom"
2. Insert SD card, connect radio via Digirig, power on
3. Connect phone to WiFi: **ReticulumHF-Setup** (password: `reticulumhf`)
4. Open **http://192.168.4.1** in browser
5. Complete setup wizard - select radio model, verify audio levels
6. In Sideband, add TCP interface: `192.168.4.1:4242`

### What's Included

- Raspberry Pi OS Bookworm Lite (64-bit)
- RNS with TCP server on port 4242
- freedvtnc2 (FreeDV DATAC1 modem)
- codec2 (built from source)
- Hamlib rigctld
- Web portal with service controls

### Network

| | |
|---|---|
| WiFi SSID | ReticulumHF-Setup |
| WiFi Password | reticulumhf |
| Gateway IP | 192.168.4.1 |
| Sideband/NomadNet Port | 4242 |
| MeshChat Port | 8001 |
| SSH | pi / reticulumhf |

### Client Connections

**Sideband / NomadNet** (port 4242, no KISS framing):

| Setting | Value |
|---------|-------|
| Host | 192.168.4.1 |
| Port | 4242 |
| Interface Type | TCP Client |

**MeshChat** (port 8001, KISS framing required):

| Setting | Value |
|---------|-------|
| Target Host | 192.168.4.1 |
| Target Port | 8001 |
| KISS Framing | YES |
| Interface Mode | Full |
| Inferred Bitrate | 290 (for DATAC1) |

### Audio Tuning

```bash
ssh pi@192.168.4.1
alsamixer
```

- Press F6 to select USB audio device
- Press F4 for capture view
- Set capture 70-80%, playback 40-60%
- Save: `sudo alsactl store`

---

## Manual Installation

Build everything from scratch on a fresh Raspberry Pi OS installation.

### Test Results

Tested with two identical setups. Local test only (73 miles). Last test was in December of 2025.

### Test Environment

| Component | Specification |
|-----------|---------------|
| Computer | Raspberry Pi 4 (4GB) |
| OS | Raspberry Pi OS Lite Bookworm 64-bit |
| Radio | Xiegu G90 (Hamlib model 3088) |
| Interface | Digirig Mobile (USB audio + CAT) |
| Reticulum | 0.8.4 |
| freedvtnc2 | Latest from PyPI |
| codec2 | Built from source |

### Performance Specifications

From codec2 documentation (https://github.com/drowe67/codec2/blob/main/README_data.md):

| Parameter | Value |
|-----------|-------|
| Data rate | 980 bps |
| RF Bandwidth | 1.7 kHz |
| Payload per frame | 510 bytes |
| Frame duration | 4.18 seconds |
| Target SNR | 5 dB |
| Carriers | 27 |

Range depends on HF propagation (ionosphere, solar activity, time of day).

### Architecture

```
+-------------+      +----------+      +-----------+
|  NomadNet   |----->|Reticulum |----->|freedvtnc2 |
|   (LXMF)    |      |  Stack   |      |   (TNC)   |
+-------------+      +----------+      +-----------+
                                             |
                                             v
                                       +---------+
                                       | rigctld |
                                       |  (PTT)  |
                                       +---------+
                                             |
                                             v
                                       +---------+
                                       |Digirig  |
                                       | Audio+  |
                                       |  CAT    |
                                       +---------+
                                             |
                                             v
                                       +---------+
                                       | G90 HF  |
                                       |  Radio  |
                                       +---------+
```

Work Flow:
1. User writes message in NomadNet
2. Reticulum encrypts packet with recipient's public key
3. freedvtnc2 modulates to FreeDV DATAC1 audio
4. rigctld keys radio, audio transmits over HF
5. Remote station decodes and delivers to NomadNet

### Step 1: System Preparation

Flash Raspberry Pi OS Lite (Bookworm, 64-bit). Enable SSH.

```bash
ssh user@<pi-ip-address>
sudo apt update && sudo apt upgrade -y
```

### Step 2: Install Dependencies

```bash
sudo apt install -y git build-essential cmake python3 python3-pip python3-venv \
    portaudio19-dev alsa-utils libhamlib-utils libhamlib-dev pipx
pipx ensurepath
source ~/.bashrc
```

### Step 3: Build Codec2

```bash
cd ~
git clone https://github.com/drowe67/codec2.git
cd codec2
mkdir build_linux && cd build_linux
cmake ..
make
sudo make install
sudo ldconfig
```

Verify:
```bash
ldconfig -p | grep codec2
```

### Step 4: Install Reticulum Stack

```bash
pipx install rns
pipx install nomadnet
pipx install freedvtnc2
pipx runpip rns install numpy pyaudio scipy
pipx runpip nomadnet install numpy pyaudio scipy
```

Initialize Reticulum config:
```bash
rnsd --config ~/.reticulum &
sleep 3
pkill rnsd
```

### Step 5: Connect Hardware

1. Connect Digirig to Pi via USB
2. Connect Digirig audio cable to G90 ACC port
3. Connect Digirig CAT cable to G90 CAT port
4. Power on G90

### Step 6: Verify Hardware

```bash
# Check USB devices
lsusb
# Expected: Silicon Labs CP210x (CAT) and C-Media CM108 (audio)

# Check audio
arecord -l
# Expected: USB PnP Sound Device

# Check serial
ls -la /dev/ttyUSB*
# Expected: /dev/ttyUSB0

# Add user to dialout group
sudo usermod -a -G dialout $USER
# Log out and back in
```

### Step 7: Test Radio CAT

```bash
rigctl -m 3088 -r /dev/ttyUSB0 -s 19200 f
```

Expected: Returns current frequency (e.g., `7100000`).

### Step 8: Configure G90 Audio Input

On the G90:
1. Press FUNC
2. Press POW until you see "INPUT"
3. Use knob to select LINE (not MIC)

Set ALSA levels (adjust card number as needed):
```bash
amixer -c 3 sset 'Speaker' 64%
amixer -c 3 sset 'Mic',0 cap 75%
amixer -c 3 sset 'Mic' unmute
sudo alsactl store
```

### Step 9: Configure Reticulum

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

### Step 10: Start the Stack

Terminal 1 - Start rigctld and freedvtnc2:
```bash
rigctld -m 3088 -r /dev/ttyUSB0 -s 19200 -t 4532 -P RTS &

freedvtnc2 --input-device 1 --output-device 1 --mode DATAC1 \
    --rigctld-port 4532 --kiss-tcp-port 8001 --kiss-tcp-address 0.0.0.0 \
    --ptt-on-delay-ms 300 --ptt-off-delay-ms 200 --output-volume -3
```

Terminal 2 - Start NomadNet:
```bash
nomadnet
```

### Step 11: Test Transmission

In NomadNet:
1. Select Network tab
2. Send an announcement

Radio should key up for 3-5 seconds and transmit.

---

## Troubleshooting

### Hardware Verification

```bash
lsusb                      # List USB devices
arecord -l                 # List audio devices
ls -la /dev/ttyUSB*        # List serial ports
```

### CAT/PTT Test

```bash
rigctl -m 3088 -r /dev/ttyUSB0 -s 19200 T 1  # Key transmitter
rigctl -m 3088 -r /dev/ttyUSB0 -s 19200 T 0  # Unkey transmitter
```

### Reticulum Status

```bash
rnstatus                        # Interface status
tail -f ~/.reticulum/logfile    # Live log
```

### Common Issues

**No /dev/ttyUSB0:**
- Check USB cable connection
- Verify Digirig is powered (LED lit)
- Run `dmesg | tail` after plugging in

**CAT command fails:**
- Verify baud rate matches radio (G90 uses 19200)
- Check user is in dialout group
- Try `rigctl -m 3088 -r /dev/ttyUSB0 -s 19200 f`

**No audio device:**
- Run `arecord -l` to find device number
- Adjust --input-device and --output-device in freedvtnc2 command

**Radio not keying:**
- Test PTT manually: `rigctl -m 3088 -r /dev/ttyUSB0 -s 19200 T 1`
- Check G90 is in correct mode (USB-D for data)
- Verify rigctld is running on port 4532

**WiFi AP not visible (pre-built image):**
- Wait 60 seconds after power on
- Check Pi LEDs (solid green = booted)
- Power cycle Pi

**Sideband won't connect (pre-built image):**
- Verify connected to ReticulumHF-Setup WiFi
- Check rnsd running: `ssh pi@192.168.4.1 "systemctl status reticulumhf-rnsd"`
- Verify port 4242: `ssh pi@192.168.4.1 "ss -tlnp | grep 4242"`

## Verified Hardware

| Component | Model | Notes |
|-----------|-------|-------|
| Computer | Raspberry Pi 4 (4GB) | |
| Radio | Xiegu G90 | Hamlib 3088, PTT via RTS |
| Radio | Yaesu FT-818 | Hamlib 1020 (use FT-817), PTT via RTS |
| Radio | (tr)uSDX | VOX mode (no CAT PTT) |
| Interface | Digirig Mobile | CAT + Audio |
| Interface | Digirig Lite | Audio only (use VOX) |
| Phone App | Sideband (Android) | Port 4242 |
| Desktop App | MeshChat | Port 8001, KISS framing |

## References

Software:
- Reticulum Manual: https://markqvist.github.io/Reticulum/manual/
- codec2 Data Modes: https://github.com/drowe67/codec2/blob/main/README_data.md
- freedvtnc2: https://github.com/xssfox/freedvtnc2
- Sideband: https://github.com/markqvist/Sideband
- MeshChat: https://github.com/liamcottle/meshtastic-meshchat
- NomadNet: https://github.com/markqvist/NomadNet
- Hamlib Supported Radios: https://github.com/Hamlib/Hamlib/wiki/Supported-Radios

Propagation:
- prop.kc2g.com: https://prop.kc2g.com/ - Real-time MUF/foF2 maps
- VOACAP Online: https://www.voacap.com/hf/ - HF propagation prediction
- HamQSL Solar Data: https://www.hamqsl.com/solar.html - Current solar indices

## License

MIT License - See LICENSE file

## Author

Light Fighter Manifesto L.L.C.
https://lightfightermanifesto.org
