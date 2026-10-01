# Storyboard - Workspace promo

This video tells teams that their files, projects, chats and data all live
in one app of their own. 128 BPM, 26 bars (49 s). Scene lengths, cuts and
music sections live in `SCENES` of `scripts/promo_video.py`; the frames below
are the plan. The style is flat and full screen throughout: module colours,
heavy type, hard cuts on the beat, no 3D, no shadows.

## Frame 1
status: built
src: compositions/open.html
type: hook
blueprint: kinetic-beat-slam + logo-assemble-lockup
beat: 3 bars, drums from the first frame. "Your files, / your projects, / your chats, / your data." as full-bleed colour hard cuts, one per beat; the four colours slam in as stripes and wipe off onto the brand violet, where the mark lands, then "your Workspace." "One app for all of it."

## Frames 2-11 - one per module
status: built
src: feature.html (template), one entry each in SCENES
type: feature_showcase
blueprint: cursor-ui-demo (flat, full screen)
transition_in: iris / push-left / blocks / split / push-up, in the module's colour
beat: a full-bleed title card in the module's colour ("Search anything.", "Every file, every photo.", "Chat in real time.", "Ask your AI.", "Ship your projects." for 2 bars each; "Write.", "Mail.", "Plan.", "Contacts.", "Passwords." for 1 bar each), then its filmed take pushed up full screen.

## Frame 12
status: built
src: compositions/wall.html
type: benefit_highlight
blueprint: grid-card-assemble (zoom-out, flat)
transition_in: colour blocks
beat: 2 bars. Every take at once, framed in its module's colour; "All in one Workspace." on a colour band.

## Frame 13
status: built
src: compositions/yours.html
type: benefit_highlight
blueprint: kinetic-beat-slam
transition_in: push-up
beat: 2 bars, breakdown and riser. "Self-hosted." / "Open source." / "Your data, your rules." as full-bleed hard cuts.

## Frame 14
status: built
src: compositions/cta.html
type: cta
blueprint: colour vortex into logo-assemble-lockup (flat)
transition_in: overexposure (the drop)
beat: 4 bars, drop then ring-out. The module colours burst out as one spiral centred on the frame, spin faster and faster and collapse into the mark on the brand violet; "Workspace", "One app. Every tool. Yours.", the repository.
