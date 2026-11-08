# Hardware Buddy judges' video (Remotion)

The whole cut is code. Storyboard: [`docs/demo-video/storyboard.md`](../docs/demo-video/storyboard.md).

## Setup (once)
```bash
cd video
npm ci
ln -sfn ~/Movies/naive-demo/raw public/footage   # footage stays out of git
```
ffmpeg is only needed to probe and convert footage (`brew install ffmpeg`).

## Where footage goes
`~/Movies/naive-demo/raw/` (seen by Remotion as `public/footage/`):
- `anim/`: Andre's scenes, screen-recorded with `?auto=1` at 1920×1080
- `bench/`: phone footage of the arm and pod; `screen/`: terminal and browser recordings
- `vo/`: one voice file per beat, `vo-<beat>-<name>.m4a`
- the heal run's `transcript.jsonl`, `report.md`, `score.json`

Phone footage is often HEVC or variable frame rate. Convert before use:
```bash
ffmpeg -i in.mov -r 30 -c:v libx264 -crf 18 -pix_fmt yuv420p -c:a aac out.mp4
```

## Re-cut
1. Edit `src/timeline.ts`: each beat's `start`/`end`, its `clip` (path under `public/footage`), `clipFrom`, `playbackRate` (speed-ups get a tag on screen) and `vo_file`.
2. Preview: `npm run studio`.
3. Render: `npm run render` → `~/Movies/naive-demo/renders/hardware-buddy.mp4`.
4. Check the length: `ffprobe -v error -show_entries format=duration -of csv=p=0 ~/Movies/naive-demo/renders/hardware-buddy.mp4` (must be ≤ 120).
