#!/usr/bin/env python3
"""
RNS Bridge - Connects beacon discovery to Reticulum network.

When beacons are received, this module:
1. Checks if Reticulum already knows the path to that identity
2. If not, requests a path (which may trigger an announce)
3. Tracks which beacon-discovered peers are reachable via RNS

This bridges the fast beacon discovery with Reticulum's routing system.
"""

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

log = logging.getLogger('rns-bridge')

# Try to import RNS - it may not be installed in dev environment
try:
    import RNS
    RNS_AVAILABLE = True
except ImportError:
    RNS_AVAILABLE = False
    log.warning("RNS not available - bridge will run in simulation mode")


@dataclass
class RNSPeerStatus:
    """Status of a beacon-discovered peer in Reticulum."""
    identity_hash: bytes
    beacon_first_seen: float
    beacon_last_seen: float
    rns_path_known: bool = False
    rns_path_requested: float = 0
    rns_path_resolved: float = 0
    rns_hops: Optional[int] = None
    rns_interface: Optional[str] = None


class RNSBridge:
    """
    Bridges beacon discovery with Reticulum routing.

    When we discover a peer via beacon, we want Reticulum to know
    how to reach them. This class:

    1. Connects to the local Reticulum instance
    2. Checks if paths exist for beacon-discovered peers
    3. Requests paths for unknown peers
    4. Tracks the status of each peer
    """

    def __init__(self, configpath: Optional[str] = None):
        self.configpath = configpath
        self._rns: Optional['RNS.Reticulum'] = None
        self._peers: Dict[bytes, RNSPeerStatus] = {}
        self._lock = threading.Lock()
        self._connected = False

        # Settings
        self.auto_request_paths = True
        self.path_request_interval = 300  # Don't re-request within 5 min

        # Callbacks
        self.on_path_discovered: Optional[Callable[[bytes, int], None]] = None

    def connect(self) -> bool:
        """Connect to the local Reticulum instance."""
        if not RNS_AVAILABLE:
            log.warning("RNS not available - running in simulation mode")
            self._connected = False
            return False

        try:
            # Connect to existing shared instance
            # RNS.Reticulum() with no args connects to shared instance
            self._rns = RNS.Reticulum(self.configpath)
            self._connected = True

            # Register announce handler to catch announces for our beacon peers
            self._setup_announce_handler()

            log.info("Connected to Reticulum instance")
            return True

        except Exception as e:
            log.error(f"Failed to connect to Reticulum: {e}")
            self._connected = False
            return False

    def _setup_announce_handler(self):
        """Register handler to catch announces for beacon-discovered peers."""
        if not self._rns:
            return

        class BeaconAnnounceHandler:
            def __init__(self, bridge: 'RNSBridge'):
                self.bridge = bridge
                self.aspect_filter = None  # Receive all announces
                self.receive_path_responses = True

            def received_announce(self, destination_hash, announced_identity,
                                  app_data, announce_packet_hash=None,
                                  is_path_response=False):
                self.bridge._handle_announce(destination_hash, is_path_response)

        handler = BeaconAnnounceHandler(self)
        RNS.Transport.register_announce_handler(handler)
        log.debug("Registered announce handler")

    def _handle_announce(self, destination_hash: bytes, is_path_response: bool):
        """Handle incoming announce - check if it's a beacon-discovered peer."""
        # Truncate to 16 bytes to match beacon format
        short_hash = destination_hash[:16]

        with self._lock:
            if short_hash in self._peers:
                peer = self._peers[short_hash]
                peer.rns_path_known = True
                peer.rns_path_resolved = time.time()

                log.info(f"Path resolved for beacon peer: {short_hash.hex()[:16]}...")

                if self.on_path_discovered:
                    try:
                        self.on_path_discovered(short_hash, 0)  # hops unknown
                    except Exception as e:
                        log.error(f"Path discovered callback error: {e}")

    def beacon_received(self, identity_hash: bytes, first_seen: float,
                        last_seen: float) -> RNSPeerStatus:
        """
        Called when a beacon is received.

        Updates peer status and optionally requests path from Reticulum.
        """
        # Use truncated hash (16 bytes) as key
        short_hash = identity_hash[:16]

        with self._lock:
            if short_hash in self._peers:
                peer = self._peers[short_hash]
                peer.beacon_last_seen = last_seen
            else:
                peer = RNSPeerStatus(
                    identity_hash=short_hash,
                    beacon_first_seen=first_seen,
                    beacon_last_seen=last_seen
                )
                self._peers[short_hash] = peer
                log.info(f"New beacon peer registered: {short_hash.hex()[:16]}...")

        # Check/request path
        if self._connected and self.auto_request_paths:
            self._check_or_request_path(peer)

        return peer

    def _check_or_request_path(self, peer: RNSPeerStatus):
        """Check if path known, request if not."""
        if not RNS_AVAILABLE or not self._rns:
            return

        # Check if path is already known
        # Note: Reticulum uses full destination hashes, but we only have 16 bytes
        # from the beacon. This may need adjustment.
        try:
            # Try to find any destination starting with our truncated hash
            # This is a limitation - we may need the full hash
            if RNS.Transport.has_path(peer.identity_hash):
                peer.rns_path_known = True
                log.debug(f"Path already known for {peer.identity_hash.hex()[:16]}...")
                return
        except Exception:
            pass

        # Check if we recently requested
        now = time.time()
        if now - peer.rns_path_requested < self.path_request_interval:
            return

        # Request path
        try:
            log.info(f"Requesting path for beacon peer: {peer.identity_hash.hex()[:16]}...")
            RNS.Transport.request_path(peer.identity_hash)
            peer.rns_path_requested = now
        except Exception as e:
            log.error(f"Path request failed: {e}")

    def get_peer_status(self, identity_hash: bytes) -> Optional[RNSPeerStatus]:
        """Get status of a beacon-discovered peer."""
        short_hash = identity_hash[:16]
        with self._lock:
            return self._peers.get(short_hash)

    def get_all_peers(self) -> List[RNSPeerStatus]:
        """Get all beacon-discovered peers."""
        with self._lock:
            return list(self._peers.values())

    def get_routable_peers(self) -> List[RNSPeerStatus]:
        """Get peers that have known Reticulum paths."""
        with self._lock:
            return [p for p in self._peers.values() if p.rns_path_known]

    def get_stats(self) -> Dict:
        """Get bridge statistics."""
        with self._lock:
            total = len(self._peers)
            routable = sum(1 for p in self._peers.values() if p.rns_path_known)
            pending = sum(1 for p in self._peers.values()
                         if p.rns_path_requested > 0 and not p.rns_path_known)

        return {
            'connected': self._connected,
            'rns_available': RNS_AVAILABLE,
            'total_beacon_peers': total,
            'routable_peers': routable,
            'pending_path_requests': pending,
        }


def integrate_with_scheduler(scheduler, bridge: RNSBridge):
    """
    Wire up the beacon scheduler to the RNS bridge.

    When beacons are received, automatically register with Reticulum.
    """
    original_callback = scheduler.on_beacon_rx

    def combined_callback(packet, peer):
        # Call original callback if set
        if original_callback:
            original_callback(packet, peer)

        # Register with RNS bridge
        bridge.beacon_received(
            identity_hash=packet.identity_hash,
            first_seen=peer.first_seen,
            last_seen=peer.last_seen
        )

    scheduler.on_beacon_rx = combined_callback
    log.info("Beacon scheduler integrated with RNS bridge")


# Example usage and testing
if __name__ == '__main__':
    import json

    logging.basicConfig(level=logging.DEBUG)

    print("RNS Bridge Test")
    print(f"RNS Available: {RNS_AVAILABLE}")

    bridge = RNSBridge()

    if bridge.connect():
        print("Connected to Reticulum")
    else:
        print("Running in simulation mode")

    # Simulate beacon reception
    test_hash = bytes.fromhex('0123456789abcdef0123456789abcdef')
    now = time.time()

    status = bridge.beacon_received(test_hash, now, now)
    print(f"\nPeer status: {status}")

    stats = bridge.get_stats()
    print(f"\nBridge stats: {json.dumps(stats, indent=2)}")
