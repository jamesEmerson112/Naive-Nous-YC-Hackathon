"""The bench camera over HTTP, for Mission Control's "Live camera" panel. Runs on the Mac that has the camera.

    PYTHONPATH=. .venv/bin/python -m armsim.camserve [--host 127.0.0.1] [--port 8790] [--camera /tmp/armcam/latest.jpg]

  GET /latest.jpg   the newest camera frame, no-cache (404 when there is none)
  GET /eye.json     armsim.eye.locate() on that frame, plus "frame_age_s" (how old the file is) and
                    "server_ts"; {"error": "...", "frame_age_s": ...} when the eye isn't calibrated or
                    there is no frame. Always 200 with JSON, so the page can say why.
Both send Access-Control-Allow-Origin: * (Mission Control runs on the Pi, the page is viewed on this Mac
through an SSH tunnel and reaches this server at http://localhost:8790 from the browser). Read-only:
it never touches the arm. Binds loopback by default; stdlib HTTP, cv2 only through armsim.eye.
"""
import argparse
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CAMERA = os.environ.get("ARMSIM_CAMERA", "/tmp/armcam/latest.jpg")
CACHE_S = 0.5                     # eye.locate is a blob search: at most twice a second however many pages ask


class Eye:
    def __init__(self, camera):
        self.camera = camera
        self._at, self._val = 0.0, None

    def age(self):
        try:
            return round(time.time() - os.path.getmtime(self.camera), 2)
        except OSError:
            return None

    def get(self):
        now = time.time()
        if self._val is not None and now - self._at < CACHE_S:
            return self._val
        age = self.age()
        out = {"frame_age_s": age, "server_ts": now}
        if age is None:
            out["error"] = f"no camera frame at {self.camera}"
        else:
            try:
                from armsim import eye
                out.update(eye.locate(eye.frame(self.camera)))
                out["calibrated"] = True
            except SystemExit as exc:                 # eye.py reports "not calibrated yet" / "no frame" this way
                out["error"] = str(exc.code) if not isinstance(exc.code, int) else "eye failed"
                out["calibrated"] = "not calibrated" not in out["error"]
            except Exception as exc:
                out["error"] = f"{type(exc).__name__}: {exc}"
        self._at, self._val = now, out
        return out


def make_handler(eye):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            try:
                if path == "/latest.jpg":
                    with open(eye.camera, "rb") as f:
                        data = f.read()
                    if not data:
                        raise OSError("empty frame")
                    return self._send(200, data, "image/jpeg")
                if path == "/eye.json":
                    return self._send(200, json.dumps(eye.get()).encode(), "application/json")
                return self._send(404, b'{"error": "try /latest.jpg or /eye.json"}', "application/json")
            except OSError:
                return self._send(404, b'{"error": "no camera frame"}', "application/json")

        do_HEAD = do_GET

    return Handler


def main():
    ap = argparse.ArgumentParser(prog="python -m armsim.camserve", description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--camera", default=CAMERA)
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(Eye(a.camera)))
    print(f"camera server: http://{a.host}:{a.port}/latest.jpg and /eye.json (frame: {a.camera})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
