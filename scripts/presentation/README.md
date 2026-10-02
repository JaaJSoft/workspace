# Presentation video

A two-minute tour of every module, filmed in the real app on a seeded demo:
a cursor searches, drags cards, types notes and messages, while the scenes
around it animate to an original soundtrack synthesized to the same tempo.
Rebuild it whenever the UI changes and the video follows.

```bash
uv run python scripts/presentation_video.py                 # film + render build/presentation/workspace.mp4
uv run python scripts/presentation_video.py --skip-capture  # reuse the last takes
uv run python scripts/presentation_video.py --skip-capture --preview        # HyperFrames Studio
uv run python scripts/presentation_video.py --skip-capture --stills 12 44.5 # snapshots only
uv run python scripts/presentation_video.py --skip-capture --draft          # fast, low-quality render
uv run python scripts/presentation_video.py --music track.mp3               # your own music instead
```

Needs the `dev` dependency group (Playwright, with `uv run playwright install
chromium`) for the takes, Node.js 22+ and `ffmpeg` on the `PATH`. The render
toolchain ([HyperFrames](https://hyperframes.heygen.com), GSAP, the fonts) is
pinned in `package.json` here and installed with `npm ci` on first run; the
script turns HyperFrames telemetry off. Everything lands in
`build/presentation/`, which git ignores. The promo (`scripts/promo/`) cuts
its ad from the same takes.

## How it fits together

| File | Role |
|---|---|
| `scenes.py` | What the video says and shows: one entry per module (tagline, features, the page, and the act filmed on it), plus the intro, platform and outro cards |
| `recording.py` | Films an act: Chrome's screencast turned into a clip, a visible cursor with click ripples and drag images, and human-paced gestures (glide, click, type, drag) |
| `capture.py` | Seeds a throwaway demo (the one `scripts/screenshots.py` uses, plus a vault) and films or screenshots every shot |
| `ai_stub.py` | A local OpenAI-compatible endpoint, so the assistant answers on camera with the same scripted reply every run |
| `soundtrack.py` | The music: a synthwave loop rendered with numpy from the timeline, so every cut lands on a phrase and the drums follow `drumBars` |
| `composition.py` | Writes the HyperFrames project: `index.html` orchestrates, each scene is a sub-composition under `compositions/`, each take a `<video>` in its window |
| `composition/` | The stylesheet and `motion.js`, the GSAP choreography every composition calls into |
| `../presentation_video.py` | Glues it together: registry lookup, timeline, soundtrack, composition, `hyperframes check` then `hyperframes render` |
| `../_landscapes.py` | The illustrated landscapes the demo's photo library is made of |

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
- **New feature worth showing**: change the module's `act`, or its
  `features`. An act has about seven seconds; the build warns when a take runs
  longer than its scene shows.
- **Tuning acts**: `--serve` keeps a seeded demo running, and later runs film
  against it instead of seeding their own, `--only chat projects` films just
  those scenes. The takes change the demo's data (messages sent, cards moved),
  so stop it and render the final video from a fresh demo.
- **Animation work**: `--skip-capture --preview` opens the composition in
  HyperFrames Studio, which reloads when the project changes; rerun the
  command after editing `composition/` or `composition.py` to regenerate it.
- Icons are Lucide names from the vendored bundle; the test above fails on any
  it cannot find.
