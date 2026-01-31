#!/usr/bin/env python3
"""
ReticulumHF Beacon Scheduler - Hybrid beacon/ARQ mode manager.

This daemon implements a stateless beacon approach for HF Reticulum:
- Scheduled beacon windows using narrowband FEC modes (DATAC4/DATAC13)
- ARQ data transfer using higher throughput modes (DATAC1/DATAC3)
- Automatic mode switching without service restarts

Architecture:
    Beacon Mode (DATAC4/DATAC13):
        - Narrowband (250 Hz / 200 Hz)
        - Strong FEC, works at -4 dB SNR
        - Used for: announcements, discovery, heartbeats
        - ~87 bps / ~64 bps - slow but robust

    ARQ Mode (DATAC1/DATAC3):
        - Wider bandwidth (1700 Hz / 500 Hz)
        - Higher throughput for data transfer
        - Used for: messages, file transfers, links
        - ~980 bps / ~321 bps

Protocol:
    :00 and :30 each hour = Beacon window (configurable)
    - Switch to DATAC4
    - TX beacon packet (identity + capabilities)
    - Listen for 30 seconds
    - Switch back to ARQ mode

    All other times = ARQ mode
    - Normal Reticulum operation via freedvtnc2
    - KISS framing handles packet boundaries
"""

import argparse
import json
import logging
import os
import signal
import socket
import struct
import sys
import threading
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger('beacon-scheduler')


class Mode(Enum):
    """Operating modes for the beacon scheduler."""
    BEACON = "beacon"      # Stateless beacon transmission/reception
    ARQ = "arq"            # Normal ARQ data transfer
    LISTENING = "listen"   # Passive listening (no TX)


class FreeDVMode(Enum):
    """FreeDV data modes supported by freedvtnc2."""
    # Beacon mode (narrowband, robust FEC)
    # DATAC4 is the only suitable beacon mode - 56 bytes/frame fits our 26-47 byte beacons
    # DATAC13/14 have only 14/3 bytes per frame - too small without fragmentation
    DATAC4 = "DATAC4"      # 250 Hz, 87 bps, -4 dB SNR, 56 bytes/frame

    # ARQ modes (higher throughput)
    DATAC0 = "DATAC0"      # 500 Hz, 291 bps, 0 dB SNR, 14 bytes/frame
    DATAC1 = "DATAC1"      # 1700 Hz, 980 bps, 5 dB SNR, 510 bytes/frame
    DATAC3 = "DATAC3"      # 500 Hz, 321 bps, 0 dB SNR, 126 bytes/frame


@dataclass
class BeaconConfig:
    """Configuration for the beacon scheduler."""
    # Beacon timing - 6-hour intervals at 00:00, 06:00, 12:00, 18:00 UTC
    beacon_hours_utc: List[int] = field(default_factory=lambda: [0, 6, 12, 18])
    beacon_minute: int = 0              # Minute within hour to beacon
    beacon_duration_sec: int = 120      # How long to stay in beacon mode (2 min)
    beacon_tx_delay_sec: int = 10       # Delay before TX (listen first)

    # Legacy support - if beacon_minutes is set, use that instead
    beacon_minutes: List[int] = field(default_factory=list)

    # FreeDV modes
    beacon_mode: FreeDVMode = FreeDVMode.DATAC4
    arq_mode: FreeDVMode = FreeDVMode.DATAC1

    # Network settings
    freedvtnc2_cmd_host: str = "127.0.0.1"
    freedvtnc2_cmd_port: int = 8002
    freedvtnc2_kiss_port: int = 8001
    command_timeout: float = 5.0

    # Beacon content
    station_id: str = ""                # Reticulum identity hash (hex)
    beacon_message: str = ""            # Optional text message

    # Behavior
    auto_switch: bool = True            # Auto-switch modes on schedule
    tx_beacon: bool = True              # Actually transmit beacons
    adaptive_mode: bool = False         # Switch ARQ mode based on SNR
    snr_threshold_low: float = -2.0     # Below this, use DATAC3
    snr_threshold_high: float = 3.0     # Above this, use DATAC1

    # Operating mode (hybrid, hf_only, internet_only)
    operating_mode: str = "hybrid"

    # Dashboard integration
    dashboard_url: str = ""             # URL to POST discovered peers (e.g., http://127.0.0.1/api/dashboard/peers)

    @classmethod
    def from_file(cls, path: Path) -> 'BeaconConfig':
        """Load configuration from JSON file."""
        if not path.exists():
            return cls()
        with open(path) as f:
            data = json.load(f)

        config = cls()
        for key, value in data.items():
            if hasattr(config, key):
                if key in ('beacon_mode', 'arq_mode'):
                    setattr(config, key, FreeDVMode(value))
                else:
                    setattr(config, key, value)
        return config

    def to_file(self, path: Path) -> None:
        """Save configuration to JSON file."""
        data = {
            'beacon_minutes': self.beacon_minutes,
            'beacon_duration_sec': self.beacon_duration_sec,
            'beacon_tx_delay_sec': self.beacon_tx_delay_sec,
            'beacon_mode': self.beacon_mode.value,
            'arq_mode': self.arq_mode.value,
            'freedvtnc2_cmd_host': self.freedvtnc2_cmd_host,
            'freedvtnc2_cmd_port': self.freedvtnc2_cmd_port,
            'freedvtnc2_kiss_port': self.freedvtnc2_kiss_port,
            'command_timeout': self.command_timeout,
            'station_id': self.station_id,
            'beacon_message': self.beacon_message,
            'auto_switch': self.auto_switch,
            'tx_beacon': self.tx_beacon,
            'adaptive_mode': self.adaptive_mode,
            'snr_threshold_low': self.snr_threshold_low,
            'snr_threshold_high': self.snr_threshold_high,
            'dashboard_url': self.dashboard_url,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)


class FreeDVTNC2Client:
    """Client for freedvtnc2-lfm command interface."""

    def __init__(self, host: str = "127.0.0.1", cmd_port: int = 8002,
                 kiss_port: int = 8001, timeout: float = 5.0):
        self.host = host
        self.cmd_port = cmd_port
        self.kiss_port = kiss_port
        self.timeout = timeout

    def send_command(self, command: str) -> Tuple[bool, str]:
        """Send command to freedvtnc2 command interface."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect((self.host, self.cmd_port))
            sock.send(f"{command}\n".encode('utf-8'))
            response = sock.recv(1024).decode('utf-8').strip()
            sock.close()

            success = response.startswith("OK")
            return success, response
        except socket.timeout:
            return False, "ERROR Connection timeout"
        except ConnectionRefusedError:
            return False, "ERROR freedvtnc2 not running"
        except Exception as e:
            return False, f"ERROR {str(e)}"

    def ping(self) -> bool:
        """Check if freedvtnc2 is responding."""
        success, _ = self.send_command("PING")
        return success

    def get_status(self) -> Optional[Dict]:
        """Get current modem status."""
        success, response = self.send_command("STATUS")
        if not success:
            return None

        # Parse: "OK STATUS MODE=DATAC1 VOLUME=0 FOLLOW=OFF PTT=OFF CHANNEL=CLEAR"
        status = {}
        parts = response.replace("OK STATUS ", "").split()
        for part in parts:
            if '=' in part:
                key, value = part.split('=', 1)
                status[key.lower()] = value
        return status

    def get_rx_level(self) -> Optional[float]:
        """Get current RX audio level in dB."""
        success, response = self.send_command("LEVELS")
        if not success:
            return None

        # Parse: "OK LEVELS RX=-12.5"
        try:
            for part in response.split():
                if part.startswith("RX="):
                    value = part.replace("RX=", "").replace("dB", "")
                    return float(value)
        except ValueError:
            pass
        return None

    def set_mode(self, mode: FreeDVMode) -> bool:
        """Switch FreeDV mode (instant, no restart)."""
        success, response = self.send_command(f"MODE {mode.value}")
        if success:
            log.info(f"Mode switched to {mode.value}")
        else:
            log.error(f"Failed to switch mode: {response}")
        return success

    def get_mode(self) -> Optional[FreeDVMode]:
        """Get current FreeDV mode."""
        status = self.get_status()
        if status and 'mode' in status:
            try:
                return FreeDVMode(status['mode'])
            except ValueError:
                pass
        return None

    def set_volume(self, volume_db: int) -> bool:
        """Set TX volume (-20 to 0 dB)."""
        success, _ = self.send_command(f"VOLUME {volume_db}")
        return success

    def is_channel_clear(self) -> bool:
        """Check if channel is clear (no RX activity)."""
        status = self.get_status()
        return status and status.get('channel', '').upper() == 'CLEAR'

    # TX Gate Control Methods
    def tx_enable(self) -> bool:
        """Enable TX (allow all PTT)."""
        success, _ = self.send_command("TX ENABLE")
        return success

    def tx_disable(self) -> bool:
        """Disable TX (block all PTT)."""
        success, _ = self.send_command("TX DISABLE")
        return success

    def tx_window(self, seconds: int) -> bool:
        """Open TX window for specified seconds."""
        success, _ = self.send_command(f"TX WINDOW {seconds}")
        if success:
            log.info(f"TX window opened for {seconds} seconds")
        return success

    def get_tx_status(self) -> Optional[Dict]:
        """Get TX gate status."""
        success, response = self.send_command("TX STATUS")
        if not success:
            return None

        # Parse: "OK TX ENABLED" or "OK TX DISABLED" or "OK TX WINDOW:45"
        status = {"enabled": False, "window": False, "remaining": 0}
        if "ENABLED" in response:
            status["enabled"] = True
        elif "WINDOW" in response:
            status["window"] = True
            # Extract remaining seconds
            if ":" in response:
                try:
                    status["remaining"] = int(response.split(":")[-1])
                except ValueError:
                    pass
        return status

    def send_kiss_frame(self, data: bytes) -> bool:
        """Send a KISS frame via the data port."""
        # KISS framing: FEND CMD DATA FEND
        FEND = 0xC0
        FESC = 0xDB
        TFEND = 0xDC
        TFESC = 0xDD

        # Escape special bytes
        escaped = bytearray()
        for byte in data:
            if byte == FEND:
                escaped.extend([FESC, TFEND])
            elif byte == FESC:
                escaped.extend([FESC, TFESC])
            else:
                escaped.append(byte)

        # Build frame: FEND + CMD(0x00 for data) + escaped_data + FEND
        frame = bytes([FEND, 0x00]) + bytes(escaped) + bytes([FEND])

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect((self.host, self.kiss_port))
            sock.send(frame)
            sock.close()
            return True
        except Exception as e:
            log.error(f"Failed to send KISS frame: {e}")
            return False


class BeaconPacket:
    """
    Beacon packet format for stateless HF announcements.

    This is a minimal packet designed for narrowband FEC modes.
    It provides enough information for discovery without full
    Reticulum announcement overhead.

    Format (variable length, max ~50 bytes for DATAC4):
        - Magic (2 bytes): 0x52 0x48 ("RH" for ReticulumHF)
        - Version (1 byte): Protocol version
        - Flags (1 byte): Capabilities
        - Identity (16 bytes): Truncated RNS identity hash
        - Timestamp (4 bytes): Unix timestamp (for replay protection)
        - Checksum (2 bytes): CRC16 of above

    Total: 26 bytes minimum, fits in single DATAC4 frame (56 bytes)
    """

    MAGIC = b'RH'
    VERSION = 1

    # Flags
    FLAG_HAS_MESSAGE = 0x01
    FLAG_PROPAGATION_NODE = 0x02
    FLAG_ACCEPTS_LINKS = 0x04
    FLAG_TRANSPORT_ENABLED = 0x08

    def __init__(self, identity_hash: bytes, flags: int = 0,
                 message: str = "", timestamp: Optional[int] = None):
        self.identity_hash = identity_hash[:16]  # Truncate to 16 bytes
        self.flags = flags
        self.message = message[:20] if message else ""  # Max 20 chars
        self.timestamp = timestamp or int(time.time())

    def encode(self) -> bytes:
        """Encode beacon packet to bytes."""
        packet = bytearray()

        # Header
        packet.extend(self.MAGIC)
        packet.append(self.VERSION)

        # Set message flag if present
        flags = self.flags
        if self.message:
            flags |= self.FLAG_HAS_MESSAGE
        packet.append(flags)

        # Identity (16 bytes, zero-padded if shorter)
        identity = self.identity_hash.ljust(16, b'\x00')
        packet.extend(identity)

        # Timestamp (4 bytes, big-endian)
        packet.extend(struct.pack('>I', self.timestamp))

        # Optional message
        if self.message:
            msg_bytes = self.message.encode('utf-8')[:20]
            packet.append(len(msg_bytes))
            packet.extend(msg_bytes)

        # CRC16 checksum
        crc = self._crc16(bytes(packet))
        packet.extend(struct.pack('>H', crc))

        return bytes(packet)

    @classmethod
    def decode(cls, data: bytes) -> Optional['BeaconPacket']:
        """Decode beacon packet from bytes."""
        if len(data) < 26:
            return None

        # Verify magic
        if data[:2] != cls.MAGIC:
            return None

        # Verify CRC
        received_crc = struct.unpack('>H', data[-2:])[0]
        calculated_crc = cls._crc16(data[:-2])
        if received_crc != calculated_crc:
            return None

        version = data[2]
        flags = data[3]
        identity_hash = data[4:20]
        timestamp = struct.unpack('>I', data[20:24])[0]

        message = ""
        if flags & cls.FLAG_HAS_MESSAGE and len(data) > 26:
            msg_len = data[24]
            if len(data) >= 25 + msg_len + 2:
                message = data[25:25+msg_len].decode('utf-8', errors='ignore')

        return cls(identity_hash, flags & ~cls.FLAG_HAS_MESSAGE,
                   message, timestamp)

    @staticmethod
    def _crc16(data: bytes) -> int:
        """Calculate CRC16-CCITT."""
        crc = 0xFFFF
        for byte in data:
            crc ^= byte << 8
            for _ in range(8):
                if crc & 0x8000:
                    crc = (crc << 1) ^ 0x1021
                else:
                    crc <<= 1
                crc &= 0xFFFF
        return crc

    def __repr__(self):
        return (f"BeaconPacket(id={self.identity_hash.hex()[:8]}..., "
                f"flags={self.flags:#x}, msg='{self.message}')")


@dataclass
class DiscoveredPeer:
    """A peer discovered via beacon."""
    identity_hash: bytes
    first_seen: float
    last_seen: float
    message: str
    flags: int
    rx_count: int = 1
    last_rx_level: Optional[float] = None

    def age_seconds(self) -> float:
        """Seconds since last beacon received."""
        return time.time() - self.last_seen

    def to_dict(self) -> Dict:
        """Convert to JSON-serializable dict."""
        return {
            'identity': self.identity_hash.hex(),
            'identity_short': self.identity_hash.hex()[:16],
            'first_seen': datetime.fromtimestamp(self.first_seen).isoformat(),
            'last_seen': datetime.fromtimestamp(self.last_seen).isoformat(),
            'age_seconds': int(self.age_seconds()),
            'message': self.message,
            'flags': self.flags,
            'is_propagation_node': bool(self.flags & BeaconPacket.FLAG_PROPAGATION_NODE),
            'accepts_links': bool(self.flags & BeaconPacket.FLAG_ACCEPTS_LINKS),
            'transport_enabled': bool(self.flags & BeaconPacket.FLAG_TRANSPORT_ENABLED),
            'rx_count': self.rx_count,
            'last_rx_level': self.last_rx_level,
        }


class PeerTable:
    """
    Thread-safe table of discovered peers.

    Maintains a list of peers discovered via beacon reception,
    with automatic expiration of stale entries.
    """

    def __init__(self, max_age_seconds: int = 7200):  # 2 hour default
        self._peers: Dict[bytes, DiscoveredPeer] = {}
        self._lock = threading.Lock()
        self.max_age_seconds = max_age_seconds

    def update(self, packet: BeaconPacket, rx_level: Optional[float] = None) -> DiscoveredPeer:
        """Add or update a peer from received beacon."""
        now = time.time()
        identity = packet.identity_hash

        with self._lock:
            if identity in self._peers:
                peer = self._peers[identity]
                peer.last_seen = now
                peer.message = packet.message or peer.message
                peer.flags = packet.flags
                peer.rx_count += 1
                peer.last_rx_level = rx_level
            else:
                peer = DiscoveredPeer(
                    identity_hash=identity,
                    first_seen=now,
                    last_seen=now,
                    message=packet.message,
                    flags=packet.flags,
                    rx_count=1,
                    last_rx_level=rx_level
                )
                self._peers[identity] = peer
                log.info(f"New peer discovered: {identity.hex()[:16]}... "
                         f"msg='{packet.message}'")

            return peer

    def get_all(self, include_stale: bool = False) -> List[DiscoveredPeer]:
        """Get all peers, optionally filtering stale ones."""
        with self._lock:
            peers = list(self._peers.values())

        if not include_stale:
            peers = [p for p in peers if p.age_seconds() < self.max_age_seconds]

        return sorted(peers, key=lambda p: p.last_seen, reverse=True)

    def get(self, identity: bytes) -> Optional[DiscoveredPeer]:
        """Get a specific peer by identity."""
        with self._lock:
            return self._peers.get(identity)

    def remove_stale(self) -> int:
        """Remove peers older than max_age_seconds. Returns count removed."""
        removed = 0
        now = time.time()
        with self._lock:
            stale = [k for k, v in self._peers.items()
                     if now - v.last_seen > self.max_age_seconds]
            for key in stale:
                del self._peers[key]
                removed += 1
        if removed:
            log.info(f"Removed {removed} stale peer(s)")
        return removed

    def clear(self) -> None:
        """Clear all peers."""
        with self._lock:
            self._peers.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._peers)


class BeaconListener:
    """
    KISS frame listener for incoming beacons.

    Connects to freedvtnc2's KISS port and listens for incoming
    beacon packets, updating the peer table when valid beacons arrive.
    """

    KISS_FEND = 0xC0
    KISS_FESC = 0xDB
    KISS_TFEND = 0xDC
    KISS_TFESC = 0xDD

    def __init__(self, host: str = "127.0.0.1", port: int = 8001,
                 peer_table: Optional[PeerTable] = None,
                 tnc_client: Optional['FreeDVTNC2Client'] = None):
        self.host = host
        self.port = port
        self.peer_table = peer_table or PeerTable()
        self.tnc_client = tnc_client

        self._running = False
        self._stop_event = threading.Event()
        self._listener_thread: Optional[threading.Thread] = None
        self._socket: Optional[socket.socket] = None

        # Callbacks
        self.on_beacon_received: Optional[Callable[[BeaconPacket, DiscoveredPeer], None]] = None

    def start(self) -> bool:
        """Start the beacon listener."""
        if self._running:
            return True

        self._running = True
        self._stop_event.clear()

        self._listener_thread = threading.Thread(
            target=self._listener_loop,
            name="beacon-listener",
            daemon=True
        )
        self._listener_thread.start()

        log.info(f"Beacon listener started on {self.host}:{self.port}")
        return True

    def stop(self) -> None:
        """Stop the beacon listener."""
        if not self._running:
            return

        self._running = False
        self._stop_event.set()

        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass

        if self._listener_thread:
            self._listener_thread.join(timeout=5)

        log.info("Beacon listener stopped")

    def _listener_loop(self) -> None:
        """Main listener loop - connects and processes KISS frames."""
        reconnect_delay = 1

        while self._running and not self._stop_event.is_set():
            try:
                self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._socket.settimeout(1.0)
                self._socket.connect((self.host, self.port))
                log.info(f"Connected to KISS port {self.host}:{self.port}")
                reconnect_delay = 1  # Reset on successful connect

                self._process_frames()

            except ConnectionRefusedError:
                log.debug(f"KISS port not available, retrying in {reconnect_delay}s")
            except socket.timeout:
                pass  # Normal during shutdown
            except Exception as e:
                log.error(f"Listener error: {e}")
            finally:
                if self._socket:
                    try:
                        self._socket.close()
                    except Exception:
                        pass
                    self._socket = None

            if self._running:
                self._stop_event.wait(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30)

    def _process_frames(self) -> None:
        """Process incoming KISS frames."""
        buffer = bytearray()
        in_frame = False

        while self._running and not self._stop_event.is_set():
            try:
                data = self._socket.recv(1024)
                if not data:
                    break  # Connection closed

                for byte in data:
                    if byte == self.KISS_FEND:
                        if in_frame and len(buffer) > 1:
                            # Complete frame received
                            frame = self._unescape_kiss(buffer)
                            if frame:
                                self._handle_frame(frame)
                        buffer.clear()
                        in_frame = True
                    elif in_frame:
                        buffer.append(byte)

            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    log.error(f"Frame processing error: {e}")
                break

    def _unescape_kiss(self, data: bytearray) -> Optional[bytes]:
        """Unescape KISS frame data."""
        if len(data) < 2:
            return None

        # First byte is command (0x00 for data frames)
        cmd = data[0]
        if cmd != 0x00:
            return None  # Not a data frame

        result = bytearray()
        i = 1
        while i < len(data):
            if data[i] == self.KISS_FESC:
                if i + 1 < len(data):
                    if data[i + 1] == self.KISS_TFEND:
                        result.append(self.KISS_FEND)
                    elif data[i + 1] == self.KISS_TFESC:
                        result.append(self.KISS_FESC)
                    else:
                        result.append(data[i + 1])
                    i += 2
                else:
                    break
            else:
                result.append(data[i])
                i += 1

        return bytes(result)

    def _handle_frame(self, frame: bytes) -> None:
        """Handle a received KISS data frame."""
        # Try to parse as beacon packet
        packet = BeaconPacket.decode(frame)
        if packet is None:
            # Not a beacon packet - likely normal Reticulum traffic
            return

        # Get current RX level if TNC client available
        rx_level = None
        if self.tnc_client:
            rx_level = self.tnc_client.get_rx_level()

        # Update peer table
        peer = self.peer_table.update(packet, rx_level)

        log.info(f"Beacon received: {packet} (rx_count={peer.rx_count})")

        # Fire callback
        if self.on_beacon_received:
            try:
                self.on_beacon_received(packet, peer)
            except Exception as e:
                log.error(f"Beacon callback error: {e}")


class BeaconScheduler:
    """
    Main beacon scheduler daemon.

    Manages the beacon/ARQ mode transitions and handles:
    - Scheduled beacon windows
    - Automatic mode switching
    - Beacon packet generation and transmission
    - RX level monitoring for adaptive mode selection
    """

    def __init__(self, config: BeaconConfig):
        self.config = config
        self.tnc = FreeDVTNC2Client(
            host=config.freedvtnc2_cmd_host,
            cmd_port=config.freedvtnc2_cmd_port,
            kiss_port=config.freedvtnc2_kiss_port,
            timeout=config.command_timeout
        )

        self.current_mode = Mode.ARQ
        self.running = False
        self._stop_event = threading.Event()
        self._scheduler_thread: Optional[threading.Thread] = None

        # Peer discovery
        self.peer_table = PeerTable(max_age_seconds=7200)  # 2 hour expiry
        self.listener = BeaconListener(
            host=config.freedvtnc2_cmd_host,
            port=config.freedvtnc2_kiss_port,
            peer_table=self.peer_table,
            tnc_client=self.tnc
        )

        # Callbacks for external integration
        self.on_mode_change: Optional[Callable[[Mode, FreeDVMode], None]] = None
        self.on_beacon_rx: Optional[Callable[[BeaconPacket, DiscoveredPeer], None]] = None
        self.on_beacon_tx: Optional[Callable[[BeaconPacket], None]] = None

        # Wire up listener callback
        self.listener.on_beacon_received = self._on_beacon_received

    def start(self) -> bool:
        """Start the beacon scheduler."""
        if self.running:
            return True

        # Verify freedvtnc2 is running
        if not self.tnc.ping():
            log.error("freedvtnc2 not responding - cannot start scheduler")
            return False

        # Set initial ARQ mode
        if not self.tnc.set_mode(self.config.arq_mode):
            log.error("Failed to set initial ARQ mode")
            return False

        self.running = True
        self._stop_event.clear()

        # Start scheduler thread
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop,
            name="beacon-scheduler",
            daemon=True
        )
        self._scheduler_thread.start()

        # Start beacon listener
        self.listener.start()

        log.info(f"Beacon scheduler started (beacon={self.config.beacon_mode.value}, "
                 f"arq={self.config.arq_mode.value})")
        log.info(f"Beacon windows: :{':'.join(f'{m:02d}' for m in self.config.beacon_minutes)} each hour")

        return True

    def stop(self) -> None:
        """Stop the beacon scheduler."""
        if not self.running:
            return

        self.running = False
        self._stop_event.set()

        # Stop listener
        self.listener.stop()

        if self._scheduler_thread:
            self._scheduler_thread.join(timeout=5)

        # Return to ARQ mode
        self.tnc.set_mode(self.config.arq_mode)

        log.info("Beacon scheduler stopped")

    def _on_beacon_received(self, packet: BeaconPacket, peer: DiscoveredPeer) -> None:
        """Internal callback when beacon is received."""
        if self.on_beacon_rx:
            try:
                self.on_beacon_rx(packet, peer)
            except Exception as e:
                log.error(f"Beacon RX callback error: {e}")

    def _scheduler_loop(self) -> None:
        """Main scheduler loop."""
        while self.running and not self._stop_event.is_set():
            now = datetime.utcnow()  # Use UTC for consistent scheduling

            # Check if we're in a beacon window
            # Support both hour-based (6-hour intervals) and legacy minute-based
            if self.config.beacon_minutes:
                # Legacy: minute-based scheduling (every hour)
                in_beacon_window = (
                    self.config.auto_switch and
                    now.minute in self.config.beacon_minutes and
                    now.second < self.config.beacon_duration_sec
                )
            else:
                # New: hour-based scheduling (e.g., every 6 hours)
                in_beacon_window = (
                    self.config.auto_switch and
                    now.hour in self.config.beacon_hours_utc and
                    now.minute == self.config.beacon_minute and
                    now.second < self.config.beacon_duration_sec
                )

            if in_beacon_window and self.current_mode != Mode.BEACON:
                self._enter_beacon_mode()
            elif not in_beacon_window and self.current_mode == Mode.BEACON:
                self._enter_arq_mode()

            # Adaptive mode selection (when in ARQ mode)
            if self.config.adaptive_mode and self.current_mode == Mode.ARQ:
                self._check_adaptive_mode()

            # Sleep until next check (1 second resolution)
            self._stop_event.wait(1.0)

    def _enter_beacon_mode(self) -> None:
        """Switch to beacon mode and optionally transmit."""
        log.info("Entering beacon window")

        # Check operating mode - skip beacon in internet_only mode
        if self.config.operating_mode == "internet_only":
            log.info("Internet Only mode - skipping beacon window")
            return

        # In hybrid mode, open TX window for beacon duration
        # In hf_only mode, TX is always enabled
        if self.config.operating_mode == "hybrid":
            log.info(f"Opening TX window for {self.config.beacon_duration_sec} seconds")
            self.tnc.tx_window(self.config.beacon_duration_sec)

        # Switch to beacon mode
        if not self.tnc.set_mode(self.config.beacon_mode):
            log.error("Failed to switch to beacon mode")
            return

        self.current_mode = Mode.BEACON

        if self.on_mode_change:
            self.on_mode_change(Mode.BEACON, self.config.beacon_mode)

        # Wait before transmitting (listen first)
        if self.config.tx_beacon:
            time.sleep(self.config.beacon_tx_delay_sec)

            # Check if channel is clear before TX
            if self.tnc.is_channel_clear():
                self._transmit_beacon()
            else:
                log.info("Channel busy - skipping beacon TX")

    def _enter_arq_mode(self) -> None:
        """Switch back to ARQ mode."""
        log.info("Exiting beacon window, returning to ARQ mode")

        if not self.tnc.set_mode(self.config.arq_mode):
            log.error("Failed to switch to ARQ mode")
            return

        self.current_mode = Mode.ARQ

        # In hybrid mode, TX window auto-closes after duration
        # No need to explicitly disable here

        if self.on_mode_change:
            self.on_mode_change(Mode.ARQ, self.config.arq_mode)

    def _transmit_beacon(self) -> None:
        """Generate and transmit a beacon packet."""
        if not self.config.station_id:
            log.warning("No station ID configured - skipping beacon TX")
            return

        try:
            identity_hash = bytes.fromhex(self.config.station_id)
        except ValueError:
            log.error(f"Invalid station ID: {self.config.station_id}")
            return

        # Build beacon packet
        flags = BeaconPacket.FLAG_ACCEPTS_LINKS
        packet = BeaconPacket(
            identity_hash=identity_hash,
            flags=flags,
            message=self.config.beacon_message
        )

        # Transmit via KISS
        data = packet.encode()
        log.info(f"Transmitting beacon ({len(data)} bytes): {packet}")

        if self.tnc.send_kiss_frame(data):
            if self.on_beacon_tx:
                self.on_beacon_tx(packet)
        else:
            log.error("Failed to transmit beacon")

    def _check_adaptive_mode(self) -> None:
        """Adjust ARQ mode based on RX signal level."""
        rx_level = self.tnc.get_rx_level()
        if rx_level is None:
            return

        current = self.tnc.get_mode()
        if current is None:
            return

        # Estimate SNR from RX level (rough approximation)
        # This assumes noise floor around -35 dB
        estimated_snr = rx_level + 35

        if estimated_snr < self.config.snr_threshold_low:
            # Poor conditions - use robust mode
            if current != FreeDVMode.DATAC3:
                log.info(f"SNR ~{estimated_snr:.1f} dB - switching to DATAC3")
                self.tnc.set_mode(FreeDVMode.DATAC3)
        elif estimated_snr > self.config.snr_threshold_high:
            # Good conditions - use fast mode
            if current != FreeDVMode.DATAC1:
                log.info(f"SNR ~{estimated_snr:.1f} dB - switching to DATAC1")
                self.tnc.set_mode(FreeDVMode.DATAC1)

    def force_beacon(self) -> bool:
        """Manually trigger a beacon transmission."""
        if not self.config.tx_beacon:
            return False

        # Temporarily switch to beacon mode
        original_mode = self.tnc.get_mode()

        if not self.tnc.set_mode(self.config.beacon_mode):
            return False

        time.sleep(0.5)  # Brief settling time
        self._transmit_beacon()
        time.sleep(0.5)

        # Return to original mode
        if original_mode:
            self.tnc.set_mode(original_mode)

        return True

    def get_status(self) -> Dict:
        """Get current scheduler status."""
        now = datetime.utcnow()
        next_beacon = None

        # Calculate next beacon window
        if self.config.beacon_minutes:
            # Legacy minute-based scheduling
            for minute in sorted(self.config.beacon_minutes):
                if minute > now.minute:
                    next_beacon = now.replace(minute=minute, second=0, microsecond=0)
                    break
            if next_beacon is None:
                next_beacon = now.replace(
                    minute=self.config.beacon_minutes[0],
                    second=0,
                    microsecond=0
                )
                next_beacon = next_beacon.replace(hour=(now.hour + 1) % 24)
        else:
            # Hour-based scheduling (6-hour intervals)
            for hour in sorted(self.config.beacon_hours_utc):
                if hour > now.hour or (hour == now.hour and now.minute < self.config.beacon_minute):
                    next_beacon = now.replace(hour=hour, minute=self.config.beacon_minute,
                                             second=0, microsecond=0)
                    break
            if next_beacon is None:
                # Wrap to next day
                from datetime import timedelta
                tomorrow = now + timedelta(days=1)
                next_beacon = tomorrow.replace(hour=self.config.beacon_hours_utc[0],
                                               minute=self.config.beacon_minute,
                                               second=0, microsecond=0)

        tnc_status = self.tnc.get_status() or {}
        peers = self.peer_table.get_all()

        return {
            'running': self.running,
            'current_mode': self.current_mode.value,
            'freedv_mode': tnc_status.get('mode', 'unknown'),
            'beacon_mode': self.config.beacon_mode.value,
            'arq_mode': self.config.arq_mode.value,
            'next_beacon_utc': next_beacon.isoformat() + 'Z' if next_beacon else None,
            'beacon_hours_utc': self.config.beacon_hours_utc,
            'beacon_minutes': self.config.beacon_minutes,  # Legacy support
            'tx_enabled': self.config.tx_beacon,
            'adaptive_mode': self.config.adaptive_mode,
            'channel_clear': self.tnc.is_channel_clear(),
            'rx_level_db': self.tnc.get_rx_level(),
            'peer_count': len(peers),
            'peers': [p.to_dict() for p in peers],
        }

    def get_peers(self) -> List[Dict]:
        """Get list of discovered peers."""
        return [p.to_dict() for p in self.peer_table.get_all()]

    def clear_peers(self) -> None:
        """Clear the peer table."""
        self.peer_table.clear()
        log.info("Peer table cleared")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='ReticulumHF Beacon Scheduler',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start with default config
  %(prog)s

  # Use custom config file
  %(prog)s --config /etc/reticulumhf/beacon.json

  # Test mode (no TX, verbose logging)
  %(prog)s --test --verbose

  # One-shot beacon (don't run scheduler)
  %(prog)s --beacon-now
"""
    )

    parser.add_argument('--config', '-c', type=Path,
                        default=Path('/etc/reticulumhf/beacon.json'),
                        help='Configuration file path')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enable verbose logging')
    parser.add_argument('--test', '-t', action='store_true',
                        help='Test mode (no TX)')
    parser.add_argument('--beacon-now', action='store_true',
                        help='Transmit one beacon and exit')
    parser.add_argument('--status', action='store_true',
                        help='Show status and exit')
    parser.add_argument('--peers', action='store_true',
                        help='Show discovered peers and exit')
    parser.add_argument('--listen-only', action='store_true',
                        help='Listen for beacons without scheduled TX')
    parser.add_argument('--generate-config', action='store_true',
                        help='Generate default config file and exit')

    args = parser.parse_args()

    if args.verbose:
        log.setLevel(logging.DEBUG)

    # Generate config
    if args.generate_config:
        config = BeaconConfig()
        config.to_file(args.config)
        print(f"Generated config: {args.config}")
        return 0

    # Load config
    config = BeaconConfig.from_file(args.config)

    if args.test:
        config.tx_beacon = False
        log.info("Test mode - TX disabled")

    # Create scheduler
    scheduler = BeaconScheduler(config)

    # Status check
    if args.status:
        if not scheduler.tnc.ping():
            print("ERROR: freedvtnc2 not responding")
            return 1
        status = scheduler.get_status()
        print(json.dumps(status, indent=2, default=str))
        return 0

    # Peers check
    if args.peers:
        peers = scheduler.get_peers()
        if not peers:
            print("No peers discovered")
        else:
            print(json.dumps(peers, indent=2, default=str))
        return 0

    # One-shot beacon
    if args.beacon_now:
        if scheduler.force_beacon():
            print("Beacon transmitted")
            return 0
        else:
            print("Failed to transmit beacon")
            return 1

    # Listen-only mode
    if args.listen_only:
        config.auto_switch = False
        config.tx_beacon = False
        log.info("Listen-only mode - no TX, no mode switching")

    # Signal handlers
    def handle_signal(signum, frame):
        log.info(f"Received signal {signum}")
        scheduler.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Callback to log beacon reception and post to dashboard
    def on_beacon_rx(packet: BeaconPacket, peer: DiscoveredPeer):
        log.info(f"BEACON RX: {packet.identity_hash.hex()[:16]}... "
                 f"msg='{packet.message}' count={peer.rx_count}")

        # POST to dashboard API if configured
        if config.dashboard_url:
            try:
                # Parse message for callsign and grid (e.g., "W1ABC FN42")
                parts = packet.message.split() if packet.message else []
                callsign = parts[0] if len(parts) > 0 else ""
                grid = parts[1] if len(parts) > 1 else ""

                peer_data = {
                    "identity": packet.identity_hash.hex(),
                    "callsign": callsign,
                    "grid": grid,
                    "rx_level_db": peer.last_rx_level if peer.last_rx_level else -99,
                    "interface": "HF",
                    "frequency_khz": 0,  # TODO: Get from radio if available
                    "flags": packet.flags,
                }
                data = json.dumps(peer_data).encode('utf-8')
                req = urllib.request.Request(
                    config.dashboard_url,
                    data=data,
                    headers={'Content-Type': 'application/json'},
                    method='POST'
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        log.debug(f"Posted peer to dashboard")
                    else:
                        log.warning(f"Dashboard returned {resp.status}")
            except urllib.error.URLError as e:
                log.debug(f"Dashboard POST failed: {e}")
            except Exception as e:
                log.debug(f"Dashboard POST error: {e}")

    scheduler.on_beacon_rx = on_beacon_rx

    # Start scheduler
    if not scheduler.start():
        log.error("Failed to start scheduler")
        return 1

    log.info(f"Listening for beacons... (Ctrl+C to stop)")

    # Run forever
    try:
        while scheduler.running:
            # Periodic stale peer cleanup
            scheduler.peer_table.remove_stale()
            time.sleep(60)
    except KeyboardInterrupt:
        pass
    finally:
        scheduler.stop()
        # Show final peer count
        peers = scheduler.get_peers()
        if peers:
            log.info(f"Session ended with {len(peers)} peer(s) discovered")

    return 0


if __name__ == '__main__':
    sys.exit(main())
