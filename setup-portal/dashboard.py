#!/usr/bin/env python3
"""
ReticulumHF Dashboard API

Provides API endpoints for the enhanced dashboard:
- Beacon peer discovery
- RX level history
- Interface status
- Network health metrics
- TAK CoT integration
"""

import json
import logging
import math
import os
import re
import socket
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

from flask import Blueprint, jsonify, request

log = logging.getLogger('dashboard')

# Create Flask blueprint
dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/api/dashboard')

# ============================================================================
# Data Storage (in-memory, resets on restart)
# ============================================================================

@dataclass
class RXReading:
    """Single RX level reading."""
    timestamp: float
    level_db: float
    mode: str = ""


@dataclass
class BeaconPeer:
    """Discovered beacon peer."""
    identity: str           # Hex string
    callsign: str = ""
    grid: str = ""
    first_seen: float = 0
    last_seen: float = 0
    rx_level_db: float = -99
    rx_count: int = 0
    interface: str = "HF"
    frequency_khz: int = 0
    flags: int = 0


class DashboardState:
    """Global dashboard state."""

    def __init__(self):
        self.rx_history: deque = deque(maxlen=720)  # 1 hour at 5s intervals
        self.peers: Dict[str, BeaconPeer] = {}
        self.lock = threading.Lock()
        self.last_rnstatus: Dict = {}
        self.last_rnstatus_time: float = 0
        self.network_health: Dict = {}

        # TAK settings
        self.tak_enabled = False
        self.tak_host = ""
        self.tak_port = 8087
        self.tak_protocol = "udp"

    def add_rx_reading(self, level_db: float, mode: str = ""):
        """Add RX level reading to history."""
        with self.lock:
            self.rx_history.append(RXReading(
                timestamp=time.time(),
                level_db=level_db,
                mode=mode
            ))

    def get_rx_history(self, minutes: int = 60) -> List[Dict]:
        """Get RX history for last N minutes."""
        cutoff = time.time() - (minutes * 60)
        with self.lock:
            return [
                {"t": r.timestamp, "db": r.level_db, "mode": r.mode}
                for r in self.rx_history
                if r.timestamp > cutoff
            ]

    def update_peer(self, identity: str, callsign: str = "", grid: str = "",
                    rx_level_db: float = -99, interface: str = "HF",
                    frequency_khz: int = 0, flags: int = 0) -> BeaconPeer:
        """Update or create a beacon peer."""
        now = time.time()
        with self.lock:
            if identity in self.peers:
                peer = self.peers[identity]
                peer.last_seen = now
                peer.rx_count += 1
                if rx_level_db > -99:
                    peer.rx_level_db = rx_level_db
                if callsign:
                    peer.callsign = callsign
                if grid:
                    peer.grid = grid
                if frequency_khz:
                    peer.frequency_khz = frequency_khz
                peer.flags = flags
            else:
                peer = BeaconPeer(
                    identity=identity,
                    callsign=callsign,
                    grid=grid,
                    first_seen=now,
                    last_seen=now,
                    rx_level_db=rx_level_db,
                    rx_count=1,
                    interface=interface,
                    frequency_khz=frequency_khz,
                    flags=flags
                )
                self.peers[identity] = peer
                log.info(f"New peer discovered: {identity[:16]}... {callsign} {grid}")
            return peer

    def get_peers(self, max_age_hours: float = 24) -> List[Dict]:
        """Get all peers seen within max_age_hours."""
        cutoff = time.time() - (max_age_hours * 3600)
        with self.lock:
            peers = []
            for p in self.peers.values():
                if p.last_seen > cutoff:
                    peers.append({
                        "identity": p.identity,
                        "identity_short": p.identity[:16],
                        "callsign": p.callsign,
                        "grid": p.grid,
                        "rx_level_db": p.rx_level_db,
                        "rx_count": p.rx_count,
                        "first_seen": p.first_seen,
                        "last_seen": p.last_seen,
                        "age_seconds": int(time.time() - p.last_seen),
                        "interface": p.interface,
                        "frequency_khz": p.frequency_khz,
                        "is_prop_node": bool(p.flags & 0x02),
                        "accepts_links": bool(p.flags & 0x04),
                    })
            return sorted(peers, key=lambda x: x["last_seen"], reverse=True)

    def clear_stale_peers(self, max_age_hours: float = 24) -> int:
        """Remove peers older than max_age_hours."""
        cutoff = time.time() - (max_age_hours * 3600)
        with self.lock:
            stale = [k for k, v in self.peers.items() if v.last_seen < cutoff]
            for k in stale:
                del self.peers[k]
            return len(stale)


# Global state instance
state = DashboardState()


# ============================================================================
# Grid Square Utilities
# ============================================================================

def grid_to_latlon(grid: str) -> Optional[Tuple[float, float]]:
    """
    Convert Maidenhead grid square to lat/lon.

    Supports 4, 6, or 8 character grid squares.
    Returns center point of the grid square.

    Examples:
        FN42 -> (42.5, -75.0)
        FN42ab -> (42.0208, -74.9583)
    """
    grid = grid.upper().strip()

    if len(grid) < 4:
        return None

    try:
        # Field (first 2 chars: A-R)
        lon = (ord(grid[0]) - ord('A')) * 20 - 180
        lat = (ord(grid[1]) - ord('A')) * 10 - 90

        # Square (next 2 chars: 0-9)
        lon += int(grid[2]) * 2
        lat += int(grid[3]) * 1

        # Subsquare (optional, chars 5-6: a-x)
        if len(grid) >= 6:
            lon += (ord(grid[4].upper()) - ord('A')) * (2/24)
            lat += (ord(grid[5].upper()) - ord('A')) * (1/24)

            # Extended (optional, chars 7-8: 0-9)
            if len(grid) >= 8:
                lon += int(grid[6]) * (2/240)
                lat += int(grid[7]) * (1/240)
                # Center of extended square
                lon += 1/240
                lat += 0.5/240
            else:
                # Center of subsquare
                lon += 1/24
                lat += 0.5/24
        else:
            # Center of square
            lon += 1
            lat += 0.5

        return (lat, lon)
    except (IndexError, ValueError):
        return None


def latlon_to_grid(lat: float, lon: float, precision: int = 6) -> str:
    """
    Convert lat/lon to Maidenhead grid square.

    precision: 4, 6, or 8 characters
    """
    lon += 180
    lat += 90

    grid = ""

    # Field
    grid += chr(ord('A') + int(lon / 20))
    grid += chr(ord('A') + int(lat / 10))

    if precision >= 4:
        # Square
        lon_remainder = lon % 20
        lat_remainder = lat % 10
        grid += str(int(lon_remainder / 2))
        grid += str(int(lat_remainder / 1))

    if precision >= 6:
        # Subsquare
        lon_remainder = (lon % 2) * 12
        lat_remainder = (lat % 1) * 24
        grid += chr(ord('a') + int(lon_remainder))
        grid += chr(ord('a') + int(lat_remainder))

    if precision >= 8:
        # Extended
        lon_remainder = (lon_remainder % 1) * 10
        lat_remainder = (lat_remainder % 1) * 10
        grid += str(int(lon_remainder))
        grid += str(int(lat_remainder))

    return grid


# ============================================================================
# Interface Status
# ============================================================================

def parse_rnstatus() -> Dict[str, Any]:
    """Parse output of rnstatus command."""
    try:
        result = subprocess.run(
            ["rnstatus", "-j"],  # JSON output if available
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip().startswith("{"):
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass

    # Fallback: parse text output
    try:
        result = subprocess.run(
            ["rnstatus"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return parse_rnstatus_text(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return {"error": "Could not get rnstatus"}


def parse_rnstatus_text(output: str) -> Dict[str, Any]:
    """Parse text output of rnstatus."""
    interfaces = []
    current_interface = None

    for line in output.split('\n'):
        line = line.strip()

        # Interface header
        if line and not line.startswith(' ') and ':' in line:
            if current_interface:
                interfaces.append(current_interface)
            name = line.split(':')[0].strip()
            current_interface = {
                "name": name,
                "status": "unknown",
                "tx_bytes": 0,
                "rx_bytes": 0,
            }

        # Interface details
        elif current_interface and ':' in line:
            key, _, value = line.partition(':')
            key = key.strip().lower()
            value = value.strip()

            if 'status' in key:
                current_interface['status'] = value
            elif 'tx' in key and 'byte' in key.lower():
                try:
                    current_interface['tx_bytes'] = int(re.sub(r'[^\d]', '', value))
                except ValueError:
                    pass
            elif 'rx' in key and 'byte' in key.lower():
                try:
                    current_interface['rx_bytes'] = int(re.sub(r'[^\d]', '', value))
                except ValueError:
                    pass

    if current_interface:
        interfaces.append(current_interface)

    return {"interfaces": interfaces}


def get_interface_status() -> List[Dict]:
    """Get status of all Reticulum interfaces."""
    # Cache for 5 seconds
    now = time.time()
    if now - state.last_rnstatus_time < 5 and state.last_rnstatus:
        return state.last_rnstatus.get("interfaces", [])

    status = parse_rnstatus()
    state.last_rnstatus = status
    state.last_rnstatus_time = now

    return status.get("interfaces", [])


# ============================================================================
# Network Health Score
# ============================================================================

def calculate_network_health() -> Dict[str, Any]:
    """
    Calculate overall network health score.

    Factors:
    - HF link quality (peer count, avg RX level)
    - Interface count and status
    - Recent activity
    """
    health = {
        "score": 0,
        "max_score": 100,
        "factors": [],
        "status": "unknown"
    }

    score = 0

    # Factor 1: HF peers (0-30 points)
    peers = state.get_peers(max_age_hours=2)
    hf_peers = [p for p in peers if p["interface"] == "HF"]

    if len(hf_peers) >= 3:
        peer_score = 30
    elif len(hf_peers) == 2:
        peer_score = 20
    elif len(hf_peers) == 1:
        peer_score = 10
    else:
        peer_score = 0

    score += peer_score
    health["factors"].append({
        "name": "HF Peers",
        "score": peer_score,
        "max": 30,
        "detail": f"{len(hf_peers)} peer(s) in last 2 hours"
    })

    # Factor 2: RX signal quality (0-25 points)
    rx_history = state.get_rx_history(minutes=30)
    if rx_history:
        avg_rx = sum(r["db"] for r in rx_history) / len(rx_history)
        if avg_rx > -10:
            rx_score = 25
        elif avg_rx > -20:
            rx_score = 20
        elif avg_rx > -30:
            rx_score = 15
        elif avg_rx > -40:
            rx_score = 10
        else:
            rx_score = 5
        detail = f"Avg RX: {avg_rx:.1f} dB"
    else:
        rx_score = 0
        detail = "No RX data"

    score += rx_score
    health["factors"].append({
        "name": "Signal Quality",
        "score": rx_score,
        "max": 25,
        "detail": detail
    })

    # Factor 3: Interface status (0-25 points)
    interfaces = get_interface_status()
    active_interfaces = [i for i in interfaces if i.get("status", "").lower() in ("up", "active", "online")]

    if len(active_interfaces) >= 3:
        iface_score = 25
    elif len(active_interfaces) == 2:
        iface_score = 20
    elif len(active_interfaces) == 1:
        iface_score = 15
    else:
        iface_score = 5

    score += iface_score
    health["factors"].append({
        "name": "Interfaces",
        "score": iface_score,
        "max": 25,
        "detail": f"{len(active_interfaces)}/{len(interfaces)} active"
    })

    # Factor 4: Recent activity (0-20 points)
    recent_rx = len([r for r in rx_history if time.time() - r["t"] < 300])
    if recent_rx >= 10:
        activity_score = 20
    elif recent_rx >= 5:
        activity_score = 15
    elif recent_rx >= 1:
        activity_score = 10
    else:
        activity_score = 0

    score += activity_score
    health["factors"].append({
        "name": "Recent Activity",
        "score": activity_score,
        "max": 20,
        "detail": f"{recent_rx} readings in last 5 min"
    })

    health["score"] = score

    # Overall status
    if score >= 80:
        health["status"] = "excellent"
    elif score >= 60:
        health["status"] = "good"
    elif score >= 40:
        health["status"] = "fair"
    elif score >= 20:
        health["status"] = "poor"
    else:
        health["status"] = "offline"

    state.network_health = health
    return health


# ============================================================================
# Band Conditions
# ============================================================================

def get_band_conditions() -> List[Dict]:
    """
    Get propagation conditions per band based on beacon reception.

    Groups peers by frequency and calculates average RX level.
    """
    # Band definitions (kHz)
    bands = [
        {"name": "160m", "min": 1800, "max": 2000},
        {"name": "80m", "min": 3500, "max": 4000},
        {"name": "40m", "min": 7000, "max": 7300},
        {"name": "30m", "min": 10100, "max": 10150},
        {"name": "20m", "min": 14000, "max": 14350},
        {"name": "17m", "min": 18068, "max": 18168},
        {"name": "15m", "min": 21000, "max": 21450},
        {"name": "12m", "min": 24890, "max": 24990},
        {"name": "10m", "min": 28000, "max": 29700},
    ]

    peers = state.get_peers(max_age_hours=2)

    conditions = []
    for band in bands:
        band_peers = [
            p for p in peers
            if band["min"] <= p.get("frequency_khz", 0) <= band["max"]
        ]

        if band_peers:
            avg_rx = sum(p["rx_level_db"] for p in band_peers) / len(band_peers)

            if avg_rx > -15:
                quality = "excellent"
                score = 100
            elif avg_rx > -25:
                quality = "good"
                score = 75
            elif avg_rx > -35:
                quality = "fair"
                score = 50
            else:
                quality = "poor"
                score = 25
        else:
            avg_rx = None
            quality = "unknown"
            score = 0

        conditions.append({
            "band": band["name"],
            "freq_min": band["min"],
            "freq_max": band["max"],
            "peer_count": len(band_peers),
            "avg_rx_db": avg_rx,
            "quality": quality,
            "score": score,
        })

    return conditions


# ============================================================================
# TAK CoT Integration
# ============================================================================

def generate_cot_event(peer: Dict, stale_minutes: int = 60) -> str:
    """
    Generate Cursor on Target (CoT) XML for a beacon peer.

    TAK uses CoT events to display markers on the map.
    """
    now = datetime.utcnow()
    stale = datetime.utcfromtimestamp(time.time() + stale_minutes * 60)

    # Get lat/lon from grid
    lat, lon = 0.0, 0.0
    if peer.get("grid"):
        coords = grid_to_latlon(peer["grid"])
        if coords:
            lat, lon = coords

    # CoT type: a-f-G-U-C (atom, friend, ground, unit, combat)
    # For ham radio, we'll use a-f-G-E-S (atom, friend, ground, equipment, sensor)
    cot_type = "a-f-G-E-S"

    # Unique ID
    uid = f"reticulumhf-{peer['identity_short']}"

    # Build XML
    event = ET.Element("event")
    event.set("version", "2.0")
    event.set("type", cot_type)
    event.set("uid", uid)
    event.set("how", "m-g")  # machine-generated
    event.set("time", now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    event.set("start", now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    event.set("stale", stale.strftime("%Y-%m-%dT%H:%M:%SZ"))

    # Point (location)
    point = ET.SubElement(event, "point")
    point.set("lat", str(lat))
    point.set("lon", str(lon))
    point.set("hae", "0")  # height above ellipsoid
    point.set("ce", "5000")  # circular error (meters) - grid square accuracy
    point.set("le", "9999999")  # linear error

    # Detail
    detail = ET.SubElement(event, "detail")

    # Contact info
    contact = ET.SubElement(detail, "contact")
    callsign = peer.get("callsign", peer["identity_short"])
    contact.set("callsign", callsign)

    # Remarks
    remarks = ET.SubElement(detail, "remarks")
    remarks_text = f"ReticulumHF Beacon\n"
    remarks_text += f"Grid: {peer.get('grid', 'Unknown')}\n"
    remarks_text += f"RX: {peer.get('rx_level_db', -99):.1f} dB\n"
    remarks_text += f"Count: {peer.get('rx_count', 0)}\n"
    remarks_text += f"Interface: {peer.get('interface', 'HF')}\n"
    remarks_text += f"ID: {peer.get('identity', '')[:32]}"
    remarks.text = remarks_text

    # Custom fields
    reticulumhf = ET.SubElement(detail, "reticulumhf")
    reticulumhf.set("identity", peer.get("identity", ""))
    reticulumhf.set("grid", peer.get("grid", ""))
    reticulumhf.set("rx_db", str(peer.get("rx_level_db", -99)))
    reticulumhf.set("rx_count", str(peer.get("rx_count", 0)))
    reticulumhf.set("is_prop_node", str(peer.get("is_prop_node", False)).lower())

    return ET.tostring(event, encoding="unicode")


def push_to_tak(peer: Dict) -> bool:
    """Push a peer as CoT event to TAK server."""
    if not state.tak_enabled or not state.tak_host:
        return False

    try:
        cot_xml = generate_cot_event(peer)

        if state.tak_protocol == "udp":
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.sendto(cot_xml.encode('utf-8'), (state.tak_host, state.tak_port))
        else:  # TCP
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(5)
                sock.connect((state.tak_host, state.tak_port))
                sock.send(cot_xml.encode('utf-8'))

        log.debug(f"Pushed {peer.get('callsign', peer['identity_short'])} to TAK")
        return True

    except Exception as e:
        log.error(f"TAK push failed: {e}")
        return False


def push_all_peers_to_tak() -> int:
    """Push all current peers to TAK server."""
    if not state.tak_enabled:
        return 0

    count = 0
    for peer in state.get_peers(max_age_hours=2):
        if push_to_tak(peer):
            count += 1

    return count


# ============================================================================
# Flask API Routes
# ============================================================================

@dashboard_bp.route('/peers', methods=['GET'])
def api_get_peers():
    """Get all discovered beacon peers."""
    max_age = request.args.get('max_age_hours', 24, type=float)
    peers = state.get_peers(max_age_hours=max_age)

    # Add lat/lon for each peer
    for peer in peers:
        if peer.get("grid"):
            coords = grid_to_latlon(peer["grid"])
            if coords:
                peer["lat"], peer["lon"] = coords

    return jsonify({
        "peers": peers,
        "count": len(peers),
        "timestamp": time.time()
    })


@dashboard_bp.route('/peers', methods=['POST'])
def api_add_peer():
    """Add or update a beacon peer (called by beacon scheduler)."""
    data = request.get_json()
    if not data or 'identity' not in data:
        return jsonify({"error": "identity required"}), 400

    peer = state.update_peer(
        identity=data['identity'],
        callsign=data.get('callsign', ''),
        grid=data.get('grid', ''),
        rx_level_db=data.get('rx_level_db', -99),
        interface=data.get('interface', 'HF'),
        frequency_khz=data.get('frequency_khz', 0),
        flags=data.get('flags', 0)
    )

    # Push to TAK if enabled
    if state.tak_enabled:
        peer_dict = {
            "identity": peer.identity,
            "identity_short": peer.identity[:16],
            "callsign": peer.callsign,
            "grid": peer.grid,
            "rx_level_db": peer.rx_level_db,
            "rx_count": peer.rx_count,
            "interface": peer.interface,
            "is_prop_node": bool(peer.flags & 0x02),
        }
        push_to_tak(peer_dict)

    return jsonify({"status": "ok", "rx_count": peer.rx_count})


@dashboard_bp.route('/rx-history', methods=['GET'])
def api_get_rx_history():
    """Get RX level history."""
    minutes = request.args.get('minutes', 60, type=int)
    history = state.get_rx_history(minutes=minutes)
    return jsonify({
        "history": history,
        "count": len(history),
        "minutes": minutes
    })


@dashboard_bp.route('/rx-level', methods=['POST'])
def api_add_rx_level():
    """Add RX level reading (called periodically)."""
    data = request.get_json()
    if not data or 'level_db' not in data:
        return jsonify({"error": "level_db required"}), 400

    state.add_rx_reading(
        level_db=data['level_db'],
        mode=data.get('mode', '')
    )
    return jsonify({"status": "ok"})


@dashboard_bp.route('/interfaces', methods=['GET'])
def api_get_interfaces():
    """Get Reticulum interface status."""
    interfaces = get_interface_status()
    return jsonify({
        "interfaces": interfaces,
        "count": len(interfaces)
    })


@dashboard_bp.route('/health', methods=['GET'])
def api_get_health():
    """Get network health score and factors."""
    health = calculate_network_health()
    return jsonify(health)


@dashboard_bp.route('/band-conditions', methods=['GET'])
def api_get_band_conditions():
    """Get propagation conditions per band."""
    conditions = get_band_conditions()
    return jsonify({
        "conditions": conditions,
        "timestamp": time.time()
    })


@dashboard_bp.route('/tak/config', methods=['GET'])
def api_get_tak_config():
    """Get TAK integration configuration."""
    return jsonify({
        "enabled": state.tak_enabled,
        "host": state.tak_host,
        "port": state.tak_port,
        "protocol": state.tak_protocol
    })


@dashboard_bp.route('/tak/config', methods=['POST'])
def api_set_tak_config():
    """Set TAK integration configuration."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    state.tak_enabled = bool(data.get('enabled', False))
    state.tak_host = str(data.get('host', ''))

    # Validate port
    try:
        port = int(data.get('port', 8087))
        if not 1 <= port <= 65535:
            return jsonify({"error": "Port must be 1-65535"}), 400
        state.tak_port = port
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid port number"}), 400

    protocol = data.get('protocol', 'udp')
    if protocol not in ('udp', 'tcp'):
        return jsonify({"error": "Protocol must be 'udp' or 'tcp'"}), 400
    state.tak_protocol = protocol

    # Persist to config file
    config_path = Path("/etc/reticulumhf/tak.json")
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w") as f:
            json.dump({
                "enabled": state.tak_enabled,
                "host": state.tak_host,
                "port": state.tak_port,
                "protocol": state.tak_protocol,
            }, f, indent=2)
    except Exception as e:
        log.warning(f"Failed to persist TAK config: {e}")

    return jsonify({"status": "ok"})


@dashboard_bp.route('/tak/push', methods=['POST'])
def api_tak_push():
    """Push all peers to TAK server."""
    count = push_all_peers_to_tak()
    return jsonify({
        "status": "ok",
        "pushed": count
    })


@dashboard_bp.route('/tak/test', methods=['POST'])
def api_tak_test():
    """Send test CoT event to TAK server."""
    if not state.tak_enabled or not state.tak_host:
        return jsonify({"error": "TAK not configured"}), 400

    # Create test peer
    test_peer = {
        "identity": "0" * 32,
        "identity_short": "0" * 16,
        "callsign": "TEST-RETICULUMHF",
        "grid": request.args.get('grid', 'FM29'),
        "rx_level_db": -15,
        "rx_count": 1,
        "interface": "TEST",
        "is_prop_node": False,
    }

    if push_to_tak(test_peer):
        return jsonify({"status": "ok", "message": "Test event sent"})
    else:
        return jsonify({"error": "Failed to send test event"}), 500


@dashboard_bp.route('/grid/convert', methods=['GET'])
def api_grid_convert():
    """Convert between grid square and lat/lon."""
    grid = request.args.get('grid')
    lat = request.args.get('lat', type=float)
    lon = request.args.get('lon', type=float)

    if grid:
        coords = grid_to_latlon(grid)
        if coords:
            return jsonify({"grid": grid, "lat": coords[0], "lon": coords[1]})
        else:
            return jsonify({"error": "Invalid grid square"}), 400
    elif lat is not None and lon is not None:
        grid = latlon_to_grid(lat, lon)
        return jsonify({"grid": grid, "lat": lat, "lon": lon})
    else:
        return jsonify({"error": "Provide 'grid' or 'lat' and 'lon'"}), 400


# ============================================================================
# Utility Functions for Integration
# ============================================================================

def integrate_with_beacon_scheduler(scheduler):
    """
    Wire up beacon scheduler to dashboard state.

    Call this from app.py to connect the beacon system to the dashboard.
    """
    original_callback = scheduler.on_beacon_rx

    def dashboard_callback(packet, peer):
        # Call original callback if set
        if original_callback:
            original_callback(packet, peer)

        # Update dashboard state
        state.update_peer(
            identity=packet.identity_hash.hex(),
            callsign=packet.message.split()[0] if packet.message else "",
            grid=packet.message.split()[1] if len(packet.message.split()) > 1 else "",
            rx_level_db=peer.last_rx_level or -99,
            interface="HF",
            flags=packet.flags
        )

    scheduler.on_beacon_rx = dashboard_callback
    log.info("Beacon scheduler integrated with dashboard")


def start_rx_monitor(get_level_func, interval: float = 5.0):
    """
    Start background thread to monitor RX levels.

    get_level_func: callable that returns current RX level in dB
    interval: seconds between readings
    """
    def monitor_loop():
        while True:
            try:
                level = get_level_func()
                if level is not None:
                    state.add_rx_reading(level)
            except Exception as e:
                log.error(f"RX monitor error: {e}")
            time.sleep(interval)

    thread = threading.Thread(target=monitor_loop, daemon=True, name="rx-monitor")
    thread.start()
    log.info(f"RX level monitor started (interval={interval}s)")
    return thread
