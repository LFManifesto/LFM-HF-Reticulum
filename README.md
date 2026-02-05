## Overview

Runs Reticulum over HF radio using FreeDV data modes. 

### Quick Start

1. Flash to SD card using [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. Insert SD card, connect radio via Digirig, power on
3. Connect to WiFi: **ReticulumHF** (password: `reticulumhf`)
4. Open **http://192.168.4.1** in browser
5. Complete 6-step setup wizard
6. Connect Sideband to `192.168.4.1:4242`

### Setup Wizard 

1. **Select Radio** - Choose from supported radios (tested radios marked)
2. **Radio Settings** - Configure your radio with the displayed menu values
3. **Hardware Detection** - Automatically detects Digirig, serial port, audio device
4. **Test Connection** - Verify CAT and PTT (or check "Using VOX mode" for audio-only)
5. **Network Security** - Optional IFAC encryption for the gateway
6. **Start Gateway** - Launches services and redirects to status page

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

## References

- [Reticulum Manual](https://markqvist.github.io/Reticulum/manual/)
- [codec2 Data Modes](https://github.com/drowe67/codec2/blob/main/README_data.md)
- [freedvtnc2](https://github.com/xssfox/freedvtnc2)
- [Sideband](https://github.com/markqvist/Sideband)
- [MeshChat](https://github.com/liamcottle/meshtastic-meshchat)
- [Hamlib Supported Radios](https://github.com/Hamlib/Hamlib/wiki/Supported-Radios)

