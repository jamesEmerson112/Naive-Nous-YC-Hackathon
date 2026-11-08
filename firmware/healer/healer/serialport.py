"""Raw serial port access with the standard library (same approach as toolkit/bin/fwtool)."""
import glob
import os
import termios
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


def set_baud(fd, baud):
    speed = getattr(termios, "B%d" % baud, None)
    if speed is None:
        raise ValueError("unsupported baud rate: %d" % baud)
    attrs = termios.tcgetattr(fd)
    attrs[4] = attrs[5] = speed
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
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
