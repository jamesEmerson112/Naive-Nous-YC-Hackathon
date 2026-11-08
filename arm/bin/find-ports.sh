#!/usr/bin/env bash
# Lists USB serial ports, then runs lerobot-find-port (unplug one arm when asked).
source "$(dirname "$0")/_common.sh"
echo "USB serial ports now:"; ls /dev/tty.usbmodem* 2>/dev/null || echo "  none (plug in the arms' USB and power)"
echo
lerobot-find-port
