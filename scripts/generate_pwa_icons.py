"""One-shot script to generate PWA icons for Workspace.

Reproduces the app logo: rounded square with a primary->secondary gradient,
white Lucide "users" icon centered.

Usage: npm install @resvg/resvg-js && python scripts/generate_pwa_icons.py

Outputs:
  workspace/common/static/icons/icon-192.png
  workspace/common/static/icons/icon-512.png
  workspace/common/static/icons/badge-72.png
"""

import json
import subprocess
from pathlib import Path


OUTPUT_DIR = Path(__file__).resolve().parent.parent / "workspace" / "common" / "static" / "icons"
SCRIPT_DIR = Path(__file__).resolve().parent

# Lucide "users" icon paths (from lucide-static)
USERS_PATHS = (
    '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />'
    '<path d="M16 3.128a4 4 0 0 1 0 7.744" />'
    '<path d="M22 21v-2a4 4 0 0 0-3-3.87" />'
    '<circle cx="9" cy="7" r="4" />'
)


def build_app_icon_svg(size):
    """Build SVG string for an app icon at the given size."""
    padding = size * 0.22
    icon_size = size - 2 * padding
    scale = icon_size / 24
    stroke_w = 2  # Lucide default, scales proportionally with icon
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#6419E6"/>
      <stop offset="100%" stop-color="#D926A9"/>
    </linearGradient>
  </defs>
  <rect width="{size}" height="{size}" rx="{size * 0.22:.0f}" fill="url(#g)"/>
  <g transform="translate({padding:.1f},{padding:.1f}) scale({scale:.4f})"
     stroke="white" stroke-width="{stroke_w:.2f}" stroke-linecap="round" stroke-linejoin="round" fill="none">
    {USERS_PATHS}
  </g>
</svg>"""


def build_badge_svg(size):
    """Build SVG string for a monochrome badge (white on transparent)."""
    padding = size * 0.1
    icon_size = size - 2 * padding
    scale = icon_size / 24
    stroke_w = 2.5
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">
  <g transform="translate({padding:.1f},{padding:.1f}) scale({scale:.4f})"
     stroke="white" stroke-width="{stroke_w:.2f}" stroke-linecap="round" stroke-linejoin="round" fill="none">
    {USERS_PATHS}
  </g>
</svg>"""


def render_svgs(icons):
    """Render multiple SVGs to PNGs using @resvg/resvg-js via Node.js."""
    # Build a small Node script that renders all SVGs at once
    node_script = """
const { Resvg } = require('@resvg/resvg-js');
const fs = require('fs');
const icons = JSON.parse(process.argv[1]);
for (const { svg, output, size } of icons) {
    const resvg = new Resvg(svg, { fitTo: { mode: 'width', value: size } });
    const png = resvg.render().asPng();
    fs.writeFileSync(output, png);
}
"""
    payload = json.dumps([
        {"svg": svg, "output": str(out), "size": size}
        for svg, out, size in icons
    ])
    result = subprocess.run(
        ["node", "-e", node_script, payload],
        capture_output=True,
        text=True,
        cwd=str(SCRIPT_DIR),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Node render failed:\n{result.stderr}")


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    icons = [
        (build_app_icon_svg(192), OUTPUT_DIR / "icon-192.png", 192),
        (build_app_icon_svg(512), OUTPUT_DIR / "icon-512.png", 512),
        (build_badge_svg(72), OUTPUT_DIR / "badge-72.png", 72),
    ]

    print("Generating PWA icons...")
    render_svgs(icons)
    for _, path, size in icons:
        print(f"  Created {path} ({size}x{size})")
    print("Done.")


if __name__ == "__main__":
    main()
