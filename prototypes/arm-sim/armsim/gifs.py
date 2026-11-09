"""GIFs of the generate-and-test runs on the real arm, and of the sim stories, for the README.

    python -m armsim.gifs                                # success-<time>.gif for each try the eye judged INSIDE
    python -m armsim.gifs --tries 14:00:30 14:09:38      # try-<time>.gif: one frame-sampled try, animated
    python -m armsim.gifs --stories                      # the sim self-healing stories, shrunk for the README

Frames come from runs/gentest/ on the bench, or from the local hackathon data folder
(<repo>/data/hackathon-2026-11-08, git-ignored) once the bench is gone; `--data` picks another. The try log keeps
absolute paths, so a frame that moved is found by its file name in that folder.

A try counts as a success when Astra's eye judged it INSIDE: a `success` row in hermes-trials.jsonl, or the next
row's `hermes_verdict_before`. Frames are captioned with the try, the recording, pan, speed, the grip and the time.
"""
import argparse
import json
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]                  # prototypes/arm-sim
RUNS = ROOT / "runs" / "gentest"                            # on the bench
DATA = ROOT.parents[1] / "data" / "hackathon-2026-11-08"    # afterwards: local only, git-ignored
STORIES = ("story-a", "story-b", "story-stall")             # the sim stories (python -m armsim.stories a|b|stall)


def gentest_dir(data=None):
    """The folder with the try log and the frames."""
    if data:
        d = Path(data)
        return d / "arm-runs" / "gentest" if (d / "arm-runs" / "gentest").exists() else d
    return RUNS if (RUNS / "hermes-trials.jsonl").exists() else DATA / "arm-runs" / "gentest"


def rows(g):
    out = []
    for name in ("trials.jsonl", "hermes-trials.jsonl"):
        f = g / name
        if f.exists():
            for line in f.read_text().splitlines():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return sorted(out, key=lambda r: r.get("ts", 0))


def local(p, g):
    """A logged frame path, or the same file name in the frames folder if it moved; None if neither exists."""
    if not p or "-grid" in str(p):
        return None
    if Path(p).exists():
        return Path(p)
    q = g / Path(p).name
    return q if q.exists() else None


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
    if r.get("regrab"):
        bits.append("re-grab check")
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


def save(frames, out, ms, colors=128):
    """ms: one duration for every frame, or a list with one per frame."""
    if not frames:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    pal = [f.convert("P", palette=Image.ADAPTIVE, colors=colors) for f in frames]
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


def sequence(r, last_stage, g):
    """The frames of one try in time order: BEFORE, the sampled frames (t+N s into the try) with the jaws-close
    frame among them, AFTER. Missing frames are skipped."""
    mid = [(local(f, g), "t+" + Path(f).stem.rsplit("-t", 1)[-1]) for f in (r.get("sequence") or [])]
    if r.get("close_image"):
        mid.append((local(r["close_image"], g), "JAWS CLOSE"))
    mid = sorted((m for m in mid if m[0]), key=lambda m: (m[0].name[:6], m[0].name))   # file names start HHMMSS
    ends = [(local(r.get("before"), g), "BEFORE"), (local(r.get("after"), g), last_stage)]
    return [x for x in (ends[0], *mid, ends[1]) if x[0]]


def build_successes(g, out_dir, width):
    tries = rows(g)
    made = []
    for r in successes(tries):
        frames = [frame(p, stage, r, width) for p, stage in sequence(r, "AFTER: IN THE BOX", g)]
        out = out_dir / f"success-{time.strftime('%Y%m%d-%H%M%S', time.localtime(r.get('ts', 0)))}.gif"
        if save(frames, out, 1100):
            made.append((out, out.stat().st_size))
    return made, len(tries)


def build_tries(g, times, out_dir, width):
    """One animated GIF per named try (HH:MM:SS): its whole frame sequence, the first and last frames held."""
    tries = rows(g)
    made = []
    for want in times:
        r = next((x for x in tries if time.strftime("%H:%M:%S", time.localtime(x.get("ts", 0))) == want), None)
        if r is None:
            print(f"no try at {want}")
            continue
        seq = sequence(r, "AFTER", g)
        frames = [frame(p, stage, r, width) for p, stage in seq]
        ms = [1600 if i in (0, len(frames) - 1) else 650 for i in range(len(frames))]
        out = out_dir / f"try-{want.replace(':', '')}.gif"
        if save(frames, out, ms, colors=96):
            made.append((out, out.stat().st_size))
    return made


def shrink(src, out, every=2, colors=64):
    """A sim GIF made README-sized: every Nth frame (shown N times as long, so the timing holds) and a small
    palette. The status line at the top stays readable because the size doesn't change."""
    im = Image.open(src)
    frames, ms = [], []
    for k in range(0, im.n_frames, every):
        im.seek(k)
        frames.append(im.convert("RGB"))
        ms.append(im.info.get("duration", 100) * every)
    return save(frames, out, ms, colors=colors)


def build_stories(data, out_dir):
    d = Path(data) if data else DATA
    made = []
    for name in STORIES:
        src = next(iter(sorted(d.rglob(f"{name}.gif"))), None)
        if src is None:
            print(f"no {name}.gif under {d}")
            continue
        out = shrink(src, out_dir / f"{name}.gif")
        made.append((out, out.stat().st_size))
    return made


def main():
    ap = argparse.ArgumentParser(prog="python -m armsim.gifs", description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default=None, help="a gentest folder, or the hackathon data folder (default: "
                                                 "runs/gentest if it has a try log, else <repo>/data/hackathon-2026-11-08)")
    ap.add_argument("--out", default=None, help="default: media/gentest, or media/sim for --stories")
    ap.add_argument("--width", type=int, default=480)
    ap.add_argument("--tries", nargs="+", metavar="HH:MM:SS", help="animate these frame-sampled tries")
    ap.add_argument("--stories", action="store_true", help="shrink the sim self-healing story GIFs for the README")
    a = ap.parse_args()
    if a.stories:
        made = build_stories(a.data, Path(a.out or ROOT / "media" / "sim"))
    elif a.tries:
        made = build_tries(gentest_dir(a.data), a.tries, Path(a.out or ROOT / "media" / "gentest"), a.width)
    else:
        made, n = build_successes(gentest_dir(a.data), Path(a.out or ROOT / "media" / "gentest"), a.width)
        print(f"{n} tries, {len(made)} successful" + ("" if made else ": no GIF yet"))
    for m, size in made:
        print(f"  {m.relative_to(ROOT) if m.is_relative_to(ROOT) else m}  {size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
