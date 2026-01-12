# LFM-HF-Reticulum

Pre-built Raspberry Pi image for Reticulum mesh networking over HF radio.

**Status:** Production Ready (v1.0)
**Latest Build:** 2026-01-11

---

## Credentials

| Item | Value |
|------|-------|
| WiFi AP SSID | ReticulumHF-Setup |
| WiFi AP Password | reticulumhf |
| SSH User | pi |
| SSH Password | reticulumhf |
| Portal URL | http://192.168.4.1 |

---

## Project Overview

Plug-and-play SD card image that turns a Raspberry Pi into an HF radio gateway for encrypted mesh messaging.

**User Flow:**
1. Flash image to SD card
2. Boot Pi with radio connected
3. Connect phone to WiFi AP
4. Run setup wizard at http://192.168.4.1
5. Use Sideband app to send encrypted messages over HF

---

## Key Files

| File | Purpose |
|------|---------|
| `image/build.sh` | Main image build script |
| `setup-portal/app.py` | Flask web portal |
| `setup-portal/templates/` | Portal HTML templates |
| `services/*.service` | Systemd service files |
| `configs/rns-config` | Base RNS configuration |
| `scripts/first-boot.sh` | First boot setup script |

---

## Building the Image

**Build Machine:** Pi2 at 192.168.8.152 (SSH alias: `pi2-github`)
**Base Image:** `~/reticulumhf-build/bookworm-lite.img`

### Standard Build Workflow

1. **Transfer code changes to Pi2:**
   ```bash
   scp <local-files> pi2-github:~/reticulumhf-build/reticulumhf/<path>/
   ```

2. **Build image on Pi2:**
   ```bash
   ssh pi2-github
   cd ~/reticulumhf-build/reticulumhf/image
   sudo bash build.sh ~/reticulumhf-build/bookworm-lite.img
   ```

3. **Transfer image to Mac for flashing:**
   ```bash
   scp pi2-github:~/reticulumhf-build/reticulumhf/output/reticulumhf-*.img.xz ~/Downloads/
   ```

4. **Flash with Raspberry Pi Imager and test on Pi4**

Output: `output/reticulumhf-YYYYMMDD.img.xz`

---

## Services

| Service | Purpose | Auto-start |
|---------|---------|------------|
| `reticulumhf-wlan` | Static IP for wlan0 | Always |
| `hostapd` | WiFi AP | Always |
| `dnsmasq` | DHCP/DNS | Always |
| `reticulumhf-rnsd` | Reticulum daemon | Always |
| `reticulumhf-portal` | Web portal | Always |
| `rigctld` | Radio CAT control | After wizard |
| `freedvtnc2` | FreeDV modem | After wizard |

---

## Network

- **WiFi AP:** ReticulumHF-Setup (192.168.4.1)
- **RNS TCP:** Port 4242
- **Portal:** Port 80
- **FreeDV TNC:** Port 8001 (localhost)

---

## Verified Hardware

- Raspberry Pi 4 (4GB)
- Xiegu G90 (Hamlib 3088)
- Digirig Mobile
- Sideband app (Android)

---

## Testing Checklist

- [x] WiFi AP visible after fresh boot (verified 2026-01-11)
- [x] Portal loads at 192.168.4.1 (verified 2026-01-11)
- [x] Setup wizard completes (verified 2026-01-11)
- [x] Services persist after reboot (verified 2026-01-11)
- [x] Sideband connects via TCP (verified 2026-01-11)
- [x] rigctld CAT control working (verified 2026-01-11)
- [x] freedvtnc2 KISS port responding (verified 2026-01-11)
- [x] RNS interfaces initialize (verified 2026-01-11)
- [ ] I2P tunnel establishes to peer
- [ ] FreeDV modem transmits/receives (HF test pending)
- [ ] End-to-end message delivery

---

## TODO Before Next Image Build

### Must Fix:
- [ ] Verify freedvtnc2 KISS data flow end-to-end (send test packet)
- [ ] Test I2P tunnel actually connects Pi4 to Pi1
- [ ] Verify radios.json setup guides for non-G90 radios
- [ ] Verify PTT timing values against manufacturer docs
- [ ] Verify RTS/DTR states per radio model

### Should Review:
- [ ] Output volume -3dB appropriateness (or remove)

### After Verification:
- [ ] Rebuild image with all fixes
- [ ] Full end-to-end HF transmission test

---

## Changelog

### 2026-01-12 (not yet in image)
**GitHub Issues Fixed:**
- Fixed #8: Field WiFi no longer overrides ReticulumHF-Setup on reboot
  - Changed default to empty (keep current)
  - Actually updates hostapd.conf when SSID changed
  - Added warning about network name change
- Fixed #7: ALSA config missing for freedvtnc2
  - Created /etc/asound.conf with proper PCM definitions
  - Dynamic ALSA config generation with correct audio card number
  - Fixes "Unknown PCM cards.pcm.modem" error
- Fixed #6: Client list now shows only currently connected devices
  - Uses ARP table instead of stale DHCP leases
  - Filters out disconnected clients immediately
- Fixed #5: Digirig detection now reports partial detection
  - Shows "Audio detected (CAT port not found)" when appropriate
  - Color-coded status indicators
- Fixed #4: Initial date/time preserved across reboots
  - Added fake-hwclock package to image build
- Fixed #3: Set Audio now validates and returns actual errors
  - Checks audio card exists before setting levels
  - Tries fallback control names (PCM, Capture)
  - Returns specific error messages on failure
- Fixed #2: I2P status shows detailed state information
  - New /api/i2p-status endpoint checks i2pd, SAM port, and RNS
  - Shows "Building I2P tunnel (this can take 5-10 minutes)"
  - Distinguishes between not configured, error, and connecting states

**Previous fixes:**
- Fixed announce_cap=0 → announce_cap=1 (0 was outside valid range 1-100)
- Removed invalid i2p_tunnels and i2p_tunnel_length params (not valid RNS options)
- Updated I2P config comment to note SAM requirement
- Identified that i2pd SAM port 7656 must be enabled for RNS I2P to work

### 2026-01-11
- Fixed systemd deadlock in first-boot service (removed Before= directive)
- Fixed wrong service name in app.py (reticulumhf-wifi → reticulumhf-wlan)
- Fixed Flask debug=True in production
- Fixed setup_complete marker created on failure
- Moved StartLimitIntervalSec to [Unit] section
- Added WiFi password validation
- Added FreeDV mode selector to wizard
- Added CI-V address support for Icom radios
- Verified all hamlib model IDs in radios.json
- Verified all CI-V addresses against manufacturer docs
- Verified WPA2 password requirements (8-63 chars)

### 2026-01-06
- Initial release

---

## References

- https://markqvist.github.io/Reticulum/manual/
- https://freedv.org/
- https://github.com/xssfox/freedvtnc2
