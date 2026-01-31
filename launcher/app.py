#!/usr/bin/env python3
"""
ReticulumHF Launcher Portal

Simple mode selector:
- ReticulumHF: HF radio gateway (freedvtnc2 + rnsd)
- ReticulumLFN: Internet gateway (i2pd + rnsd)

Only one mode runs at a time.
"""

import json
import os
import subprocess
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, url_for

app = Flask(__name__)

# Configuration
CONFIG_DIR = Path("/etc/reticulumhf")
CONFIG_FILE = CONFIG_DIR / "launcher.json"
HF_CONFIG_FILE = CONFIG_DIR / "hf.json"

# Default config
DEFAULT_CONFIG = {
    "active_mode": None,  # None, "hf", or "lfn"
    "hf_configured": False,
    "lfn_configured": False,
}


def load_config():
    """Load launcher configuration."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(config):
    """Save launcher configuration."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def get_active_mode():
    """Check which mode is currently active via systemd."""
    try:
        # Check if HF target is active
        result = subprocess.run(
            ["systemctl", "is-active", "reticulumhf-hf.target"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return "hf"

        # Check if LFN target is active
        result = subprocess.run(
            ["systemctl", "is-active", "reticulumhf-lfn.target"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return "lfn"
    except Exception:
        pass

    return None


def switch_mode(new_mode):
    """Switch to a different mode."""
    current = get_active_mode()

    # Stop current mode if active
    if current == "hf":
        subprocess.run(["systemctl", "stop", "reticulumhf-hf.target"], timeout=30)
    elif current == "lfn":
        subprocess.run(["systemctl", "stop", "reticulumhf-lfn.target"], timeout=30)

    # Start new mode
    if new_mode == "hf":
        subprocess.run(["systemctl", "start", "reticulumhf-hf.target"], timeout=30)
    elif new_mode == "lfn":
        subprocess.run(["systemctl", "start", "reticulumhf-lfn.target"], timeout=30)

    # Update config
    config = load_config()
    config["active_mode"] = new_mode
    save_config(config)


def get_system_info():
    """Get basic system info."""
    info = {
        "hostname": "reticulumhf",
        "ip_address": "192.168.4.1",
    }

    try:
        result = subprocess.run(["hostname"], capture_output=True, text=True, timeout=5)
        info["hostname"] = result.stdout.strip()
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["hostname", "-I"],
            capture_output=True, text=True, timeout=5
        )
        ips = result.stdout.strip().split()
        if ips:
            info["ip_address"] = ips[0]
    except Exception:
        pass

    return info


# ============================================================================
# Routes
# ============================================================================

@app.route("/")
def index():
    """Main launcher page - mode selection."""
    config = load_config()
    active_mode = get_active_mode()
    system_info = get_system_info()

    # If a mode is active, redirect to its status page
    if active_mode == "hf":
        return redirect(url_for("hf_status"))
    elif active_mode == "lfn":
        return redirect(url_for("lfn_status"))

    return render_template(
        "launcher.html",
        config=config,
        active_mode=active_mode,
        system_info=system_info
    )


@app.route("/select/<mode>", methods=["POST"])
def select_mode(mode):
    """Select and start a mode."""
    if mode not in ("hf", "lfn"):
        return jsonify({"success": False, "error": "Invalid mode"}), 400

    config = load_config()

    # Check if HF mode needs setup first
    if mode == "hf" and not config.get("hf_configured"):
        return redirect(url_for("hf_setup"))

    # Switch to the selected mode
    try:
        switch_mode(mode)
        if mode == "hf":
            return redirect(url_for("hf_status"))
        else:
            return redirect(url_for("lfn_status"))
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/stop", methods=["POST"])
def stop_mode():
    """Stop current mode and return to launcher."""
    current = get_active_mode()
    if current:
        switch_mode(None)
    return redirect(url_for("index"))


# ============================================================================
# HF Mode Routes
# ============================================================================

@app.route("/hf/setup")
def hf_setup():
    """HF setup wizard - hardware detection and config."""
    system_info = get_system_info()
    return render_template("hf_setup.html", system_info=system_info)


@app.route("/hf/status")
def hf_status():
    """HF mode status page."""
    system_info = get_system_info()
    return render_template("hf_status.html", system_info=system_info)


@app.route("/api/hf/detect", methods=["POST"])
def api_hf_detect():
    """Detect HF hardware (serial ports, audio devices)."""
    hardware = {
        "serial_ports": [],
        "audio_devices": [],
    }

    # Detect serial ports
    try:
        import glob
        for pattern in ["/dev/ttyUSB*", "/dev/ttyACM*"]:
            hardware["serial_ports"].extend(glob.glob(pattern))
    except Exception:
        pass

    # Detect audio devices
    try:
        result = subprocess.run(
            ["aplay", "-l"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.split("\n"):
            if line.startswith("card "):
                # Parse: "card 1: Device [USB Audio Device], device 0: ..."
                parts = line.split(":")
                if len(parts) >= 2:
                    card_num = parts[0].replace("card ", "").strip()
                    card_name = parts[1].split("[")[0].strip() if "[" in parts[1] else parts[1].split(",")[0].strip()
                    hardware["audio_devices"].append({
                        "card": card_num,
                        "name": card_name,
                        "id": f"hw:{card_num}"
                    })
    except Exception:
        pass

    return jsonify(hardware)


@app.route("/api/hf/test-cat", methods=["POST"])
def api_hf_test_cat():
    """Test CAT connection to radio."""
    data = request.get_json() or {}
    serial_port = data.get("serial_port")
    baud_rate = data.get("baud_rate", 9600)
    hamlib_model = data.get("hamlib_model", 1)  # 1 = dummy

    if not serial_port:
        return jsonify({"success": False, "error": "No serial port specified"})

    try:
        # Try to query radio frequency via rigctl
        result = subprocess.run(
            ["rigctl", "-m", str(hamlib_model), "-r", serial_port, "-s", str(baud_rate), "f"],
            capture_output=True, text=True, timeout=10
        )

        if result.returncode == 0:
            freq = result.stdout.strip()
            return jsonify({
                "success": True,
                "frequency": freq,
                "message": f"Connected! Frequency: {freq} Hz"
            })
        else:
            return jsonify({
                "success": False,
                "error": result.stderr.strip() or "CAT connection failed"
            })
    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "error": "Timeout connecting to radio"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/hf/save-config", methods=["POST"])
def api_hf_save_config():
    """Save HF configuration and mark as configured."""
    data = request.get_json() or {}

    hf_config = {
        "serial_port": data.get("serial_port"),
        "baud_rate": data.get("baud_rate", 9600),
        "hamlib_model": data.get("hamlib_model", 1),
        "audio_device": data.get("audio_device"),
        "ptt_type": data.get("ptt_type", "RTS"),  # RTS, DTR, CAT, VOX
    }

    # Save HF-specific config
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(HF_CONFIG_FILE, "w") as f:
        json.dump(hf_config, f, indent=2)

    # Mark HF as configured
    config = load_config()
    config["hf_configured"] = True
    save_config(config)

    return jsonify({"success": True})


@app.route("/api/hf/status")
def api_hf_status():
    """Get HF mode status."""
    status = {
        "active": get_active_mode() == "hf",
        "rigctld": {"running": False},
        "freedvtnc2": {"running": False},
        "rnsd": {"running": False},
    }

    for service in ["rigctld", "freedvtnc2", "reticulumhf-hf-rnsd"]:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", service],
                capture_output=True, text=True, timeout=5
            )
            key = service.replace("reticulumhf-hf-", "")
            if key in status:
                status[key]["running"] = result.returncode == 0
        except Exception:
            pass

    return jsonify(status)


# ============================================================================
# LFN Mode Routes
# ============================================================================

@app.route("/lfn/status")
def lfn_status():
    """LFN mode status page."""
    system_info = get_system_info()
    return render_template("lfn_status.html", system_info=system_info)


@app.route("/api/lfn/status")
def api_lfn_status():
    """Get LFN mode status."""
    status = {
        "active": get_active_mode() == "lfn",
        "i2pd": {"running": False, "tunnels": 0, "routers": 0},
        "rnsd": {"running": False},
    }

    # Check i2pd
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "i2pd"],
            capture_output=True, text=True, timeout=5
        )
        status["i2pd"]["running"] = result.returncode == 0
    except Exception:
        pass

    # Get I2P router count (count files in netDb)
    try:
        netdb_path = Path("/var/lib/i2pd/netDb")
        if netdb_path.exists():
            router_count = sum(1 for f in netdb_path.rglob("routerInfo-*.dat"))
            status["i2pd"]["routers"] = router_count
    except Exception:
        pass

    # Check rnsd
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "reticulumhf-lfn-rnsd"],
            capture_output=True, text=True, timeout=5
        )
        status["rnsd"]["running"] = result.returncode == 0
    except Exception:
        pass

    return jsonify(status)


# ============================================================================
# Common API
# ============================================================================

@app.route("/api/rnstatus")
def api_rnstatus():
    """Get rnstatus output."""
    try:
        result = subprocess.run(
            ["/home/pi/.local/bin/rnstatus"],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "PATH": f"/home/pi/.local/bin:{os.environ.get('PATH', '')}"}
        )
        return jsonify({
            "success": True,
            "output": result.stdout + result.stderr
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80, debug=False)
