"""Illustrated landscapes for the demo photo library.

Deterministic and generated with numpy: a sky gradient, a sun or a moon with
its glow, stars or an aurora, layered ridges fading into haze, and water
mirroring it all. They stand in for holiday photos in the screenshots and the
promo video without shipping binary files or licensed images.
"""

import io

import numpy as np

WIDTH, HEIGHT = 1200, 900


def _rgb(value):
    return np.array([int(value[i : i + 2], 16) for i in (1, 3, 5)], dtype=float)


# Each scene: sky stops top to horizon, the horizon (fraction of the height),
# an optional sun/moon (x, y, radius, colour, glow), ridge layers far to near,
# and extras. Ridge styles: soft hills, sharp peaks, dunes, mesas, trees,
# city, cone (a lone volcano).
SCENES = {
    "alpine-morning": {
        "sky": ["#3b82f6", "#93c5fd", "#e0f2fe"],
        "horizon": 0.62,
        "sun": (0.74, 0.22, 34, "#fffbeb", 0.55),
        "ridges": ("sharp", ["#a5b4fc", "#64748b", "#334155", "#0f172a"]),
        "snow": True,
        "water": 0.74,
    },
    "desert-dusk": {
        "sky": ["#1e1b4b", "#9d174d", "#fb923c", "#fde68a"],
        "horizon": 0.64,
        "sun": (0.3, 0.6, 62, "#fff7ed", 0.9),
        "ridges": ("dunes", ["#ea580c", "#c2410c", "#9a3412", "#431407"]),
    },
    "northern-lights": {
        "sky": ["#020617", "#0b1d3a", "#134e4a"],
        "horizon": 0.66,
        "stars": 260,
        "aurora": True,
        "ridges": ("trees", ["#0f2a2a", "#081a1c", "#030b0d"]),
        "water": 0.8,
    },
    "ocean-sunset": {
        "sky": ["#312e81", "#be185d", "#fb7185", "#fed7aa"],
        "horizon": 0.6,
        "sun": (0.52, 0.58, 58, "#fff1d6", 1.0),
        "ridges": ("soft", ["#4c0519"]),
        "ridge_span": (0.62, 0.9),
        "water": 0.6,
    },
    "misty-forest": {
        "sky": ["#99f6e4", "#d1fae5", "#f0fdf4"],
        "horizon": 0.5,
        "sun": (0.62, 0.3, 40, "#ffffff", 0.45),
        "ridges": ("trees", ["#99d5b8", "#5fae8c", "#2f7a5d", "#14533d", "#052e22"]),
    },
    "canyon-light": {
        "sky": ["#0ea5e9", "#7dd3fc", "#fef3c7"],
        "horizon": 0.55,
        "sun": (0.18, 0.2, 30, "#ffffff", 0.5),
        "ridges": ("mesa", ["#fdba74", "#f97316", "#c2410c", "#7c2d12"]),
    },
    "city-at-night": {
        "sky": ["#0b1026", "#312e81", "#7c3aed", "#f0abfc"],
        "horizon": 0.66,
        "sun": (0.8, 0.2, 26, "#fef9c3", 0.35),
        "stars": 120,
        "ridges": ("city", ["#1e1b4b", "#0b0a1f"]),
        "water": 0.72,
    },
    "fuji-dawn": {
        "sky": ["#a78bfa", "#f9a8d4", "#fde68a"],
        "horizon": 0.6,
        "sun": (0.24, 0.42, 36, "#fff7ed", 0.6),
        "ridges": ("cone", ["#6d5b97", "#3b2f5c"]),
        "snow": True,
        "water": 0.74,
    },
    "starry-hills": {
        "sky": ["#0b0820", "#312e81", "#6d28d9"],
        "horizon": 0.66,
        "sun": (0.72, 0.24, 30, "#e0e7ff", 0.4),
        "stars": 320,
        "ridges": ("soft", ["#3b2a7a", "#241a55", "#120c33", "#07051a"]),
    },
    "tropical-lagoon": {
        "sky": ["#0284c7", "#38bdf8", "#e0f2fe"],
        "horizon": 0.56,
        "sun": (0.78, 0.16, 38, "#ffffff", 0.55),
        "ridges": ("soft", ["#22c55e", "#15803d", "#14532d"]),
        "ridge_span": (0.3, 0.6),
        "water": 0.56,
        "water_tint": "#14b8a6",
    },
    "autumn-valley": {
        "sky": ["#f59e0b", "#fcd34d", "#fef3c7"],
        "horizon": 0.52,
        "sun": (0.5, 0.42, 48, "#fffbeb", 0.8),
        "ridges": ("soft", ["#fdba74", "#f97316", "#c2410c", "#7c2d12", "#431407"]),
    },
    "glacier-bay": {
        "sky": ["#bae6fd", "#e0f2fe", "#f8fafc"],
        "horizon": 0.56,
        "ridges": ("sharp", ["#bfdbfe", "#7dd3fc", "#0369a1", "#0c4a6e"]),
        "snow": True,
        "water": 0.72,
        "water_tint": "#0e7490",
    },
}


def _noise(rng, octaves, frequency):
    """Smooth 1D value noise across the width, in [0, 1]."""
    x = np.linspace(0, 1, WIDTH)
    out = np.zeros(WIDTH)
    amplitude = total = 1.0
    total = 0.0
    for _ in range(octaves):
        points = rng.random(int(frequency) + 2)
        position = x * frequency
        i = np.floor(position).astype(int)
        t = position - i
        t = t * t * (3 - 2 * t)
        out += amplitude * (points[i] * (1 - t) + points[i + 1] * t)
        total += amplitude
        amplitude *= 0.5
        frequency *= 2
    return out / total


def _profile(style, rng, layer, layers, top, bottom):
    """Ridge line (y in pixels per column) for one layer."""
    depth = layer / max(1, layers - 1)
    base = top + (bottom - top) * (0.15 + 0.7 * depth)
    height = (bottom - top) * (0.9 - 0.45 * depth)
    x = np.linspace(0, 1, WIDTH)
    if style == "sharp":
        n = 1 - np.abs(2 * _noise(rng, 5, 3 + layer) - 1)
        return base - height * n**1.6
    if style == "dunes":
        phase = rng.random() * 6
        n = (
            0.5
            + 0.35 * np.sin(x * (3 + layer) * np.pi + phase)
            + 0.15 * _noise(rng, 3, 4)
        )
        return base - height * 0.5 * n
    if style == "mesa":
        n = np.clip((_noise(rng, 3, 3 + layer) - 0.45) * 5, 0, 1)
        return base - height * 0.7 * n
    if style == "trees":
        hills = base - height * 0.4 * _noise(rng, 3, 2 + layer)
        spacing = 14 + 10 * (1 - depth)
        saw = np.abs(((x * WIDTH / spacing) % 1) - 0.5) * 2
        tall = np.repeat(rng.random(int(WIDTH / spacing) + 2), int(np.ceil(spacing)))[
            :WIDTH
        ]
        return hills - (1 - saw) * spacing * (1.4 + 1.6 * tall)
    if style == "city":
        profile = np.full(WIDTH, base)
        column = 0
        while column < WIDTH:
            width = int(rng.integers(26, 70))
            profile[column : column + width] = base - height * (
                0.2 + 0.8 * rng.random() ** 1.5
            )
            column += width + int(rng.integers(0, 6))
        return profile
    if style == "cone":
        if layer == 0:
            peak = 0.58
            return (
                base - height * 0.95 * np.clip(1 - np.abs(x - peak) * 2.2, 0, 1) ** 1.25
            )
        return base - height * 0.25 * _noise(rng, 3, 3)
    hills = np.clip((_noise(rng, 4, 2 + layer) - 0.5) * 1.9 + 0.5, 0, 1)
    return base - height * 0.55 * hills  # soft hills


def _sky(scene):
    stops = [_rgb(c) for c in scene["sky"]]
    horizon = scene["horizon"] * HEIGHT
    y = np.clip(np.arange(HEIGHT) / horizon, 0, 1) * (len(stops) - 1)
    i = np.minimum(y.astype(int), len(stops) - 2)
    t = (y - i)[:, None]
    column = np.array(stops)[i] * (1 - t) + np.array(stops)[i + 1] * t
    return np.repeat(column[:, None, :], WIDTH, axis=1)


def render(name, seed=0):
    """The landscape *name* as an RGB uint8 array."""
    scene = SCENES[name]
    rng = np.random.default_rng(seed + sum(map(ord, name)))
    image = _sky(scene)
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]

    stars = scene.get("stars", 0)
    if stars:
        sx = rng.integers(0, WIDTH, stars)
        sy = (rng.random(stars) ** 1.6 * scene["horizon"] * HEIGHT * 0.9).astype(int)
        glow = rng.random(stars) * 0.8 + 0.2
        for x, y, g in zip(sx, sy, glow, strict=True):
            image[y, x] = image[y, x] * (1 - g) + 255 * g
            if g > 0.8:
                image[max(0, y - 1) : y + 2, max(0, x - 1) : x + 2] += 40

    if scene.get("aurora"):
        x = np.linspace(0, 1, WIDTH)
        for k, (color, offset) in enumerate(
            ((("#34d399"), 0.18), ("#22d3ee", 0.26), ("#a78bfa", 0.12))
        ):
            center = HEIGHT * (
                offset + 0.07 * np.sin(x * 4 + k) + 0.025 * np.sin(x * 11 + k * 2)
            )
            rise = (center[None, :] - yy).astype(float)
            # Rays climb far above the curtain's lower edge and end sharply below it.
            band = np.where(
                rise > 0, np.exp(-rise / (120 + 30 * k)), np.exp(-((rise / 14) ** 2))
            )
            rays = (
                0.55
                + 0.45 * np.sin(xx * 0.11 + k * 1.7) ** 2 * np.sin(xx * 0.017 + k) ** 2
            )
            image += (band * rays * 0.5)[..., None] * _rgb(color)[None, None, :]

    if "sun" in scene:
        sx, sy, radius, color, strength = scene["sun"]
        distance = np.hypot(xx - sx * WIDTH, yy - sy * HEIGHT)
        halo = np.exp(-distance / (radius * 3.2)) * strength
        image = (
            image * (1 - halo[..., None] * 0.35) + halo[..., None] * _rgb(color) * 0.35
        )
        disk = np.clip(radius - distance + 1, 0, 1)[..., None]
        image = image * (1 - disk) + _rgb(color) * disk

    style, colors = scene["ridges"]
    span = scene.get("ridge_span", (scene["horizon"] * 0.55, scene["horizon"] + 0.12))
    top, bottom = span[0] * HEIGHT, span[1] * HEIGHT
    haze = _rgb(scene["sky"][-1])
    for layer, color in enumerate(colors):
        profile = _profile(style, rng, layer, len(colors), top, bottom)
        alpha = np.clip(yy - profile[None, :] + 0.5, 0, 1)[..., None]
        depth = layer / max(1, len(colors) - 1)
        tone = _rgb(color) * (0.55 + 0.45 * depth) + haze * (0.45 - 0.45 * depth)
        # Shade on the absolute height, not from each column's own ridge
        # line: per-column shading streaks every cliff vertically.
        shade = np.clip((yy - top) / (HEIGHT - top) * 0.45, 0, 0.45)[..., None]
        fill = tone * (1 - shade)
        if scene.get("snow") and layer == 0 and style in ("sharp", "cone"):
            peak, foot = profile.min(), profile.max()
            snowline = peak + (foot - peak) * 0.28 + 22 * (_noise(rng, 4, 9) - 0.5)
            cover = np.clip((snowline[None, :] - yy) / 10, 0, 1)[..., None]
            fill = fill * (1 - cover) + (fill * 0.12 + 232) * cover
        if style == "city":
            lit = (
                (xx % 14 < 5)
                & (yy % 18 < 7)
                & (yy > profile[None, :] + 8)
                & (rng.random((HEIGHT, WIDTH)) < 0.35)
            )[..., None]
            fill = np.where(
                lit, np.array([253.0, 224.0, 71.0]) * (0.5 + 0.5 * depth), fill
            )
        image = image * (1 - alpha) + fill * alpha

    water = scene.get("water")
    if water:
        line = int(water * HEIGHT)
        rows = np.arange(line, HEIGHT)
        source = np.clip(2 * line - rows, 0, line - 1)
        ripple = (np.sin(rows * 0.9) * 3 + np.sin(rows * 0.23) * 5).astype(int)
        mirrored = np.stack(
            [np.roll(image[s], r, axis=0) for s, r in zip(source, ripple, strict=True)]
        )
        tint = _rgb(scene.get("water_tint", "#0f172a"))
        fade = np.linspace(0.25, 0.6, len(rows))[:, None, None]
        reflection = mirrored * (1 - fade) + tint * fade
        glints = ((np.arange(WIDTH)[None, :] + rows[:, None] * 7) % 97 < 2) & (
            rows[:, None] % 6 == 0
        )
        reflection = np.where(glints[..., None], reflection * 0.6 + 100, reflection)
        image[line:] = reflection

    vignette = 1 - 0.28 * (((xx / WIDTH - 0.5) ** 2 + (yy / HEIGHT - 0.5) ** 2) * 2.2)
    image = image * vignette[..., None] + rng.normal(0, 3.5, image.shape)
    return np.clip(image, 0, 255).astype(np.uint8)


def jpeg(name, taken_at, seed=0):
    """*name* as a JPEG whose EXIF capture time is *taken_at*."""
    from PIL import ExifTags, Image

    exif = Image.Exif()
    exif.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.DateTimeOriginal] = taken_at.strftime(
        "%Y:%m:%d %H:%M:%S"
    )
    buf = io.BytesIO()
    Image.fromarray(render(name, seed)).save(buf, format="JPEG", quality=88, exif=exif)
    return buf.getvalue()
