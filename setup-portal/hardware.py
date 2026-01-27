#!/usr/bin/env python3
"""
Hardware detection and testing for ReticulumHF setup.
Detects serial ports, audio devices, and tests radio CAT control.
"""

import subprocess
import re
import json
import os
import time
import threading
import math
from pathlib import Path
from typing import Optional, Dict, List
from collections import deque

# Path to radio configurations
RADIOS_CONFIG = Path(__file__).parent.parent / "configs" / "radios.json"

# Mock mode for testing without hardware
MOCK_MODE = os.environ.get("RETICULUMHF_MOCK", "0") == "1"


class ALSALevelMonitor:
    """
    Monitor ALSA audio input levels using arecord.
    No external Python dependencies - uses subprocess.
    """

    def __init__(self, card: int, sample_rate: int = 48000):
        self.card = card
        self.sample_rate = sample_rate
        self.running = False
        self._process = None
        self._thread = None
        self._lock = threading.Lock()
        self._levels = deque(maxlen=50)  # ~1 second at 20Hz
        self.current_level_db = -60.0
        self.peak_level_db = -60.0

    def start(self) -> bool:
        """Start monitoring audio levels."""
        if self.running:
            return True

        try:
            # Use arecord with VU meter output
            cmd = [
                "arecord",
                "-D", f"hw:{self.card},0",
                "-f", "S16_LE",
                "-r", str(self.sample_rate),
                "-c", "1",
                "-t", "raw",
                "-vv",  # Verbose output includes level info
                "/dev/null"
            ]
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
            self.running = True
            self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self._thread.start()
            return True
        except Exception as e:
            print(f"Failed to start audio monitor: {e}")
            return False

    def stop(self):
        """Stop monitoring."""
        self.running = False
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=2)
            except Exception:
                self._process.kill()
            self._process = None

    def _monitor_loop(self):
        """Parse arecord VU meter output for level info."""
        # arecord -vv outputs lines like: "#+     | 04%" or "####+  | 25%"
        # The percentage at the end is what we need
        pct_pattern = re.compile(r'\|\s*(\d+)%')

        while self.running and self._process:
            try:
                line = self._process.stderr.readline()
                if not line:
                    break

                # Parse percentage from arecord -vv output
                # Format: "#+                                                 | 04%"
                match = pct_pattern.search(line)
                if match:
                    pct = int(match.group(1))
                    # Convert percentage to dB (0% = -60dB, 100% = 0dB)
                    if pct > 0:
                        # pct is 0-100, representing 0-100% of full scale
                        level_db = 20 * math.log10(pct / 100.0)
                    else:
                        level_db = -60.0

                    with self._lock:
                        self.current_level_db = max(-60.0, min(0.0, level_db))
                        self.peak_level_db = max(
                            self.peak_level_db * 0.95,  # Slow decay
                            self.current_level_db
                        )
                        self._levels.append(self.current_level_db)

            except Exception:
                pass

    def get_levels(self) -> Dict:
        """Get current audio levels (thread-safe)."""
        with self._lock:
            avg = sum(self._levels) / len(self._levels) if self._levels else -60.0
            return {
                "rms_db": round(self.current_level_db, 1),
                "peak_db": round(self.peak_level_db, 1),
                "average_db": round(avg, 1),
                "status": self._get_level_status(self.current_level_db)
            }

    def _get_level_status(self, level_db: float) -> Dict:
        """Get status info for the current level."""
        if level_db > -3:
            return {"state": "high", "message": "Too high - reduce radio output", "color": "#ef4444"}
        elif level_db > -10:
            return {"state": "good", "message": "Good level for FreeDV", "color": "#4ade80"}
        elif level_db > -20:
            return {"state": "low", "message": "Low - increase radio output", "color": "#fbbf24"}
        else:
            return {"state": "very_low", "message": "Very low or no signal", "color": "#666666"}


# Global audio monitor instance
_audio_monitor: Optional[ALSALevelMonitor] = None


def start_audio_monitor(card: int) -> Dict:
    """Start the audio level monitor for a given card."""
    global _audio_monitor

    # Check if device is in use
    if is_audio_device_busy(card):
        return {
            "success": False,
            "error": "Audio device in use by modem",
            "device_busy": True
        }

    if _audio_monitor and _audio_monitor.running:
        _audio_monitor.stop()

    _audio_monitor = ALSALevelMonitor(card)
    if _audio_monitor.start():
        return {"success": True, "message": f"Monitoring audio card {card}"}
    else:
        return {"success": False, "error": "Failed to start audio monitor"}


def stop_audio_monitor() -> Dict:
    """Stop the audio level monitor."""
    global _audio_monitor

    if _audio_monitor:
        _audio_monitor.stop()
        _audio_monitor = None
    return {"success": True}


def get_audio_levels() -> Dict:
    """Get current audio levels from the monitor."""
    global _audio_monitor

    if not _audio_monitor or not _audio_monitor.running:
        return {"success": False, "error": "Monitor not running"}

    levels = _audio_monitor.get_levels()
    return {"success": True, "levels": levels}


def is_audio_device_busy(card: int) -> bool:
    """Check if the audio device is in use by another process (e.g., freedvtnc2)."""
    try:
        result = subprocess.run(
            ["fuser", f"/dev/snd/pcmC{card}D0c"],
            capture_output=True, timeout=5
        )
        return result.returncode == 0  # 0 means something is using it
    except Exception:
        return False


def get_audio_level_single(card: int) -> Dict:
    """
    Get a single audio level reading using arecord.
    Useful for one-off checks without starting a continuous monitor.
    """
    # Check if device is in use
    if is_audio_device_busy(card):
        return {
            "success": False,
            "error": "Audio device in use by modem",
            "device_busy": True
        }

    try:
        # Record a brief sample and analyze
        cmd = [
            "arecord",
            "-D", f"hw:{card},0",
            "-f", "S16_LE",
            "-r", "48000",
            "-c", "1",
            "-d", "1",  # 1 second
            "-t", "raw",
            "-q",  # Quiet
            "-"  # Output to stdout
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=5)

        if result.returncode != 0:
            return {"success": False, "error": "Failed to capture audio"}

        # Analyze the raw audio data
        data = result.stdout
        if len(data) < 100:
            return {"success": False, "error": "No audio data captured"}

        # Parse as 16-bit signed samples
        samples = []
        for i in range(0, len(data) - 1, 2):
            sample = int.from_bytes(data[i:i+2], byteorder='little', signed=True)
            samples.append(abs(sample))

        if not samples:
            return {"success": False, "error": "No samples parsed"}

        # Calculate RMS and peak
        rms = math.sqrt(sum(s*s for s in samples) / len(samples))
        peak = max(samples)

        rms_db = 20 * math.log10(rms / 32768.0) if rms > 0 else -60.0
        peak_db = 20 * math.log10(peak / 32768.0) if peak > 0 else -60.0

        return {
            "success": True,
            "rms_db": round(max(-60.0, rms_db), 1),
            "peak_db": round(max(-60.0, peak_db), 1)
        }

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Audio capture timeout"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def set_single_audio_control(card: int, control: str, level: int) -> Dict:
    """
    Set a single ALSA mixer control to a specific level.
    Level should be 0-100 (percentage).
    """
    if not 0 <= level <= 100:
        return {"success": False, "error": "Level must be 0-100"}

    try:
        result = subprocess.run(
            ["amixer", "-c", str(card), "sset", control, f"{level}%"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return {"success": True, "control": control, "level": level}
        else:
            return {"success": False, "error": result.stderr.strip() or "Failed to set level"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_single_audio_control(card: int, control: str) -> Dict:
    """Get the current level of a specific ALSA mixer control."""
    try:
        result = subprocess.run(
            ["amixer", "-c", str(card), "sget", control],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            # Parse percentage from output like "Playback 64 [75%]"
            match = re.search(r'\[(\d+)%\]', result.stdout)
            if match:
                return {"success": True, "control": control, "level": int(match.group(1))}
            return {"success": False, "error": "Could not parse level from output"}
        return {"success": False, "error": result.stderr.strip() or "Control not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}

# Mock data for testing
MOCK_SERIAL_PORTS = [
    {"port": "/dev/ttyUSB0", "type": "USB-Serial", "description": "Silicon Labs CP210x (Digirig CAT)"},
    {"port": "/dev/ttyUSB1", "type": "USB-Serial", "description": "FTDI USB-Serial (Xiegu blue cable)"}
]

MOCK_AUDIO_DEVICES = [
    {"card": 0, "name": "bcm2835", "description": "Built-in Audio", "type": "builtin", "is_digirig": False},
    {"card": 3, "name": "Device", "description": "USB PnP Sound Device", "type": "digirig", "is_digirig": True}
]


def load_radios() -> list:
    """Load radio configurations from JSON."""
    if RADIOS_CONFIG.exists():
        with open(RADIOS_CONFIG) as f:
            data = json.load(f)
            return data.get("radios", [])
    return []


def detect_serial_ports() -> list:
    """
    Detect available serial ports on the system.
    Returns list of dicts with port info.
    """
    if MOCK_MODE:
        return MOCK_SERIAL_PORTS.copy()

    ports = []

    # Check /dev/ttyUSB* (Linux USB-serial adapters)
    for port_path in Path("/dev").glob("ttyUSB*"):
        port_info = {
            "port": str(port_path),
            "type": "USB-Serial",
            "description": get_usb_description(str(port_path))
        }
        ports.append(port_info)

    # Check /dev/ttyACM* (Linux USB-ACM devices like Arduino)
    for port_path in Path("/dev").glob("ttyACM*"):
        port_info = {
            "port": str(port_path),
            "type": "USB-ACM",
            "description": get_usb_description(str(port_path))
        }
        ports.append(port_info)

    # Check /dev/serial/by-id/* for more descriptive names
    serial_by_id = Path("/dev/serial/by-id")
    if serial_by_id.exists():
        for link in serial_by_id.iterdir():
            if link.is_symlink():
                target = link.resolve()
                # Find if we already have this port
                for p in ports:
                    if p["port"] == str(target):
                        p["by_id"] = str(link)
                        # Extract description from link name
                        name = link.name
                        if "FTDI" in name:
                            p["description"] = "FTDI USB-Serial (Xiegu blue cable)"
                        elif "Prolific" in name or "PL2303" in name:
                            p["description"] = "Prolific USB-Serial"
                        elif "Silicon_Labs" in name or "CP210" in name:
                            p["description"] = "Silicon Labs CP210x (Digirig CAT)"
                        break

    return ports


def get_usb_description(port: str) -> str:
    """Get USB device description for a serial port."""
    try:
        # Get device number from port name
        port_num = port.split("/")[-1]

        # Try to get info from udevadm
        result = subprocess.run(
            ["udevadm", "info", "--query=property", "--name=" + port],
            capture_output=True, text=True, timeout=5
        )

        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                if line.startswith("ID_MODEL_FROM_DATABASE="):
                    return line.split("=", 1)[1]
                elif line.startswith("ID_MODEL="):
                    return line.split("=", 1)[1].replace("_", " ")

        return "Unknown USB device"
    except Exception:
        return "Unknown"


def detect_audio_devices() -> list:
    """
    Detect available audio devices using ALSA.
    Returns list of dicts with audio device info.
    """
    if MOCK_MODE:
        return MOCK_AUDIO_DEVICES.copy()

    devices = []

    try:
        # Get recording devices
        result = subprocess.run(
            ["arecord", "-l"],
            capture_output=True, text=True, timeout=10
        )

        if result.returncode == 0:
            # Parse arecord output
            # Format: card N: DeviceName [Description], device M: SubdeviceName
            for line in result.stdout.split("\n"):
                match = re.match(r"card (\d+): (\w+) \[([^\]]+)\]", line)
                if match:
                    card_num = match.group(1)
                    card_name = match.group(2)
                    card_desc = match.group(3)

                    device_type = "unknown"
                    if "USB" in card_desc.upper() or "USB" in card_name.upper():
                        device_type = "usb"
                        # Digirig uses C-Media CM108 chip, shows as "USB PnP Sound Device"
                        if ("C-Media" in card_desc or "CM108" in card_desc or
                            "USB PnP Sound Device" in card_desc):
                            device_type = "digirig"
                    elif "bcm2835" in card_name.lower():
                        device_type = "builtin"

                    devices.append({
                        "card": int(card_num),
                        "name": card_name,
                        "description": card_desc,
                        "type": device_type,
                        "is_digirig": device_type == "digirig"
                    })

    except Exception as e:
        pass

    return devices


def find_digirig() -> Optional[dict]:
    """
    Find Digirig Mobile device.
    Returns dict with serial port, audio card, and detection status.
    Detection status can be: "full", "audio_only", "serial_only", or "none"
    """
    if MOCK_MODE:
        return {
            "serial_port": "/dev/ttyUSB0",
            "audio_card": 3,
            "found": True,
            "status": "full",
            "message": "Digirig detected (audio + CAT)"
        }

    result = {
        "serial_port": None,
        "audio_card": None,
        "found": False,
        "status": "none",
        "message": "Not detected"
    }

    # Look for Silicon Labs CP210x serial (CAT)
    serial_by_id = Path("/dev/serial/by-id")
    if serial_by_id.exists():
        for link in serial_by_id.iterdir():
            name = link.name.lower()
            if "cp210" in name or "silicon" in name:
                result["serial_port"] = str(link.resolve())
                break

    # Look for C-Media CM108 audio (Digirig uses this chip)
    audio_devices = detect_audio_devices()
    for dev in audio_devices:
        if dev.get("is_digirig"):
            result["audio_card"] = dev["card"]
            break

    # Determine detection status
    has_serial = result["serial_port"] is not None
    has_audio = result["audio_card"] is not None

    if has_serial and has_audio:
        result["found"] = True
        result["status"] = "full"
        result["message"] = "Digirig detected (audio + CAT)"
    elif has_audio:
        # Audio found but no serial - common if CAT cable not connected
        result["found"] = True  # Consider partial detection as "found" for usability
        result["status"] = "audio_only"
        result["message"] = "Audio detected (CAT port not found - check USB cable)"
    elif has_serial:
        result["found"] = True
        result["status"] = "serial_only"
        result["message"] = "CAT port detected (audio not found)"
    else:
        result["found"] = False
        result["status"] = "none"
        result["message"] = "Not detected - check USB connections"

    return result


def test_cat_connection(port: str, radio_id: str) -> dict:
    """
    Test CAT connection to a radio using rigctl.
    Returns dict with test results.
    """
    if MOCK_MODE:
        return {
            "success": True,
            "frequency_hz": 7074000,
            "frequency_mhz": 7.074,
            "message": "Connected! Radio on 7.074 MHz (MOCK)"
        }

    radios = load_radios()
    radio = next((r for r in radios if r["id"] == radio_id), None)

    if not radio:
        return {
            "success": False,
            "error": f"Unknown radio: {radio_id}"
        }

    hamlib_id = radio["hamlib_id"]
    baud_rate = radio["baud_rate"]

    # Build rigctl command
    cmd = ["rigctl", "-m", str(hamlib_id), "-r", port, "-s", str(baud_rate)]

    # Add serial settings if specified
    serial_settings = radio.get("serial_settings", {})
    conf_parts = []
    if serial_settings.get("handshake") == "none":
        conf_parts.append("serial_handshake=None")
    if serial_settings.get("rts_state"):
        conf_parts.append(f"rts_state={serial_settings['rts_state']}")
    if serial_settings.get("dtr_state"):
        conf_parts.append(f"dtr_state={serial_settings['dtr_state']}")

    if conf_parts:
        cmd.extend(["--set-conf=" + ",".join(conf_parts)])

    # Add frequency query command
    cmd.append("f")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=10
        )

        if result.returncode == 0:
            # Parse frequency from output
            freq_str = result.stdout.strip()
            try:
                freq = int(freq_str)
                freq_mhz = freq / 1_000_000
                return {
                    "success": True,
                    "frequency_hz": freq,
                    "frequency_mhz": round(freq_mhz, 3),
                    "message": f"Connected! Radio on {freq_mhz:.3f} MHz"
                }
            except ValueError:
                return {
                    "success": True,
                    "message": "Connected (frequency parse error)",
                    "raw_output": freq_str
                }
        else:
            return {
                "success": False,
                "error": result.stderr.strip() or "CAT connection failed",
                "returncode": result.returncode
            }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "Connection timeout - check cable and radio power"
        }
    except FileNotFoundError:
        return {
            "success": False,
            "error": "rigctl not found - hamlib not installed"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


def release_ptt(port: str, radio_id: str) -> dict:
    """
    Emergency PTT release - unkey the radio.
    """
    radios = load_radios()
    radio = next((r for r in radios if r["id"] == radio_id), None)

    if not radio:
        return {"success": False, "error": f"Unknown radio: {radio_id}"}

    hamlib_id = radio["hamlib_id"]
    baud_rate = radio["baud_rate"]

    base_cmd = ["rigctl", "-m", str(hamlib_id), "-r", port, "-s", str(baud_rate)]

    serial_settings = radio.get("serial_settings", {})
    conf_parts = ["serial_handshake=None"]
    if serial_settings.get("rts_state"):
        conf_parts.append(f"rts_state={serial_settings['rts_state']}")
    if serial_settings.get("dtr_state"):
        conf_parts.append(f"dtr_state={serial_settings['dtr_state']}")

    base_cmd.extend(["--set-conf=" + ",".join(conf_parts)])

    try:
        cmd_off = base_cmd + ["T", "0"]
        result = subprocess.run(cmd_off, capture_output=True, text=True, timeout=5)
        return {"success": True, "message": "PTT released"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def test_ptt(port: str, radio_id: str) -> dict:
    """
    Test PTT (transmit) on a radio.
    Keys the radio briefly then unkeys.
    ALWAYS releases PTT even on error.
    """
    if MOCK_MODE:
        return {
            "success": True,
            "message": "PTT test passed - radio keyed and unkeyed (MOCK)"
        }

    radios = load_radios()
    radio = next((r for r in radios if r["id"] == radio_id), None)

    if not radio:
        return {"success": False, "error": f"Unknown radio: {radio_id}"}

    hamlib_id = radio["hamlib_id"]
    baud_rate = radio["baud_rate"]

    # Build rigctl command
    base_cmd = ["rigctl", "-m", str(hamlib_id), "-r", port, "-s", str(baud_rate)]

    serial_settings = radio.get("serial_settings", {})
    conf_parts = ["serial_handshake=None"]
    if serial_settings.get("rts_state"):
        conf_parts.append(f"rts_state={serial_settings['rts_state']}")
    if serial_settings.get("dtr_state"):
        conf_parts.append(f"dtr_state={serial_settings['dtr_state']}")

    base_cmd.extend(["--set-conf=" + ",".join(conf_parts)])

    keyed = False

    try:
        # Key transmitter
        cmd_on = base_cmd + ["T", "1"]
        result_on = subprocess.run(cmd_on, capture_output=True, text=True, timeout=5)

        if result_on.returncode != 0:
            return {
                "success": False,
                "error": f"PTT on failed: {result_on.stderr.strip()}"
            }

        keyed = True
        time.sleep(0.3)  # Brief key

    finally:
        # ALWAYS unkey - this runs even if there's an exception
        try:
            cmd_off = base_cmd + ["T", "0"]
            subprocess.run(cmd_off, capture_output=True, text=True, timeout=5)
        except Exception:
            pass  # Swallow errors during PTT release - safety critical

    if keyed:
        return {
            "success": True,
            "message": "PTT test passed - radio keyed and unkeyed"
        }
    else:
        return {
            "success": False,
            "error": "PTT test failed"
        }


def get_audio_controls(card: int) -> dict:
    """
    Enumerate available ALSA mixer controls for an audio card.
    Returns dict with playback_controls, capture_controls, and other_controls.
    """
    controls = {
        "playback": [],
        "capture": [],
        "switches": [],
        "all": []
    }

    try:
        # Get list of simple controls
        result = subprocess.run(
            ["amixer", "-c", str(card), "scontrols"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return controls

        # Parse control names from output like: Simple mixer control 'Speaker',0
        for line in result.stdout.split('\n'):
            if "Simple mixer control" in line:
                # Extract control name between quotes
                start = line.find("'") + 1
                end = line.find("'", start)
                if start > 0 and end > start:
                    control_name = line[start:end]
                    controls["all"].append(control_name)

                    # Categorize based on common naming patterns
                    name_lower = control_name.lower()
                    if any(p in name_lower for p in ['speaker', 'pcm', 'master', 'headphone', 'line out', 'playback']):
                        controls["playback"].append(control_name)
                    elif any(p in name_lower for p in ['mic', 'capture', 'line in', 'aux', 'input', 'record']):
                        controls["capture"].append(control_name)
                    elif any(p in name_lower for p in ['agc', 'auto gain', 'boost']):
                        controls["switches"].append(control_name)

    except Exception:
        pass

    return controls


def set_audio_levels(card: int, speaker_pct: int = 64, mic_pct: int = 75) -> dict:
    """
    Set ALSA audio levels for digital modes.
    Automatically discovers available controls and sets appropriate levels.
    Works with Digirig, SignaLink, and built-in radio USB audio.
    """
    if MOCK_MODE:
        return {
            "success": True,
            "message": f"Audio levels set: Speaker {speaker_pct}%, Mic {mic_pct}%, AGC off (MOCK)"
        }

    # First verify the audio card exists
    try:
        result = subprocess.run(
            ["amixer", "-c", str(card), "info"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return {
                "success": False,
                "error": f"Audio card {card} not found or not accessible"
            }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Timeout checking audio card"}
    except FileNotFoundError:
        return {"success": False, "error": "amixer not installed"}
    except Exception as e:
        return {"success": False, "error": f"Error checking audio card: {str(e)}"}

    # Enumerate available controls
    controls = get_audio_controls(card)
    results = {
        "playback_set": None,
        "capture_set": None,
        "agc_disabled": False,
        "warnings": [],
        "available_controls": controls["all"]
    }

    # Known playback control names (in order of preference)
    playback_names = ['Speaker', 'PCM', 'Master', 'Headphone', 'Line Out', 'Playback']
    # Known capture control names (in order of preference)
    capture_names = ['Capture', 'Mic', 'Line In', 'Aux In', 'Input', 'Record']
    # AGC-related controls to disable
    agc_names = ['Auto Gain Control', 'AGC', 'Mic Boost', 'Boost']

    try:
        # Set playback (output) level
        playback_set = False
        for control in playback_names:
            if control in controls["all"] or control in controls["playback"]:
                result = subprocess.run(
                    ["amixer", "-c", str(card), "sset", control, f"{speaker_pct}%"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    results["playback_set"] = control
                    playback_set = True
                    break

        if not playback_set:
            # Try all discovered playback controls
            for control in controls["playback"]:
                result = subprocess.run(
                    ["amixer", "-c", str(card), "sset", control, f"{speaker_pct}%"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    results["playback_set"] = control
                    playback_set = True
                    break

        if not playback_set:
            results["warnings"].append("No playback control found - set TX level in radio menu")

        # Set capture (input) level
        capture_set = False
        for control in capture_names:
            if control in controls["all"] or control in controls["capture"]:
                # Try with 'cap' flag to enable capture
                result = subprocess.run(
                    ["amixer", "-c", str(card), "sset", control, f"{mic_pct}%", "cap"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode != 0:
                    # Try without 'cap' flag
                    result = subprocess.run(
                        ["amixer", "-c", str(card), "sset", control, f"{mic_pct}%"],
                        capture_output=True, text=True, timeout=5
                    )
                if result.returncode == 0:
                    results["capture_set"] = control
                    capture_set = True
                    # Also try to unmute
                    subprocess.run(
                        ["amixer", "-c", str(card), "sset", control, "unmute"],
                        capture_output=True, text=True, timeout=5
                    )
                    break

        if not capture_set:
            # Try all discovered capture controls
            for control in controls["capture"]:
                result = subprocess.run(
                    ["amixer", "-c", str(card), "sset", control, f"{mic_pct}%"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    results["capture_set"] = control
                    capture_set = True
                    break

        if not capture_set:
            results["warnings"].append("No capture control found - set RX level in radio menu")

        # Disable AGC and boost controls (critical for digital modes)
        for control in agc_names:
            if control in controls["all"] or control in controls["switches"]:
                # Try 'off' for toggles
                result = subprocess.run(
                    ["amixer", "-c", str(card), "sset", control, "off"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode != 0:
                    # Try '0' for numeric controls (like Mic Boost)
                    subprocess.run(
                        ["amixer", "-c", str(card), "sset", control, "0"],
                        capture_output=True, text=True, timeout=5
                    )
                results["agc_disabled"] = True

        # Store settings persistently
        subprocess.run(["alsactl", "store"], capture_output=True, timeout=5)

        # Build response message
        if results["playback_set"] and results["capture_set"]:
            message = f"Audio set: Output {speaker_pct}% ({results['playback_set']}), Input {mic_pct}% ({results['capture_set']})"
            if results["agc_disabled"]:
                message += ", AGC disabled"
            return {"success": True, "message": message, "details": results}
        elif results["playback_set"] or results["capture_set"]:
            # Partial success
            parts = []
            if results["playback_set"]:
                parts.append(f"Output {speaker_pct}% ({results['playback_set']})")
            if results["capture_set"]:
                parts.append(f"Input {mic_pct}% ({results['capture_set']})")
            message = "Partial: " + ", ".join(parts)
            if results["warnings"]:
                message += ". " + "; ".join(results["warnings"])
            return {"success": True, "message": message, "details": results}
        else:
            # No controls found - likely built-in radio USB
            return {
                "success": False,
                "error": "No ALSA mixer controls found. This device may have fixed audio levels. Adjust TX/RX audio levels in the radio's menu instead.",
                "details": results
            }

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Timeout setting audio levels"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_radio_audio_guidance(radio_id: str, audio_card: Optional[int] = None) -> dict:
    """
    Get audio configuration guidance for a specific radio.
    Returns guidance based on the radio's audio_settings from radios.json.

    Args:
        radio_id: Radio identifier from radios.json
        audio_card: Optional ALSA card number to check available controls
    """
    radios = load_radios()
    radio = next((r for r in radios if r["id"] == radio_id), None)

    if not radio:
        return {
            "found": False,
            "error": f"Unknown radio: {radio_id}"
        }

    audio_interface = radio.get("audio_interface", "unknown")
    audio_settings = radio.get("audio_settings", {})

    guidance = {
        "found": True,
        "radio_name": f"{radio['manufacturer']} {radio['model']}",
        "audio_interface": audio_interface,
        "has_alsa_control": audio_interface == "external",
        "instructions": [],
        "freedv_target": "-5 dB (acceptable: -10 to 0 dB)"
    }

    if audio_interface == "builtin":
        # Built-in USB audio - no ALSA mixer controls
        guidance["has_alsa_control"] = False
        guidance["instructions"] = [
            "This radio has built-in USB audio - connect directly to Pi via USB",
            "Audio levels are controlled via the radio's menu ONLY",
            "The Pi cannot adjust input levels for this radio"
        ]
        if audio_settings.get("radio_rx_menu"):
            guidance["instructions"].append(f"To adjust RX audio: {audio_settings['radio_rx_menu']}")
        if audio_settings.get("radio_tx_menu"):
            guidance["instructions"].append(f"To adjust TX audio: {audio_settings['radio_tx_menu']}")
        if audio_settings.get("freedv_notes"):
            guidance["instructions"].append(audio_settings["freedv_notes"])

    elif audio_interface == "external":
        # External interface (Digirig/SignaLink) - ALSA controls available
        guidance["has_alsa_control"] = True
        guidance["recommended_alsa_rx"] = audio_settings.get("recommended_alsa_rx", 75)
        guidance["recommended_alsa_tx"] = audio_settings.get("recommended_alsa_tx", 70)
        guidance["instructions"] = [
            "Audio is controlled via ALSA mixer AND radio settings",
            f"Recommended ALSA Capture: {guidance['recommended_alsa_rx']}%",
            f"Recommended ALSA Playback: {guidance['recommended_alsa_tx']}%"
        ]
        if audio_settings.get("radio_rx_menu"):
            guidance["instructions"].append(f"Radio RX setting: {audio_settings['radio_rx_menu']}")
        if audio_settings.get("radio_tx_menu"):
            guidance["instructions"].append(f"Radio TX setting: {audio_settings['radio_tx_menu']}")
        if audio_settings.get("freedv_notes"):
            guidance["instructions"].append(audio_settings["freedv_notes"])

    # Check actual ALSA controls if card number provided
    if audio_card is not None:
        controls = get_audio_controls(audio_card)
        guidance["alsa_controls_found"] = len(controls["all"]) > 0
        guidance["alsa_playback_controls"] = controls["playback"]
        guidance["alsa_capture_controls"] = controls["capture"]

        if not controls["all"]:
            guidance["instructions"].append(
                "WARNING: No ALSA mixer controls found on this device - "
                "all audio adjustment must be done via radio menu"
            )

    return guidance


def get_system_info() -> dict:
    """Get basic system information."""
    info = {
        "hostname": "unknown",
        "os": "unknown",
        "kernel": "unknown",
        "arch": "unknown",
        "pi_model": None,
        "ip_address": None
    }

    try:
        info["hostname"] = subprocess.run(
            ["hostname"], capture_output=True, text=True, timeout=5
        ).stdout.strip()

        info["kernel"] = subprocess.run(
            ["uname", "-r"], capture_output=True, text=True, timeout=5
        ).stdout.strip()

        info["arch"] = subprocess.run(
            ["uname", "-m"], capture_output=True, text=True, timeout=5
        ).stdout.strip()

        # Check for Raspberry Pi
        if Path("/proc/device-tree/model").exists():
            info["pi_model"] = Path("/proc/device-tree/model").read_text().strip().rstrip('\x00')

        # Get OS info
        if Path("/etc/os-release").exists():
            for line in Path("/etc/os-release").read_text().split("\n"):
                if line.startswith("PRETTY_NAME="):
                    info["os"] = line.split("=", 1)[1].strip('"')
                    break

        # Get IP address (prefer eth0, then wlan0)
        try:
            result = subprocess.run(
                ["hostname", "-I"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                ips = result.stdout.strip().split()
                if ips:
                    info["ip_address"] = ips[0]  # First IP (usually primary)
        except Exception:
            pass

    except Exception:
        pass

    return info


if __name__ == "__main__":
    # Test hardware detection
    print("=== System Info ===")
    print(json.dumps(get_system_info(), indent=2))

    print("\n=== Serial Ports ===")
    print(json.dumps(detect_serial_ports(), indent=2))

    print("\n=== Audio Devices ===")
    print(json.dumps(detect_audio_devices(), indent=2))

    print("\n=== Digirig Detection ===")
    print(json.dumps(find_digirig(), indent=2))
