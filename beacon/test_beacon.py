#!/usr/bin/env python3
"""
Two-Station Beacon Test Script

Run this on two ReticulumHF stations to verify beacon TX/RX.

Station A (transmitter):
    python3 test_beacon.py --tx --id "0123456789abcdef" --message "W1ABC FN42"

Station B (receiver):
    python3 test_beacon.py --rx

Both stations (full test):
    python3 test_beacon.py --both --id "0123456789abcdef" --message "W1ABC FN42"
"""

import argparse
import json
import socket
import sys
import time
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from beacon.scheduler import (
    BeaconPacket, BeaconConfig, BeaconScheduler,
    FreeDVTNC2Client, FreeDVMode, PeerTable, BeaconListener
)


def test_tnc_connection(host: str = "127.0.0.1", port: int = 8002) -> bool:
    """Test connection to freedvtnc2 command interface."""
    print(f"Testing connection to freedvtnc2 at {host}:{port}...")
    client = FreeDVTNC2Client(host=host, cmd_port=port)

    if not client.ping():
        print("  FAILED: freedvtnc2 not responding")
        return False

    status = client.get_status()
    if status:
        print(f"  OK: Mode={status.get('mode')}, PTT={status.get('ptt')}")
    else:
        print("  WARNING: Could not get status")

    return True


def test_beacon_tx(identity: str, message: str, mode: str = "DATAC4") -> bool:
    """Transmit a single beacon packet."""
    client = FreeDVTNC2Client()

    if not client.ping():
        print("ERROR: freedvtnc2 not responding")
        return False

    # Switch to beacon mode
    print(f"Switching to {mode}...")
    if not client.set_mode(FreeDVMode(mode)):
        print("ERROR: Failed to switch mode")
        return False

    time.sleep(0.5)

    # Build beacon packet
    try:
        identity_bytes = bytes.fromhex(identity.ljust(32, '0'))
    except ValueError:
        print(f"ERROR: Invalid identity hex: {identity}")
        return False

    packet = BeaconPacket(
        identity_hash=identity_bytes,
        flags=BeaconPacket.FLAG_ACCEPTS_LINKS,
        message=message
    )

    data = packet.encode()
    print(f"Beacon packet: {len(data)} bytes")
    print(f"  Identity: {identity[:16]}...")
    print(f"  Message: {message}")
    print(f"  Hex: {data.hex()}")

    # Check channel
    if not client.is_channel_clear():
        print("WARNING: Channel busy, transmitting anyway...")

    # Transmit
    print("Transmitting...")
    if client.send_kiss_frame(data):
        print("  OK: Beacon transmitted")
        return True
    else:
        print("  FAILED: Could not send KISS frame")
        return False


def test_beacon_rx(duration: int = 60) -> int:
    """Listen for beacon packets for specified duration."""
    print(f"Listening for beacons for {duration} seconds...")
    print("(Press Ctrl+C to stop early)\n")

    peer_table = PeerTable()
    client = FreeDVTNC2Client()

    listener = BeaconListener(
        peer_table=peer_table,
        tnc_client=client
    )

    received_count = 0

    def on_beacon(packet, peer):
        nonlocal received_count
        received_count += 1
        print(f"\n*** BEACON RECEIVED ***")
        print(f"  Identity: {packet.identity_hash.hex()}")
        print(f"  Message: '{packet.message}'")
        print(f"  Flags: {packet.flags:#x}")
        print(f"  RX Count: {peer.rx_count}")
        if peer.last_rx_level:
            print(f"  RX Level: {peer.last_rx_level:.1f} dB")
        print()

    listener.on_beacon_received = on_beacon

    try:
        listener.start()
        start_time = time.time()

        while time.time() - start_time < duration:
            elapsed = int(time.time() - start_time)
            remaining = duration - elapsed
            print(f"\rListening... {remaining}s remaining, {received_count} beacon(s) received", end="")
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\nStopped by user")
    finally:
        listener.stop()

    print(f"\n\nSession complete: {received_count} beacon(s) received")

    if peer_table.get_all():
        print("\nDiscovered peers:")
        for peer in peer_table.get_all():
            print(f"  {peer.identity_hash.hex()[:16]}... '{peer.message}' (count={peer.rx_count})")

    return received_count


def test_full(identity: str, message: str, beacon_interval: int = 30) -> None:
    """Run full beacon scheduler with TX and RX."""
    config = BeaconConfig()
    config.station_id = identity.ljust(32, '0')
    config.beacon_message = message
    config.beacon_minutes = [0, 30]  # Every half hour
    config.tx_beacon = True
    config.beacon_mode = FreeDVMode.DATAC4
    config.arq_mode = FreeDVMode.DATAC1

    scheduler = BeaconScheduler(config)

    def on_tx(packet):
        print(f"\n*** BEACON TX ***")
        print(f"  {packet}")

    def on_rx(packet, peer):
        print(f"\n*** BEACON RX ***")
        print(f"  From: {packet.identity_hash.hex()[:16]}...")
        print(f"  Message: '{packet.message}'")
        print(f"  Count: {peer.rx_count}")

    scheduler.on_beacon_tx = on_tx
    scheduler.on_beacon_rx = on_rx

    print(f"Starting beacon scheduler...")
    print(f"  Identity: {identity[:16]}...")
    print(f"  Message: {message}")
    print(f"  Beacon mode: {config.beacon_mode.value}")
    print(f"  ARQ mode: {config.arq_mode.value}")
    print(f"  Beacon windows: :{':'.join(f'{m:02d}' for m in config.beacon_minutes)}")
    print("\nPress Ctrl+C to stop\n")

    if not scheduler.start():
        print("ERROR: Failed to start scheduler")
        return

    try:
        while scheduler.running:
            status = scheduler.get_status()
            print(f"\rMode: {status['current_mode']} | "
                  f"FreeDV: {status['freedv_mode']} | "
                  f"Peers: {status['peer_count']} | "
                  f"Next beacon: {status['next_beacon']}", end="")
            time.sleep(5)
    except KeyboardInterrupt:
        print("\n\nStopping...")
    finally:
        scheduler.stop()

    peers = scheduler.get_peers()
    if peers:
        print(f"\nFinal peer list ({len(peers)}):")
        print(json.dumps(peers, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="Two-Station Beacon Test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument('--tx', action='store_true',
                        help='Transmit a single beacon')
    parser.add_argument('--rx', action='store_true',
                        help='Listen for beacons')
    parser.add_argument('--both', action='store_true',
                        help='Run full scheduler (TX and RX)')
    parser.add_argument('--test-connection', action='store_true',
                        help='Test freedvtnc2 connection only')

    parser.add_argument('--id', type=str, default="",
                        help='Station identity (hex string)')
    parser.add_argument('--message', '-m', type=str, default="",
                        help='Beacon message (callsign/grid)')
    parser.add_argument('--mode', type=str, default="DATAC4",
                        choices=["DATAC4"],
                        help='FreeDV mode for beacon TX (DATAC4 only - others have insufficient frame size)')
    parser.add_argument('--duration', '-d', type=int, default=60,
                        help='RX listen duration in seconds')

    args = parser.parse_args()

    if args.test_connection:
        sys.exit(0 if test_tnc_connection() else 1)

    if args.tx:
        if not args.id:
            print("ERROR: --id required for TX")
            sys.exit(1)
        sys.exit(0 if test_beacon_tx(args.id, args.message, args.mode) else 1)

    if args.rx:
        count = test_beacon_rx(args.duration)
        sys.exit(0 if count > 0 else 1)

    if args.both:
        if not args.id:
            print("ERROR: --id required")
            sys.exit(1)
        test_full(args.id, args.message)
        sys.exit(0)

    # Default: show help
    parser.print_help()
    sys.exit(1)


if __name__ == '__main__':
    main()
