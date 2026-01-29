# ReticulumHF

**Current Version:** v0.2.0-alpha

Reticulum mesh networking over HF radio via FreeDV modem. Raspberry Pi image that creates a WiFi gateway for Sideband/MeshChat to communicate over HF.

## Development Workflow

### IMPORTANT: Always Build Image After Commits

After committing and pushing changes to this repository, **always build a new Pi image**:

```bash
# 1. Push changes to GitHub first
git push

# 2. Build image on pi2 (takes ~15-20 minutes)
ssh pi2 "cd /home/user/ReticulumHF && git fetch origin && git reset --hard origin/main && sudo ./image/build.sh image/bookworm-lite.img"

# 3. Create GitHub release (from pi2)
ssh pi2 "cd /home/user/ReticulumHF && gh release create vX.X.X output/reticulumhf-YYYYMMDD.img.xz --title 'vX.X.X' --notes 'Release notes here'"
```

### Build Machine (pi2)

Pi2 is the **build machine only** - do NOT deploy ReticulumHF to pi2 for testing.

| Item | Location |
|------|----------|
| Repo | `/home/user/ReticulumHF` |
| Base image | `/home/user/ReticulumHF/image/bookworm-lite.img` |
| Output | `/home/user/ReticulumHF/output/reticulumhf-YYYYMMDD.img.xz` |
| User | `user` (not `pi`) |

**What runs on pi2:** TAK server, lfnet docker container, GitHub Actions runner

### Testing

To test the image:
1. Build image on pi2
2. Flash to SD card
3. Boot on a **separate** Raspberry Pi 4
4. Connect to ReticulumHF WiFi and test

Do NOT use deploy-to-pi.sh on pi2 - that script is for deploying to fresh Pi devices.

## Architecture

```
Phone (Sideband/MeshChat)
    |
    | WiFi (TCP Client Interface)
    | 192.168.4.1:4242
    v
Pi Gateway (rnsd boundary mode)
    |
    | Internal (KISS TCP)
    | 127.0.0.1:8001
    v
freedvtnc2 (FreeDV modem)
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
| `image/build.sh` | Pi image build script (run on pi2) |
| `scripts/first-boot.sh` | First boot setup (WiFi AP, services) |
| `services/*.service` | Systemd service files |

## Configuration

| Setting | Value |
|---------|-------|
| WiFi AP | ReticulumHF / reticulumhf |
| Gateway Port | 4242 (all clients) |
| Pi User | pi / reticulumhf |
| Config File | `/etc/reticulumhf/config.env` |
| RNS Config | `/home/pi/.reticulum/config` |
| TCP Mode | boundary (prevents TCP flooding HF) |
| TX Audio Default | -6 dB |

## Client Connection Settings

All clients (Sideband, MeshChat, Columba) use the same settings:

| Setting | Value |
|---------|-------|
| Interface Type | TCP Client Interface |
| Host/Address | 192.168.4.1 |
| Port | 4242 |
| IFAC | Match gateway if configured |

**Sideband:** Settings → Connectivity → Add Interface → TCP Client
**MeshChat:** Settings → Interfaces → Add → TCP Client Interface

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Power fluctuates during TX (ALC) | Reduce TX Audio Level on status page (-8 to -12 dB) |
| No RX signal | Check radio audio output, ALSA Mic level |
| Can't connect from phone | Verify WiFi, use TCP Client Interface on port 4242 |
| freedvtnc2 won't start | Check rigctld running first (CAT control) |
| rnsd not bridging | Verify boundary mode + transport enabled in config |

## Version History

| Version | Changes |
|---------|---------|
| v0.2.0-alpha | Transport bridging fix (boundary mode), TX audio control, unified port 4242, troubleshooting UI |
| v0.1.0-alpha | Major UI overhaul, audio monitoring, ALSA controls, FreeDV mode selection |

## Release Checklist

1. Code changes committed and pushed to GitHub
2. README.md updated with new version
3. Website updated (lightfightermanifesto.org/software/reticulumhf)
4. Image built on pi2
5. GitHub release created with image attached
6. Old release deleted (if replacing)
