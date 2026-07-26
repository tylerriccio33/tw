#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow"]
# ///
"""Tile every preset in shots/current/ into one labelled contact sheet.

diff_shots.py's sheet only exists once there is a golden/ to compare against,
which is useless while a scene is still changing shape - there is nothing to
diff yet. This tiles whatever is in shots/current/ on its own, so one image
read shows every preset instead of opening overview/coast/ridge separately.

Exit codes: 0 on success, 1 if shots/current/ is empty or missing.
"""

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
CURRENT = ROOT / "shots" / "current"
SHEET = ROOT / "shots" / "sheet.png"

PANEL_W = 640
LABEL_H = 28
MAX_COLS = 3


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    if not CURRENT.is_dir():
        fail(f"no current shots at {CURRENT.relative_to(ROOT)} - run `make shot` first")

    shots = sorted(CURRENT.glob("*.png"))
    if not shots:
        fail(f"{CURRENT.relative_to(ROOT)} contains no PNGs - run `make shot` first")

    images = [(shot.stem, Image.open(shot).convert("RGB")) for shot in shots]

    cols = min(MAX_COLS, len(images))
    rows = math.ceil(len(images) / cols)

    sample = images[0][1]
    panel_h = round(PANEL_W * sample.height / sample.width)
    row_h = panel_h + LABEL_H

    sheet = Image.new("RGB", (PANEL_W * cols, row_h * rows), (18, 18, 20))
    draw = ImageDraw.Draw(sheet)

    for i, (name, img) in enumerate(images):
        col = i % cols
        row = i // cols
        x = col * PANEL_W
        y = row * row_h
        sheet.paste(img.resize((PANEL_W, panel_h), Image.LANCZOS), (x, y))
        draw.text((x + 8, y + panel_h + 7), name, fill=(220, 220, 225))

    SHEET.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(SHEET)
    print(f"wrote {SHEET.relative_to(ROOT)} ({len(images)} preset(s))")


if __name__ == "__main__":
    main()
