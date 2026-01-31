# ReticulumHF

**v0.5.0-alpha** - Reticulum networking over HF radio using FreeDV data modes.

## What This Is

A Raspberry Pi image that creates a WiFi gateway for [Reticulum](https://reticulum.network) networking over HF radio. Connect Sideband or Meshchat to the Pi's WiFi, and it bridges your traffic to HF using [FreeDV](https://freedv.org) digital modes. You can also ssh into the pi and use Nomadnet. 

## Technical Specifications

### FreeDV Data Modes

| Mode | Data Rate | Bandwidth | Bytes/Frame | Frame Time | Min SNR |
|------|-----------|-----------|-------------|------------|---------|
| DATAC1 | 980 bps | 1700 Hz | 510 | 4.18s | 5 dB |
| DATAC4 | 87 bps | 250 Hz | 56 | 5.17s | -4 dB |

Source: [codec2 README_data.md](https://github.com/drowe67/codec2/blob/main/README_data.md)

### Throughput (DATAC1)

- **Raw throughput**: 122 bytes/second
- **LXMF overhead**: 111 bytes per message
- **Short message (200 chars)**: ~2.5 seconds TX time
- **Full frame (510 bytes)**: 4.18 seconds TX time

### Reticulum Requirements

- MTU: 500 bytes
- Minimum throughput: 5 bps (DATAC1 provides 980 bps)
- Half-duplex supported

Source: [Reticulum Manual](https://reticulum.network/manual/understanding.html)

## What Works

- **LXMF messaging**: Asynchronous encrypted messages with store-and-forward
- **Mesh discovery**: Beacon announces find other nodes
- **Multi-path routing**: HF + I2P internet transport
- **Offline operation**: Works without any internet infrastructure

## What Doesn't Work Well

- **Real-time chat**: HF propagation causes variable latency
- **Large transfers**: 122 bytes/sec means files take a long time
- **Guaranteed delivery**: Propagation is unpredictable

## Quick Start

1. Flash [image](https://github.com/LFManifesto/ReticulumHF/releases) to SD card
2. Boot Pi with radio + Digirig connected
3. Connect to WiFi: `ReticulumHF` / `reticulumhf`
4. Open `http://192.168.4.1` - complete setup wizard
5. Connect Sideband/MeshChat to `192.168.4.1:4242` (TCP Client Interface)

## Operating Modes

| Mode | HF TX | I2P | Description |
|------|-------|-----|-------------|
| Hybrid | Beacon windows | Enabled | I2P for bulk, HF for discovery |
| HF Only | Full | Disabled | Field ops, no internet |
| Internet Only | Disabled | Enabled | Radio off/maintenance |

## Hardware

**Required:**
- Raspberry Pi 4 (2GB+)
- HF radio with data port
- Audio interface (Digirig Mobile)
- Antenna for your band
- Amateur radio license

**Tested Radios:**

| Radio | Interface | CAT Control |
|-------|-----------|-------------|
| Xiegu G90 | Digirig Mobile | Hamlib 3088 |
| Yaesu FT-818 | Digirig Mobile | Hamlib 1020 |
| (tr)uSDX | Digirig Lite | VOX only |

## Network

| Setting | Value |
|---------|-------|
| WiFi AP | `ReticulumHF` / `reticulumhf` |
| Portal | `http://192.168.4.1` |
| Client Port | 4242 |
| SSH | `pi` / `reticulumhf` |

## Dashboard

- Solar/propagation data (N0NBH)
- RX level monitor
- Network health score
- Beacon scheduler
- Station map (grid squares)

## Architecture

```
Sideband/MeshChat ──WiFi:4242──► Pi Gateway ──Audio/PTT──► HF Radio
                                     │
                                     └──I2P──► Internet (optional)
```

## References

- [Reticulum Network](https://reticulum.network)
- [FreeDV](https://freedv.org)
- [LXMF Protocol](https://github.com/markqvist/LXMF)
- [freedvtnc2](https://github.com/xssfox/freedvtnc2)
- [codec2 Data Modes](https://github.com/drowe67/codec2/blob/main/README_data.md)

## License

MIT

## Author

[Light Fighter Manifesto](https://lightfightermanifesto.org)
