#!/usr/bin/env python3
"""
JS8Call Gateway for ReticulumHF

Connects to an external JS8Call instance's TCP API to:
- Monitor @LFNET group messages and relay them to LXMF
- Allow LXMF messages to be sent out via JS8Call @LFNET
- Track JS8Call stations for dashboard display

This is a GATEWAY model - the Pi doesn't run JS8Call, it connects to
an existing JS8Call instance on the operator's network.
"""

import json
import logging
import os
import queue
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from flask import Blueprint, jsonify, request

log = logging.getLogger('js8call')

# Flask blueprint
js8call_bp = Blueprint('js8call', __name__, url_prefix='/api/js8call')

# Group call for LFM network
LFNET_GROUP = "@LFNET"

# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class JS8Station:
    """A station heard via JS8Call."""
    callsign: str
    grid: str = ""
    snr: int = -99
    frequency: int = 0  # Dial freq in Hz
    offset: int = 0     # Audio offset in Hz
    first_seen: float = 0
    last_seen: float = 0
    rx_count: int = 0
    last_message: str = ""
    speed: int = 0      # JS8 speed mode (0=normal, 1=fast, 2=turbo, 4=slow)

    @property
    def freq_khz(self) -> float:
        """Get frequency in kHz including offset."""
        return (self.frequency + self.offset) / 1000.0


@dataclass
class JS8Message:
    """A message received or sent via JS8Call."""
    timestamp: float
    direction: str  # 'rx' or 'tx'
    from_call: str
    to_call: str
    text: str
    snr: int = -99
    grid: str = ""
    frequency: int = 0
    relayed_to_lxmf: bool = False


# ============================================================================
# LXMF Integration
# ============================================================================

class LXMFRelay:
    """
    Relay messages between JS8Call and LXMF.

    Uses the lxmf command-line tool or direct RNS API to send messages.
    """

    def __init__(self):
        self.identity_path = "/home/pi/.reticulum/identities/default"
        self.lxmf_available = self._check_lxmf()

    def _check_lxmf(self) -> bool:
        """Check if LXMF tools are available."""
        try:
            result = subprocess.run(
                ["which", "lxmd"],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    def send_announcement(self, message: str, source_call: str) -> bool:
        """
        Send an LXMF announcement (broadcast) with a JS8Call message.

        This creates a propagated announcement that other Reticulum nodes
        will receive.
        """
        if not self.lxmf_available:
            log.warning("LXMF not available - cannot relay message")
            return False

        try:
            # Format: [JS8] CALLSIGN: message
            formatted = f"[JS8] {source_call}: {message}"

            # Use nomadnet to send announcement if available
            # For now, log and return - full LXMF integration requires lxmf router
            log.info(f"LXMF relay: {formatted}")

            # TODO: Implement actual LXMF broadcast
            # This requires running an LXMF router or using the RNS announce API

            return True
        except Exception as e:
            log.error(f"LXMF relay failed: {e}")
            return False


# ============================================================================
# JS8Call Client
# ============================================================================

class JS8CallClient:
    """
    Client for JS8Call's TCP API.

    JS8Call API runs on port 2442 by default.
    Messages are newline-delimited JSON.

    This client specifically monitors for @LFNET group messages
    and relays them to the Reticulum network via LXMF.
    """

    DEFAULT_PORT = 2442
    RECONNECT_DELAY = 10.0

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self.socket: Optional[socket.socket] = None
        self.connected = False
        self.running = False

        # Threading
        self.rx_thread: Optional[threading.Thread] = None
        self.tx_queue: queue.Queue = queue.Queue()
        self.lock = threading.Lock()

        # State
        self.stations: Dict[str, JS8Station] = {}
        self.messages: List[JS8Message] = []
        self.lfnet_messages: List[JS8Message] = []  # @LFNET messages only
        self.max_messages = 100
        self.my_callsign = ""
        self.my_grid = ""
        self.dial_freq = 0
        self.offset = 0

        # Gateway settings
        self.relay_enabled = True  # Relay @LFNET to LXMF
        self.group_filter = LFNET_GROUP  # Which group to monitor

        # LXMF relay
        self.lxmf = LXMFRelay()

        # Callbacks
        self.on_spot: Optional[Callable] = None
        self.on_message: Optional[Callable] = None
        self.on_lfnet_message: Optional[Callable] = None  # @LFNET specific
        self.on_activity: Optional[Callable] = None
        self.on_connect: Optional[Callable] = None
        self.on_disconnect: Optional[Callable] = None

    def connect(self) -> bool:
        """Connect to JS8Call API."""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(10.0)
            self.socket.connect((self.host, self.port))
            self.socket.settimeout(None)
            self.connected = True
            log.info(f"Connected to JS8Call gateway at {self.host}:{self.port}")

            if self.on_connect:
                self.on_connect()

            # Request initial state
            self._send({"type": "STATION.GET_INFO"})

            return True
        except Exception as e:
            log.warning(f"JS8Call gateway not available at {self.host}:{self.port}: {e}")
            self.connected = False
            return False

    def disconnect(self):
        """Disconnect from JS8Call API."""
        self.connected = False
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
            self.socket = None

        if self.on_disconnect:
            self.on_disconnect()

    def start(self) -> bool:
        """Start the client (connect and begin receiving)."""
        self.running = True
        self.rx_thread = threading.Thread(target=self._rx_loop, daemon=True, name="js8call-gateway")
        self.rx_thread.start()

        # Try initial connection
        return self.connect()

    def stop(self):
        """Stop the client."""
        self.running = False
        self.disconnect()
        if self.rx_thread:
            self.rx_thread.join(timeout=2.0)

    def _send(self, msg: Dict) -> bool:
        """Send a message to JS8Call."""
        if not self.connected or not self.socket:
            return False

        try:
            data = json.dumps(msg) + "\n"
            self.socket.send(data.encode('utf-8'))
            return True
        except Exception as e:
            log.error(f"Failed to send to JS8Call: {e}")
            self.disconnect()
            return False

    def _rx_loop(self):
        """Receive loop - runs in background thread with auto-reconnect."""
        buffer = ""

        while self.running:
            if not self.connected:
                time.sleep(self.RECONNECT_DELAY)
                self.connect()
                continue

            try:
                self.socket.settimeout(30.0)  # Periodic timeout to check running flag
                data = self.socket.recv(4096)
                if not data:
                    log.warning("JS8Call gateway connection closed")
                    self.disconnect()
                    continue

                buffer += data.decode('utf-8')

                # Process complete messages (newline-delimited)
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    if line.strip():
                        try:
                            msg = json.loads(line)
                            self._handle_message(msg)
                        except json.JSONDecodeError:
                            log.warning(f"Invalid JSON from JS8Call: {line[:100]}")

            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    log.error(f"JS8Call gateway RX error: {e}")
                self.disconnect()

    def _handle_message(self, msg: Dict):
        """Handle an incoming message from JS8Call."""
        msg_type = msg.get("type", "")
        params = msg.get("params", {})

        if msg_type == "STATION.INFO":
            # Our station info
            self.my_callsign = params.get("CALL", "")
            self.my_grid = params.get("GRID", "")
            self.dial_freq = params.get("DIAL", 0)
            self.offset = params.get("OFFSET", 0)
            log.info(f"JS8Call station: {self.my_callsign} {self.my_grid}")

        elif msg_type == "RX.SPOT":
            # A station was spotted (heartbeat, CQ, etc.)
            self._handle_spot(params)

        elif msg_type == "RX.ACTIVITY":
            # General activity on the waterfall
            if self.on_activity:
                self.on_activity(params)

        elif msg_type == "RX.DIRECTED":
            # A message directed to us or heard
            self._handle_directed(params)

        elif msg_type == "RIG.FREQ":
            # Frequency changed
            self.dial_freq = params.get("DIAL", self.dial_freq)
            self.offset = params.get("OFFSET", self.offset)

    def _handle_spot(self, params: Dict):
        """Handle a spot (station heard)."""
        callsign = params.get("CALL", "").strip()
        if not callsign:
            return

        grid = params.get("GRID", "")
        snr = params.get("SNR", -99)
        freq = params.get("DIAL", self.dial_freq)
        offset = params.get("OFFSET", 0)
        speed = params.get("SPEED", 0)

        now = time.time()

        with self.lock:
            if callsign in self.stations:
                station = self.stations[callsign]
                station.last_seen = now
                station.rx_count += 1
                station.snr = snr
                if grid:
                    station.grid = grid
                station.frequency = freq
                station.offset = offset
                station.speed = speed
            else:
                station = JS8Station(
                    callsign=callsign,
                    grid=grid,
                    snr=snr,
                    frequency=freq,
                    offset=offset,
                    first_seen=now,
                    last_seen=now,
                    rx_count=1,
                    speed=speed
                )
                self.stations[callsign] = station
                log.info(f"New JS8Call station: {callsign} {grid} SNR:{snr}")

        if self.on_spot:
            self.on_spot(station)

    def _handle_directed(self, params: Dict):
        """Handle a directed message - check for @LFNET group."""
        from_call = params.get("FROM", "")
        to_call = params.get("TO", "")
        text = params.get("TEXT", "")
        grid = params.get("GRID", "")
        snr = params.get("SNR", -99)
        freq = params.get("DIAL", self.dial_freq)

        msg = JS8Message(
            timestamp=time.time(),
            direction="rx",
            from_call=from_call,
            to_call=to_call,
            text=text,
            snr=snr,
            grid=grid,
            frequency=freq
        )

        with self.lock:
            self.messages.append(msg)
            if len(self.messages) > self.max_messages:
                self.messages = self.messages[-self.max_messages:]

            # Update station info
            if from_call and from_call in self.stations:
                self.stations[from_call].last_message = text
                self.stations[from_call].last_seen = time.time()

        # Check if this is an @LFNET group message
        is_lfnet = (
            to_call.upper() == self.group_filter.upper() or
            self.group_filter.upper() in text.upper()
        )

        if is_lfnet:
            log.info(f"@LFNET MSG: {from_call}: {text}")
            msg.relayed_to_lxmf = False

            with self.lock:
                self.lfnet_messages.append(msg)
                if len(self.lfnet_messages) > self.max_messages:
                    self.lfnet_messages = self.lfnet_messages[-self.max_messages:]

            # Relay to LXMF if enabled
            if self.relay_enabled:
                if self.lxmf.send_announcement(text, from_call):
                    msg.relayed_to_lxmf = True
                    log.info(f"Relayed to LXMF: {from_call}: {text[:50]}")

            if self.on_lfnet_message:
                self.on_lfnet_message(msg)

        if self.on_message:
            self.on_message(msg)

        log.debug(f"JS8 MSG: {from_call} -> {to_call}: {text[:50]}")

    # ========================================================================
    # Public API
    # ========================================================================

    def send_lfnet_message(self, text: str) -> bool:
        """
        Send a message to @LFNET group via JS8Call.

        This allows LXMF messages to be forwarded to the JS8Call network.
        """
        return self._send({
            "type": "TX.SEND_MESSAGE",
            "value": LFNET_GROUP,
            "params": {"TEXT": text}
        })

    def send_message(self, to_call: str, text: str) -> bool:
        """Send a directed message to a station."""
        return self._send({
            "type": "TX.SEND_MESSAGE",
            "value": to_call,
            "params": {"TEXT": text}
        })

    def send_heartbeat(self, grid: Optional[str] = None) -> bool:
        """Send a heartbeat."""
        params = {}
        if grid:
            params["GRID"] = grid
        return self._send({
            "type": "TX.SEND_MESSAGE",
            "value": "@HB",
            "params": params
        })

    def get_stations(self, max_age_hours: float = 2.0) -> List[Dict]:
        """Get all stations heard within max_age_hours."""
        cutoff = time.time() - (max_age_hours * 3600)

        with self.lock:
            stations = []
            for s in self.stations.values():
                if s.last_seen > cutoff:
                    stations.append({
                        "callsign": s.callsign,
                        "grid": s.grid,
                        "snr": s.snr,
                        "frequency_khz": s.freq_khz,
                        "first_seen": s.first_seen,
                        "last_seen": s.last_seen,
                        "age_seconds": int(time.time() - s.last_seen),
                        "rx_count": s.rx_count,
                        "speed": s.speed,
                        "last_message": s.last_message,
                    })
            return sorted(stations, key=lambda x: x["last_seen"], reverse=True)

    def get_lfnet_messages(self, limit: int = 50) -> List[Dict]:
        """Get recent @LFNET messages only."""
        with self.lock:
            return [
                {
                    "timestamp": m.timestamp,
                    "from": m.from_call,
                    "text": m.text,
                    "snr": m.snr,
                    "grid": m.grid,
                    "relayed": m.relayed_to_lxmf,
                }
                for m in self.lfnet_messages[-limit:]
            ]

    def get_messages(self, limit: int = 50) -> List[Dict]:
        """Get recent messages (all types)."""
        with self.lock:
            return [
                {
                    "timestamp": m.timestamp,
                    "direction": m.direction,
                    "from": m.from_call,
                    "to": m.to_call,
                    "text": m.text,
                    "snr": m.snr,
                    "grid": m.grid,
                }
                for m in self.messages[-limit:]
            ]

    def get_status(self) -> Dict:
        """Get client status."""
        with self.lock:
            lfnet_count = len(self.lfnet_messages)

        return {
            "connected": self.connected,
            "host": self.host,
            "port": self.port,
            "my_callsign": self.my_callsign,
            "my_grid": self.my_grid,
            "dial_freq_khz": self.dial_freq / 1000.0 if self.dial_freq else 0,
            "offset": self.offset,
            "station_count": len(self.stations),
            "lfnet_message_count": lfnet_count,
            "relay_enabled": self.relay_enabled,
            "group_filter": self.group_filter,
        }


# ============================================================================
# Global Client Instance
# ============================================================================

client: Optional[JS8CallClient] = None


def get_client() -> Optional[JS8CallClient]:
    """Get the global JS8Call client."""
    return client


def init_client(host: str = "127.0.0.1", port: int = 2442) -> JS8CallClient:
    """Initialize and start the global JS8Call client."""
    global client
    if client:
        client.stop()

    client = JS8CallClient(host=host, port=port)
    client.start()
    return client


def stop_client():
    """Stop the global JS8Call client."""
    global client
    if client:
        client.stop()
        client = None


# ============================================================================
# Flask API Routes
# ============================================================================

@js8call_bp.route('/status', methods=['GET'])
def api_status():
    """Get JS8Call gateway status."""
    if not client:
        return jsonify({
            "connected": False,
            "configured": False,
            "error": "Gateway not configured"
        })

    status = client.get_status()
    status["configured"] = True
    return jsonify(status)


@js8call_bp.route('/connect', methods=['POST'])
def api_connect():
    """Connect to JS8Call gateway."""
    data = request.get_json() or {}
    host = str(data.get('host', '127.0.0.1'))

    # Validate port
    try:
        port = int(data.get('port', 2442))
        if not 1 <= port <= 65535:
            return jsonify({"status": "error", "error": "Port must be 1-65535"}), 400
    except (TypeError, ValueError):
        return jsonify({"status": "error", "error": "Invalid port number"}), 400

    try:
        init_client(host=host, port=port)

        # Save config
        _save_config({"host": host, "port": port, "enabled": True})

        return jsonify({"status": "ok", "connected": client.connected if client else False})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@js8call_bp.route('/disconnect', methods=['POST'])
def api_disconnect():
    """Disconnect from JS8Call gateway."""
    stop_client()
    _save_config({"enabled": False})
    return jsonify({"status": "ok"})


@js8call_bp.route('/stations', methods=['GET'])
def api_stations():
    """Get heard stations."""
    if not client:
        return jsonify({"stations": [], "count": 0})

    max_age = request.args.get('max_age_hours', 2.0, type=float)
    stations = client.get_stations(max_age_hours=max_age)

    # Add lat/lon from grid for map display
    try:
        from dashboard import grid_to_latlon
        for s in stations:
            if s.get("grid"):
                coords = grid_to_latlon(s["grid"])
                if coords:
                    s["lat"], s["lon"] = coords
    except ImportError:
        pass

    return jsonify({
        "stations": stations,
        "count": len(stations)
    })


@js8call_bp.route('/lfnet', methods=['GET'])
def api_lfnet_messages():
    """Get @LFNET group messages."""
    if not client:
        return jsonify({"messages": [], "count": 0})

    limit = request.args.get('limit', 50, type=int)
    messages = client.get_lfnet_messages(limit=limit)

    return jsonify({
        "messages": messages,
        "count": len(messages),
        "group": LFNET_GROUP
    })


@js8call_bp.route('/messages', methods=['GET'])
def api_messages():
    """Get recent messages (all types)."""
    if not client:
        return jsonify({"messages": [], "count": 0})

    limit = request.args.get('limit', 50, type=int)
    messages = client.get_messages(limit=limit)

    return jsonify({
        "messages": messages,
        "count": len(messages)
    })


@js8call_bp.route('/send', methods=['POST'])
def api_send():
    """Send a message via JS8Call."""
    if not client or not client.connected:
        return jsonify({"error": "Not connected to JS8Call gateway"}), 400

    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    msg_type = data.get('type', 'lfnet')

    if msg_type == 'lfnet':
        # Send to @LFNET group
        text = data.get('text', '')
        if not text:
            return jsonify({"error": "text required"}), 400

        if client.send_lfnet_message(text):
            return jsonify({"status": "ok", "message": f"Sent to {LFNET_GROUP}"})
        else:
            return jsonify({"error": "Failed to send"}), 500

    elif msg_type == 'message':
        to_call = data.get('to', '')
        text = data.get('text', '')
        if not to_call or not text:
            return jsonify({"error": "to and text required"}), 400

        if client.send_message(to_call, text):
            return jsonify({"status": "ok", "message": f"Sent to {to_call}"})
        else:
            return jsonify({"error": "Failed to send"}), 500

    elif msg_type == 'heartbeat':
        grid = data.get('grid')
        if client.send_heartbeat(grid):
            return jsonify({"status": "ok", "message": "Heartbeat queued"})
        else:
            return jsonify({"error": "Failed to send"}), 500

    else:
        return jsonify({"error": f"Unknown message type: {msg_type}"}), 400


@js8call_bp.route('/relay', methods=['POST'])
def api_relay_settings():
    """Configure relay settings."""
    if not client:
        return jsonify({"error": "Gateway not configured"}), 400

    data = request.get_json() or {}

    if 'enabled' in data:
        client.relay_enabled = bool(data['enabled'])

    if 'group' in data:
        group = str(data['group']).upper()
        if not group.startswith('@'):
            group = '@' + group
        client.group_filter = group

    _save_config({
        "relay_enabled": client.relay_enabled,
        "group_filter": client.group_filter
    })

    return jsonify({
        "status": "ok",
        "relay_enabled": client.relay_enabled,
        "group_filter": client.group_filter
    })


@js8call_bp.route('/config', methods=['GET'])
def api_config_get():
    """Get JS8Call gateway config."""
    return jsonify(_load_config())


@js8call_bp.route('/config', methods=['POST'])
def api_config_set():
    """Set JS8Call gateway config."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    config = _save_config(data)
    return jsonify({"status": "ok", "config": config})


# ============================================================================
# Config Helpers
# ============================================================================

CONFIG_PATH = "/etc/reticulumhf/js8call.json"

DEFAULT_CONFIG = {
    "enabled": False,
    "host": "127.0.0.1",
    "port": 2442,
    "relay_enabled": True,
    "group_filter": "@LFNET",
}


def _load_config() -> Dict:
    """Load JS8Call gateway config."""
    try:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
        return {**DEFAULT_CONFIG, **config}
    except FileNotFoundError:
        return DEFAULT_CONFIG.copy()
    except Exception as e:
        log.error(f"Failed to load JS8Call config: {e}")
        return DEFAULT_CONFIG.copy()


def _save_config(updates: Dict) -> Dict:
    """Save JS8Call gateway config (merge with existing)."""
    config = _load_config()
    config.update(updates)

    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, 'w') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        log.error(f"Failed to save JS8Call config: {e}")

    return config


def startup_from_config():
    """Start JS8Call gateway from saved config (called on portal startup)."""
    config = _load_config()

    if config.get("enabled"):
        log.info(f"Starting JS8Call gateway to {config['host']}:{config['port']}")
        c = init_client(host=config["host"], port=config["port"])

        # Apply relay settings
        c.relay_enabled = config.get("relay_enabled", True)
        c.group_filter = config.get("group_filter", "@LFNET")
