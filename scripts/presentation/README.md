# Presentation video

A two-minute tour of every module, rendered from the real app and a seeded
demo, with an original soundtrack synthesized to the same tempo. Rebuild it
whenever the UI changes and the video follows.

```bash
uv run python scripts/presentation_video.py                 # capture + render build/presentation/workspace.mp4
uv run python scripts/presentation_video.py --skip-capture  # reuse the last captures
uv run python scripts/presentation_video.py --skip-capture --player-only
uv run python scripts/presentation_video.py --skip-capture --stills 12 44.5
uv run python scripts/presentation_video.py --music track.mp3   # your own music instead
```

Needs the `dev` dependency group (Playwright), a Chromium install
(`uv run playwright install chromium`) and `ffmpeg` on the `PATH`. A full
render takes a few minutes; the output lands in `build/presentation/`, which
git ignores.

## How it fits together

| File | Role |
|---|---|
| `scenes.py` | What the video says and shows: one entry per module (tagline, features, pages to capture, camera focus, callouts), plus the intro, platform and outro cards |
| `capture.py` | Seeds a throwaway demo (the one `scripts/screenshots.py` uses, plus a vault) and screenshots every shot |
| `stage/` | The animation: an HTML page whose `render(t)` paints any instant. The live player and the mp4 are the same page |
| `soundtrack.py` | The music: a synthwave loop rendered with numpy from the timeline, so every cut lands on a phrase and the drums follow `drumBars` |
| `../presentation_video.py` | Glues it together: registry lookup, timeline, player, soundtrack, frame-by-frame render piped into ffmpeg |

Every scene lasts four bars at 120 BPM (8 seconds). The timeline is computed
once in Python and handed to both the stage and the soundtrack, which is what
keeps picture and music in sync: change a duration in one place and both move.

## Updating it

- **New module**: it appears on its own with a generic scene (registry name,
  icon, colour and description over a capture of its page), and
  `core.tests.test_presentation_video` fails until it gets a proper entry in
  `SCENES`.
- **New feature worth showing**: edit the module's `features`, or add a shot.
  Coordinates (`focus`, `callout`) are CSS pixels of the 1440x900 capture.
- **Animation work**: build the player once (`--skip-capture --player-only`),
  open `build/presentation/index.html`, and reload after each edit of
  `stage/` (rerun the command to copy it over). Space plays and pauses, the
  arrows seek by 5 s (one frame with Shift), `index.html?t=42` opens paused on
  second 42.
- Icons are Lucide names from the vendored bundle; the build warns about any
  it cannot find.
