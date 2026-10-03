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
blueprint: kinetic-beat-slam
beat: 1 bar, drums from the first frame. "Your files, / your projects, / your chats, / your mail," as full-bleed hard cuts in the modules' colours, one per beat. The name they add up to is kept for the close.

## Frame 2
status: built
src: compositions/rush.html
type: build
blueprint: kinetic-beat-slam (accelerating)
transition_in: hard cut on the beat
beat: 1 bar, the build. "your notes, / your photos, / your calendar, / your contacts, / your passwords, / your AI," on the eighths, a clap on each (the scene's `hits` drive both), then "your data." held for the drop.

## Frames 3-12 - one per module
status: built
src: feature.html (template), one entry each in SCENES
type: feature_showcase
blueprint: cursor-ui-demo (flat, full screen)
transition_in: the drop (overexposure) into Files, then push / blocks / split / iris in each module's colour
beat: the drop lands straight on the Files take, no card: the app answers the hook. Then the modules in the hook's order, each a full-bleed title card in its colour then its take pushed up full screen ("Ship your projects.", "Chat in real time.", "Ask your AI." for 2 bars; "Write.", "Mail.", "Plan.", "Contacts.", "Passwords." for 1 bar), and "Find anything." last, once there is everything to search.

## Frame 13
status: built
src: compositions/wall.html
type: benefit_highlight
blueprint: grid-card-assemble (zoom-out, flat)
transition_in: colour blocks
beat: 2 bars. Every take at once, framed in its module's colour; "All in one place." on a colour band.

## Frame 14
status: built
src: compositions/yours.html
type: benefit_highlight
blueprint: kinetic-beat-slam
transition_in: push-up
beat: 2 bars, breakdown and riser. "Self-hosted." / "Open source." / "Your data, your rules." (the payoff of the hook's "your data.") as full-bleed hard cuts.

## Frame 15
status: built
src: compositions/roll.html
type: build
blueprint: kinetic-beat-slam (colour roll)
transition_in: hard cut on the beat
beat: 2 bars, the build into the drop. Every module again, full screen in its colour with its icon, one per hit: beats, then eighths, then sixteenths, a clap on each (the `hits` of the scene drive both).

## Frame 16
status: built
src: compositions/cta.html
type: cta
blueprint: logo-assemble-lockup (flat)
transition_in: overexposure (the drop)
beat: 3 bars, drop then ring-out. The mark slams in on the flash, then "your Workspace.", "One app for all of it.", the repository, on the brand violet.
