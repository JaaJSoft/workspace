# Promo video

A fifty-second ad for Workspace, cut to 128 BPM, in 16:9 and 9:16: flat,
colourful, full screen, every module in its own colour. A hook promises
("your files, your projects, your chats..."), the drop answers with the real
app, every module gets a beat, and the name only lands at the very end.
Everything in it is built from this directory: the footage is filmed in the
real app on a throwaway demo, the music is synthesized, nothing recorded or
licensed is committed.

## Recreate it

```bash
uv run python scripts/promo_video.py --refilm   # film every take on a fresh demo, then render both formats
uv run python scripts/promo_video.py            # render again from the takes already filmed
```

Outputs `build/promo/workspace-promo-16x9.mp4` and `-9x16.mp4` (git ignores
`build/`). Both commands run `hyperframes check` on each format before
rendering and stop if it fails.

Requirements, all local:

- the `dev` dependency group (`uv sync`) and Chromium for Playwright
  (`uv run playwright install chromium`), to film the takes;
- Node.js 22+ (`npm`, `npx`): the render toolchain (HyperFrames, GSAP, the
  fonts) is pinned in `package.json` here and installed with `npm ci` on first
  run, telemetry off;
- `ffmpeg` and `ffprobe` on the `PATH`.

On Windows under Git Bash, set `PYTHONIOENCODING=utf-8` for the filming step:
the demo seeder prints characters the default console encoding cannot.

### The takes

One take per module, filmed in the real app on the seeded demo of
`scripts/screenshots.py` (plus a vault, and a local stand-in for the AI
model so the assistant gives the same answer every run), with a scripted
cursor: searching, dragging cards, typing messages. They land in
`build/promo/takes/<module>.mp4`.

- `--refilm` films all of them again; `--refilm chat projects` only those.
  Do it whenever the UI changes: the promo then shows the current app.
- Without `--refilm`, missing takes are filmed and existing ones reused.
- What a take shows is its `act` in `takes.py`; the promo picks the moment it
  plays (`media`, in seconds) and its speed (`rate`) in `SCENES`.

## Iterating

```bash
uv run python scripts/promo_video.py --aspect portrait   # one format
uv run python scripts/promo_video.py --stills 3 12 40    # snapshots only, under build/promo/<format>/snapshots/
uv run python scripts/promo_video.py --preview           # HyperFrames Studio (16:9)
uv run python scripts/promo_video.py --draft             # fast, lower-quality render
```

`build/promo/landscape/` and `build/promo/portrait/` are ordinary HyperFrames
projects, regenerated on every run: edit the sources here, never the
generated files.

## How it fits together

| File | Role |
|---|---|
| `../promo_video.py` | The timing: scene order and length in bars, the cut into each scene, the part of the music each sits on, the hits of the builds. Films missing takes, synthesizes the music, assembles one HyperFrames project per format and renders it |
| `takes.py` | What is filmed in each module: the page, how it is readied off camera, and the act played on it |
| `capture.py`, `recording.py`, `ai_stub.py` | The filming: the seeded demo, Chrome's screencast turned into a clip with a visible cursor and human-paced gestures, the stand-in AI model |
| `soundtrack.py` | The music, synthesized with numpy from the same timeline: drums where the scenes say, a clap on every hit of a build, a drop after each |
| `feature.html` | The template of a module's scene: a title card in its colour, then its take full screen. One line per module in `SCENES` fills it |
| `compositions/*.html` | The hand-written scenes: the hook (`open`), its build (`rush`), the wall of takes, the breakdown (`yours`), the colour roll and the close (`cta`) |
| `promo.js` | The shared motion vocabulary (beat-grid slams, pops, the module beat) and the root timeline: every full-screen cut, the fades |
| `promo.css` | Palette, type, the module scene layout, and the portrait overrides |
| `BRIEF.md`, `STORYBOARD.md` | The brief and the frame plan, in HyperFrames' formats |

Scenes are authored once at 1920x1080 and switch layout with
`@media (orientation: portrait)`; the build only changes the declared canvas.
Times inside a scene are on the beat grid (`at(beats)` from `promo.scene()`),
and the builds' `hits` drive both the cuts and the claps, so picture and
music cannot drift apart.

## Updating it

- **A module**: its take in `takes.py`, then its line in `SCENES` (colour,
  icon, title, take, the moment and speed of the take; `card: 0` starts on
  the take with no title card), its panel in the hook and its colour in the
  roll. The build warns when a take is too short for its scene.
- **The other scenes**: their file in `compositions/`.
- **Order, lengths, cuts**: `SCENES` in `promo_video.py`. The music follows
  the bars.
- **Colours** follow the app's module hues (`color` in each module's
  `apps.py`), in their Tailwind 700 shade so white type reads on them.

`core.tests.test_promo_video` fails when a module on the dashboard has no
take, a scene has no composition, a scene plays a take that is not filmed, an
icon is missing from the vendored Lucide bundle, or the promo runs past 90
seconds.
