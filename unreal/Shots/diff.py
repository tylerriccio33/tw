"""Compare the preset shots in current/ against golden/.

This is step 3 of the visual loop. A screenshot on its own answers "does it
render"; it does not answer "did that change do what I meant, and only that".
Two numbers per preset do:

  mean   average per-channel difference, 0-255. The overall shift — a tint, an
         exposure change, a fog tweak. Small and non-zero is a nudge.
  moved  the fraction of pixels that changed by more than a threshold. Structure
         rather than tone: geometry, a border line, a marker appearing.

Both matter, and they separate the two failure modes worth catching. A shader
edit meant to change the snow line should light up `mountain` and leave
`lowlands` near zero; if it moves all five by the same mean, it was a global
tint and not the local change it was supposed to be.

Deliberately dependency-light and run through `uv run --with pillow`, so there
is nothing to install and nothing added to sim/'s lockfile for a dev tool.
"""

import argparse
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageChops

SHOTS = Path(__file__).parent
CURRENT = SHOTS / "current"
GOLDEN = SHOTS / "golden"

# A pixel counts as "moved" past this per-channel difference. Below it is
# dithering, temporal AA jitter and fog noise, which are not a real change --
# a shot compared against itself across two runs sits well under this.
MOVED_THRESHOLD = 12

# Report a shot as changed past these. Chosen to be quiet about renderer noise
# and loud about anything a person would notice on screen.
MEAN_TOLERANCE = 1.0
MOVED_TOLERANCE = 0.002


def compare(a: Path, b: Path) -> tuple[float, float]:
    """Return (mean difference 0-255, fraction of pixels past the threshold)."""
    with Image.open(a) as ia, Image.open(b) as ib:
        ia = ia.convert("RGB")
        ib = ib.convert("RGB")
        if ia.size != ib.size:
            raise ValueError(f"size differs: {ia.size} vs {ib.size}")
        delta = ImageChops.difference(ia, ib)
        # Collapse the three channels to the largest per-pixel difference, so a
        # pure-red change is not diluted to a third of its magnitude.
        channels = delta.split()
        worst = channels[0]
        for channel in channels[1:]:
            worst = ImageChops.lighter(worst, channel)

        histogram = worst.histogram()
        total = sum(histogram)
        mean = sum(value * count for value, count in enumerate(histogram)) / total
        moved = sum(histogram[MOVED_THRESHOLD:]) / total
        return mean, moved


def bless() -> int:
    shots = sorted(CURRENT.glob("*.png"))
    if not shots:
        print(f"nothing in {CURRENT} to bless — run `make unreal-shots` first")
        return 1
    GOLDEN.mkdir(parents=True, exist_ok=True)
    for shot in shots:
        shutil.copy2(shot, GOLDEN / shot.name)
        print(f"  blessed {shot.name}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bless",
        action="store_true",
        help="accept current/ as the new golden/ — do this only with a "
        "deliberate visual change in hand",
    )
    args = parser.parse_args()
    if args.bless:
        return bless()

    shots = sorted(CURRENT.glob("*.png"))
    if not shots:
        print(f"no shots in {CURRENT} — run `make unreal-shots` first")
        return 1

    changed = False
    missing_golden = []
    print(f"{'shot':<12} {'mean':>7} {'moved':>8}")
    for shot in shots:
        reference = GOLDEN / shot.name
        if not reference.exists():
            missing_golden.append(shot.name)
            continue
        try:
            mean, moved = compare(reference, shot)
        except ValueError as error:
            print(f"{shot.stem:<12} {error}")
            changed = True
            continue
        flag = ""
        if mean > MEAN_TOLERANCE or moved > MOVED_TOLERANCE:
            flag = "  <-- changed"
            changed = True
        print(f"{shot.stem:<12} {mean:>7.2f} {moved:>7.2%}{flag}")

    for name in missing_golden:
        print(f"{Path(name).stem:<12} (no golden — `make shots-bless` to record it)")

    if changed:
        print("\nlook at the changed pairs before blessing:")
        print(f"  {CURRENT}")
        print(f"  {GOLDEN}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
