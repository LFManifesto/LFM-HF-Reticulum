# LFM-HF-Reticulum - Project Context

**Last Updated:** 2025-12-03
**Project:** Reticulum Network Stack over HF Radio
**Status:** Documentation/Configuration Project (Verified Working)
**Company:** Light Fighter Manifesto L.L.C.

---

## PURPOSE

This project provides complete documentation for running encrypted, censorship-resistant communications over HF radio using the Reticulum Network Stack and FreeDV digital modes.

**Key Value Proposition:**
- Infrastructure-independent communication (no internet required)
- Strong end-to-end encryption (X25519 + Ed25519, forward secrecy)
- Long-range capability (50-5000+ km without repeaters)
- Mesh routing and store-and-forward messaging via LXMF
- Optional I2P bridge for base stations

---

## DIRECTORY STRUCTURE

```
LFM-HF-Reticulum/
├── .claude/CLAUDE.md    # This file
├── README.md            # Complete installation and operation guide
└── LICENSE              # MIT License (to be created)
```

---

## TECHNOLOGY STACK

### Software Components

| Component | Purpose | Install Method |
|-----------|---------|----------------|
| Reticulum (RNS) | Mesh networking stack | `pipx install rns` |
| NomadNet | Terminal UI client (LXMF) | `pipx install nomadnet` |
| freedvtnc2 | FreeDV TNC for HF modem | `pipx install freedvtnc2` |
| Codec2 | Voice/data codec (FreeDV) | Build from source |
| rigctld | Hamlib radio control | `apt install libhamlib-utils` |
| I2P (optional) | Anonymous internet bridge | `apt install i2p` |

### Verified Hardware

- **Computer:** Raspberry Pi 4 (4GB+ RAM)
- **OS:** Raspberry Pi OS Lite (Bookworm, 64-bit)
- **Radio:** Xiegu G90 HF Transceiver
- **Interface:** Digirig Mobile (USB audio + CAT)
- **Hamlib Model:** 3088 (Xiegu G90)

### FreeDV DATAC1 Performance

- Data rate: ~980 bps
- Bandwidth: 1.7 kHz
- Latency: 3-5 seconds per transmission
- Range: 50-5000+ km (propagation dependent)

---

## PYTHON ENVIRONMENT

**Use the shared Reticulum venv:**

```bash
source /Users/user/Claude-Work/Projects/Reticulum/venv/bin/activate
```

However, this project is primarily documentation. The actual software (RNS, NomadNet, freedvtnc2) is installed via pipx on target systems, not in this venv.

---

## PROJECT SCOPE

### What This Project Is

- A complete, tested installation guide
- Configuration templates for Reticulum interfaces
- Operational procedures for HF mesh networking
- Hardware/software verification steps

### What This Project Is NOT

- Custom code development (uses existing tools)
- A software library or package
- A development environment

---

## DEVELOPMENT GUIDELINES

### README.md Maintenance

The README is the primary deliverable. When updating:

1. **Accuracy First:** All commands must be tested and verified
2. **Sequential Flow:** Steps must work in exact order shown
3. **Specific Hardware:** Document actual tested hardware configurations
4. **No Assumptions:** Explain every step, even "obvious" ones

### Configuration Files

Reticulum config templates in README cover two scenarios:

1. **Field/Remote Station:** HF-only, no internet
2. **Base Station:** HF + I2P internet bridge with transport enabled

Any new configurations should follow this pattern with clear section headers.

### Adding New Content

Acceptable additions:
- Tested configurations for additional radios
- Troubleshooting procedures
- Performance optimization tips
- Regional regulatory guidance

Required before adding:
- Actual testing on real hardware
- Clear documentation of test environment
- Step-by-step verification

---

## KEY COMMANDS

### On Development Machine (Mac)

```bash
# Activate Reticulum environment
source /Users/user/Claude-Work/Projects/Reticulum/venv/bin/activate

# Check local Reticulum status
rnstatus
```

### On Target System (Raspberry Pi)

```bash
# Start stack (Terminal 1)
rigctld -m 3088 -r /dev/ttyUSB0 -s 19200 --set-conf=serial_handshake=None,rts_state=OFF,dtr_state=OFF &
freedvtnc2 --input-device 1 --output-device 1 --mode DATAC1 --rigctld-port 4532 --ptt-on-delay-ms 300 --ptt-off-delay-ms 200 --output-volume -3

# Start client (Terminal 2)
nomadnet

# Diagnostics
lsusb
arecord -l
ls -la /dev/ttyUSB*
rnstatus
```

---

## CRITICAL PARAMETERS

### Xiegu G90 CAT Settings

- Hamlib model: 3088
- Baud rate: 19200
- Serial port: /dev/ttyUSB0
- Handshake: None
- RTS/DTR: OFF

### freedvtnc2 Settings

- Mode: DATAC1 (most reliable for HF)
- PTT on delay: 300ms
- PTT off delay: 200ms
- Output volume: -3 dB

### Audio Levels (Digirig on card 3)

```bash
amixer -c 3 sset 'Speaker' 64%
amixer -c 3 sset 'Mic',0 cap 75%
amixer -c 3 sset 'Mic' unmute
```

---

## OPERATIONAL SECURITY NOTES

**HF Radio Is Not Anonymous:**
- Transmissions can be direction-found
- Use from non-attributable locations when needed
- Rotate frequencies, avoid patterns
- Use I2P for internet bridging

**Reticulum Provides:**
- Initiator anonymity (no source addresses)
- Strong encryption (cannot read content)
- But NOT transmission location privacy

---

## FUTURE ENHANCEMENTS

Potential additions (require testing):

- [ ] systemd service files for automatic startup
- [ ] Additional radio configurations (IC-7300, FT-891, etc.)
- [ ] WebUI for remote monitoring
- [ ] Mesh network topology documentation
- [ ] Field deployment kit lists

---

## REFERENCES

- **Reticulum Manual:** https://markqvist.github.io/Reticulum/manual/
- **FreeDV Project:** https://freedv.org/
- **freedvtnc2:** https://github.com/xssfox/freedvtnc2
- **Hamlib Radios:** https://github.com/Hamlib/Hamlib/wiki/Supported-Radios
- **I2P:** https://geti2p.net/

---

## COMPLIANCE

**Amateur Radio Regulations:**
- Users must hold appropriate license for HF transmission
- Encryption restrictions vary by jurisdiction
- Identification requirements apply
- This documentation assumes legal, licensed operation

---

**Remember:** This is infrastructure for sovereign, resilient communications. Keep documentation accurate, tested, and accessible.
