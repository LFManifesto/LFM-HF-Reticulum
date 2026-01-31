# ReticulumHF

**v0.3.3-alpha** - Reticulum networking over HF radio using FreeDV data modes.

## What It Does

Raspberry Pi multi-interface transport node. Bridges Reticulum networks over HF radio and I2P. Encrypted mesh communication without internet infrastructure.

- Multi-interface transport (HF + I2P + TCP)
- Operating modes: Hybrid, HF Only, Internet Only
- TX gating (HF only transmits during beacon windows)
- Web setup wizard and operator dashboard
- JS8Call integration (station discovery)
- TAK/CoT push (map markers)

## Quick Start

1. Flash [image](https://github.com/LFManifesto/ReticulumHF/releases) to SD card
2. Boot Pi, connect radio via Digirig
3. Connect to WiFi: `ReticulumHF` / `reticulumhf`
4. Open `http://192.168.4.1`
5. Complete setup wizard
6. Connect clients to `192.168.4.1:4242` (TCP Client Interface)

**Optional:** Connect ethernet for internet backhaul (I2P transport, NAT for WiFi clients).

## Operating Modes

| Mode | HF TX | I2P | Use Case |
|------|-------|-----|----------|
| Hybrid | Beacon windows only | Enabled | Normal ops - I2P bulk, HF discovery |
| HF Only | Full control | Disabled | Field ops, no internet |
| Internet Only | Disabled | Enabled | Radio maintenance |

## Network

| Setting | Value |
|---------|-------|
| WiFi AP | ReticulumHF / reticulumhf |
| Web Portal | http://192.168.4.1 |
| Client Port | 4242 (TCP Client Interface) |
| SSH | pi / reticulumhf |
| I2P Peer | Lightfighter node (default) |

## Architecture

```
                    ┌─────────────────┐
                    │   I2P Network   │
                    └────────┬────────┘
                             │
┌─────────────┐    ┌────────▼────────┐    ┌─────────────┐
│ WiFi Client │───►│   Pi Gateway    │◄───│  Ethernet   │
│ (Sideband)  │    │  Multi-Iface    │    │  (Internet) │
└─────────────┘    └────────┬────────┘    └─────────────┘
     :4242                  │
                   ┌────────▼────────┐
                   │  FreeDV Modem   │
                   │  (TX Gated)     │
                   └────────┬────────┘
                            │
                   ┌────────▼────────┐
                   │    HF Radio     │
                   └─────────────────┘
```

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

## References

- [Reticulum](https://reticulum.network)
- [FreeDV](https://freedv.org)
- [freedvtnc2](https://github.com/xssfox/freedvtnc2)

## License

MIT

## Author

[Light Fighter Manifesto](https://lightfightermanifesto.org)
