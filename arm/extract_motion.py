#!/usr/bin/env python3
"""Pull one episode's joint commands out of a LeRobot dataset on the Hugging Face Hub into a small JSON
file that safe_replay.py plays back.

Default: episode 0 of lerobot/svla_so101_pickplace (SO-101 pick-and-place, 50 episodes, 30 fps).
https://huggingface.co/datasets/lerobot/svla_so101_pickplace

The dataset's "action" column holds the leader-arm positions that were sent to the follower each frame.
They are in LeRobot's RANGE_M100_100 units (-100..100 across each joint's calibrated range; gripper
0..100): shoulder_lift and elbow_flex sit at exactly -100 / +100 in the rest pose. So the replay must run
the arm with use_degrees=False, which safe_replay.py does for motions marked "range_m100_100".

  ~/lerobot-venv/bin/python arm/extract_motion.py [--repo lerobot/svla_so101_pickplace] [--episode 0]
"""
import argparse
import json
from pathlib import Path

import pyarrow.parquet as pq
from huggingface_hub import hf_hub_download


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="lerobot/svla_so101_pickplace")
    ap.add_argument("--episode", type=int, default=0)
    ap.add_argument("--out", help="default: arm/motions/<dataset>_ep<N>.json")
    args = ap.parse_args()

    info = json.loads(Path(hf_hub_download(args.repo, "meta/info.json", repo_type="dataset")).read_text())
    joints = info["features"]["action"]["names"]
    # Episodes 0..999 live in data/chunk-000/; small datasets keep them all in file-000.parquet.
    data = hf_hub_download(args.repo, "data/chunk-000/file-000.parquet", repo_type="dataset")
    t = pq.read_table(data, columns=["episode_index", "frame_index", "action"]).to_pydict()
    rows = sorted((f, a) for e, f, a in zip(t["episode_index"], t["frame_index"], t["action"]) if e == args.episode)
    if not rows:
        raise SystemExit("episode %d not in %s" % (args.episode, data))

    motion = {
        "source": "https://huggingface.co/datasets/%s (episode %d, 'action' column)" % (args.repo, args.episode),
        "robot_type": info.get("robot_type"),
        "units": "range_m100_100",
        "fps": info["fps"],
        "joints": joints,
        "frames": [[round(v, 2) for v in a] for _, a in rows],
    }
    out = Path(args.out) if args.out else (Path(__file__).resolve().parent / "motions" /
                                          ("%s_ep%d.json" % (args.repo.split("/")[-1], args.episode)))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(motion, separators=(",", ":")) + "\n")
    print("wrote %s: %d frames, %.1f s" % (out, len(rows), len(rows) / info["fps"]))


if __name__ == "__main__":
    main()
