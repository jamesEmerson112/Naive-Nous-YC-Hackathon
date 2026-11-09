"""The README's loop animations (armsim/loopgif.py) still draw: a few frames of each scene, and a short GIF.
No host, no network. Run from prototypes/arm-sim:

    PYTHONPATH=. .venv/bin/python tests/test_loopgif.py      (or: python -m pytest tests/)
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from armsim import loopgif  # noqa: E402


def test_every_scene_draws_its_whole_story():
    for name, (draw, _, end, _) in loopgif.SCENES.items():
        for t in (0.0, end / 2, end, end + 5):
            im = draw(t)
            assert im.size == (loopgif.OUT_W, loopgif.OUT_H), (name, t, im.size)


def test_the_palette_keeps_the_theme_colours():
    draw, _, end, times = loopgif.SCENES["heal"]
    pal = loopgif.palette_from(draw, times[:2]).getpalette()[: 3 * 255]
    colours = {tuple(pal[i:i + 3]) for i in range(0, len(pal), 3)}
    for col in (loopgif.AMBER, loopgif.RED, loopgif.GREEN, loopgif.VIOLET, loopgif.PINK, loopgif.CYAN):
        assert col in colours, col


def test_a_short_gif_stores_only_what_changed():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "loop.gif"
        old = loopgif.SCENES["heal"]
        claim = lambda t: old[0](t + 4.8)                       # noqa: E731  the card moving to doing, 1 s of it
        loopgif.SCENES["heal"] = (claim, out, 1.0, old[3][:2])
        try:
            loopgif.build("heal", out, 5)
        finally:
            loopgif.SCENES["heal"] = old
        im = Image.open(out)
        assert im.size == (loopgif.OUT_W, loopgif.OUT_H) and im.n_frames >= 2, im.n_frames
        im.seek(1)
        x0, y0, x1, y1 = im.tile[0][1]
        assert (x1 - x0) * (y1 - y0) < loopgif.OUT_W * loopgif.OUT_H / 4, im.tile    # a delta, not a full frame


if __name__ == "__main__":
    for fn in (test_every_scene_draws_its_whole_story, test_the_palette_keeps_the_theme_colours,
               test_a_short_gif_stores_only_what_changed):
        fn()
        print("ok ", fn.__name__)
