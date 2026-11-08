"""GIFs of the successful generate-and-test tries on the real arm (the red bag in the box), for the README.

    python -m armsim.gifs [--out media/gentest] [--width 480]

A try counts as a success when Astra's eye judged it INSIDE: a `success` row in runs/gentest/hermes-trials.jsonl,
or the next row's `hermes_verdict_before`. Each success becomes success-<time>.gif: BEFORE -> the moment the jaws
close -> AFTER, captioned with the try, the recording, pan, speed, the grip and the time. No success, no GIF.
"""
import argparse
import json
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs" / "gentest"


def rows():
    out = []
    for name in ("trials.jsonl", "hermes-trials.jsonl"):
        f = RUNS / name
        if f.exists():
            for line in f.read_text().splitlines():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return sorted(out, key=lambda r: r.get("ts", 0))


def font(size):
    for p in ("/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/SFNSMono.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def result(r):
    if r.get("probe"):
        return "probe: " + ("GO" if r.get("go") else "NO-GO")
    if r.get("grasped") is None:
        return "no result"
    return f"grasped {r.get('holding_s')} s" if r.get("grasped") else "no grasp"


def caption(r):
    pan = r.get("pan_offset")
    bits = [str(r.get("try")), str(r.get("recording")), f"pan {pan:+.1f}" if isinstance(pan, (int, float)) else "",
            f"speed {r.get('speed')}" if r.get("speed") else "", result(r)]
    if r.get("effort"):
        bits.append(f"effort {r['effort']}")
    if r.get("forced_by"):
        bits.append("James's call")
    return " | ".join(b for b in bits if b), time.strftime("%H:%M:%S", time.localtime(r.get("ts", 0)))


def frame(path, stage, r, width):
    im = Image.open(path).convert("RGB")
    im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    d, small, big = ImageDraw.Draw(im), font(max(10, width // 46)), font(max(13, width // 30))
    text, clock = caption(r)
    bar = max(22, width // 22)
    d.rectangle([0, im.height - bar, im.width, im.height], fill=(0, 0, 0))
    d.text((6, im.height - bar + 4), text, font=small, fill=(255, 255, 255))
    tw = d.textlength(stage, font=big)
    d.rectangle([0, 0, tw + 14, bar + 2], fill=(200, 30, 45))
    d.text((7, 4), stage, font=big, fill=(255, 255, 255))
    cw = d.textlength(clock, font=small)
    d.rectangle([im.width - cw - 12, 0, im.width, bar], fill=(0, 0, 0))
    d.text((im.width - cw - 6, 4), clock, font=small, fill=(220, 220, 220))
    return im


def existing(p):
    return p and Path(p).exists() and "-grid" not in str(p)


def save(frames, out, ms):
    if not frames:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    pal = [f.convert("P", palette=Image.ADAPTIVE, colors=128) for f in frames]
    pal[0].save(out, save_all=True, append_images=pal[1:], duration=ms, loop=0, optimize=True)
    return out


def successes(tries):
    """The full tries the eye judged INSIDE."""
    ok = []
    for k, r in enumerate(tries):
        if r.get("success"):
            hit = next((x for x in tries if x.get("ts") == r.get("of_ts")), None) if r.get("of_ts") else None
            hit = hit or next((x for x in reversed(tries[:k]) if not x.get("success")), None)
        elif r.get("hermes_verdict_before") == "INSIDE" and k:
            hit = tries[k - 1]
        else:
            continue
        if hit and not hit.get("probe") and not hit.get("success") and hit not in ok:
            ok.append(hit)
    return ok


def build(out_dir, width):
    tries = rows()
    made = []
    for r in successes(tries):
        frames = [frame(p, stage, r, width) for p, stage in ((r.get("before"), "BEFORE"),
                  (r.get("close_image"), "JAWS CLOSE"), (r.get("after"), "AFTER: IN THE BOX")) if existing(p)]
        out = out_dir / f"success-{time.strftime('%Y%m%d-%H%M%S', time.localtime(r.get('ts', 0)))}.gif"
        if save(frames, out, 1100):
            made.append((out, out.stat().st_size))
    return made, len(tries)


def main():
    ap = argparse.ArgumentParser(prog="python -m armsim.gifs", description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(ROOT / "media" / "gentest"))
    ap.add_argument("--width", type=int, default=480)
    a = ap.parse_args()
    made, n = build(Path(a.out), a.width)
    print(f"{n} tries, {len(made)} successful" + ("" if made else ": no GIF yet"))
    for m, size in made:
        print(f"  {m}  {size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
