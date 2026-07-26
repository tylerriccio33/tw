#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow"]
# ///
"""Crop shots/play/play.png down to just the bottom HUD banner.

play_shot.sh captures the whole OS window (titlebar chrome + game content)
at whatever --resolution `make play` was launched with (RESOLUTION env var,
default 1280x800). The HUD banner (city panel/buildings row/end-turn ribbon,
see campaign_ui.gd) is anchored to roughly the bottom 40% of the content
area, so this trims off the titlebar and the map above it and writes
shots/play/hud.png - one crop to review just the HUD instead of eyeballing a
full 1280x800 window shot.
"""

import os
import sys
from pathlib import Path

from PIL import Image

SRC = Path("shots/play/play.png")
DST = Path("shots/play/hud.png")
# A hair above the topmost banner element's anchor_top (0.617, the city
# panel in campaign_ui.gd) so the crop isn't razored right against its edge.
BANNER_TOP_FRAC = 0.60


def main() -> int:
    if not SRC.exists():
        print(f"error: {SRC} not found - run `make play-shot` first", file=sys.stderr)
        return 1

    resolution = os.environ.get("RESOLUTION", "1280x800")
    try:
        content_height = int(resolution.split("x")[1])
    except (IndexError, ValueError):
        print(
            f"error: malformed RESOLUTION {resolution!r}, expected WxH", file=sys.stderr
        )
        return 1

    im = Image.open(SRC)
    chrome_height = im.height - content_height
    if chrome_height < 0:
        print(
            f"error: {SRC} ({im.height}px tall) is shorter than the content "
            f"height ({content_height}px) implied by RESOLUTION={resolution} - "
            "was play-shot run with a different RESOLUTION?",
            file=sys.stderr,
        )
        return 1

    top = chrome_height + int(content_height * BANNER_TOP_FRAC)
    im.crop((0, top, im.width, im.height)).save(DST)
    print(f"wrote {DST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
