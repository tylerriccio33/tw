#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow", "opencv-python-headless", "numpy", "scikit-image"]
# ///
"""Golden-image regression gate for the campaign renderer.

`make render-shots` captures the deterministic post-boot campaign scene with
tools/shoot.gd (no OS window involved, see that file's docstring) to
shots/render_test/actual/campaign_boot.png. This script crops the HUD banner
out of that same capture. This needs no separate render: shoot.gd's output
has no OS chrome to subtract, unlike play_shot.sh/hud_shot.py. It then
SSIM-compares both images against tests/golden/*.png with a pass/fail
threshold.

tools/image_inspect.py's `compare` subcommand does the same SSIM math but
always exits 0 - it's a reporting tool, not a gate. This wraps the same
approach with a threshold and a nonzero exit, so it can sit in pre-commit.

Two subcommands:
    compare   SSIM-diff shots/render_test/actual/ against tests/golden/,
              fail (nonzero exit) if any pair drops below --threshold.
    update    Overwrite tests/golden/ with the current actual/ capture.
              Run this after an intended `compare` failure. Look at the
              diff images it just wrote before doing so.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from skimage.metrics import structural_similarity as ssim

ROOT = Path(__file__).resolve().parent.parent
ACTUAL_DIR = ROOT / "shots" / "render_test" / "actual"
DIFF_DIR = ROOT / "shots" / "render_test" / "diff"
GOLDEN_DIR = ROOT / "tests" / "golden"

BOOT_SHOT = "campaign_boot"
HUD_SHOT = "campaign_hud"
# Same crop line as tools/hud_shot.py's BANNER_TOP_FRAC - a hair above the
# city panel's anchor_top (0.617 in campaign_ui.gd) so the crop isn't
# razored right against its edge. shoot.gd's capture is exactly the
# viewport, with no OS titlebar to subtract first.
BANNER_TOP_FRAC = 0.60

DEFAULT_THRESHOLD = 0.97


def _make_hud_crop() -> None:
    boot_path = ACTUAL_DIR / f"{BOOT_SHOT}.png"
    if not boot_path.exists():
        return
    im = Image.open(boot_path)
    top = int(im.height * BANNER_TOP_FRAC)
    im.crop((0, top, im.width, im.height)).save(ACTUAL_DIR / f"{HUD_SHOT}.png")


def _ssim_score(before: Path, after: Path) -> tuple[float, np.ndarray]:
    before_img = cv2.cvtColor(cv2.imread(str(before)), cv2.COLOR_BGR2GRAY)
    after_img = cv2.cvtColor(cv2.imread(str(after)), cv2.COLOR_BGR2GRAY)
    if before_img.shape != after_img.shape:
        after_img = cv2.resize(after_img, (before_img.shape[1], before_img.shape[0]))
    score, diff = ssim(before_img, after_img, full=True)
    return float(score), diff


def cmd_compare(args: argparse.Namespace) -> int:
    _make_hud_crop()
    DIFF_DIR.mkdir(parents=True, exist_ok=True)

    ok = True
    for name in (BOOT_SHOT, HUD_SHOT):
        golden = GOLDEN_DIR / f"{name}.png"
        actual = ACTUAL_DIR / f"{name}.png"
        if not actual.exists():
            print(f"FAIL {name}: no capture at {actual} - did `make render-shots` run?")
            ok = False
            continue
        if not golden.exists():
            print(
                f"FAIL {name}: no baseline at {golden} - run `make render-test-update` "
                "once you've reviewed the capture and want to accept it as the baseline"
            )
            ok = False
            continue

        score, diff = _ssim_score(golden, actual)
        passed = score >= args.threshold
        ok = ok and passed
        status = "PASS" if passed else "FAIL"
        print(f"{status} {name}: ssim={score:.4f} (threshold {args.threshold})")

        if not passed:
            diff_img = ((1 - diff) * 255).astype(np.uint8)
            heatmap = cv2.applyColorMap(diff_img, cv2.COLORMAP_JET)
            diff_path = DIFF_DIR / f"{name}.png"
            cv2.imwrite(str(diff_path), heatmap)
            print(f"     wrote diff heatmap to {diff_path}")

    return 0 if ok else 1


def cmd_update(_args: argparse.Namespace) -> int:
    _make_hud_crop()
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for name in (BOOT_SHOT, HUD_SHOT):
        actual = ACTUAL_DIR / f"{name}.png"
        if not actual.exists():
            print(
                f"error: no capture at {actual} - run `make render-shots` first",
                file=sys.stderr,
            )
            return 1
        shutil.copyfile(actual, GOLDEN_DIR / f"{name}.png")
        print(f"wrote {GOLDEN_DIR / f'{name}.png'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "compare", help="SSIM-gate the current capture against tests/golden/"
    )
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser(
        "update", help="overwrite tests/golden/ with the current capture"
    )
    p.set_defaults(func=cmd_update)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
