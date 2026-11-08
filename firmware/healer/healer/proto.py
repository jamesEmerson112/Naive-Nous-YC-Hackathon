"""Parse the pod's telemetry lines ("<TYPE> key=value ...", see pod/src/tl.h)."""
import re

TYPES = "DAFHER"
_TOKEN = re.compile(r'(\w+)=("([^"]*)"|\S*)')
_LINE = re.compile(r'(?:\s*\w+=(?:"[^"]*"|\S*))*\s*')
_INT = re.compile(r"-?\d+")
_FLOAT = re.compile(r"-?\d+\.\d+")


def _value(raw, quoted):
    if quoted is not None:
        return quoted
    if _INT.fullmatch(raw):
        return int(raw)
    if _FLOAT.fullmatch(raw):
        return float(raw)
    return raw                       # hex stays a string ("0x08002B9A"), words stay words


def parse_line(line):
    """Return a dict for a valid line ({"type": "A", "t": 123, ...}), or None if it isn't one."""
    if not line:
        return None
    if line[0] == "#":
        return {"type": "#", "text": line[1:].strip()}
    if len(line) < 3 or line[0] not in TYPES or line[1] != " ":
        return None
    rest = line[2:]
    if not _LINE.fullmatch(rest):
        return None
    rec = {"type": line[0]}
    for m in _TOKEN.finditer(rest):
        rec[m.group(1)] = _value(m.group(2), m.group(3))
    return rec if len(rec) > 1 else None


def hexint(v):
    """'0x08002B9A' -> int; ints pass through."""
    if isinstance(v, int):
        return v
    return int(str(v), 16)


def decode_frame(hexstr):
    """Decode an F line's radio frame (layout in pod/src/frame.h)."""
    b = bytes.fromhex(hexstr)
    if len(b) != 16:
        return {"error": "length %d, expected 16" % len(b)}
    crc = 0
    for x in b[:15]:
        crc ^= x
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    s16 = lambda o: int.from_bytes(b[o:o + 2], "little", signed=True)
    return {"type": "0x%02X" % b[0], "t_ms": int.from_bytes(b[1:5], "little"),
            "mean_x": s16(5), "mean_y": s16(7), "mean_z": s16(9),
            "rms_mg": int.from_bytes(b[11:13], "little"), "n": int.from_bytes(b[13:15], "little"),
            "crc_ok": crc == b[15]}
