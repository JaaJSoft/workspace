# Promo video

A forty-five-second ad for Workspace, cut to 128 BPM, in 16:9 and 9:16: flat,
colourful, full screen, every module in its own colour. It is the
trailer of the long presentation video (`scripts/presentation/`), and reuses
its filmed takes, its soundtrack synthesizer and its pinned HyperFrames
toolchain.

```bash
uv run python scripts/promo_video.py                    # both formats
uv run python scripts/promo_video.py --aspect portrait  # one format
uv run python scripts/promo_video.py --preview          # HyperFrames Studio (16:9)
uv run python scripts/promo_video.py --stills 3 12 40   # snapshots only
```

Outputs `build/promo/workspace-promo-16x9.mp4` and `-9x16.mp4`. The takes come
from `build/presentation/clips/`; when any is missing the presentation
pipeline films it first (see its README for the requirements).

## How it fits together

| File | Role |
|---|---|
| `../promo_video.py` | The timing: scene order and length in bars, the cut into each scene, the part of the music each sits on. Assembles one HyperFrames project per format and renders it |
| `feature.html` | The template of a module's scene: a title card in its colour, then its take full screen. One line per module in `SCENES` fills it |
| `compositions/*.html` | The hand-written scenes: the opening, the wall of takes, the breakdown, the close |
| `promo.js` | The shared motion vocabulary (beat-grid slams, pops, the module beat) and the root timeline: every full-screen cut, the fades |
| `promo.css` | Palette, type, the module scene layout, and the portrait overrides |
| `BRIEF.md`, `STORYBOARD.md` | The brief and the frame plan, in HyperFrames' formats |

Scenes are authored once at 1920x1080 and switch layout with
`@media (orientation: portrait)`; the build only changes the declared canvas.
Times inside a scene are on the beat grid (`at(beats)` from `promo.scene()`),
so a scene follows a tempo change without edits.

## Updating it

- **A module**: its line in `SCENES` (colour, icon, title, take, the moment
  and speed of the take). A new module is a new line; the build warns when a
  take is too short for its scene.
- **The other scenes**: their file in `compositions/`.
- **Order, lengths, cuts**: `SCENES` in `promo_video.py`. The music follows
  the bars; `core.tests.test_presentation_video` checks every scene has its
  file and the whole stays under 90 seconds.
- **Footage**: rerun `scripts/presentation_video.py` to refilm the takes.
