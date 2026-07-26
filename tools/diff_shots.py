#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow", "numpy"]
# ///
"""Compare shots/current against shots/golden.

Prints a per-preset PSNR table and writes a contact sheet
(shots/contact_sheet.png) laying out golden | current | amplified difference
for every preset, so a single image read tells you what moved.

Exit codes: 0 identical, 2 differences found, 1 harness problem (missing
files, size mismatch). Differences are not failures - they are usually the
point - but they are distinguishable from breakage.
"""

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
CURRENT = ROOT / "shots" / "current"
GOLDEN = ROOT / "shots" / "golden"
SHEET = ROOT / "shots" / "contact_sheet.png"

# Each row of the sheet is one preset, downscaled to this width per panel.
PANEL_W = 640
LABEL_H = 28
# Difference panel gain. Raw deltas on a subtle lighting change are only a few
# LSBs and read as pure black without amplification.
DIFF_GAIN = 8.0


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2)
    if mse == 0:
        return float("inf")
    return 10.0 * np.log10((255.0**2) / mse)


def compare_dirs(dir_a: Path, dir_b: Path, min_psnr: float) -> None:
    """Gate two render directories on a PSNR floor instead of byte equality.

    `make accept` used to render twice and require the two runs to be
    byte-identical. That was a genuinely good check - it caught anything
    reading an unseeded RNG or the wall clock - but TAA, SDFGI and volumetric
    fog are all temporally accumulated and will not reproduce bit-exactly in a
    fixed frame budget, so the strict form now fails on correct renders.

    A PSNR floor keeps most of the value: temporal jitter between two runs of
    the same config lands far above it, while a real nondeterminism bug (an
    unseeded seed, a wall-clock-driven wave phase) moves whole regions of the
    image and craters the score well below.
    """
    shots_a = sorted(dir_a.glob("*.png"))
    if not shots_a:
        fail(f"{dir_a} contains no PNGs")

    worst = float("inf")
    for shot in shots_a:
        other = dir_b / shot.name
        if not other.is_file():
            fail(f"{shot.name}: missing from {dir_b}")

        img_a = Image.open(shot).convert("RGB")
        img_b = Image.open(other).convert("RGB")
        if img_a.size != img_b.size:
            fail(f"{shot.name}: size mismatch {img_a.size} vs {img_b.size}")

        score = psnr(np.asarray(img_a), np.asarray(img_b))
        label = "identical" if score == float("inf") else f"{score:.2f}dB"
        print(f"  {shot.stem}: {label}")
        worst = min(worst, score)

    if worst < min_psnr:
        print(
            f"error: two consecutive renders of the same config differ by more than "
            f"the {min_psnr:.1f}dB floor (worst {worst:.2f}dB).\n"
            f"Temporal accumulation (TAA/SDFGI/volumetric fog) alone should stay well "
            f"above this - a score this low means something is reading an unseeded "
            f"input (wall clock, uninitialized RNG, etc).",
            file=sys.stderr,
        )
        sys.exit(1)

    if worst == float("inf"):
        print("renders were byte-identical")
    else:
        print(f"renders agree to {worst:.2f}dB (floor {min_psnr:.1f}dB)")


def main() -> None:
    # `--compare A B --min-psnr N` is the gate `make accept` uses; with no
    # arguments this stays the current-vs-golden review tool it has always been.
    argv = sys.argv[1:]
    if argv and argv[0] == "--compare":
        if len(argv) < 3:
            fail("--compare needs two directories")
        min_psnr = 45.0
        if "--min-psnr" in argv:
            min_psnr = float(argv[argv.index("--min-psnr") + 1])
        compare_dirs(Path(argv[1]), Path(argv[2]), min_psnr)
        return

    if not CURRENT.is_dir():
        fail(f"no current shots at {CURRENT.relative_to(ROOT)} - run `make shot` first")

    current_shots = sorted(CURRENT.glob("*.png"))
    if not current_shots:
        fail(f"{CURRENT.relative_to(ROOT)} contains no PNGs - run `make shot` first")

    if not GOLDEN.is_dir() or not any(GOLDEN.glob("*.png")):
        print(
            f"no golden shots yet. Review {CURRENT.relative_to(ROOT)} by eye, "
            f"then run `make accept` to promote them."
        )
        sys.exit(0)

    rows = []
    results = []
    problems = []

    for shot in current_shots:
        golden = GOLDEN / shot.name
        if not golden.is_file():
            problems.append(f"{shot.name}: no golden counterpart (new preset?)")
            continue

        cur_img = Image.open(shot).convert("RGB")
        gold_img = Image.open(golden).convert("RGB")
        if cur_img.size != gold_img.size:
            problems.append(
                f"{shot.name}: size changed {gold_img.size} -> {cur_img.size}; "
                f"check `resolution` in config/shots.json"
            )
            continue

        cur = np.asarray(cur_img)
        gold = np.asarray(gold_img)
        score = psnr(gold, cur)
        changed_px = int(np.count_nonzero(np.any(gold != cur, axis=2)))
        pct = 100.0 * changed_px / (cur.shape[0] * cur.shape[1])
        results.append((shot.stem, score, pct))

        delta = np.abs(gold.astype(np.float32) - cur.astype(np.float32))
        delta = np.clip(delta * DIFF_GAIN, 0, 255).astype(np.uint8)
        rows.append((shot.stem, gold_img, cur_img, Image.fromarray(delta)))

    if problems:
        for p in problems:
            print(f"error: {p}", file=sys.stderr)
        sys.exit(1)

    name_w = max(len(n) for n, _, _ in results)
    print(f"{'preset'.ljust(name_w)}  {'PSNR':>9}  {'changed':>8}")
    for name, score, pct in results:
        label = "identical" if score == float("inf") else f"{score:8.2f}dB"
        print(f"{name.ljust(name_w)}  {label:>9}  {pct:7.3f}%")

    _write_sheet(rows)
    print(f"\ncontact sheet: {SHEET.relative_to(ROOT)}")

    if all(score == float("inf") for _, score, _ in results):
        print("all presets identical to golden")
        sys.exit(0)
    sys.exit(2)


def _write_sheet(rows) -> None:
    if not rows:
        return
    sample = rows[0][1]
    panel_h = round(PANEL_W * sample.height / sample.width)
    row_h = panel_h + LABEL_H
    sheet = Image.new("RGB", (PANEL_W * 3, row_h * len(rows)), (18, 18, 20))
    draw = ImageDraw.Draw(sheet)

    for i, (name, gold, cur, delta) in enumerate(rows):
        y = i * row_h
        for j, (img, tag) in enumerate(
            ((gold, "golden"), (cur, "current"), (delta, f"diff x{DIFF_GAIN:g}"))
        ):
            sheet.paste(img.resize((PANEL_W, panel_h), Image.LANCZOS), (j * PANEL_W, y))
            draw.text(
                (j * PANEL_W + 8, y + panel_h + 7),
                f"{name} - {tag}",
                fill=(220, 220, 225),
            )

    SHEET.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(SHEET)


if __name__ == "__main__":
    main()
