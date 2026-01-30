"""
ReticulumHF Beacon Module

Hybrid beacon/ARQ protocol for HF mesh discovery.

Components:
- scheduler: Beacon scheduler daemon with mode switching
- rns_bridge: Reticulum network integration
- test_beacon: Two-station testing utilities
"""

from .scheduler import (
    BeaconScheduler,
    BeaconConfig,
    BeaconPacket,
    BeaconListener,
    PeerTable,
    DiscoveredPeer,
    FreeDVTNC2Client,
    FreeDVMode,
    Mode,
)

from .rns_bridge import (
    RNSBridge,
    RNSPeerStatus,
    integrate_with_scheduler,
)

__all__ = [
    'BeaconScheduler',
    'BeaconConfig',
    'BeaconPacket',
    'BeaconListener',
    'PeerTable',
    'DiscoveredPeer',
    'FreeDVTNC2Client',
    'FreeDVMode',
    'Mode',
    'RNSBridge',
    'RNSPeerStatus',
    'integrate_with_scheduler',
]
