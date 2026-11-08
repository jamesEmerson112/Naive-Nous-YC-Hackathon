"""Raw serial port access with the standard library (same approach as toolkit/bin/fwtool)."""
import glob
import os
import termios
import time
import tty

PROBE_PATTERNS = ("SEGGER", "J-Link", "STMicro", "STLINK", "ST-LINK", "DAPLink", "CMSIS-DAP", "Debug_Probe")


def find_ports():
    found = []
    for link in sorted(glob.glob("/dev/serial/by-id/*")):
        if any(p.lower() in link.lower() for p in PROBE_PATTERNS):
            found.append(os.path.realpath(link))
    found += sorted(glob.glob("/dev/ttyACM*")) + sorted(glob.glob("/dev/cu.usbmodem*"))
    out = []
    for p in found:
        if p not in out:
            out.append(p)
    return out


def resolve_port(port):
    if port and port != "auto":
        return port
    ports = find_ports()
    return ports[0] if ports else None


def _apply(fd, baud):
    speed = getattr(termios, "B%d" % baud, None)
    if speed is None:
        raise ValueError("unsupported baud rate: %d" % baud)
    attrs = termios.tcgetattr(fd)
    attrs[4] = attrs[5] = speed
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def set_baud(fd, baud, kick=True):
    """Set the baud rate. With kick (default), switch to a different rate first.

    The probe's USB-serial bridge (J-Link OB VCOM) reprograms its own UART when the host sets a baud,
    but Linux's cdc-acm only sends the request when the setting changes. After a sweep of rates the
    bridge was seen stuck receiving at the wrong rate while the host already "was" at 115200: most
    digits were lost to framing errors. Going to another rate and back forces a real reconfiguration.
    (Measured on this board, Nov 7.)"""
    if kick:
        _apply(fd, 9600 if baud != 9600 else 19200)
        time.sleep(0.15)
    _apply(fd, baud)
    time.sleep(0.15 if kick else 0)
    termios.tcflush(fd, termios.TCIFLUSH)


def open_serial(port, baud):
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    tty.setraw(fd)
    attrs = termios.tcgetattr(fd)
    attrs[2] |= termios.CLOCAL | termios.CREAD
    if hasattr(termios, "CRTSCTS"):
        attrs[2] &= ~termios.CRTSCTS
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    set_baud(fd, baud)
    termios.tcflush(fd, termios.TCIOFLUSH)
    return fd
