# ReticulumHF

Reticulum mesh networking over HF radio via FreeDV modem. Raspberry Pi image that creates a WiFi gateway for Sideband/MeshChat to communicate over HF.

## Development Workflow

### IMPORTANT: Always Build Image After Commits

After committing changes to this repository, **always build a new Pi image**:

```bash
ssh pi2 "cd /home/user/ReticulumHF && git pull && sudo ./image/build.sh image/bookworm-lite.img"
```

The build takes ~10-15 minutes. Output image will be in `output/` directory.

### Build Machine

- **Host:** pi2
- **Repo location:** `/home/user/ReticulumHF`
- **Base image:** `/home/user/ReticulumHF/image/bookworm-lite.img`
- **Output:** `/home/user/ReticulumHF/output/reticulumhf-YYYYMMDD.img.xz`

## Architecture

```
Phone (Sideband/MeshChat)
    |
    | WiFi (192.168.4.1:4242)
    v
Pi Gateway (rnsd + freedvtnc2)
    |
    | Audio + PTT
    v
HF Radio --> RF --> Remote Station
```

## Key Files

| File | Purpose |
|------|---------|
| `setup-portal/app.py` | Flask web app for setup wizard and status page |
| `setup-portal/templates/status.html` | Status page UI |
| `setup-portal/templates/setup.html` | Setup wizard UI |
| `setup-portal/hardware.py` | Hardware detection (serial, audio, radios) |
| `configs/radios.json` | Supported radio definitions |
| `image/build.sh` | Pi image build script |
| `scripts/first-boot.sh` | First boot setup (WiFi AP, services) |
| `services/*.service` | Systemd service files |

## Configuration

- **WiFi AP:** ReticulumHF / reticulumhf
- **Gateway Port:** 4242 (TCP Client Interface)
- **Pi User:** pi / reticulumhf
- **Config:** `/etc/reticulumhf/config.env`
- **RNS Config:** `/home/pi/.reticulum/config`

## Interface Settings (for Sideband/MeshChat)

| Setting | Value |
|---------|-------|
| Interface Type | TCP Client Interface |
| Host | 192.168.4.1 |
| Port | 4242 |
| IFAC | Match gateway if configured |

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Power fluctuates during TX | Reduce TX Audio Level (ALC activating) |
| No RX signal | Check radio audio output, ALSA Mic level |
| Can't connect from phone | Verify WiFi, check interface settings |
| freedvtnc2 won't start | Check rigctld running first |
| rnsd won't connect | Verify freedvtnc2 running on port 8001 |

## Version History

- **v0.2.0-alpha** - Transport bridging fix, TX audio control, UI improvements
- **v0.1.0-alpha** - Major UI overhaul, audio monitoring
