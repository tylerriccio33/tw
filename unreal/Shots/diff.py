"""Compare unreal/Shots/current/ against golden/ and report what moved.

Run via `twctl diff` / `twctl bless` (or `uv run --with pillow python
unreal/Shots/diff.py [--bless]`). For each preset present in current/, prints the
mean per-pixel difference (0..255) and the percentage of pixels that moved by more
than a small threshold. Two identical renders diff to exactly 0.00 / 0.00%, so any
non-zero number is a real change, not renderer noise.

`--bless` copies current/ over golden/, accepting the new baseline.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageChops

HERE = Path(__file__).resolve().parent
CURRENT = HERE / "current"
GOLDEN = HERE / "golden"
_MOVED_THRESHOLD = 8  # per-channel delta that counts a pixel as "moved"


def _compare(a: Path, b: Path) -> tuple[float, float]:
    ia = Image.open(a).convert("RGB")
    ib = Image.open(b).convert("RGB")
    if ia.size != ib.size:
        raise ValueError(f"size mismatch {a.name}: {ia.size} vs {ib.size}")
    diff = ImageChops.difference(ia, ib)
    hist = diff.histogram()  # 3 x 256 (R,G,B)
    total = ia.size[0] * ia.size[1]
    # Mean absolute difference across all channels.
    weighted = sum(i % 256 * count for i, count in enumerate(hist))
    mean = weighted / (total * 3)
    # Fraction of pixels with any channel over the threshold.
    moved = 0
    gray = diff.convert("L")
    for count, value in zip(gray.histogram(), range(256)):
        if value > _MOVED_THRESHOLD:
            moved += count
    return mean, 100.0 * moved / total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bless", action="store_true", help="accept current/ as golden/")
    args = ap.parse_args()

    if args.bless:
        GOLDEN.mkdir(parents=True, exist_ok=True)
        n = 0
        for png in sorted(CURRENT.glob("*.png")):
            shutil.copy2(png, GOLDEN / png.name)
            n += 1
        print(f"blessed {n} shots into {GOLDEN}")
        return 0

    if not CURRENT.exists():
        print("no current/ shots — run `twctl shot` first")
        return 1

    worst = 0.0
    for png in sorted(CURRENT.glob("*.png")):
        golden = GOLDEN / png.name
        if not golden.exists():
            print(f"  {png.stem:10s}  NEW (no golden)")
            continue
        mean, moved = _compare(png, golden)
        worst = max(worst, moved)
        flag = "" if moved == 0 else "  <-- changed"
        print(f"  {png.stem:10s}  mean {mean:6.2f}  moved {moved:5.2f}%{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
