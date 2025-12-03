# LFM-HF-Reticulum

**Censorship-Resistant Communications: Reticulum Network Stack over HF Radio**

*"When traditional communication channels are compromised, surveilled, or shut down entirely, HF radio remains. When the internet is filtered, blocked, or monitored, mesh networks persist. This project combines both."*

## Overview

LFM-HF-Reticulum enables encrypted, peer-to-peer communication over high-frequency radio using the Reticulum Network Stack and FreeDV digital modes. This system operates independently of traditional internet infrastructure, making it resilient against:

- Network shutdowns and internet blackouts
- Mass surveillance and traffic analysis  
- Centralized control and censorship
- Infrastructure compromise

**Core Principle**: Your communication should be private, unstoppable, and controlled by you - not by corporations, governments, or intermediaries.

## Why This Matters

### The Surveillance Reality

Modern communication infrastructure is inherently compromised:
- Centralized services log, analyze, and monetize your data
- State-level actors conduct mass surveillance on digital communications
- Internet shutdowns are increasingly common during civil unrest
- End-to-end encryption is under constant legal and technical attack

### The HF Radio

HF radio provides:
- **No infrastructure dependency**: Direct station-to-station communication
- **Long-range capability**: 50-5000+ km without repeaters
- **Difficult to block**: Requires physical radio jamming
- **Difficult to trace**: No source addressing in Reticulum packets

### The Reticulum Advantage

Reticulum Network Stack adds:
- **Strong cryptography**: X25519 + Ed25519, forward secrecy
- **Initiator anonymity**: No source addresses in packets
- **Mesh routing**: Automatic path discovery and healing
- **Store-and-forward**: Asynchronous messaging via LXMF
- **Interoperability**: Connect HF to I2P, LoRa, or other mediums

### System Components

```
┌─────────────┐      ┌──────────┐      ┌───────── ┐
│  NomadNet   │─────▶│Reticulum │─────▶│freedvtnc2│
│   (LXMF)    │      │  Stack   │      │  (TNC)   │
└─────────────┘      └──────────┘      └───────── ┘
                                              │
                                              ▼
                                        ┌─────────┐
                                        │ rigctld │
                                        │  (PTT)  │
                                        └─────────┘
                                              │
                                              ▼
                                        ┌─────────┐
                                        │Digirig  │
                                        │ Audio+  │
                                        │  CAT    │
                                        └─────────┘
                                              │
                                              ▼
                                        ┌─────────┐
                                        │ G90 HF  │
                                        │  Radio  │
                                        └─────────┘
```

### Data Flow

1. **Compose**: User writes message in NomadNet (terminal UI)
2. **Encrypt**: Reticulum encrypts packet with recipient's public key
3. **Route**: Reticulum determines path through network
4. **Modulate**: freedvtnc2 converts to FreeDV DATAC1 audio (OFDM)
5. **Transmit**: rigctld keys radio, audio sent over HF
6. **Receive**: Remote station decodes, Reticulum decrypts, delivers to NomadNet

All packets are encrypted end-to-end. No intermediate node can read content.

## Tested Hardware

**This configuration has been verified working:**

- **Computer**: Raspberry Pi 4 (4GB RAM recommended)
- **OS**: Raspberry Pi OS Lite (Bookworm, 64-bit)
- **Radio**: Xiegu G90 HF Transceiver
- **Interface**: Digirig Mobile (USB audio + CAT)
- **Antenna**: User-dependent (dipole, EFHW, vertical, etc.)

**Other radios should work** if they:
- Support CAT control via Hamlib (check [supported radios](https://github.com/Hamlib/Hamlib/wiki/Supported-Radios))
- Can interface with Digirig or similar sound card interface

## Installation Guide

This step-by-step procedure has been tested and verified. Follow it exactly.

### Prerequisites

- Raspberry Pi 4 (4GB+ RAM)
- MicroSD card (16GB+)
- Raspberry Pi OS Lite (Bookworm, 64-bit) flashed to SD
- SSH enabled
- Xiegu G90 + Digirig + cables
- Basic Linux command line familiarity

### Step 1: System Preparation

Flash Raspberry Pi OS Lite (Bookworm) to SD card. Enable SSH during setup.

SSH into the Pi:

```bash
ssh user@<pi-ip-address>
```

Update system:

```bash
sudo apt update && sudo apt upgrade -y
```

### Step 2: Install System Dependencies

```bash
sudo apt install -y git build-essential cmake python3 python3-pip python3-venv portaudio19-dev alsa-utils libhamlib-utils libhamlib-dev pipx
```

Configure pipx PATH:

```bash
pipx ensurepath
source ~/.bashrc
```

### Step 3: Build Codec2 from Source

```bash
cd ~
git clone https://github.com/drowe67/codec2.git
cd codec2
mkdir build_linux
cd build_linux
cmake ..
make
sudo make install
sudo ldconfig
```

Verify installation:

```bash
ldconfig -p | grep codec2
```

Should show `libcodec2.so` in `/usr/local/lib/`

### Step 4: Install Reticulum and NomadNet

```bash
cd ~
pipx install rns
pipx install nomadnet
```

Install Python dependencies:

```bash
pipx runpip rns install numpy pyaudio scipy
pipx runpip nomadnet install numpy pyaudio scipy
```

Initialize Reticulum:

```bash
rnsd --config ~/.reticulum &
sleep 3
pkill rnsd
```

This creates `~/.reticulum/config` with default settings.

### Step 5: Install freedvtnc2

```bash
pipx install freedvtnc2
```

### Step 6: Connect Hardware

1. Connect Digirig to Raspberry Pi via USB
2. Connect Digirig to G90:
   - Audio cable to ACC port
   - CAT cable to CAT port
3. Power on G90

### Step 7: Verify Hardware Detection

Check USB devices:

```bash
lsusb
```

Should show:
- `Silicon Labs CP210x UART Bridge` (CAT interface)
- `C-Media Electronics, Inc. CM108 Audio Controller` (Digirig audio)

Check audio devices:

```bash
arecord -l
aplay -l
```

Should show `USB PnP Sound Device` as card 3 (may vary).

Check serial port:

```bash
ls -la /dev/ttyUSB*
```

Should show `/dev/ttyUSB0` (CAT connection).

Add user to dialout group:

```bash
sudo usermod -a -G dialout $USER
```

**Log out and back in** for group change to take effect.

### Step 8: Test Radio CAT Connection

```bash
rigctl -m 3088 -r /dev/ttyUSB0 -s 19200 f
```

Should return current radio frequency (e.g., `7100000`). Radio will briefly key.

### Step 9: Configure Audio Levels

List audio devices in freedvtnc2:

```bash
freedvtnc2 --list-audio-devices
```

Note the device ID for "USB PnP Sound Device" (typically device 1).

Test freedvtnc2 without PTT:

```bash
freedvtnc2 --input-device 1 --output-device 1 --mode DATAC1 --rigctld-port 0
```

Should start without errors. Press Ctrl+C to stop.

**Configure G90 Audio Input:**

On the G90, you must set the audio input to LINE (not MIC) for Digirig operation:

1. Press **FUNC** button
2. Press **POW** button repeatedly until you see "INPUT"
3. Use main knob to switch from "MIC" to **LINE**

**Set ALSA audio levels:**

```bash
amixer -c 3 sset 'Speaker' 64%
amixer -c 3 sset 'Mic',0 cap 75%
amixer -c 3 sset 'Mic' unmute
sudo alsactl store
```

Verify:

```bash
amixer -c 3
```

### Step 10: Configure Reticulum

Edit `~/.reticulum/config`:

**For Field/Remote Station** (no internet):

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

**For Base Station** (with I2P internet bridge):

```ini
[reticulum]
  enable_transport = yes

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

  [[Lightfighter I2P]]
    type = I2PInterface
    enabled = yes
    connectable = yes
    peers = kfamlmwnlw3acqfxip4x6kt53i2tr4ksp5h4qxwvxhoq7mchpolq.b32.i2p
```

### Step 11: Operating Procedure

**Terminal 1** - Start rigctld and freedvtnc2:

```bash
rigctld -m 3088 -r /dev/ttyUSB0 -s 19200 --set-conf=serial_handshake=None,rts_state=OFF,dtr_state=OFF &
freedvtnc2 --input-device 1 --output-device 1 --mode DATAC1 --rigctld-port 4532 --ptt-on-delay-ms 300 --ptt-off-delay-ms 200 --output-volume -3
```

**Terminal 2** - Start NomadNet:

```bash
nomadnet
```

### Step 12: First Transmission Test

In NomadNet:
1. Select Network tab
2. Send an announcement

Radio should key up for 3-5 seconds and transmit. Watch freedvtnc2 terminal for PTT status.

## Optional: I2P Installation (Base Station Only)

For base stations that bridge HF to internet:

```bash
sudo apt update
sudo apt install -y apt-transport-https curl
curl -o i2p-archive-keyring.gpg https://geti2p.net/_static/i2p-archive-keyring.gpg
gpg --keyid-format long --import --import-options show-only --with-fingerprint i2p-archive-keyring.gpg
sudo cp i2p-archive-keyring.gpg /usr/share/keyrings/
echo "deb [signed-by=/usr/share/keyrings/i2p-archive-keyring.gpg] https://deb.i2p.net/ bookworm main" | sudo tee /etc/apt/sources.list.d/i2p.list
sudo apt update
sudo apt install -y i2p i2p-keyring
sudo systemctl enable i2p
sudo systemctl start i2p
```

I2P requires 5-10 minutes to build tunnels after first start.

Console available at: `http://localhost:7657`

## Performance Characteristics

**FreeDV DATAC1 Mode:**
- Data rate: 980 bits/second
- Bandwidth: 1700 Hz
- Frame size: 510 bytes
- Frame duration: 4.18 seconds
- Range: 50-5000+ km (band/propagation dependent)
- Power: 1-10W typical

**Reticulum Packet Overhead:**
- Header + Address: 18-34 bytes (depending on packet type)
- Destination hash: 16 bytes
- Link establishment: 297 bytes (3 packets)
- Efficient for messages, not large file transfers. Use brevity and prowords. 

## Operational Security Guidance

### DO:
- Use from non-attributable locations when needed
- Understand that HF transmission can be direction-found
- Use I2P for internet bridging (adds network-layer anonymity)
- Rotate operating frequencies
- Use short transmissions when OPSEC is critical
- Understand your local regulations regarding encryption

### DON'T:
- Transmit from home if location must remain private
- Use same frequency/time patterns repeatedly  
- Assume HF provides location anonymity (it doesn't)
- Ignore local radio regulations
- Transmit unnecessarily (increases exposure)

**Quick Diagnostics:**

```bash
# Check hardware
lsusb
arecord -l
ls -la /dev/ttyUSB*

# Test CAT/PTT (model 3088 = Xiegu G90)
rigctl -m 3088 -r /dev/ttyUSB0 -s 19200 T 1  # Key radio
rigctl -m 3088 -r /dev/ttyUSB0 -s 19200 T 0  # Unkey

# Check Reticulum
rnstatus

# View logs
tail -f ~/.reticulum/logfile
```

## Contributing

This project documents a working configuration. Contributions welcome for:
- Testing with other radio models
- Performance optimization
- Documentation improvements
- Regional regulatory guidance

**Pull requests must be tested** before submission.

## License

MIT License - See [LICENSE](LICENSE) file.

## Acknowledgments

- **Mark Qvist** - [Reticulum Network Stack](https://reticulum.network/)
- **David Rowe (VK5DGR)** - [Codec2/FreeDV](https://freedv.org/)
- **xssfox** - [freedvtnc2](https://github.com/xssfox/freedvtnc2)
- **Hamlib Team** - [Radio control library](https://hamlib.github.io/)

## Contact

**Light Fighter Manifesto**  
GitHub: [@LFManifesto](https://github.com/LFManifesto)

## Disclaimer

This project is for research, education, and emergency preparedness. Users are responsible for:
- Compliance with local amateur radio regulations
- Understanding encryption restrictions in their jurisdiction
- Proper licensing and identification
- Operational security appropriate to their threat model

**The authors assume no liability for misuse or regulatory violations.**

*"The best time to build resilient communication systems is before you need them."*
