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
    for name, (draw, _, end, *_) in loopgif.SCENES.items():
        for t in (0.0, end / 2, end, end + 5):
            im = draw(t)
            assert im.size == (loopgif.OUT_W, loopgif.OUT_H), (name, t, im.size)


def test_every_caption_stays_up_long_enough_to_read():
    fps = loopgif.FPS
    for name, (_, _, end, _, captions) in loopgif.SCENES.items():
        ts = loopgif.story_times(end, fps, captions)
        for t0, t1, _, _, words in captions[:-1]:
            shown = sum(1 for t in ts if t0 <= t < t1) / fps
            assert shown >= len(words.split()) / loopgif.WPS - 1 / fps, (name, words, shown)


def test_every_character_has_a_glyph():
    """A character the font lacks draws as an empty box (Avenir Next has no "→", for one)."""
    from PIL import ImageDraw

    def glyph(kind, ch):
        im = Image.new("L", (96, 96))
        ImageDraw.Draw(im).text((8, 8), ch, font=loopgif.font(kind, 20), fill=255)
        return im.tobytes()

    words = [w for caps in (loopgif.CAPTIONS, loopgif.EXP_CAPTIONS, loopgif.FLEET_CAPTIONS) for *_, w in caps]
    body = words + [s for _, s, _ in loopgif.OLIR_LINES + loopgif.PROBE_LINES]
    mono = [s for _, s, _ in loopgif.GATE_LINES + loopgif.AGENT_LINES + loopgif.ASTRA_LINES + loopgif.TRIAL_LINES
            + loopgif.LOG_ROWS + loopgif.NOTES]
    for kind, texts in (("body", body), ("bold", words), ("mono", mono)):
        missing = glyph(kind, "\U0010fffd")                    # a private-use point no font draws
        for ch in sorted({ch for s in texts for ch in s if ord(ch) > 127}):
            assert glyph(kind, ch) != missing, (kind, ch)


def test_the_palette_keeps_the_theme_colours():
    draw, _, end, times, _ = loopgif.SCENES["heal"]
    pal = loopgif.palette_from(draw, times[:2]).getpalette()[: 3 * 255]
    colours = {tuple(pal[i:i + 3]) for i in range(0, len(pal), 3)}
    for col in (loopgif.AMBER, loopgif.RED, loopgif.GREEN, loopgif.VIOLET, loopgif.PINK, loopgif.CYAN):
        assert col in colours, col


def test_a_short_gif_stores_only_what_changed():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "loop.gif"
        old = loopgif.SCENES["heal"]
        claim = lambda t: old[0](t + 4.8)                       # noqa: E731  the card moving to doing, 1 s of it
        loopgif.SCENES["heal"] = (claim, out, 1.0, old[3][:2], old[4][:1])      # one caption: no pauses
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
    for fn in (test_every_scene_draws_its_whole_story, test_every_caption_stays_up_long_enough_to_read,
               test_every_character_has_a_glyph, test_the_palette_keeps_the_theme_colours,
               test_a_short_gif_stores_only_what_changed):
        fn()
        print("ok ", fn.__name__)
