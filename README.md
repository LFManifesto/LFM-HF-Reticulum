# ReticulumHF

**v0.3.1-alpha** - Reticulum networking over HF radio using FreeDV data modes.

## What It Does

Raspberry Pi gateway that bridges Reticulum networks over HF radio. Provides encrypted communication without internet infrastructure.

- Web setup wizard and operator dashboard
- JS8Call integration (station discovery)
- TAK/CoT push (map markers)
- Hybrid beacon/ARQ protocol
- FreeDV modes: DATAC4 (beacon), DATAC1 (data transfer)

## Quick Start

1. Flash [image](https://github.com/LFManifesto/ReticulumHF/releases) to SD card
2. Boot Pi, connect radio via Digirig
3. Connect to WiFi: `ReticulumHF` / `reticulumhf`
4. Open `http://192.168.4.1`
5. Complete setup wizard
6. Connect clients to `192.168.4.1:4242` (TCP Client Interface)

## Network

| Setting | Value |
|---------|-------|
| WiFi | ReticulumHF / reticulumhf |
| Web Portal | http://192.168.4.1 |
| Client Port | 4242 (TCP Client Interface) |
| SSH | pi / reticulumhf |

## Tested Hardware

| Radio | Interface | Notes |
|-------|-----------|-------|
| Xiegu G90 | Digirig Mobile | Hamlib 3088, 19200 baud |
| Yaesu FT-818 | Digirig Mobile | Hamlib 1020, 4800 baud |
| (tr)uSDX | Digirig Lite | VOX mode |

## FreeDV Modes

| Mode | Bitrate | Min SNR | Use |
|------|---------|---------|-----|
| DATAC4 | 87 bps | -4 dB | Beacons, weak signals |
| DATAC1 | 980 bps | 3 dB | Data transfer |

All stations must use the same mode to communicate.

## Architecture

```
Client (Sideband/MeshChat) → WiFi → Pi Gateway → Digirig → HF Radio → RF
                            4242      8001/8002
```

- Port 4242: Reticulum gateway (boundary mode)
- Port 8001: freedvtnc2 KISS
- Port 8002: freedvtnc2 command interface

## Manual Install

```bash
# Prerequisites
sudo apt install -y git build-essential cmake python3-pip pipx portaudio19-dev

# Codec2
git clone https://github.com/drowe67/codec2.git
cd codec2 && mkdir build && cd build
cmake .. && make && sudo make install && sudo ldconfig

# Reticulum + FreeDV
pipx install rns freedvtnc2
```

## References

- [Reticulum](https://reticulum.network)
- [FreeDV](https://freedv.org)
- [freedvtnc2](https://github.com/xssfox/freedvtnc2)

## License

MIT

## Author

[Light Fighter Manifesto](https://lightfightermanifesto.org)
