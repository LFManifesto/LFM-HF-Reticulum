#!/usr/bin/env python3
"""
ReticulumHF Dashboard API

Provides API endpoints for the enhanced dashboard:
- Beacon peer discovery
- RX level history
- Interface status
- Network health metrics
- Solar/propagation data (N0NBH)
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

    # Valid operating modes
    VALID_MODES = ("hybrid", "hf_only", "internet_only")

    def __init__(self):
        self.rx_history: deque = deque(maxlen=720)  # 1 hour at 5s intervals
        self.peers: Dict[str, BeaconPeer] = {}
        self.lock = threading.Lock()
        self.last_rnstatus: Dict = {}
        self.last_rnstatus_time: float = 0
        self.network_health: Dict = {}

        # Operating mode: hybrid (default), hf_only, internet_only
        self.operating_mode = "hybrid"

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
            parsed = parse_rnstatus_text(result.stdout)
            if parsed.get("interfaces"):
                return parsed
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Final fallback: derive interface status from config and services
    return get_interface_status_from_config()


def parse_rnstatus_text(output: str) -> Dict[str, Any]:
    """Parse text output of rnstatus."""
    interfaces = []
    current_interface = None
    lines = output.split('\n')

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Look for interface headers - they start with "[" or have interface keywords
        # Example formats:
        #   [TCP Gateway]
        #   TCP Gateway (connected)
        #   Interface Name: Status
        if stripped.startswith('[') and ']' in stripped:
            # Format: [Interface Name]
            if current_interface:
                interfaces.append(current_interface)
            name = stripped.strip('[]').strip()
            current_interface = {
                "name": name,
                "status": "unknown",
                "tx_bytes": 0,
                "rx_bytes": 0,
            }
        elif stripped and not stripped.startswith(' ') and '(' in stripped:
            # Format: Interface Name (status)
            if current_interface:
                interfaces.append(current_interface)
            parts = stripped.split('(')
            name = parts[0].strip()
            status = parts[1].rstrip(')').strip() if len(parts) > 1 else "unknown"
            current_interface = {
                "name": name,
                "status": status,
                "tx_bytes": 0,
                "rx_bytes": 0,
            }
        elif current_interface and ':' in stripped:
            # Parse details
            key, _, value = stripped.partition(':')
            key = key.strip().lower()
            value = value.strip()

            if any(x in key for x in ['status', 'state']):
                current_interface['status'] = value
            elif 'tx' in key and 'byte' in key:
                try:
                    current_interface['tx_bytes'] = int(re.sub(r'[^\d]', '', value))
                except ValueError:
                    pass
            elif 'rx' in key and 'byte' in key:
                try:
                    current_interface['rx_bytes'] = int(re.sub(r'[^\d]', '', value))
                except ValueError:
                    pass
            elif 'mode' in key:
                current_interface['mode'] = value

    if current_interface:
        interfaces.append(current_interface)

    return {"interfaces": interfaces}


def get_interface_status_from_config() -> Dict[str, Any]:
    """
    Fallback: Get interface status by reading RNS config and checking services.

    Used when rnstatus command doesn't return usable output.
    """
    interfaces = []

    # Check for RNS config file
    config_paths = [
        Path("/home/pi/.reticulum/config"),
        Path("/etc/reticulumhf/reticulum.config"),
    ]

    config_path = None
    for p in config_paths:
        if p.exists():
            config_path = p
            break

    if not config_path:
        return {"interfaces": [], "error": "No RNS config found"}

    try:
        with open(config_path) as f:
            config_text = f.read()

        # Parse interface sections [[Name]]
        import re
        interface_pattern = r'\[\[([^\]]+)\]\]'
        matches = re.findall(interface_pattern, config_text)

        for name in matches:
            interface = {
                "name": name,
                "status": "unknown",
                "tx_bytes": 0,
                "rx_bytes": 0,
            }

            # Determine status based on interface type and service status
            if "TCP Gateway" in name:
                # Check if rnsd is running
                try:
                    result = subprocess.run(
                        ["systemctl", "is-active", "reticulumhf-rnsd"],
                        capture_output=True, text=True, timeout=2
                    )
                    interface["status"] = "Up" if result.stdout.strip() == "active" else "Down"
                    interface["mode"] = "gateway"
                except Exception:
                    interface["status"] = "unknown"

            elif "I2P" in name or "Lightfighter" in name:
                # Check if i2pd is running and has tunnels
                try:
                    result = subprocess.run(
                        ["systemctl", "is-active", "i2pd"],
                        capture_output=True, text=True, timeout=2
                    )
                    if result.stdout.strip() == "active":
                        # Check for tunnel connectivity
                        try:
                            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                                sock.settimeout(1.0)
                                sock.connect(("127.0.0.1", 7070))
                                interface["status"] = "Up (tunnels)"
                        except Exception:
                            interface["status"] = "Up (starting)"
                    else:
                        interface["status"] = "Down"
                except Exception:
                    interface["status"] = "unknown"

            elif "FreeDV" in name or "HF" in name:
                # Check if freedvtnc2 is running
                try:
                    result = subprocess.run(
                        ["systemctl", "is-active", "freedvtnc2"],
                        capture_output=True, text=True, timeout=2
                    )
                    if result.stdout.strip() == "active":
                        # Check for KISS port connectivity
                        try:
                            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                                sock.settimeout(1.0)
                                sock.connect(("127.0.0.1", 8001))
                                interface["status"] = "Up"
                                interface["mode"] = "boundary"
                        except Exception:
                            interface["status"] = "Connecting"
                    else:
                        interface["status"] = "Down"
                except Exception:
                    interface["status"] = "unknown"

            interfaces.append(interface)

    except Exception as e:
        log.error(f"Failed to read RNS config: {e}")
        return {"interfaces": [], "error": str(e)}

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


# Solar data cache
_solar_cache = {"data": None, "timestamp": 0, "ttl": 300}  # 5 minute cache


@dashboard_bp.route('/solar', methods=['GET'])
def api_get_solar_data():
    """
    Get solar/propagation data from N0NBH.

    Returns comprehensive solar indices and HF band conditions.
    Data is cached for 5 minutes to avoid hammering the API.
    """
    global _solar_cache

    # Return cached data if fresh
    if _solar_cache["data"] and (time.time() - _solar_cache["timestamp"]) < _solar_cache["ttl"]:
        cached = _solar_cache["data"].copy()
        cached["cached"] = True
        return jsonify(cached)

    try:
        import urllib.request
        import xml.etree.ElementTree as XMLParser

        url = "https://www.hamqsl.com/solarxml.php"
        req = urllib.request.Request(url, headers={'User-Agent': 'ReticulumHF/0.3'})

        with urllib.request.urlopen(req, timeout=10) as response:
            xml_data = response.read().decode('utf-8')

        # Parse XML
        root = XMLParser.fromstring(xml_data)
        solar = root.find('solardata')

        if solar is None:
            return jsonify({"success": False, "error": "Invalid XML response"})

        # Extract all solar indices
        def get_text(tag, default=""):
            el = solar.find(tag)
            return el.text.strip() if el is not None and el.text else default

        def get_int(tag, default=0):
            try:
                return int(get_text(tag, str(default)))
            except ValueError:
                return default

        def get_float(tag, default=0.0):
            try:
                return float(get_text(tag, str(default)))
            except ValueError:
                return default

        result = {
            "success": True,
            "updated": get_text("updated"),
            "source": "N0NBH",

            # Solar indices
            "solarflux": get_int("solarflux"),
            "sunspots": get_int("sunspots"),
            "aindex": get_int("aindex"),
            "kindex": get_int("kindex"),
            "xray": get_text("xray"),
            "protonflux": get_int("protonflux"),
            "electronflux": get_int("electonflux"),  # Note: typo in XML
            "aurora": get_int("aurora"),
            "solarwind": get_float("solarwind"),
            "magneticfield": get_float("magneticfield"),
            "geomagfield": get_text("geomagfield"),
            "signalnoise": get_text("signalnoise"),

            # Band conditions (day and night)
            "bands": {}
        }

        # Parse band conditions
        calc = solar.find('calculatedconditions')
        if calc is not None:
            for band in calc.findall('band'):
                name = band.get('name', '')
                time_of_day = band.get('time', '')
                condition = band.text.strip() if band.text else 'Unknown'

                if name not in result["bands"]:
                    result["bands"][name] = {}
                result["bands"][name][time_of_day] = condition

        # Cache result
        _solar_cache["data"] = result
        _solar_cache["timestamp"] = time.time()

        return jsonify(result)

    except Exception as e:
        log.error(f"Solar data fetch error: {e}")
        # Return stale cache if available
        if _solar_cache["data"]:
            cached = _solar_cache["data"].copy()
            cached["cached"] = True
            cached["cache_error"] = str(e)
            return jsonify(cached)
        return jsonify({"success": False, "error": str(e)})


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
# Operating Mode API
# ============================================================================

@dashboard_bp.route('/mode', methods=['GET'])
def get_operating_mode():
    """Get current operating mode."""
    return jsonify({
        "mode": state.operating_mode,
        "valid_modes": list(DashboardState.VALID_MODES),
        "descriptions": {
            "hybrid": "HF gated to beacon windows, I2P/TCP full transport",
            "hf_only": "HF full control, I2P/TCP disabled",
            "internet_only": "HF disabled, I2P/TCP full transport"
        }
    })


@dashboard_bp.route('/mode', methods=['POST'])
def set_operating_mode():
    """
    Set operating mode.

    Modes:
    - hybrid: HF TX only during beacon windows, I2P/TCP enabled
    - hf_only: HF full control, I2P/TCP disabled
    - internet_only: HF disabled, I2P/TCP enabled
    """
    data = request.get_json() or {}
    new_mode = data.get('mode', '').lower()

    if new_mode not in DashboardState.VALID_MODES:
        return jsonify({
            "success": False,
            "error": f"Invalid mode. Must be one of: {', '.join(DashboardState.VALID_MODES)}"
        }), 400

    old_mode = state.operating_mode
    state.operating_mode = new_mode

    # Save to config file
    config_path = Path("/etc/reticulumhf/beacon.json")
    if not config_path.exists():
        config_path = Path("/opt/reticulumhf/configs/beacon.json")

    try:
        if config_path.exists():
            with open(config_path) as f:
                config = json.load(f)
            config['operating_mode'] = new_mode
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)
    except Exception as e:
        log.warning(f"Could not save mode to config: {e}")

    # Apply mode changes
    _apply_operating_mode(new_mode, old_mode)

    log.info(f"Operating mode changed: {old_mode} -> {new_mode}")
    return jsonify({"success": True, "mode": new_mode, "previous": old_mode})


def _apply_operating_mode(new_mode: str, old_mode: str):
    """
    Apply operating mode changes to interfaces.

    This controls which interfaces are active:
    - hybrid: HF (gated), I2P/TCP (enabled)
    - hf_only: HF (full), I2P/TCP (disabled)
    - internet_only: HF (disabled), I2P/TCP (enabled)
    """
    if new_mode == "hf_only":
        # Disable I2P, enable full HF TX
        log.info("Mode: HF Only - disabling internet transports, enabling full HF TX")
        subprocess.run(["systemctl", "stop", "i2pd"], capture_output=True)
        _send_modem_command("TX ENABLE")

    elif new_mode == "internet_only":
        # Enable I2P, disable HF TX
        log.info("Mode: Internet Only - disabling HF TX, enabling internet")
        subprocess.run(["systemctl", "start", "i2pd"], capture_output=True)
        _send_modem_command("TX DISABLE")

    else:  # hybrid
        # Enable I2P, gate HF TX (beacon scheduler controls windows)
        log.info("Mode: Hybrid - HF gated, internet enabled")
        subprocess.run(["systemctl", "start", "i2pd"], capture_output=True)
        _send_modem_command("TX DISABLE")  # Beacon scheduler will open windows


def _send_modem_command(command: str) -> Optional[str]:
    """Send command to freedvtnc2 command interface."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(5.0)
            sock.connect(("127.0.0.1", 8002))
            sock.sendall(f"{command}\n".encode())
            response = sock.recv(1024).decode().strip()
            return response
    except Exception as e:
        log.warning(f"Failed to send modem command '{command}': {e}")
        return None


# ============================================================================
# TX Gate and I2P Status API
# ============================================================================

@dashboard_bp.route('/txgate', methods=['GET'])
def get_txgate_status():
    """Get TX gate status from modem."""
    response = _send_modem_command("TX STATUS")
    if response:
        # Parse response formats:
        # "OK TX ENABLED" -> ENABLED
        # "OK TX DISABLED" -> DISABLED
        # "OK TX WINDOW:30" -> WINDOW with 30s remaining
        # "ERROR ..." -> error
        if response.startswith("OK TX "):
            status_part = response[6:]  # Remove "OK TX "
            if ":" in status_part:
                status, remaining_str = status_part.split(":", 1)
                remaining = int(remaining_str) if remaining_str.isdigit() else None
            else:
                status = status_part
                remaining = None

            return jsonify({
                "success": True,
                "status": status.upper(),
                "remaining_seconds": remaining,
                "mode": state.operating_mode
            })
        elif response.startswith("ERROR"):
            return jsonify({
                "success": True,
                "status": "UNAVAILABLE",
                "remaining_seconds": None,
                "mode": state.operating_mode,
                "error": response
            })
        else:
            return jsonify({
                "success": True,
                "status": response,
                "remaining_seconds": None,
                "mode": state.operating_mode
            })
    else:
        return jsonify({
            "success": False,
            "status": "UNKNOWN",
            "error": "Cannot reach modem"
        })


@dashboard_bp.route('/txgate', methods=['POST'])
def set_txgate():
    """Manually control TX gate (for testing/override)."""
    data = request.get_json() or {}
    action = data.get('action', '').upper()

    if action == "ENABLE":
        response = _send_modem_command("TX ENABLE")
    elif action == "DISABLE":
        response = _send_modem_command("TX DISABLE")
    elif action == "WINDOW":
        seconds = data.get('seconds', 60)
        response = _send_modem_command(f"TX WINDOW {seconds}")
    else:
        return jsonify({"success": False, "error": "Invalid action"}), 400

    success = response is not None and response.startswith("OK")
    return jsonify({"success": success, "response": response})


@dashboard_bp.route('/i2p', methods=['GET'])
def get_i2p_status():
    """Get I2P daemon status."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "i2pd"],
            capture_output=True, text=True, timeout=5
        )
        running = result.stdout.strip() == "active"

        # Try to get tunnel count from i2pd console
        tunnel_count = None
        if running:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(2.0)
                    sock.connect(("127.0.0.1", 7070))
                    # Basic check - if we can connect, i2pd web console is up
                    tunnel_count = "connected"
            except Exception:
                tunnel_count = "starting"

        return jsonify({
            "success": True,
            "running": running,
            "tunnels": tunnel_count,
            "peer": "kfamlmwnlw3acqfxip4x6kt53i2tr4ksp5h4qxwvxhoq7mchpolq.b32.i2p"
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@dashboard_bp.route('/i2p', methods=['POST'])
def control_i2p():
    """Start/stop I2P daemon."""
    data = request.get_json() or {}
    action = data.get('action', '').lower()

    if action == "start":
        result = subprocess.run(["systemctl", "start", "i2pd"], capture_output=True)
    elif action == "stop":
        result = subprocess.run(["systemctl", "stop", "i2pd"], capture_output=True)
    elif action == "restart":
        result = subprocess.run(["systemctl", "restart", "i2pd"], capture_output=True)
    else:
        return jsonify({"success": False, "error": "Invalid action"}), 400

    return jsonify({"success": result.returncode == 0})


@dashboard_bp.route('/ethernet', methods=['GET'])
def get_ethernet_status():
    """Get ethernet (eth0) status including IP, link state, and NAT info."""
    status = {
        "success": True,
        "connected": False,
        "ip_address": None,
        "gateway": None,
        "link_speed": None,
        "nat_enabled": False,
    }

    try:
        # Check link state
        result = subprocess.run(
            ["ip", "link", "show", "eth0"],
            capture_output=True, text=True, timeout=5
        )
        if "state UP" in result.stdout:
            status["connected"] = True

            # Get IP address
            result = subprocess.run(
                ["ip", "-4", "addr", "show", "eth0"],
                capture_output=True, text=True, timeout=5
            )
            # Parse: inet 192.168.8.191/24 brd 192.168.8.255 scope global dynamic eth0
            for line in result.stdout.split('\n'):
                if 'inet ' in line:
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        status["ip_address"] = parts[1].split('/')[0]
                    break

            # Get default gateway
            result = subprocess.run(
                ["ip", "route", "show", "default"],
                capture_output=True, text=True, timeout=5
            )
            # Parse: default via 192.168.8.1 dev eth0
            for line in result.stdout.split('\n'):
                if 'default via' in line and 'eth0' in line:
                    parts = line.split()
                    if 'via' in parts:
                        idx = parts.index('via')
                        if idx + 1 < len(parts):
                            status["gateway"] = parts[idx + 1]
                    break

            # Get link speed
            result = subprocess.run(
                ["ethtool", "eth0"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.split('\n'):
                if 'Speed:' in line:
                    status["link_speed"] = line.split(':')[1].strip()
                    break

        # Check if NAT masquerading is enabled
        result = subprocess.run(
            ["iptables", "-t", "nat", "-L", "POSTROUTING", "-n"],
            capture_output=True, text=True, timeout=5
        )
        if "MASQUERADE" in result.stdout and "eth0" in result.stdout:
            status["nat_enabled"] = True

    except Exception as e:
        log.error(f"Ethernet status error: {e}")
        status["error"] = str(e)

    return jsonify(status)


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
