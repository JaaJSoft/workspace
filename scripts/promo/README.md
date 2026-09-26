# Promo video

A one-minute ad for Workspace, cut to 128 BPM, in 16:9 and 9:16. It is the
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
| `compositions/*.html` | One hand-written sub-composition per scene; each fills its own paused GSAP timeline, entrances only (the cut is the exit) |
| `promo.js` | The shared motion vocabulary (beat-grid slams, pops, window entrances) and the root timeline: backdrop pulsing on the kick, every cut, the fades |
| `promo.css` | Palette, type, the app window, and the portrait overrides shared by the scenes |
| `BRIEF.md`, `STORYBOARD.md` | The brief and the frame plan, in HyperFrames' formats |

Scenes are authored once at 1920x1080 and switch layout with
`@media (orientation: portrait)`; the build only changes the declared canvas.
Times inside a scene are on the beat grid (`at(beats)` from `promo.scene()`),
so a scene follows a tempo change without edits.

## Updating it

- **Copy or a scene's motion**: edit its file in `compositions/`.
- **Order, lengths, cuts**: `SCENES` in `promo_video.py`. The music follows
  the bars; `core.tests.test_presentation_video` checks every scene has its
  file and the whole stays under 90 seconds.
- **Footage**: rerun `scripts/presentation_video.py` to refilm the takes; a
  scene picks its moment in a take with `data-media-start` and its speed with
  `data-playback-rate`.
