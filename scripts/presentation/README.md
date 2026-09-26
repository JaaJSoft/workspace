# Presentation video

A two-minute tour of every module, rendered from the real app and a seeded
demo, with an original soundtrack synthesized to the same tempo. Rebuild it
whenever the UI changes and the video follows.

```bash
uv run python scripts/presentation_video.py                 # capture + render build/presentation/workspace.mp4
uv run python scripts/presentation_video.py --skip-capture  # reuse the last captures
uv run python scripts/presentation_video.py --skip-capture --preview        # HyperFrames Studio
uv run python scripts/presentation_video.py --skip-capture --stills 12 44.5 # snapshots only
uv run python scripts/presentation_video.py --skip-capture --draft          # fast, low-quality render
uv run python scripts/presentation_video.py --music track.mp3               # your own music instead
```

Needs the `dev` dependency group (Playwright, with `uv run playwright install
chromium`) for the captures, Node.js 22+ and `ffmpeg` on the `PATH`. The
render toolchain ([HyperFrames](https://hyperframes.heygen.com), GSAP, the
fonts) is pinned in `package.json` here and installed with `npm ci` on first
run; the script turns HyperFrames telemetry off. Everything lands in
`build/presentation/`, which git ignores.

## How it fits together

| File | Role |
|---|---|
| `scenes.py` | What the video says and shows: one entry per module (tagline, features, pages to capture, callouts), plus the intro, platform and outro cards |
| `capture.py` | Seeds a throwaway demo (the one `scripts/screenshots.py` uses, plus a vault) and screenshots every shot |
| `soundtrack.py` | The music: a synthwave loop rendered with numpy from the timeline, so every cut lands on a phrase and the drums follow `drumBars` |
| `composition.py` | Writes the HyperFrames project: `index.html` orchestrates, each scene is a sub-composition under `compositions/` |
| `composition/` | The stylesheet and `motion.js`, the GSAP choreography every composition calls into |
| `../presentation_video.py` | Glues it together: registry lookup, timeline, soundtrack, composition, `hyperframes check` then `hyperframes render` |

Every scene lasts four bars at 120 BPM (8 seconds). The timeline is computed
once in Python and handed to both the composition and the soundtrack, which is
what keeps picture and music in sync: change a duration in one place and both
move.

`build/presentation/` is an ordinary HyperFrames project, so the whole CLI
works on it (`npx hyperframes timeline`, `snapshot`, `check --snapshots` from
this directory). Edit the sources here, not the generated files there: the
next run overwrites them.

## Updating it

- **New module**: it appears on its own with a generic scene (registry name,
  icon, colour and description over a capture of its page), and
  `core.tests.test_presentation_video` fails until it gets a proper entry in
  `SCENES`.
- **New feature worth showing**: edit the module's `features`, or add a shot.
  `callout` coordinates are CSS pixels of the 1440x900 capture.
- **Animation work**: `--skip-capture --preview` opens the composition in
  HyperFrames Studio, which reloads when the project changes; rerun the
  command after editing `composition/` or `composition.py` to regenerate it.
- Icons are Lucide names from the vendored bundle; the test above fails on any
  it cannot find.
