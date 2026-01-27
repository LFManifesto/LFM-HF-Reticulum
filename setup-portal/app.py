#!/usr/bin/env python3
"""
ReticulumHF Setup Portal - Web-based configuration wizard.
Runs as a captive portal on first boot for zero-config setup.
"""

import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from flask import Flask, render_template, request, jsonify, redirect, url_for

from hardware import (
    load_radios, detect_serial_ports, detect_audio_devices,
    find_digirig, test_cat_connection, test_ptt, release_ptt,
    set_audio_levels, get_audio_controls, get_radio_audio_guidance,
    get_system_info, start_audio_monitor, stop_audio_monitor,
    get_audio_levels, get_audio_level_single, set_single_audio_control,
    get_single_audio_control
)

# Configuration constants
FREEDVTNC2_STARTUP_TIMEOUT_SECS = 15  # Wait for freedvtnc2 to start listening
FREEDVTNC2_POLL_INTERVAL_SECS = 0.5   # Check interval during startup

app = Flask(__name__)


@app.after_request
def add_cache_headers(response):
    """Prevent browser caching - always serve fresh content."""
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    # Remove ETag to prevent 304 responses
    response.headers.pop('ETag', None)
    response.headers.pop('Last-Modified', None)
    return response


# Configuration paths
CONFIG_DIR = Path(__file__).parent.parent / "configs"
RETICULUMHF_DIR = Path("/etc/reticulumhf")
RETICULUMHF_CONFIG_ENV = RETICULUMHF_DIR / "config.env"
RETICULUMHF_BACKUPS_DIR = RETICULUMHF_DIR / "backups"
SETUP_COMPLETE_FLAG = RETICULUMHF_DIR / ".setup_complete"
PI_HOME = Path("/home/pi")
RETICULUM_DIR = PI_HOME / ".reticulum"
RETICULUM_CONFIG = RETICULUM_DIR / "config"
FREEDVTNC2_BIN = PI_HOME / ".local/bin/freedvtnc2"
HOSTAPD_CONF = Path("/etc/hostapd/hostapd.conf")
ASOUND_CONF = Path("/etc/asound.conf")


def load_peers() -> dict:
    """Load peer configurations."""
    peers_file = CONFIG_DIR / "peers.json"
    if peers_file.exists():
        with open(peers_file) as f:
            return json.load(f)
    return {}


def is_setup_complete() -> bool:
    """Check if initial setup has been completed."""
    return SETUP_COMPLETE_FLAG.exists()


def get_radio_by_id(radio_id: str) -> Optional[dict]:
    """
    Look up radio configuration by ID.
    Returns radio dict or None if not found.
    """
    radios = load_radios()
    return next((r for r in radios if r["id"] == radio_id), None)


def backup_existing_configs() -> dict:
    """
    Backup existing configuration files before overwriting.
    Returns dict with backup paths or None if no backup needed.
    """
    RETICULUMHF_BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backups = {}

    # Backup config.env if it exists
    if RETICULUMHF_CONFIG_ENV.exists():
        backup_path = RETICULUMHF_BACKUPS_DIR / f"config.env.{timestamp}"
        try:
            shutil.copy2(RETICULUMHF_CONFIG_ENV, backup_path)
            backups["config_env"] = str(backup_path)
        except Exception:
            pass

    # Backup Reticulum config if it exists
    reticulum_config = RETICULUM_CONFIG
    if reticulum_config.exists():
        backup_path = RETICULUMHF_BACKUPS_DIR / f"reticulum_config.{timestamp}"
        try:
            shutil.copy2(reticulum_config, backup_path)
            backups["reticulum_config"] = str(backup_path)
        except Exception:
            pass

    return backups


def validate_config_env(config_path: Optional[Path] = None) -> Tuple[bool, str, dict]:
    """
    Validate that config.env exists and contains required variables.
    Returns (is_valid, error_message, config_dict).
    """
    if config_path is None:
        config_path = RETICULUMHF_CONFIG_ENV

    if not config_path.exists():
        return False, "config.env not found", {}

    required_keys = ["RADIO_ID", "AUDIO_CARD", "FREEDVTNC2_CMD"]
    config = {}

    try:
        content = config_path.read_text()
        for line in content.split('\n'):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, value = line.partition('=')
                # Remove quotes from value
                value = value.strip('"').strip("'")
                config[key.strip()] = value
    except Exception as e:
        return False, f"Failed to read config.env: {e}", {}

    # Check required keys
    missing = [k for k in required_keys if not config.get(k)]
    if missing:
        return False, f"Missing required config: {', '.join(missing)}", config

    # Validate FREEDVTNC2_CMD is not empty
    if not config.get("FREEDVTNC2_CMD", "").strip():
        return False, "FREEDVTNC2_CMD is empty", config

    return True, "", config


def generate_reticulum_config(radio_id: str, serial_port: str, audio_card: int,
                               ifac_name: str = "", ifac_pass: str = "") -> str:
    """
    Generate Reticulum configuration file content.
    Configures HF interface via freedvtnc2.

    Args:
        radio_id: Radio identifier from radios.json
        serial_port: Serial port for CAT control (or empty for VOX)
        audio_card: ALSA audio card number
        ifac_name: Optional IFAC network name for gateway security
        ifac_pass: Optional IFAC passphrase for gateway security
    """
    radio = get_radio_by_id(radio_id)
    if not radio:
        raise ValueError(f"Unknown radio: {radio_id}")

    config_lines = [
        "# ReticulumHF Gateway Configuration",
        "# Generated by setup wizard",
        f"# Radio: {radio['manufacturer']} {radio['model']}",
        "",
        "[reticulum]",
        "  # Gateway mode - routes traffic between phone and HF radio",
        "  enable_transport = yes",
        "  share_instance = yes",
        "  shared_instance_port = 37428",
        "  instance_control_port = 37429",
        "",
        "[interfaces]",
        "",
        "  # Gateway interface for Sideband/Columba connections",
        "  # Phone connects to this via TCPClientInterface",
        "  [[TCP Gateway]]",
        "    type = TCPServerInterface",
        "    enabled = yes",
        "    listen_ip = 0.0.0.0",
        "    listen_port = 4242",
        "    mode = gateway",
    ]

    # Add IFAC security if configured
    if ifac_name:
        config_lines.append(f"    network_name = {ifac_name}")
    if ifac_pass:
        config_lines.append(f"    passphrase = {ifac_pass}")

    config_lines.extend([
        "",
        "  # FreeDV HF Interface (via freedvtnc2)",
        "  [[FreeDV HF]]",
        "    type = TCPClientInterface",
        "    enabled = yes",
        "    target_host = 127.0.0.1",
        "    target_port = 8001",
        "    kiss_framing = yes",
        "    # Rate limit announces on HF to reduce unnecessary transmissions",
        "    announce_cap = 1",
        "    # Re-announce at most once per hour",
        "    announce_rate_target = 3600",
        "    announce_rate_grace = 3",
        "",
    ])

    return "\n".join(config_lines)


def get_freedvtnc2_device_id(alsa_card: int) -> int:
    """
    Map ALSA card number to freedvtnc2 device ID.
    freedvtnc2 uses portaudio which numbers devices differently.
    For Digirig on card 3, it's typically device 1.
    """
    # Run freedvtnc2 --list-audio-devices and find the matching device
    try:
        result = subprocess.run(
            [str(FREEDVTNC2_BIN), "--list-audio-devices"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                # Look for line containing hw:N where N is our card
                if f"hw:{alsa_card}" in line:
                    # Extract the device ID from the start of the line
                    parts = line.split()
                    if parts and parts[0].isdigit():
                        return int(parts[0])
    except Exception:
        pass
    # Default fallback - device 1 is usually the USB audio
    return 1


def generate_freedvtnc2_command(radio_id: str, serial_port: str, audio_card: int,
                                  freedv_mode: str = "DATAC1") -> str:
    """
    Generate freedvtnc2 launch command.

    Args:
        radio_id: Radio identifier from radios.json
        serial_port: Serial port for radio CAT control
        audio_card: ALSA audio card number
        freedv_mode: FreeDV data mode (DATAC1, DATAC3, DATAC4)
    """
    radio = get_radio_by_id(radio_id)
    if not radio:
        raise ValueError(f"Unknown radio: {radio_id}")

    # Validate FreeDV mode
    valid_modes = ["DATAC0", "DATAC1", "DATAC3", "DATAC4", "DATAC13", "DATAC14"]
    if freedv_mode not in valid_modes:
        freedv_mode = "DATAC1"  # Default to DATAC1 if invalid

    # Map ALSA card to freedvtnc2 device ID
    device_id = get_freedvtnc2_device_id(audio_card)

    # Get PTT timing from radio config or use defaults
    ptt_on_delay = radio.get("ptt_on_delay_ms", 300)
    ptt_off_delay = radio.get("ptt_off_delay_ms", 200)

    # Check if VOX mode (audio-only interface, no CAT control)
    ptt_method = radio.get("ptt_method", "")
    use_vox = (ptt_method.upper() == "VOX") or (serial_port is None or serial_port == "")
    rigctld_port = "0" if use_vox else "4532"

    cmd_parts = [
        str(FREEDVTNC2_BIN),
        "--no-cli",
        f"--input-device {device_id}",
        f"--output-device {device_id}",
        f"--mode {freedv_mode}",
        f"--rigctld-port {rigctld_port}",
        "--kiss-tcp-port 8001",
        "--kiss-tcp-address 0.0.0.0",
        f"--ptt-on-delay-ms {ptt_on_delay}",
        f"--ptt-off-delay-ms {ptt_off_delay}",
        "--output-volume -3"
    ]

    return " ".join(cmd_parts)


def generate_rigctld_command(radio_id: str, serial_port: str) -> str:
    """
    Generate rigctld launch command.
    """
    radio = get_radio_by_id(radio_id)
    if not radio:
        raise ValueError(f"Unknown radio: {radio_id}")

    cmd_parts = [
        "rigctld",
        f"-m {radio['hamlib_id']}",
        f"-r {serial_port}",
        f"-s {radio['baud_rate']}",
        "-t 4532"
    ]

    # PTT method (RTS for Digirig, CAT for some radios)
    ptt_method = radio.get("ptt_method")
    if ptt_method and ptt_method.upper() != "VOX":
        cmd_parts.append(f"-P {ptt_method}")

    return " ".join(cmd_parts)


@app.route("/")
def index():
    """Main setup page."""
    if is_setup_complete():
        return redirect(url_for("status"))

    radios = load_radios()
    system_info = get_system_info()

    # Group radios by manufacturer
    manufacturers = {}
    for radio in radios:
        mfr = radio["manufacturer"]
        if mfr not in manufacturers:
            manufacturers[mfr] = []
        manufacturers[mfr].append(radio)

    return render_template("setup.html",
                           manufacturers=manufacturers,
                           system_info=system_info)


@app.route("/status")
def status():
    """System status page (shown after setup)."""
    return render_template("status.html",
                           system_info=get_system_info())


@app.route("/api/detect-hardware")
def api_detect_hardware():
    """API endpoint to detect connected hardware."""
    serial_ports = detect_serial_ports()
    audio_devices = detect_audio_devices()
    digirig = find_digirig()

    return jsonify({
        "serial_ports": serial_ports,
        "audio_devices": audio_devices,
        "digirig": digirig,
        "recommended": {
            "serial_port": digirig.get("serial_port") if digirig.get("found") else (
                serial_ports[0]["port"] if serial_ports else None
            ),
            "audio_card": digirig.get("audio_card") if digirig.get("found") else (
                next((d["card"] for d in audio_devices if d.get("type") == "usb"), None)
            )
        }
    })


@app.route("/api/test-cat", methods=["POST"])
def api_test_cat():
    """API endpoint to test CAT connection."""
    data = request.json
    port = data.get("port")
    radio_id = data.get("radio_id")

    if not port or not radio_id:
        return jsonify({"success": False, "error": "Missing port or radio_id"}), 400

    result = test_cat_connection(port, radio_id)
    return jsonify(result)


@app.route("/api/test-ptt", methods=["POST"])
def api_test_ptt():
    """API endpoint to test PTT."""
    data = request.json
    port = data.get("port")
    radio_id = data.get("radio_id")

    if not port or not radio_id:
        return jsonify({"success": False, "error": "Missing port or radio_id"}), 400

    result = test_ptt(port, radio_id)
    return jsonify(result)


@app.route("/api/release-ptt", methods=["POST"])
def api_release_ptt():
    """API endpoint to emergency release PTT (unkey radio)."""
    data = request.json
    port = data.get("port")
    radio_id = data.get("radio_id")

    if not port or not radio_id:
        return jsonify({"success": False, "error": "Missing port or radio_id"}), 400

    result = release_ptt(port, radio_id)
    return jsonify(result)


@app.route("/api/set-audio", methods=["POST"])
def api_set_audio():
    """API endpoint to set audio levels."""
    data = request.json
    card = data.get("card")
    speaker = data.get("speaker", 64)
    mic = data.get("mic", 75)

    if card is None:
        return jsonify({"success": False, "error": "Missing card"}), 400

    result = set_audio_levels(card, speaker, mic)
    return jsonify(result)


@app.route("/api/audio-controls/<int:card>")
def api_audio_controls(card):
    """API endpoint to enumerate available audio controls for a card."""
    controls = get_audio_controls(card)
    return jsonify({
        "success": True,
        "card": card,
        "controls": controls,
        "hint": "If no controls found, audio levels must be set in radio menu"
    })


@app.route("/api/radio/<radio_id>/audio-guide")
def api_radio_audio_guide(radio_id):
    """
    API endpoint to get audio configuration guide for a specific radio.
    Returns the audio_settings from radios.json with helpful context for the Pi gateway.
    """
    radio = get_radio_by_id(radio_id)
    if not radio:
        return jsonify({"success": False, "error": f"Unknown radio: {radio_id}"}), 404

    audio_interface = radio.get("audio_interface", "unknown")
    audio_settings = radio.get("audio_settings", {})

    # Build response with gateway-specific guidance
    response = {
        "success": True,
        "radio_id": radio_id,
        "radio_name": f"{radio['manufacturer']} {radio['model']}",
        "audio_interface": audio_interface,
        "requires_external_audio": radio.get("requires_external_audio", audio_interface == "external"),
        "audio_settings": audio_settings,
        "freedv_target_level": "-5 dB (acceptable range: -10 to 0 dB)",
        "gateway_notes": []
    }

    # Add gateway-specific notes based on audio interface type
    if audio_interface == "builtin":
        response["gateway_notes"] = [
            "This radio has built-in USB audio - connect directly to the Pi via USB",
            "Audio levels are controlled via the radio's menu (not ALSA mixer)",
            "If freedvtnc2 shows input level below -10 dB, increase the radio's RX audio output setting",
            "The Pi cannot adjust input levels for this radio - you must use the radio's menu"
        ]
        if "radio_rx_menu" in audio_settings:
            response["gateway_notes"].append(f"RX level setting: {audio_settings['radio_rx_menu']}")
    elif audio_interface == "external":
        response["gateway_notes"] = [
            "This radio requires an external interface (Digirig/SignaLink)",
            "Audio levels can be adjusted via ALSA mixer AND radio settings",
            "If freedvtnc2 shows input level below -10 dB, increase ALSA Capture AND radio output level",
            f"Recommended ALSA Capture: {audio_settings.get('recommended_alsa_rx', 75)}%"
        ]
        if "radio_rx_menu" in audio_settings:
            response["gateway_notes"].append(f"Radio RX level: {audio_settings['radio_rx_menu']}")

    return jsonify(response)


@app.route("/api/audio-monitor/start", methods=["POST"])
def api_audio_monitor_start():
    """Start real-time audio level monitoring."""
    data = request.json or {}
    card = data.get("card")

    if card is None:
        return jsonify({"success": False, "error": "Missing 'card' parameter"}), 400

    try:
        card = int(card)
    except ValueError:
        return jsonify({"success": False, "error": "Invalid card number"}), 400

    result = start_audio_monitor(card)
    return jsonify(result)


@app.route("/api/audio-monitor/stop", methods=["POST"])
def api_audio_monitor_stop():
    """Stop audio level monitoring."""
    result = stop_audio_monitor()
    return jsonify(result)


@app.route("/api/audio-monitor/levels")
def api_audio_monitor_levels():
    """Get current audio levels from the monitor."""
    result = get_audio_levels()
    return jsonify(result)


@app.route("/api/audio-level/check/<int:card>")
def api_audio_level_check(card):
    """Get a single audio level reading (1 second sample)."""
    result = get_audio_level_single(card)
    return jsonify(result)


@app.route("/api/audio-level/set", methods=["POST"])
def api_audio_level_set():
    """Set a specific ALSA mixer control level."""
    data = request.json or {}
    card = data.get("card")
    control = data.get("control")
    level = data.get("level")

    if card is None or control is None or level is None:
        return jsonify({"success": False, "error": "Missing card, control, or level"}), 400

    try:
        card = int(card)
        level = int(level)
    except ValueError:
        return jsonify({"success": False, "error": "Invalid card or level"}), 400

    result = set_single_audio_control(card, control, level)
    return jsonify(result)


@app.route("/api/audio-level/get/<int:card>/<control>")
def api_audio_level_get(card, control):
    """Get current level for a specific ALSA mixer control."""
    result = get_single_audio_control(card, control)
    return jsonify(result)


def validate_wifi_settings(ssid: str, password: str) -> Tuple[bool, str]:
    """
    Validate WiFi SSID and password.
    Returns (is_valid, error_message)
    """
    if ssid and len(ssid) > 32:
        return False, "WiFi SSID must be 32 characters or less"

    if password and (len(password) < 8 or len(password) > 63):
        return False, "WiFi password must be 8-63 characters (WPA2 requirement)"

    # Check for dangerous characters in SSID that could break hostapd config
    if ssid and any(c in ssid for c in ['"', "'", "\\", "\n", "\r"]):
        return False, "WiFi SSID contains invalid characters"

    return True, ""


def get_current_wifi_ssid() -> str:
    """Get the current WiFi SSID from hostapd.conf."""
    hostapd_conf = Path("/etc/hostapd/hostapd.conf")
    if hostapd_conf.exists():
        try:
            content = hostapd_conf.read_text()
            for line in content.split('\n'):
                if line.startswith('ssid='):
                    return line.split('=', 1)[1].strip()
        except Exception:
            pass
    return "ReticulumHF-Setup"


def update_alsa_config(audio_card: int) -> bool:
    """
    Update /etc/asound.conf with the correct audio card number.
    Returns True on success, False on failure.
    """
    asound_conf = Path("/etc/asound.conf")

    config_content = f"""# ReticulumHF ALSA Configuration
# Generated by setup wizard for audio card {audio_card}

# Disable the modem PCM type which causes "Unknown PCM cards.pcm.modem" error
pcm.!modem {{
    type null
}}

ctl.!modem {{
    type null
}}

# Define USB audio device (card {audio_card})
pcm.usbaudio {{
    type hw
    card {audio_card}
    device 0
}}

ctl.usbaudio {{
    type hw
    card {audio_card}
}}

# Software mixing for USB audio
pcm.usbmix {{
    type dmix
    ipc_key 1024
    slave {{
        pcm "usbaudio"
        period_time 0
        period_size 1024
        buffer_size 4096
    }}
}}

# Duplex device for simultaneous input/output
pcm.usbduplex {{
    type asym
    playback.pcm "usbmix"
    capture.pcm "usbaudio"
}}

# Default device - use built-in audio to avoid conflicts
defaults.pcm.card 0
defaults.ctl.card 0
"""

    try:
        with open(asound_conf, 'w') as f:
            f.write(config_content)
        return True
    except Exception:
        return False


def update_hostapd_config(ssid: str, password: str = None) -> bool:
    """
    Update hostapd.conf with new SSID and optionally password.
    Returns True on success, False on failure.
    """
    hostapd_conf = Path("/etc/hostapd/hostapd.conf")

    # Build new config
    config_lines = [
        "# ReticulumHF WiFi Access Point",
        "# Updated by setup wizard",
        "interface=wlan0",
        "driver=nl80211",
        f"ssid={ssid}",
        "hw_mode=g",
        "channel=7",
        "wmm_enabled=0",
        "macaddr_acl=0",
        "auth_algs=1",
        "ignore_broadcast_ssid=0",
        "country_code=US",
        "ieee80211n=1",
    ]

    if password:
        # WPA2 secured network
        config_lines.extend([
            "wpa=2",
            f"wpa_passphrase={password}",
            "wpa_key_mgmt=WPA-PSK",
            "wpa_pairwise=TKIP",
            "rsn_pairwise=CCMP",
        ])
    else:
        # Open network
        config_lines.append("wpa=0")

    try:
        with open(hostapd_conf, 'w') as f:
            f.write('\n'.join(config_lines) + '\n')
        return True
    except Exception:
        return False


@app.route("/api/complete-setup", methods=["POST"])
def api_complete_setup():
    """API endpoint to finalize setup and generate configs."""
    data = request.json

    radio_id = data.get("radio_id")
    serial_port = data.get("serial_port")
    audio_card = data.get("audio_card")
    freedv_mode = data.get("freedv_mode", "DATAC1")
    user_vox_mode = data.get("vox_mode", False)  # User explicitly chose VOX mode

    if not all([radio_id, audio_card is not None]):
        return jsonify({"success": False, "error": "Missing required fields"}), 400

    # Validate serial_port requirement based on radio's PTT method
    radios = load_radios()
    radio = next((r for r in radios if r["id"] == radio_id), None)
    if not radio:
        return jsonify({"success": False, "error": f"Unknown radio: {radio_id}"}), 400

    ptt_method = radio.get("ptt_method", "")
    is_vox_radio = ptt_method.upper() == "VOX" or user_vox_mode  # Radio default OR user choice

    # Non-VOX radios require a serial port for CAT control
    if not is_vox_radio and not serial_port:
        return jsonify({
            "success": False,
            "error": "Serial port required for CAT control. Select a port or use a VOX-capable radio."
        }), 400

    # Get IFAC security settings
    ifac_name = data.get("ifac_name", "")
    ifac_pass = data.get("ifac_pass", "")

    # Get WiFi settings (empty means keep current)
    wifi_ssid = data.get("wifi_ssid", "").strip()
    wifi_password = data.get("wifi_password", "")
    wifi_changed = False

    # Get current SSID before any changes
    current_ssid = get_current_wifi_ssid()

    # Only validate and update WiFi if user provided new settings
    if wifi_ssid:
        wifi_valid, wifi_error = validate_wifi_settings(wifi_ssid, wifi_password)
        if not wifi_valid:
            return jsonify({"success": False, "error": wifi_error}), 400
        wifi_changed = (wifi_ssid != current_ssid)
    else:
        # Keep current SSID
        wifi_ssid = current_ssid

    try:
        # Backup existing configs before overwriting (prevents data loss)
        config_backups = backup_existing_configs()

        # Generate Reticulum config
        reticulum_config = generate_reticulum_config(
            radio_id, serial_port, audio_card,
            ifac_name=ifac_name, ifac_pass=ifac_pass
        )

        # Ensure .reticulum directory exists for pi user
        reticulum_dir = Path("/home/pi/.reticulum")
        reticulum_dir.mkdir(parents=True, exist_ok=True)

        # Write Reticulum config
        with open(reticulum_dir / "config", "w") as f:
            f.write(reticulum_config)

        # Ensure pi owns the config
        subprocess.run(["chown", "-R", "pi:pi", str(reticulum_dir)], capture_output=True)

        # Update ALSA configuration with the correct audio card number
        # This fixes "Unknown PCM cards.pcm.modem" errors in freedvtnc2
        update_alsa_config(audio_card)

        # Generate service environment file
        env_content = f"""# ReticulumHF Service Configuration
# Generated by setup wizard

RADIO_ID={radio_id}
SERIAL_PORT={serial_port}
AUDIO_CARD={audio_card}
FREEDV_MODE={freedv_mode}

# rigctld command
RIGCTLD_CMD="{generate_rigctld_command(radio_id, serial_port)}"

# freedvtnc2 command
FREEDVTNC2_CMD="{generate_freedvtnc2_command(radio_id, serial_port, audio_card, freedv_mode)}"

# WiFi AP settings (for Sideband connections)
RETICULUMHF_AP_SSID={wifi_ssid}
RETICULUMHF_AP_PASS={wifi_password}
"""

        # Write environment file
        env_dir = Path("/etc/reticulumhf")
        env_dir.mkdir(parents=True, exist_ok=True)
        with open(env_dir / "config.env", "w") as f:
            f.write(env_content)

        # Validate the config we just wrote
        config_valid, config_error, _ = validate_config_env(env_dir / "config.env")
        if not config_valid:
            return jsonify({"success": False, "error": f"Config validation failed: {config_error}"}), 500

        # Mark setup as complete
        SETUP_COMPLETE_FLAG.parent.mkdir(parents=True, exist_ok=True)
        SETUP_COMPLETE_FLAG.touch()

        # Update hostapd.conf if WiFi settings changed
        if wifi_changed:
            if not update_hostapd_config(wifi_ssid, wifi_password if wifi_password else None):
                return jsonify({"success": False, "error": "Failed to update WiFi configuration"}), 500
            # Restart hostapd to apply new SSID
            subprocess.run(["systemctl", "restart", "hostapd"], capture_output=True)

        # Determine if using VOX mode (no CAT control needed)
        # Note: radio and is_vox_radio already loaded during validation above
        use_vox = is_vox_radio or (not serial_port)

        # Enable and start the HF stack services
        subprocess.run(["systemctl", "daemon-reload"], capture_output=True)

        if use_vox:
            # VOX mode - only enable freedvtnc2 and rnsd (no rigctld needed)
            subprocess.run(["systemctl", "enable", "freedvtnc2", "reticulumhf-rnsd"], capture_output=True)
            subprocess.run(["systemctl", "disable", "rigctld"], capture_output=True)
        else:
            # CAT mode - enable all services including rigctld
            subprocess.run(["systemctl", "enable", "rigctld", "freedvtnc2", "reticulumhf-rnsd"], capture_output=True)

        # Start radio services FIRST (freedvtnc2 must be listening before rnsd connects)
        if not use_vox:
            subprocess.run(["systemctl", "start", "rigctld"], capture_output=True)
        subprocess.run(["systemctl", "start", "freedvtnc2"], capture_output=True)

        # Wait for freedvtnc2 to be listening on KISS port before restarting rnsd
        max_checks = int(FREEDVTNC2_STARTUP_TIMEOUT_SECS / FREEDVTNC2_POLL_INTERVAL_SECS)
        for _ in range(max_checks):
            result = subprocess.run(
                ["ss", "-tln", "sport", "=", "8001"],
                capture_output=True, text=True
            )
            if "8001" in result.stdout:
                break
            time.sleep(FREEDVTNC2_POLL_INTERVAL_SECS)

        # Now restart rnsd to connect to freedvtnc2
        subprocess.run(["systemctl", "restart", "reticulumhf-rnsd"], capture_output=True)

        # Start persistent WiFi AP for Sideband connections
        subprocess.run(["systemctl", "start", "reticulumhf-wlan"], capture_output=True)

        return jsonify({
            "success": True,
            "message": "Setup complete! Gateway ready for Sideband.",
            "wifi_ssid": wifi_ssid,
            "wifi_changed": wifi_changed,
            "gateway_port": 4242,
            "ifac_name": ifac_name,
            "ifac_enabled": bool(ifac_name or ifac_pass),
            "reboot_required": False
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/radios")
def api_radios():
    """API endpoint to get radio list."""
    return jsonify(load_radios())


@app.route("/api/peers")
def api_peers():
    """API endpoint to get peer list."""
    return jsonify(load_peers())


@app.route("/api/system-info")
def api_system_info():
    """API endpoint to get system information."""
    return jsonify(get_system_info())


@app.route("/api/rnstatus")
def api_rnstatus():
    """API endpoint to get rnstatus output."""
    # Run rnstatus as pi user since rnsd runs as pi and the shared instance
    # socket is in pi's home directory
    try:
        result = subprocess.run(
            ["su", "-", "pi", "-c", "/home/pi/.local/bin/rnstatus"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return jsonify({
                "success": True,
                "output": result.stdout
            })
        elif "No shared RNS instance" in (result.stdout + result.stderr):
            return jsonify({
                "success": True,
                "output": "No shared RNS instance running. Start rnsd first."
            })
        else:
            return jsonify({
                "success": False,
                "error": result.stderr.strip() or result.stdout.strip() or "rnstatus failed"
            })
    except subprocess.TimeoutExpired:
        return jsonify({
            "success": False,
            "error": "rnstatus timed out"
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        })


@app.route("/api/service-status")
def api_service_status():
    """API endpoint to check service status."""
    def check_service(name):
        try:
            result = subprocess.run(
                ["systemctl", "is-active", name],
                capture_output=True, text=True, timeout=5
            )
            return {"running": result.stdout.strip() == "active"}
        except Exception:
            return {"running": False}

    def check_process(name):
        try:
            result = subprocess.run(
                ["pgrep", "-x", name],
                capture_output=True, timeout=5
            )
            return {"running": result.returncode == 0}
        except Exception:
            return {"running": False}

    def check_wifi():
        """Check WiFi AP status and get SSID from config."""
        try:
            # Check if hostapd is running
            result = subprocess.run(
                ["systemctl", "is-active", "hostapd"],
                capture_output=True, text=True, timeout=5
            )
            running = result.stdout.strip() == "active"

            # Get SSID from config
            ssid = "ReticulumHF"  # default
            env_file = Path("/etc/reticulumhf/config.env")
            if env_file.exists():
                content = env_file.read_text()
                for line in content.split('\n'):
                    if line.startswith('RETICULUMHF_AP_SSID='):
                        ssid = line.split('=', 1)[1].strip()
                        break

            return {"running": running, "ssid": ssid}
        except Exception:
            return {"running": False, "ssid": "ReticulumHF"}

    def get_gateway_config():
        """Get gateway configuration including IFAC settings."""
        config = {
            "host": "192.168.4.1",
            "port": 4242,
            "ifac_name": "",
            "ifac_pass_set": False
        }
        try:
            reticulum_config = Path("/home/pi/.reticulum/config")
            if reticulum_config.exists():
                content = reticulum_config.read_text()
                for line in content.split('\n'):
                    line = line.strip()
                    if line.startswith('network_name ='):
                        config["ifac_name"] = line.split('=', 1)[1].strip()
                    elif line.startswith('passphrase ='):
                        config["ifac_pass_set"] = True
        except Exception:
            pass
        return config

    return jsonify({
        "rnsd": check_process("rnsd"),
        "rigctld": check_process("rigctld"),
        "freedvtnc2": check_process("freedvtnc2"),
        "dnsmasq": check_process("dnsmasq"),
        "wifi": check_wifi(),
        "gateway": get_gateway_config()
    })


@app.route("/api/lxmf-address")
def api_lxmf_address():
    """API endpoint to get user's LXMF address."""
    try:
        # Try to get address from NomadNet identity
        identity_path = Path("/home/pi/.nomadnetwork/storage/identity")
        if identity_path.exists():
            result = subprocess.run(
                ["su", "-", "pi", "-c", "/home/pi/.local/bin/rnid -i ~/.nomadnetwork/storage/identity -p"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                # Parse the hash from rnid output
                for line in result.stdout.split("\n"):
                    if "<" in line and ">" in line:
                        # Extract hash between < and >
                        start = line.find("<") + 1
                        end = line.find(">")
                        if start > 0 and end > start:
                            return jsonify({
                                "success": True,
                                "address": line[start:end],
                                "note": "Share this address to receive messages"
                            })

        # If NomadNet hasn't been run yet
        return jsonify({
            "success": True,
            "address": None,
            "note": "Run 'nomadnet' once to generate your address"
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/config-info")
def api_config_info():
    """API endpoint to get current configuration info."""
    config = {
        "radio": None,
        "serial_port": None,
        "audio_card": None,
        "freedv_mode": "DATAC1",
        "setup_complete": is_setup_complete()
    }

    # Read from config.env if it exists
    env_file = Path("/etc/reticulumhf/config.env")
    if env_file.exists():
        try:
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("RADIO_ID="):
                        radio_id = line.split("=", 1)[1]
                        # Look up radio name
                        radios = load_radios()
                        radio = next((r for r in radios if r["id"] == radio_id), None)
                        if radio:
                            config["radio"] = f"{radio['manufacturer']} {radio['model']}"
                        else:
                            config["radio"] = radio_id
                    elif line.startswith("SERIAL_PORT="):
                        config["serial_port"] = line.split("=", 1)[1]
                    elif line.startswith("AUDIO_CARD="):
                        try:
                            config["audio_card"] = int(line.split("=", 1)[1])
                        except ValueError:
                            config["audio_card"] = None
                    elif line.startswith("FREEDV_MODE="):
                        config["freedv_mode"] = line.split("=", 1)[1]
        except Exception:
            pass

    return jsonify(config)


@app.route("/api/set-freedv-mode", methods=["POST"])
def api_set_freedv_mode():
    """API endpoint to change FreeDV mode."""
    data = request.get_json()
    new_mode = data.get("mode", "DATAC1")

    valid_modes = ["DATAC1", "DATAC3", "DATAC4"]
    if new_mode not in valid_modes:
        return jsonify({"success": False, "error": f"Invalid mode. Must be one of: {', '.join(valid_modes)}"})

    env_file = Path("/etc/reticulumhf/config.env")
    if not env_file.exists():
        return jsonify({"success": False, "error": "Configuration not found. Run setup first."})

    try:
        # Read current config
        config_lines = []
        radio_id = None
        serial_port = None
        audio_card = None

        with open(env_file) as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("RADIO_ID="):
                    radio_id = stripped.split("=", 1)[1]
                elif stripped.startswith("SERIAL_PORT="):
                    serial_port = stripped.split("=", 1)[1]
                elif stripped.startswith("AUDIO_CARD="):
                    try:
                        audio_card = int(stripped.split("=", 1)[1])
                    except ValueError:
                        pass

                # Update FREEDV_MODE line
                if stripped.startswith("FREEDV_MODE="):
                    config_lines.append(f"FREEDV_MODE={new_mode}\n")
                # Update FREEDVTNC2_CMD line
                elif stripped.startswith("FREEDVTNC2_CMD="):
                    if radio_id and audio_card is not None:
                        new_cmd = generate_freedvtnc2_command(radio_id, serial_port or "", audio_card, new_mode)
                        config_lines.append(f'FREEDVTNC2_CMD="{new_cmd}"\n')
                    else:
                        config_lines.append(line)
                else:
                    config_lines.append(line if line.endswith('\n') else line + '\n')

        # Write updated config
        with open(env_file, "w") as f:
            f.writelines(config_lines)

        # Restart freedvtnc2 service
        subprocess.run(["systemctl", "restart", "freedvtnc2"], capture_output=True, timeout=10)

        return jsonify({"success": True, "mode": new_mode})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/versions")
def api_versions():
    """API endpoint to get software versions."""
    versions = {}

    # RNS version
    try:
        result = subprocess.run(
            ["su", "-", "pi", "-c", "/home/pi/.local/bin/rnstatus --version"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            versions["rns"] = result.stdout.strip().split()[-1] if result.stdout else "unknown"
        else:
            versions["rns"] = "unknown"
    except Exception:
        versions["rns"] = "unknown"

    # NomadNet version
    try:
        result = subprocess.run(
            ["su", "-", "pi", "-c", "/home/pi/.local/bin/nomadnet --version"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            versions["nomadnet"] = result.stdout.strip().split()[-1] if result.stdout else "unknown"
        else:
            versions["nomadnet"] = "unknown"
    except Exception:
        versions["nomadnet"] = "unknown"

    # freedvtnc2 version (use pipx list since --version doesn't work)
    try:
        result = subprocess.run(
            ["su", "-", "pi", "-c", "pipx list 2>/dev/null"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if 'freedvtnc2' in line and 'package' in line:
                    # Parse "package freedvtnc2 0.0.1, installed..."
                    parts = line.strip().split()
                    if len(parts) >= 3:
                        versions["freedvtnc2"] = parts[2].rstrip(',')
                        break
            else:
                versions["freedvtnc2"] = "unknown"
        else:
            versions["freedvtnc2"] = "unknown"
    except Exception:
        versions["freedvtnc2"] = "unknown"

    return jsonify(versions)


@app.route("/api/restart-services", methods=["POST"])
def api_restart_services():
    """API endpoint to restart all services."""
    try:
        # Restart radio services first (freedvtnc2 must be ready before rnsd connects)
        subprocess.run(["systemctl", "restart", "rigctld"], capture_output=True, timeout=10)
        subprocess.run(["systemctl", "restart", "freedvtnc2"], capture_output=True, timeout=10)

        # Wait for freedvtnc2 to be listening
        max_checks = int(FREEDVTNC2_STARTUP_TIMEOUT_SECS / FREEDVTNC2_POLL_INTERVAL_SECS)
        for _ in range(max_checks):
            result = subprocess.run(
                ["ss", "-tln", "sport", "=", "8001"],
                capture_output=True, text=True
            )
            if "8001" in result.stdout:
                break
            time.sleep(FREEDVTNC2_POLL_INTERVAL_SECS)

        # Now restart rnsd
        subprocess.run(["systemctl", "restart", "reticulumhf-rnsd"], capture_output=True, timeout=10)
        return jsonify({"success": True, "message": "Services restarted"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/restore-defaults", methods=["POST"])
def api_restore_defaults():
    """API endpoint to restore default configuration files from backups."""
    backup_dir = Path("/etc/reticulumhf/backups")
    restored = []
    errors = []

    try:
        # Stop services before restoring
        subprocess.run(["systemctl", "stop", "rigctld", "freedvtnc2"], capture_output=True)

        # Restore hostapd config
        hostapd_backup = backup_dir / "hostapd.conf.default"
        if hostapd_backup.exists():
            subprocess.run(["cp", str(hostapd_backup), "/etc/hostapd/hostapd.conf"], capture_output=True)
            restored.append("hostapd.conf")

        # Restore dnsmasq config
        dnsmasq_backup = backup_dir / "dnsmasq.conf.default"
        if dnsmasq_backup.exists():
            subprocess.run(["cp", str(dnsmasq_backup), "/etc/dnsmasq.d/reticulumhf.conf"], capture_output=True)
            restored.append("dnsmasq.conf")

        # Restart network services
        subprocess.run(["systemctl", "restart", "hostapd"], capture_output=True)
        subprocess.run(["systemctl", "restart", "dnsmasq"], capture_output=True)

        return jsonify({
            "success": True,
            "message": f"Restored: {', '.join(restored)}" if restored else "No backups found",
            "restored": restored
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/reset-setup", methods=["POST"])
def api_reset_setup():
    """API endpoint to reset setup and allow reconfiguration."""
    try:
        # 1. Stop and disable radio services
        subprocess.run(["systemctl", "stop", "rigctld", "freedvtnc2"], capture_output=True)
        subprocess.run(["systemctl", "disable", "rigctld", "freedvtnc2"], capture_output=True)

        # 2. Remove setup complete flag
        if SETUP_COMPLETE_FLAG.exists():
            SETUP_COMPLETE_FLAG.unlink()

        # 3. Remove config.env
        config_env = Path("/etc/reticulumhf/config.env")
        if config_env.exists():
            config_env.unlink()

        # 4. Generate a clean Reticulum config WITHOUT HF interface
        #    This prevents rnsd from trying to connect to freedvtnc2 that isn't running
        clean_config = """# ReticulumHF Configuration
# Reset state - awaiting setup

[reticulum]
  enable_transport = no
  share_instance = yes
  shared_instance_port = 37428
  instance_control_port = 37429

[interfaces]

  # Local network discovery
  [[Default Interface]]
    type = AutoInterface
    enabled = yes
"""
        reticulum_dir = Path("/home/pi/.reticulum")
        reticulum_dir.mkdir(parents=True, exist_ok=True)
        with open(reticulum_dir / "config", "w") as f:
            f.write(clean_config)
        subprocess.run(["chown", "-R", "pi:pi", str(reticulum_dir)], capture_output=True)

        # 5. Restart rnsd to load clean config (no HF interface)
        subprocess.run(["systemctl", "restart", "reticulumhf-rnsd"], capture_output=True)

        return jsonify({"success": True, "message": "Setup reset. Redirecting to setup wizard..."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# Captive portal detection endpoints
@app.route("/generate_204")
@app.route("/gen_204")
@app.route("/hotspot-detect.html")
@app.route("/ncsi.txt")
@app.route("/connecttest.txt")
@app.route("/redirect")
@app.route("/success.txt")
def captive_portal_detect():
    """Handle captive portal detection requests from various OSes."""
    return redirect(url_for("index"))


@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    """API endpoint to safely shut down the Pi."""
    subprocess.Popen("sleep 2 && sudo poweroff", shell=True)
    return jsonify({"success": True, "message": "Shutting down in 2 seconds..."})


@app.route("/api/reboot", methods=["POST"])
def api_reboot():
    """API endpoint to reboot the Pi."""
    subprocess.Popen("sleep 2 && sudo reboot", shell=True)
    return jsonify({"success": True, "message": "Rebooting in 2 seconds..."})


@app.route("/api/logs/<service>")
def api_logs(service):
    """API endpoint to get service logs."""
    # Whitelist allowed services for security
    allowed_services = [
        "reticulumhf-rnsd", "reticulumhf-portal", "reticulumhf-firstboot",
        "hostapd", "dnsmasq", "rigctld", "freedvtnc2", "i2pd"
    ]
    if service not in allowed_services:
        return jsonify({"success": False, "error": f"Unknown service: {service}"}), 400

    lines = request.args.get("lines", 50, type=int)
    lines = min(lines, 200)  # Cap at 200 lines

    try:
        result = subprocess.run(
            ["journalctl", "-u", service, "--no-pager", "-n", str(lines)],
            capture_output=True, text=True, timeout=10
        )
        return jsonify({
            "success": True,
            "service": service,
            "logs": result.stdout or "No logs available"
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/system-health")
def api_system_health():
    """API endpoint to get system health info."""
    health = {
        "cpu_temp": None,
        "cpu_percent": None,
        "memory_percent": None,
        "disk_percent": None,
        "uptime": None,
        "load_avg": None
    }

    # CPU temperature
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            health["cpu_temp"] = round(int(f.read().strip()) / 1000, 1)
    except Exception:
        pass

    # Uptime
    try:
        with open("/proc/uptime", "r") as f:
            uptime_seconds = float(f.read().split()[0])
            days = int(uptime_seconds // 86400)
            hours = int((uptime_seconds % 86400) // 3600)
            minutes = int((uptime_seconds % 3600) // 60)
            if days > 0:
                health["uptime"] = f"{days}d {hours}h {minutes}m"
            elif hours > 0:
                health["uptime"] = f"{hours}h {minutes}m"
            else:
                health["uptime"] = f"{minutes}m"
    except Exception:
        pass

    # Load average
    try:
        with open("/proc/loadavg", "r") as f:
            parts = f.read().split()
            health["load_avg"] = f"{parts[0]}, {parts[1]}, {parts[2]}"
    except Exception:
        pass

    # Disk usage
    try:
        result = subprocess.run(
            ["df", "-h", "/"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            if len(lines) >= 2:
                parts = lines[1].split()
                health["disk_percent"] = int(parts[4].rstrip("%"))
                health["disk_used"] = parts[2]
                health["disk_total"] = parts[1]
    except Exception:
        pass

    # Memory usage
    try:
        with open("/proc/meminfo", "r") as f:
            meminfo = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    meminfo[parts[0].rstrip(":")] = int(parts[1])
            total = meminfo.get("MemTotal", 0)
            available = meminfo.get("MemAvailable", 0)
            if total > 0:
                used = total - available
                health["memory_percent"] = round((used / total) * 100, 1)
                health["memory_used"] = f"{used // 1024}MB"
                health["memory_total"] = f"{total // 1024}MB"
    except Exception:
        pass

    return jsonify(health)


@app.route("/api/service/<service>/<action>", methods=["POST"])
def api_service_control(service, action):
    """API endpoint to control individual services."""
    # Whitelist allowed services
    allowed_services = [
        "reticulumhf-rnsd", "hostapd", "dnsmasq", "rigctld", "freedvtnc2", "i2pd"
    ]
    if service not in allowed_services:
        return jsonify({"success": False, "error": f"Cannot control service: {service}"}), 400

    if action not in ["start", "stop", "restart"]:
        return jsonify({"success": False, "error": f"Invalid action: {action}"}), 400

    try:
        result = subprocess.run(
            ["systemctl", action, service],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return jsonify({"success": True, "message": f"Service {service} {action}ed"})
        else:
            return jsonify({
                "success": False,
                "error": result.stderr.strip() or f"Failed to {action} {service}"
            })
    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "error": f"Timeout {action}ing {service}"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/connected-clients")
def api_connected_clients():
    """API endpoint to get connected WiFi clients.

    Uses ARP table to show only currently connected devices,
    not stale DHCP leases that may have expired.
    """
    clients = []
    try:
        # Get currently reachable devices from ARP table
        arp_result = subprocess.run(
            ["ip", "neigh", "show", "dev", "wlan0"],
            capture_output=True, text=True, timeout=5
        )

        reachable_ips = set()
        if arp_result.returncode == 0:
            for line in arp_result.stdout.strip().split('\n'):
                if not line:
                    continue
                parts = line.split()
                if len(parts) >= 4:
                    ip = parts[0]
                    state = parts[-1] if parts[-1] in ['REACHABLE', 'STALE', 'DELAY', 'PROBE'] else None
                    # Include REACHABLE, STALE, DELAY, PROBE (recently seen)
                    # Exclude FAILED, INCOMPLETE (not connected)
                    if state and state not in ['FAILED', 'INCOMPLETE']:
                        reachable_ips.add(ip)

        # Get hostname info from DHCP leases
        leases = {}
        leases_file = "/var/lib/misc/dnsmasq.leases"
        if os.path.exists(leases_file):
            with open(leases_file, "r") as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) >= 4:
                        leases[parts[2]] = {
                            "mac": parts[1],
                            "hostname": parts[3] if parts[3] != "*" else "unknown"
                        }

        # Build client list from reachable IPs
        for ip in reachable_ips:
            if ip == "192.168.4.1":
                continue  # Skip gateway itself
            lease_info = leases.get(ip, {})
            clients.append({
                "ip": ip,
                "mac": lease_info.get("mac", "unknown"),
                "hostname": lease_info.get("hostname", "unknown"),
                "status": "connected"
            })

        # Sort by IP address
        clients.sort(key=lambda x: [int(n) for n in x["ip"].split(".")])

        return jsonify({"success": True, "clients": clients, "count": len(clients)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/service-detail/<service>")
def api_service_detail(service):
    """API endpoint to get detailed service status including failure reason."""
    allowed_services = [
        "reticulumhf-rnsd", "reticulumhf-portal", "reticulumhf-firstboot",
        "hostapd", "dnsmasq", "rigctld", "freedvtnc2"
    ]
    if service not in allowed_services:
        return jsonify({"success": False, "error": f"Unknown service: {service}"}), 400

    try:
        result = subprocess.run(
            ["systemctl", "status", service, "--no-pager"],
            capture_output=True, text=True, timeout=10
        )

        # Parse status
        status = "unknown"
        if "Active: active (running)" in result.stdout:
            status = "running"
        elif "Active: failed" in result.stdout:
            status = "failed"
        elif "Active: inactive" in result.stdout:
            status = "stopped"
        elif "could not be found" in result.stderr:
            status = "not_installed"

        return jsonify({
            "success": True,
            "service": service,
            "status": status,
            "detail": result.stdout[-1000:] if result.stdout else result.stderr[-500:]
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


if __name__ == "__main__":
    # Production mode - use gunicorn in production, this is for testing only
    # WARNING: Do not use debug=True in production - security risk
    app.run(host="0.0.0.0", port=80, debug=False)
