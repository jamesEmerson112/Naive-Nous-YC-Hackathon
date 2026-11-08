"""The arm's eye in table coordinates: the bench camera's frame -> centimetres on the table.

Table frame (a person can measure it with a ruler): the origin is the centre of the arm's base, x points
forward (where the arm faces at pan 0), y points to the arm's left, both in cm, on the table's surface.

    python -m armsim.eye detect --color green            # numbered note blobs -> /tmp/armcam/eye/detect.jpg
    python -m armsim.eye calibrate --pairs "1:30,10 2:30,-10 3:15,-15 4:15,15" [--box 20,-25]
                                                          # pixel <-> cm pairs -> armsim/eye_calibration.json
                                                          # and a check grid -> /tmp/armcam/eye/grid.jpg
    python -m armsim.eye locate [--json]                  # where the bag of chips is, in cm (and the box)

One plane, so a homography is enough: four or more points on the table, none three in a line. A fifth
point gives a check: its reprojection error is reported. The camera must stay fixed relative to the arm
(it's mounted on the same table); move it and calibrate again. Positions are of the object's footprint on
the table, from the lowest part of its blob, so a tall object reads from where it touches the table.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np

CAMERA = os.environ.get("ARMSIM_CAMERA", "/tmp/armcam/latest.jpg")
OUT = Path(os.environ.get("ARMSIM_EYE_OUT", "/tmp/armcam/eye"))
CALIBRATION = Path(__file__).resolve().parent / "eye_calibration.json"

# HSV ranges (OpenCV: hue 0-179). Green is the sticky-note colour: nothing else on the bench is green.
COLORS = {
    "green": [((35, 70, 60), (85, 255, 255))],
    "yellow": [((20, 90, 120), (34, 255, 255))],
    "orange": [((8, 120, 120), (19, 255, 255))],
    "pink": [((150, 60, 120), (172, 255, 255))],
    "blue": [((95, 110, 50), (125, 255, 255))],          # the bag of chips on the bench (blue Doritos)
}


def frame(path=CAMERA):
    im = cv2.imread(str(path))
    if im is None:
        raise SystemExit(f"no camera frame at {path}: is the capture running (VS Code terminal)?")
    age = time.time() - os.path.getmtime(path)
    if age > 10:
        print(f"warning: the camera frame is {age:.0f} s old", file=sys.stderr)
    return im


def blobs(im, color, *, min_frac=0.0004, max_frac=0.25, join_px=5):
    """Connected blobs of one colour, biggest first: centroid, footprint (lowest-middle point), area.
    join_px closes gaps that size, so an object broken up by its print (the glossy bag) is one blob."""
    hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
    mask = np.zeros(hsv.shape[:2], np.uint8)
    for lo, hi in COLORS[color]:
        mask |= cv2.inRange(hsv, np.array(lo), np.array(hi))
    k = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(cv2.morphologyEx(mask, cv2.MORPH_OPEN, k), cv2.MORPH_CLOSE,
                            np.ones((join_px, join_px), np.uint8))
    n, labels, stats, cents = cv2.connectedComponentsWithStats(mask)
    total = im.shape[0] * im.shape[1]
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if not (min_frac * total <= area <= max_frac * total):
            continue
        ys, xs = np.nonzero(labels == i)
        low = ys >= np.percentile(ys, 90)                  # the bottom tenth: where it meets the table
        out.append({"centroid": [round(float(cents[i][0]), 1), round(float(cents[i][1]), 1)],
                    "footprint": [round(float(xs[low].mean()), 1), round(float(ys[low].mean()), 1)],
                    "area": int(area), "bbox": [int(x), int(y), int(w), int(h)]})
    return sorted(out, key=lambda b: -b["area"])


def to_table(H, px):
    p = cv2.perspectiveTransform(np.array([[px]], dtype=np.float64), np.array(H, dtype=np.float64))[0][0]
    return [round(float(p[0]), 1), round(float(p[1]), 1)]


def load():
    if not CALIBRATION.exists():
        raise SystemExit(f"not calibrated yet: run `python -m armsim.eye calibrate` ({CALIBRATION} is missing)")
    return json.loads(CALIBRATION.read_text())


def cmd_detect(a):
    im = frame(a.image)
    found = blobs(im, a.color)[: a.max]
    OUT.mkdir(parents=True, exist_ok=True)
    shown = im.copy()
    for i, b in enumerate(found, 1):
        x, y = map(int, b["centroid"])
        cv2.circle(shown, (x, y), 10, (0, 0, 255), 2)
        cv2.putText(shown, str(i), (x + 12, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)
    cv2.imwrite(str(OUT / "detect.jpg"), shown)
    print(f"{len(found)} {a.color} blob(s); numbered on {OUT / 'detect.jpg'}")
    for i, b in enumerate(found, 1):
        print(f"  {i}: pixel {b['centroid']}  area {b['area']}")
    json.dump(found, open(OUT / "detect.json", "w"))


def cmd_calibrate(a):
    found = json.load(open(OUT / "detect.json"))
    img_pts, tab_pts = [], []
    for pair in a.pairs.split():
        n, xy = pair.split(":")
        x, y = (float(v) for v in xy.split(","))
        img_pts.append(found[int(n) - 1]["centroid"])
        tab_pts.append([x, y])
    if len(img_pts) < 4:
        raise SystemExit("need at least 4 points on the table")
    H, _ = cv2.findHomography(np.array(img_pts, dtype=np.float64), np.array(tab_pts, dtype=np.float64), 0)
    if H is None:
        raise SystemExit("couldn't fit: check that no three points are in a line")
    err = [float(np.hypot(*(np.array(to_table(H, p)) - t))) for p, t in zip(img_pts, tab_pts)]
    cal = {"homography": H.tolist(), "image_size": list(frame(a.image).shape[1::-1]),
           "points": [{"pixel": p, "cm": t} for p, t in zip(img_pts, tab_pts)],
           "fit_error_cm": {"max": round(max(err), 2), "mean": round(sum(err) / len(err), 2)},
           "box_cm": [float(v) for v in a.box.split(",")] if a.box else None,
           "frame": "origin at the centre of the arm's base; x forward (pan 0), y to the arm's left; cm",
           "made": time.strftime("%Y-%m-%d %H:%M:%S %Z")}
    CALIBRATION.write_text(json.dumps(cal, indent=2) + "\n")
    grid(cal, frame(a.image))
    print(f"saved {CALIBRATION} | fit error max {cal['fit_error_cm']['max']} cm, mean {cal['fit_error_cm']['mean']} cm"
          f"{' (4 points fit exactly; add a 5th for a real check)' if len(img_pts) == 4 else ''}")
    print(f"check grid (5 cm lines, the origin marked) -> {OUT / 'grid.jpg'}")


def grid(cal, im):
    """Draw the table's 5 cm grid and the arm-base origin back onto the image, to check the fit by eye."""
    Hinv = np.linalg.inv(np.array(cal["homography"]))
    def px(x, y):
        p = cv2.perspectiveTransform(np.array([[[x, y]]], dtype=np.float64), Hinv)[0][0]
        return int(round(p[0])), int(round(p[1]))
    shown = im.copy()
    for v in range(-40, 45, 5):
        cv2.line(shown, px(0, v), px(50, v), (255, 200, 0), 1)
        cv2.line(shown, px(v + 40 if v < 10 else v, -40), px(v + 40 if v < 10 else v, 40), (255, 200, 0), 1)
    cv2.drawMarker(shown, px(0, 0), (0, 0, 255), cv2.MARKER_CROSS, 30, 3)
    cv2.putText(shown, "arm base", px(0, 0), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
    for p in cal["points"]:
        cv2.circle(shown, tuple(int(v) for v in p["pixel"]), 8, (0, 255, 0), 2)
    if cal.get("box_cm"):
        cv2.drawMarker(shown, px(*cal["box_cm"]), (0, 140, 255), cv2.MARKER_SQUARE, 30, 3)
    OUT.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(OUT / "grid.jpg"), shown)


def locate(im=None, *, color="blue"):
    """Where the bag of chips is on the table (cm, arm frame), from the latest camera frame."""
    cal = load()
    im = frame() if im is None else im
    found = blobs(im, color, join_px=41, max_frac=0.6)
    out = {"bag": None, "box": cal.get("box_cm"), "frame": cal["frame"], "ts": time.time()}
    if found:
        b = found[0]
        x, y = to_table(cal["homography"], b["footprint"])
        out["bag"] = {"x_cm": x, "y_cm": y, "distance_cm": round(float(np.hypot(x, y)), 1),
                      "bearing_deg": round(float(np.degrees(np.arctan2(y, x))), 1), "pixel": b["footprint"]}
        if cal.get("box_cm"):
            bx, by = cal["box_cm"]
            out["bag_to_box_cm"] = round(float(np.hypot(bx - x, by - y)), 1)
    return out


def cmd_locate(a):
    r = locate(frame(a.image) if a.image else None, color=a.color)
    if a.json:
        print(json.dumps(r))
        return
    if not r["bag"]:
        print("no bag of chips in view")
    else:
        b = r["bag"]
        print(f"bag of chips: {b['x_cm']} cm forward, {b['y_cm']} cm left of the arm's base "
              f"({b['distance_cm']} cm away, bearing {b['bearing_deg']} deg)")
    if r["box"]:
        print(f"box: {r['box'][0]} cm forward, {r['box'][1]} cm left" +
              (f" | bag to box: {r['bag_to_box_cm']} cm" if r.get("bag_to_box_cm") is not None else ""))


def main():
    ap = argparse.ArgumentParser(prog="python -m armsim.eye", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("detect", help="find the sticky notes and number them")
    d.add_argument("--color", default="green", choices=sorted(COLORS))
    d.add_argument("--max", type=int, default=8)
    d.add_argument("--image", default=CAMERA)
    c = sub.add_parser("calibrate", help="pair numbered notes with their measured cm and save the mapping")
    c.add_argument("--pairs", required=True, help='"n:x_cm,y_cm ..." using the numbers from detect')
    c.add_argument("--box", default=None, help="the box's measured centre, x_cm,y_cm")
    c.add_argument("--image", default=CAMERA)
    loc = sub.add_parser("locate", help="where the bag of chips is, in cm")
    loc.add_argument("--color", default="blue", choices=sorted(COLORS))
    loc.add_argument("--image", default=None)
    loc.add_argument("--json", action="store_true")
    a = ap.parse_args()
    {"detect": cmd_detect, "calibrate": cmd_calibrate, "locate": cmd_locate}[a.cmd](a)


if __name__ == "__main__":
    main()
