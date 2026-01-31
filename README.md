# ReticulumHF

**v0.4.0-alpha** - Experimental Reticulum networking over HF radio.

## What This Is

A Raspberry Pi image that creates a WiFi gateway for Reticulum mesh networking over HF radio using FreeDV data modes. Connect your phone (Sideband) or laptop (MeshChat) to the Pi's WiFi, and it bridges your traffic to HF.

**This is experimental alpha software.** Expect bugs, limitations, and ongoing development.

## What It Can Do

- **HF Data Gateway**: Bridge Reticulum traffic over HF using FreeDV DATAC1 (980 bps)
- **Multi-Path Routing**: Combine HF radio with I2P internet transport
- **Web Dashboard**: Monitor solar conditions, RX levels, and network health
- **TX Gating**: Limit HF transmissions to scheduled beacon windows (hybrid mode)
- **Automatic Setup**: Web wizard configures radio, audio, and network settings

## What It Cannot Do

- **Fast file transfers**: 980 bps is roughly 120 bytes/second. A 1KB message takes ~10 seconds.
- **Reliable delivery**: HF propagation is inconsistent. Messages may not get through.
- **Long range guaranteed**: Range depends on band conditions, antenna, power, and luck.
- **Replace internet**: Even with I2P, this is slow mesh networking, not broadband.
- **Work without setup**: You need a radio, antenna, audio interface, and amateur license.

## Realistic Expectations

| Use Case | Feasibility |
|----------|-------------|
| Emergency text messaging | Good - short messages work |
| LXMF mail (async) | Good - store-and-forward designed for this |
| Real-time chat | Poor - latency and packet loss |
| File sharing | Poor - painfully slow |
| Voice/video | No - insufficient bandwidth |

**Best for:** Off-grid text messaging, emergency backup comms, mesh networking experiments, amateur radio digital modes.

## Quick Start

1. Flash [latest image](https://github.com/LFManifesto/ReticulumHF/releases) to SD card
2. Boot Pi with radio connected via Digirig/audio interface
3. Connect to WiFi: `ReticulumHF` / `reticulumhf`
4. Open `http://192.168.4.1` and complete setup wizard
5. Connect Sideband/MeshChat to `192.168.4.1:4242` (TCP Client Interface)

**Optional:** Connect ethernet for I2P transport (internet backhaul).

## Operating Modes

| Mode | Description |
|------|-------------|
| **Hybrid** (default) | I2P handles bulk traffic, HF only for beacons. Best for most users. |
| **HF Only** | All traffic over HF. For field ops without internet. Slow but works. |
| **Internet Only** | HF TX disabled. For testing or when radio is off. |

## Hardware Requirements

- Raspberry Pi 4 (2GB+ RAM)
- HF radio with data port
- Audio interface (Digirig Mobile recommended)
- Antenna appropriate for your band
- Amateur radio license (required in most countries)

### Tested Radios

| Radio | Interface | CAT Control |
|-------|-----------|-------------|
| Xiegu G90 | Digirig Mobile | Hamlib 3088, 19200 baud |
| Yaesu FT-818 | Digirig Mobile | Hamlib 1020, 4800 baud |
| (tr)uSDX | Digirig Lite | VOX mode only |

## Network Details

| Setting | Value |
|---------|-------|
| WiFi AP | `ReticulumHF` / `reticulumhf` |
| Web Portal | `http://192.168.4.1` |
| Client Port | 4242 (TCP Client Interface) |
| SSH | `pi` / `reticulumhf` |
| I2P Peer | Lightfighter Reticulum node |

## Dashboard Features

- **Solar/Propagation**: Real-time N0NBH data (SFI, A/K index, band conditions)
- **RX Level Monitor**: Live signal strength from modem
- **Network Health**: Score based on peers, signal, and interface status
- **Beacon Scheduler**: Control TX windows and force manual beacons
- **Station Map**: Leaflet map showing discovered peers by grid square

## Architecture

```
┌─────────────┐         ┌─────────────────┐         ┌─────────────┐
│ Sideband/   │  WiFi   │   Raspberry Pi  │  Audio  │  HF Radio   │
│ MeshChat    │────────►│   Gateway       │────────►│  + Antenna  │
└─────────────┘  :4242  │                 │  PTT    └─────────────┘
                        │  ┌───────────┐  │
                        │  │ freedvtnc2│  │         ┌─────────────┐
                        │  │ (modem)   │  │  I2P    │  Internet   │
                        │  └───────────┘  │────────►│  (optional) │
                        └─────────────────┘         └─────────────┘
```

## Limitations & Known Issues

- **Alpha software**: Bugs expected, not for critical use
- **HF is slow**: Don't expect internet-like speeds
- **Propagation varies**: What works at noon may not work at midnight
- **Single frequency**: Currently operates on one frequency at a time
- **No ALE**: Manual frequency selection only
- **Power hungry**: Pi + radio draws significant current for portable use

## Development

Built on:
- [Reticulum](https://reticulum.network) - Cryptographic mesh networking
- [freedvtnc2](https://github.com/xssfox/freedvtnc2) - FreeDV TNC (LFM fork with TX gating)
- [FreeDV](https://freedv.org) - Open source digital voice/data modes

## License

MIT

## Author

[Light Fighter Manifesto](https://lightfightermanifesto.org)

---

**Remember:** This is amateur radio experimentation. Results will vary. Have fun, learn something, and don't rely on this for anything critical.
