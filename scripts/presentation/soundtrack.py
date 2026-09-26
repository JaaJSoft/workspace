"""The presentation soundtrack, synthesized from the timeline.

An original synthwave loop (A minor, i-VI-III-VII) rendered with numpy, so the
video carries no licensed audio and the music follows the cut: every scene
starts on a phrase, the drums play where the timeline says ``drumBars``, a
riser leads into each drop and a whoosh lands on every transition.

Deterministic: the same timeline always renders the same file.
"""

import wave
from pathlib import Path

import numpy as np

SR = 44100
RNG_SEED = 1234

# One chord per bar, looping: Am, F, C, G. Voicings as MIDI notes, bass apart.
PROGRESSION = [
    ((57, 60, 64), 33),
    ((53, 57, 60), 29),
    ((55, 60, 64), 36),
    ((55, 59, 62), 31),
]

# Index into the arpeggio's chord tones (chord + its first two notes an octave
# up), one per sixteenth. Scenes alternate between the two.
ARP_PATTERNS = [
    [0, 2, 1, 3, 2, 4, 3, 1, 0, 2, 1, 3, 2, 4, 3, 4],
    [0, 1, 2, 3, 4, 3, 2, 1, 0, 1, 2, 4, 3, 2, 1, 2],
]

# The hook, over one pass of the progression: (beat, MIDI note, beats held).
# Played on every other module scene so the loop does not wear thin.
HOOK = [
    (0, 76, 1), (1.5, 74, 0.5), (2, 72, 1), (3, 69, 1),
    (4, 72, 1.5), (5.5, 74, 0.5), (6, 76, 2),
    (8, 79, 1), (9, 76, 1), (10, 74, 1), (11, 72, 1),
    (12, 74, 1.5), (13.5, 71, 0.5), (14, 74, 2),
]  # fmt: skip


def midi_hz(note):
    return 440.0 * 2 ** ((note - 69) / 12)


# -- oscillators and filters ---------------------------------------------------


def saw(freq, n, phase=0.0):
    """Band-limited (PolyBLEP) sawtooth."""
    dt = freq / SR
    ph = (phase + dt * np.arange(n)) % 1.0
    y = 2 * ph - 1
    lo = ph < dt
    x = ph[lo] / dt
    y[lo] -= x + x - x * x - 1
    hi = ph > 1 - dt
    x = (ph[hi] - 1) / dt
    y[hi] -= x * x + x + x + 1
    return y


def spectral(signal, low=None, high=None, order=2):
    """Zero-phase Butterworth-shaped low/high-pass, applied in one FFT."""
    spectrum = np.fft.rfft(signal)
    f = np.fft.rfftfreq(len(signal), 1 / SR)
    gain = np.ones_like(f)
    if high is not None:
        gain /= np.sqrt(1 + (f / high) ** (2 * order))
    if low is not None:
        with np.errstate(divide="ignore"):
            gain /= np.sqrt(1 + (low / np.maximum(f, 1e-6)) ** (2 * order))
    return np.fft.irfft(spectrum * gain, len(signal))


def convolve(signal, kernel):
    size = 1 << (len(signal) + len(kernel) - 1).bit_length()
    out = np.fft.irfft(np.fft.rfft(signal, size) * np.fft.rfft(kernel, size), size)
    return out[: len(signal)]


def envelope(n, attack, release, sustain_end=None):
    """Linear attack, flat sustain, linear release over the last *release* s."""
    env = np.ones(n)
    a = max(1, int(attack * SR))
    env[:a] = np.linspace(0, 1, a)
    r = max(1, int(release * SR))
    env[-r:] *= np.linspace(1, 0, r)
    return env


class Track:
    """A stereo bus the instruments add into."""

    def __init__(self, n):
        self.left = np.zeros(n)
        self.right = np.zeros(n)

    def add(self, start, sound, pan=0.0, gain=1.0):
        """Add a mono or (left, right) sound at *start* seconds."""
        i = int(round(start * SR))
        if isinstance(sound, tuple):
            left, right = sound
        else:
            left = right = sound
        for bus, part, g in (
            (self.left, left, np.sqrt(0.5 * (1 - pan))),
            (self.right, right, np.sqrt(0.5 * (1 + pan))),
        ):
            if i >= len(bus):
                return
            j = min(len(bus), i + len(part))
            bus[i:j] += part[: j - i] * gain * g * np.sqrt(2)

    def stereo(self):
        return np.stack([self.left, self.right])


# -- instruments -------------------------------------------------------------------


def pad_note(freq, dur, rng):
    n = int(dur * SR)
    env = envelope(n, 0.35, 0.8)
    voices = []
    for detunes in ((-9, 0, 7), (-5, 3, 11)):
        v = sum(saw(freq * 2 ** (c / 1200), n, rng.random()) for c in detunes)
        voices.append(v * env / 3)
    return tuple(voices)


def bass_note(freq, dur):
    n = int(dur * SR)
    t = np.arange(n) / SR
    env = np.minimum(1, t / 0.004) * (0.55 + 0.45 * np.exp(-t / 0.09))
    env *= envelope(n, 0.0, 0.03)
    return (np.sin(2 * np.pi * freq * t) * 0.9 + saw(freq, n) * 0.35) * env


def pluck(freq, dur=0.3):
    n = int(dur * SR)
    t = np.arange(n) / SR
    env = np.minimum(1, t / 0.002) * np.exp(-t / 0.075)
    tone = saw(freq, n) * 0.6 + np.sign(np.sin(2 * np.pi * freq * 2.001 * t)) * 0.15
    return tone * env


def lead_note(freq, dur):
    n = int((dur + 0.25) * SR)
    t = np.arange(n) / SR
    vibrato = 1 + 0.004 * np.sin(2 * np.pi * 5.5 * t) * np.minimum(1, t / 0.3)
    phase = np.cumsum(freq * vibrato) / SR
    tone = (
        (2 * (phase % 1) - 1) * 0.5
        + (2 * ((phase * 1.0035) % 1) - 1) * 0.5
        + np.sin(np.pi * phase) * 0.6
    )
    env = np.minimum(1, t / 0.02) * (0.7 + 0.3 * np.exp(-t / 0.2))
    env *= np.clip((dur + 0.25 - t) / 0.25, 0, 1)
    return tone * env


def kick():
    n = int(0.45 * SR)
    t = np.arange(n) / SR
    freq = 46 + 120 * np.exp(-t / 0.032)
    body = np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t / 0.3)
    click = np.random.default_rng(1).standard_normal(n) * np.exp(-t / 0.0025) * 0.25
    return np.tanh((body + click) * 1.6)


def clap(rng):
    n = int(0.5 * SR)
    t = np.arange(n) / SR
    noise = spectral(rng.standard_normal(n), low=900, high=6000)
    env = np.exp(-t / 0.12)
    for offset in (0.0, 0.011, 0.022):
        env += 0.8 * (t >= offset) * np.exp(-np.maximum(t - offset, 0) / 0.006)
    tone = np.sin(2 * np.pi * 185 * t) * np.exp(-t / 0.05) * 0.5
    return (noise * 0.9 + tone) * env * 0.5


def hat(rng, decay=0.028):
    n = int(0.2 * SR)
    t = np.arange(n) / SR
    return spectral(rng.standard_normal(n), low=7000, order=3) * np.exp(-t / decay)


def crash(rng):
    n = int(2.5 * SR)
    t = np.arange(n) / SR
    noise = spectral(rng.standard_normal(n), low=4000, order=2)
    return noise * np.exp(-t / 0.7) * 0.6


def boom():
    n = int(2.0 * SR)
    t = np.arange(n) / SR
    freq = 38 + 60 * np.exp(-t / 0.08)
    return np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t / 0.6)


def riser(rng, dur):
    n = int(dur * SR)
    t = np.arange(n) / SR
    k = t / dur
    noise = spectral(rng.standard_normal(n), low=1500) * k**2.2
    sweep = np.sin(2 * np.pi * np.cumsum(220 * 2 ** (3.5 * k)) / SR) * k**3 * 0.3
    return (noise + sweep) * 0.5


def whoosh(rng, dur=1.0):
    """Swells up to its middle, where the cut is, then dies away."""
    n = int(dur * SR)
    k = np.arange(n) / n
    env = np.where(k < 0.5, (k / 0.5) ** 2.5, np.exp(-(k - 0.5) * 9))
    return spectral(rng.standard_normal(n), low=500, high=5000) * env * 0.5


def impulse_response(rng, seconds=2.4):
    n = int(seconds * SR)
    t = np.arange(n) / SR
    decay = np.exp(-t / 0.55)
    return tuple(
        spectral(rng.standard_normal(n), high=7000) * decay * 0.03 for _ in range(2)
    )


# -- arrangement -------------------------------------------------------------------


def render_soundtrack(timeline, path: Path):
    rng = np.random.default_rng(RNG_SEED)
    beat = 60 / timeline["bpm"]
    bar = beat * timeline["beatsPerBar"]
    duration = timeline["duration"]
    n = int(duration * SR) + SR

    pads, bass, arps, leads, drums, fx = (Track(n) for _ in range(6))
    kicks = []
    kick_sound, crash_sound, boom_sound = kick(), crash(rng), boom()
    clap_sound = clap(rng)
    hats = [hat(rng) for _ in range(4)]

    total_bars = round(duration / bar)
    modules_seen = 0
    for section in timeline["sections"]:
        kind = section["kind"]
        start = section["start"]
        bars = section["bars"]
        drum_bars = section.get("drumBars", 0)
        if kind == "module":
            modules_seen += 1
        pattern = ARP_PATTERNS[modules_seen % 2]

        if kind == "module" and modules_seen % 2 == 0:
            for at, note, held_beats in HOOK:
                leads.add(
                    start + at * beat,
                    lead_note(midi_hz(note), held_beats * beat),
                    gain=0.075,
                )

        if kind in ("module", "platform", "outro") and start > 0:
            fx.add(start - 0.5, whoosh(rng), gain=0.35 if kind == "module" else 0.5)
        if kind == "module" and modules_seen == 1 or kind == "outro":
            fx.add(start, crash_sound, gain=0.5)
            fx.add(start, boom_sound, gain=0.55)

        for b in range(bars):
            t0 = start + b * bar
            global_bar = round(t0 / bar)
            chord, root = PROGRESSION[global_bar % len(PROGRESSION)]
            last_bar = global_bar == total_bars - 1
            held = kind == "outro" and b >= bars - 2
            if held and b > bars - 2:
                continue  # the final chord of the tail is held from the bar before
            length = bar * (2 if held else 1) + 0.8

            # pads: darker in the intro, opening up as it builds
            for note in chord:
                pads.add(t0, pad_note(midi_hz(note), length, rng), gain=0.115)
            if held:
                pads.add(t0, pad_note(midi_hz(chord[0] - 12), length, rng), gain=0.09)

            drums_on = b < drum_bars
            # bass: pumping eighths under the drums, a held sub otherwise
            if drums_on:
                for e in range(8):
                    octave = 12 if e % 2 else 0
                    bass.add(
                        t0 + e * beat / 2,
                        bass_note(midi_hz(root + octave), beat / 2),
                        gain=0.27,
                    )
            elif kind != "intro" or b >= 2:
                sub = bass_note(midi_hz(root), bar * (2 if held else 1))
                bass.add(t0, sub * envelope(len(sub), 0.2, 0.5), gain=0.22)

            # arpeggio: sixteenths through the chord tones
            arp_on = not (held or last_bar) and not (kind == "intro" and b == 0)
            if arp_on:
                tones = [*chord, chord[0] + 12, chord[1] + 12]
                level = 0.17
                if kind == "intro":
                    level *= b / 3
                for s, idx in enumerate(pattern):
                    arps.add(
                        t0 + s * beat / 4,
                        pluck(midi_hz(tones[idx] + 12)),
                        pan=(-0.35 if s % 2 else 0.35),
                        gain=level,
                    )

            if drums_on:
                for q in range(4):
                    kicks.append(t0 + q * beat)
                    drums.add(t0 + q * beat, kick_sound, gain=0.85)
                    drums.add(t0 + (q + 0.5) * beat, hats[q], pan=0.2, gain=0.28)
                    if q % 2:
                        drums.add(t0 + q * beat, clap_sound, gain=0.55)
                    if modules_seen % 2 == 0 or kind == "outro":
                        for s in (1, 3):
                            drums.add(
                                t0 + (q + s / 4) * beat,
                                hats[(q + s) % 4],
                                pan=-0.25,
                                gain=0.12,
                            )

            # the bar before a drop builds up
            leads_to_drop = b == bars - 1 and kind in ("intro", "platform")
            if leads_to_drop:
                fx.add(t0, riser(rng, bar), gain=0.45)
                for s in range(16):
                    level = 0.1 + 0.5 * (s / 15) ** 2
                    drums.add(t0 + s * beat / 4, clap_sound, gain=level * 0.6)

    # sidechain: everything melodic ducks under each kick
    t = np.arange(n) / SR
    duck = np.ones(n)
    for k in kicks:
        i = int(k * SR)
        j = min(n, i + int(0.35 * SR))
        duck[i:j] = np.minimum(duck[i:j], 1 - 0.6 * np.exp(-(t[i:j] - k) / 0.1))

    stems = {
        "pads": spectral_stereo(pads.stereo(), high=2600),
        "bass": spectral_stereo(bass.stereo(), high=1400),
        "arps": spectral_stereo(arps.stereo(), high=4200),
        "leads": spectral_stereo(leads.stereo(), high=3800),
    }
    # the intro's pad opens from a muffled start
    intro = timeline["sections"][0]
    dark = spectral_stereo(pads.stereo(), high=500)
    open_up = np.clip((t - intro["start"]) / (intro["bars"] * bar), 0, 1) ** 1.5
    stems["pads"] = dark * (1 - open_up) + stems["pads"] * open_up

    arps_delayed = stems["arps"].copy()
    for tap in range(1, 5):
        d = int(beat * 0.75 * tap * SR)
        side = tap % 2
        arps_delayed[side, d:] += stems["arps"][side, :-d] * 0.42**tap
    stems["arps"] = arps_delayed

    melodic = (stems["pads"] + stems["arps"] + stems["bass"]) * duck + stems["leads"]
    dry = melodic + drums.stereo() + fx.stereo()

    ir = impulse_response(rng)
    send = (
        stems["pads"] * 0.5
        + stems["arps"] * 0.6
        + stems["leads"] * 0.45
        + drums.stereo() * 0.15
        + fx.stereo() * 0.5
    )
    wet = np.stack([convolve(send[0], ir[0]), convolve(send[1], ir[1])])
    master = dry + wet

    # the video fades to black over its last two seconds; the music with it
    fade_out = np.clip((duration - t) / 2.0, 0, 1)
    master *= fade_out
    master[:, : int(0.02 * SR)] *= np.linspace(0, 1, int(0.02 * SR))
    master = master[:, : int(duration * SR)]
    master /= np.max(np.abs(master)) + 1e-9
    master = np.tanh(master * 1.4) / np.tanh(1.4) * 0.93

    pcm = (master.T * 32767).astype("<i2")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(SR)
        out.writeframes(pcm.tobytes())


def spectral_stereo(stereo, **kwargs):
    return np.stack([spectral(ch, **kwargs) for ch in stereo])
