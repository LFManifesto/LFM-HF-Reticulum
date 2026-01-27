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
from pathlib import Path
from typing import Optional

# Path to radio configurations
RADIOS_CONFIG = Path(__file__).parent.parent / "configs" / "radios.json"

# Mock mode for testing without hardware
MOCK_MODE = os.environ.get("RETICULUMHF_MOCK", "0") == "1"

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


def set_audio_levels(card: int, speaker_pct: int = 64, mic_pct: int = 75) -> dict:
    """
    Set ALSA audio levels for Digirig (digital modes).
    Disables AGC which can distort digital signals.
    Returns success only if the audio card exists and commands succeed.
    """
    if MOCK_MODE:
        return {
            "success": True,
            "message": f"Audio levels set: Speaker {speaker_pct}%, Mic {mic_pct}%, AGC off (MOCK)"
        }

    errors = []
    warnings = []

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

    try:
        # Set speaker (output) level - may be named "Speaker" or "PCM"
        result = subprocess.run(
            ["amixer", "-c", str(card), "sset", "Speaker", f"{speaker_pct}%"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            # Try PCM instead
            result2 = subprocess.run(
                ["amixer", "-c", str(card), "sset", "PCM", f"{speaker_pct}%"],
                capture_output=True, text=True, timeout=5
            )
            if result2.returncode != 0:
                warnings.append("Could not set output level (no Speaker or PCM control)")

        # Set mic (input) level and enable capture
        result = subprocess.run(
            ["amixer", "-c", str(card), "sset", "Mic", f"{mic_pct}%", "cap"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            # Try Capture instead
            result2 = subprocess.run(
                ["amixer", "-c", str(card), "sset", "Capture", f"{mic_pct}%"],
                capture_output=True, text=True, timeout=5
            )
            if result2.returncode != 0:
                errors.append("Could not set input level (no Mic or Capture control)")

        # Unmute mic
        subprocess.run(
            ["amixer", "-c", str(card), "sset", "Mic", "unmute"],
            capture_output=True, text=True, timeout=5
        )

        # Disable Auto Gain Control (required for digital modes)
        # This control may not exist on all devices - that's OK
        subprocess.run(
            ["amixer", "-c", str(card), "sset", "Auto Gain Control", "off"],
            capture_output=True, text=True, timeout=5
        )

        # Store settings
        subprocess.run(
            ["alsactl", "store"],
            capture_output=True, timeout=5
        )

        # Return result based on errors
        if errors:
            return {
                "success": False,
                "error": "; ".join(errors)
            }

        message = f"Audio levels set: Output {speaker_pct}%, Input {mic_pct}%"
        if warnings:
            message += f" (Warnings: {'; '.join(warnings)})"

        return {
            "success": True,
            "message": message
        }

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Timeout setting audio levels"}
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


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
