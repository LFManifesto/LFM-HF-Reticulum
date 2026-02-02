#!/bin/bash
# ReticulumHF: Set ALSA audio levels for digital modes
#
# Called by udev when CM108/Digirig USB audio device is plugged in.
# Also can be run manually: ./set-digirig-audio.sh <card_number>
#
# Sets optimal defaults for FreeDV digital modes:
# - Speaker (TX output): 80%
# - Mic Capture (RX input): 75%
# - Mic Playback (monitoring): Muted
# - Auto Gain Control: Off

CARD="${1:-}"
LOG_FILE="/var/log/reticulumhf-audio.log"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> "$LOG_FILE"
}

# If no card number provided, try to find CM108 device
if [ -z "$CARD" ]; then
    CARD=$(arecord -l 2>/dev/null | grep -i "USB PnP Sound Device\|C-Media\|CM108" | head -1 | sed -n 's/^card \([0-9]*\):.*/\1/p')
fi

if [ -z "$CARD" ]; then
    log "ERROR: No USB audio card found"
    exit 1
fi

log "Setting audio levels for card $CARD"

# Wait for device to fully initialize
sleep 2

# Set TX output (Speaker) to 80%
if amixer -c "$CARD" sset 'Speaker' 80% unmute 2>/dev/null; then
    log "Set Speaker to 80%"
else
    log "Speaker control not found"
fi

# Set RX input (Mic Capture) to 75%
# Try specific "Mic Capture" first, then generic "Mic"
if amixer -c "$CARD" sset 'Mic Capture' 75% 2>/dev/null; then
    log "Set Mic Capture to 75%"
elif amixer -c "$CARD" cset name='Mic Capture Volume' 75% 2>/dev/null; then
    log "Set Mic Capture Volume to 75%"
elif amixer -c "$CARD" sset 'Mic' 75% 2>/dev/null; then
    log "Set Mic to 75%"
else
    log "Mic Capture control not found"
fi

# Mute monitoring (Mic Playback) - prevents feedback/sidetone
if amixer -c "$CARD" sset 'Mic Playback' 0% mute 2>/dev/null; then
    log "Muted Mic Playback (sidetone)"
elif amixer -c "$CARD" cset name='Mic Playback Switch' off 2>/dev/null; then
    log "Disabled Mic Playback Switch"
fi

# Disable Auto Gain Control - critical for digital modes
if amixer -c "$CARD" sset 'Auto Gain Control' off 2>/dev/null; then
    log "Disabled AGC"
elif amixer -c "$CARD" sset 'AGC' off 2>/dev/null; then
    log "Disabled AGC"
fi

# Save settings persistently
alsactl store 2>/dev/null
log "Audio levels saved"

exit 0
