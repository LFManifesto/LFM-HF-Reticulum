# ReticulumHF Beacon Protocol

## Overview

The beacon protocol implements a **hybrid stateless/ARQ approach** for HF Reticulum networking. This addresses the fundamental tension between:

- **Fast discovery** (stateless beacons)
- **Reliable data transfer** (ARQ with retries)

Traditional Reticulum announcements over HF are problematic:
- MFSK-32: ~70 seconds per announcement (too slow)
- OFDM750 without FEC: Fast but unreliable on HF

This protocol uses FreeDV's narrowband FEC modes for beacons and wider modes for data.

## Protocol Design

### Mode Separation

| Mode | FreeDV | Bandwidth | Bitrate | Bytes/Frame | Min SNR | Purpose |
|------|--------|-----------|---------|-------------|---------|---------|
| **Beacon** | DATAC4 | 250 Hz | 87 bps | 56 | -4 dB | Discovery, heartbeats |
| ARQ | DATAC1 | 1700 Hz | 980 bps | 510 | 5 dB | Good conditions |
| ARQ | DATAC3 | 500 Hz | 321 bps | 126 | 0 dB | Marginal conditions |

**Note:** DATAC13/14 are not suitable for beacons - they only have 14/3 bytes per frame,
but our beacon packet is 26-47 bytes. DATAC4's 56-byte frame fits all beacon sizes.

### Beacon Windows

Scheduled beacon windows (default: :00 and :30 each hour):

```
:00:00 - Switch to DATAC4
:00:05 - Listen, then TX beacon if clear
:00:30 - RX window (listen for responses)
:01:00 - Switch back to ARQ mode (DATAC1)

Normal Reticulum operation via KISS...

:30:00 - Switch to DATAC4
:30:05 - Listen, then TX beacon if clear
:30:30 - RX window
:31:00 - Switch back to ARQ mode
```

### Beacon Packet Format

Minimal packet designed for DATAC4's 56-byte frame capacity:

```
+--------+--------+---------+-------+------------------+-----------+----------+
| Magic  | Ver    | Flags   | Pad   | Identity Hash    | Timestamp | CRC16    |
| 2B     | 1B     | 1B      | -     | 16B              | 4B        | 2B       |
+--------+--------+---------+-------+------------------+-----------+----------+
| 0x5248 | 0x01   | 0bXXXX  |       | truncated RNS ID | Unix time | checksum |
+--------+--------+---------+-------+------------------+-----------+----------+

Total: 26 bytes (fits in single DATAC4 frame with room for optional message)
```

**Flags:**
- `0x01` - Has message (variable length follows)
- `0x02` - Is propagation node
- `0x04` - Accepts links
- `0x08` - Transport enabled

**Optional Message:**
- 1 byte length + up to 20 bytes UTF-8 text
- For human-readable callsign/location

### Beacon Timing

```
Beacon TX Time = Frame Size / Bitrate + Preamble + Postamble

DATAC4: 56 bytes × 8 / 87 bps ≈ 5.1 seconds per frame
        + preamble/postamble ≈ 7-8 seconds total

Compare:
- MFSK-32: ~70 seconds (unacceptable)
- OFDM750: ~7 seconds but no FEC (unreliable)
- DATAC4: ~8 seconds WITH FEC (acceptable)
```

## Implementation

### Components

1. **Beacon Scheduler** (`beacon/scheduler.py`)
   - Time-based mode switching
   - Beacon packet generation
   - KISS frame transmission
   - RX level monitoring

2. **FreeDVTNC2 Client**
   - Command interface (port 8002)
   - KISS data interface (port 8001)
   - Instant mode switching

3. **Beacon Packet Codec**
   - Encode/decode beacon format
   - CRC validation
   - Timestamp replay protection

### Integration with Reticulum

The beacon scheduler operates **alongside** normal Reticulum:

```
┌─────────────────────────────────────────────────────┐
│                    Reticulum (rnsd)                 │
│  ┌─────────────┐                ┌─────────────┐    │
│  │ TCP Server  │                │ KISS Client │    │
│  │ Port 4242   │                │ Port 8001   │    │
│  │ (phones)    │                │ (modem)     │    │
│  └─────────────┘                └──────┬──────┘    │
└────────────────────────────────────────┼───────────┘
                                         │
┌────────────────────────────────────────┼───────────┐
│              Beacon Scheduler          │           │
│  ┌──────────┐  ┌──────────┐  ┌────────▼────────┐  │
│  │ Timer    │→ │ Mode     │→ │ FreeDVTNC2      │  │
│  │ Thread   │  │ Switcher │  │ Cmd (8002)      │  │
│  └──────────┘  └──────────┘  │ KISS (8001)     │  │
│                              └─────────────────┘  │
└───────────────────────────────────────────────────┘
                         │
                         ▼
┌───────────────────────────────────────────────────┐
│              freedvtnc2-lfm                        │
│  Mode: DATAC4 (beacon) ←→ DATAC1 (ARQ)            │
└───────────────────────────────────────────────────┘
                         │
                         ▼
                    HF Radio
```

**Key insight:** KISS framing abstracts the modem. Reticulum doesn't know or care that the underlying FreeDV mode changed. Packets flow normally; they just travel at different speeds.

## Adaptive Mode Selection

Optional feature: auto-select ARQ mode based on RX signal level.

```python
if estimated_snr < -2 dB:
    use DATAC3 (robust, 321 bps)
elif estimated_snr > 3 dB:
    use DATAC1 (fast, 980 bps)
```

## Configuration

`/etc/reticulumhf/beacon.json`:

```json
{
  "beacon_minutes": [0, 30],
  "beacon_duration_sec": 60,
  "beacon_tx_delay_sec": 5,
  "beacon_mode": "DATAC4",
  "arq_mode": "DATAC1",
  "station_id": "abc123...",
  "beacon_message": "W1XYZ Grid FN42",
  "auto_switch": true,
  "tx_beacon": true,
  "adaptive_mode": false
}
```

## CLI Usage

```bash
# Start scheduler daemon
python3 beacon/scheduler.py

# One-shot beacon (for testing)
python3 beacon/scheduler.py --beacon-now

# Status check (includes peer list)
python3 beacon/scheduler.py --status

# Show discovered peers only
python3 beacon/scheduler.py --peers

# Listen-only mode (no TX, no mode switching)
python3 beacon/scheduler.py --listen-only --verbose

# Test mode (no TX)
python3 beacon/scheduler.py --test --verbose

# Generate default config
python3 beacon/scheduler.py --generate-config
```

## Peer Discovery

The beacon listener automatically tracks discovered peers:

```python
# Peer data structure
{
    "identity": "0123456789abcdef...",      # Full RNS identity hash
    "identity_short": "0123456789abcdef",   # Truncated for display
    "first_seen": "2026-01-30T10:00:00",
    "last_seen": "2026-01-30T10:30:00",
    "age_seconds": 1800,
    "message": "W1ABC FN42",                # Callsign/grid from beacon
    "flags": 12,
    "is_propagation_node": true,
    "accepts_links": true,
    "transport_enabled": false,
    "rx_count": 5,                          # Beacons received
    "last_rx_level": -15.5                  # dB at last reception
}
```

Peers expire after 2 hours (configurable) if no beacon received.

## Reticulum Network Integration

The beacon system plugs into your existing Reticulum network:

```
┌─────────────────────────────────────────────────────────────┐
│                     YOUR RETICULUM NODE                     │
│                                                             │
│  Interfaces:                                                │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐           │
│  │ I2P     │ │ TCP     │ │ LoRa    │ │ HF+     │           │
│  │         │ │         │ │ RNode   │ │ Beacon  │           │
│  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘           │
│       │          │          │          │                   │
│       └──────────┴──────────┴──────────┘                   │
│                         │                                   │
│              ┌──────────▼──────────┐                       │
│              │  Reticulum Router   │                       │
│              │  (path table,       │                       │
│              │   announces,        │                       │
│              │   transport)        │                       │
│              └──────────┬──────────┘                       │
│                         │                                   │
│              ┌──────────▼──────────┐                       │
│              │  LXMF / Sideband    │                       │
│              │  (messages, files)  │                       │
│              └─────────────────────┘                       │
└─────────────────────────────────────────────────────────────┘
```

### How It Works

1. **Station A** (you) has HF, LoRa, and I2P interfaces
2. **Station B** (1000 miles away) has only HF
3. **Station C** (local) has only LoRa

**Discovery:**
- Station B sends beacon on HF → you hear it → you know B exists
- Station C announces on LoRa → you hear it → you know C exists

**Routing:**
- C wants to message B
- C sends to your LoRa interface
- Your Reticulum routes it to your HF interface
- B receives it via HF

**You are the bridge.** Your multi-interface node connects isolated networks.

### RNS Bridge Module

The `rns_bridge.py` module connects beacon discovery to Reticulum:

```python
from beacon.scheduler import BeaconScheduler, BeaconConfig
from beacon.rns_bridge import RNSBridge, integrate_with_scheduler

# Create scheduler and bridge
config = BeaconConfig.from_file('/etc/reticulumhf/beacon.json')
scheduler = BeaconScheduler(config)
bridge = RNSBridge()

# Connect bridge to Reticulum
bridge.connect()

# Wire them together
integrate_with_scheduler(scheduler, bridge)

# Now when beacons are received:
# 1. Peer added to beacon peer table
# 2. RNS bridge checks if path known
# 3. If not, requests path from Reticulum
# 4. When path resolves, peer is routable
```

### Multi-Interface Example Config

```ini
# /home/pi/.reticulum/config

[reticulum]
  enable_transport = yes

[interfaces]
  # Local connections (phones)
  [[TCP Gateway]]
    type = TCPServerInterface
    listen_port = 4242

  # HF Radio (via freedvtnc2 + beacon)
  [[FreeDV HF]]
    type = TCPClientInterface
    target_host = 127.0.0.1
    target_port = 8001
    kiss_framing = yes

  # LoRa RNode (local mesh)
  [[LoRa RNode]]
    type = RNodeInterface
    port = /dev/ttyUSB1
    frequency = 915000000
    bandwidth = 125000
    spreading_factor = 8

  # I2P (anonymous global)
  [[I2P]]
    type = I2PInterface
    peers = i2p.reticulum.network
```

With this config, your node routes between all four networks automatically.

## Future Work

### Phase 1: Basic Beacon - COMPLETE
- [x] Mode switching infrastructure
- [x] Beacon packet format (26-37 bytes with FEC)
- [x] Scheduler daemon with time-based windows
- [x] KISS frame TX via freedvtnc2
- [ ] Integration testing on Pi hardware

### Phase 2: Discovery - COMPLETE
- [x] Beacon RX and KISS frame parsing
- [x] Peer table with automatic expiration
- [x] RX level tracking per peer
- [x] Listen-only mode
- [ ] Out-of-band ID exchange (QR code generation)
- [ ] Web UI for beacon status/peers

### Phase 3: Intelligent ARQ - PARTIAL
- [x] Adaptive mode selection framework (SNR-based)
- [x] Channel sensing before TX
- [ ] Link quality tracking over time
- [ ] Automatic fallback to robust modes
- [ ] Hysteresis to prevent mode flapping

### Phase 4: Reticulum Integration - NOT STARTED
- [ ] Custom Reticulum interface with beacon support
- [ ] Announce suppression during beacon windows
- [ ] Beacon-discovered peer announcement injection
- [ ] Propagation node sync over beacon links
- [ ] LXMF message routing to beacon-discovered peers

## References

- [Codec2 Data Modes](https://github.com/drowe67/codec2/blob/main/README_data.md)
- [Reticulum Manual](https://reticulum.network/manual/)
- [freedvtnc2](https://github.com/xssfox/freedvtnc2)
- [FreeDATA](https://github.com/DJ2LS/FreeDATA)
- [Reticulum Low Bit Rate Discussion](https://github.com/markqvist/Reticulum/discussions/11)
