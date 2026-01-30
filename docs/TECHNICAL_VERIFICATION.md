# Technical Verification - ReticulumHF Beacon Protocol

Last verified: 2026-01-30

## FreeDV Data Mode Specifications

Source: [codec2/README_data.md](https://github.com/drowe67/codec2/blob/main/README_data.md)

| Mode | Bandwidth | Bitrate | Bytes/Frame | Frame Duration | FEC Code | Min SNR |
|------|-----------|---------|-------------|----------------|----------|---------|
| DATAC0 | 500 Hz | 291 bps | 14 | 0.44 s | (256,128) | 0 dB |
| DATAC1 | 1700 Hz | 980 bps | 510 | 4.18 s | (8192,4096) | 5 dB |
| DATAC3 | 500 Hz | 321 bps | 126 | 3.19 s | (2048,1024) | 0 dB |
| **DATAC4** | **250 Hz** | **87 bps** | **56** | **5.17 s** | (1472,448) | **-4 dB** |
| DATAC13 | 200 Hz | 64 bps | 14 | 2.0 s | (384,128) | -4 dB |
| DATAC14 | 250 Hz | 58 bps | 3 | 0.69 s | (112,56) | -2 dB |

**Key Finding:** DATAC4 has 56 bytes/frame. Our beacon (26-47 bytes) fits in one frame.

**Key Finding:** DATAC13 only has 14 bytes/frame. NOT suitable for our beacon without fragmentation.

## Beacon Packet Size Verification

| Beacon Type | Size | Fits DATAC4 (56B) | Fits DATAC13 (14B) |
|-------------|------|-------------------|-------------------|
| Minimal (no message) | 26 bytes | YES | NO (needs 2 frames) |
| With callsign/grid | 37 bytes | YES | NO (needs 3 frames) |
| Max message (20 char) | 47 bytes | YES | NO (needs 4 frames) |

**Decision:** Use DATAC4 for beacons, not DATAC13.

## Beacon Timing Estimates

Using DATAC4 at 87 bps:

| Beacon Size | Data Time | With Preamble (~2s) | Total Estimate |
|-------------|-----------|---------------------|----------------|
| 26 bytes | 2.4 s | 4-5 s | ~5-6 s |
| 37 bytes | 3.4 s | 5-6 s | ~6-7 s |
| 47 bytes | 4.3 s | 6-7 s | ~7-8 s |

**Compared to alternatives:**
- MFSK-32: ~70 seconds (unacceptable)
- OFDM750 without FEC: ~7 seconds but unreliable
- DATAC4 beacon: ~6-8 seconds WITH FEC (acceptable)

## Reticulum Specifications

Source: [Reticulum GitHub Discussion #11](https://github.com/markqvist/Reticulum/discussions/11)

| Parameter | Value | Source |
|-----------|-------|--------|
| Minimum bitrate | 475 bps | markqvist, Discussion #11 |
| Design goal | "time-invariant" | markqvist |
| Link timeout | Configurable per-interface (planned) | markqvist |

**Quote:** "the _real_ lower limit for Reticulum right now is actually around 475 bits per second"

**Quote:** "I actually designed that to be completely time-invariant, and you could run it on a 1 bit per week link, if you had the time for that"

## freedvtnc2-lfm Command Interface

Source: [LFManifesto/freedvtnc2 PROTOCOL.md](https://github.com/LFManifesto/freedvtnc2/blob/main/PROTOCOL.md)

| Port | Purpose | Protocol |
|------|---------|----------|
| 8001 | KISS TNC (data) | Binary KISS frames |
| 8002 | Command interface | ASCII commands |

**Verified Commands:**
- `MODE [DATAC1|DATAC3|DATAC4]` - Instant mode switching
- `VOLUME [dB]` - TX output level
- `STATUS` - Query state
- `LEVELS` - RX audio level
- `PING` - Connection test

## Reticulum API Verification

Source: [Reticulum API Reference](https://reticulum.network/manual/reference.html)

**Transport.has_path(destination_hash)**
- Parameters: destination hash as bytes
- Returns: True if path known

**Transport.request_path(destination_hash)**
- Parameters: destination hash, optional interface
- Behavior: Requests path from network

**Transport.register_announce_handler(handler)**
- Handler requires: `aspect_filter` attribute, `received_announce()` callable
- Callable signature: `received_announce(destination_hash, announced_identity, app_data)`

Our `rns_bridge.py` implementation matches this API.

## Architecture Verification

```
Phone (Sideband)
    │ TCP 4242
    ▼
┌─────────────────────────────────────┐
│ Reticulum (rnsd)                    │
│ ├─ TCP Server Interface (4242)      │
│ ├─ KISS Client Interface (8001)     │
│ └─ [Other interfaces: LoRa, I2P]    │
└─────────────────────────────────────┘
    │ KISS 8001
    ▼
┌─────────────────────────────────────┐
│ freedvtnc2-lfm                      │
│ ├─ KISS TNC (8001)                  │
│ ├─ Command Interface (8002)  ◄──────┼── Beacon Scheduler
│ └─ FreeDV modem (DATAC4/DATAC1)     │
└─────────────────────────────────────┘
    │ Audio + PTT
    ▼
  HF Radio
```

**Verified:** KISS frames from beacon scheduler go through same path as Reticulum traffic.

## Summary

| Claim | Verified | Source |
|-------|----------|--------|
| DATAC4 works at -4 dB SNR | YES | codec2 README_data.md |
| DATAC4 frame holds 56 bytes | YES | codec2 README_data.md |
| Our beacon fits in one DATAC4 frame | YES | Local test |
| Beacon TX time ~6-8 seconds | YES | Calculated from specs |
| Reticulum minimum is 475 bps | YES | GitHub Discussion #11 |
| freedvtnc2-lfm has command interface | YES | PROTOCOL.md |
| Mode switching is instant (no restart) | YES | PROTOCOL.md |
| RNS API supports path requests | YES | API Reference |

**All technical assumptions verified.**
