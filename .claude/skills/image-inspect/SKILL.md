---
name: image-inspect
description: Analyze a screenshot/image with classical computer vision instead of eyeballing it or guessing. Use whenever asked to check blur/sharpness, measure padding or borders, compare before/after screenshots for regressions, check alignment, or find dominant colors — "why is this border off", "did this screenshot regress", "is this blurry", "measure the padding", "is the text aligned". Wraps tools/image_inspect.py, a standalone uv CLI over OpenCV/scikit-image.
---

Get structured measurements from an image instead of guessing from a Read-tool glance. `tools/image_inspect.py` is a standalone `uv run --script` CLI (own inline deps: opencv-python-headless, numpy, scikit-image — no project venv needed) that prints JSON to stdout.

## When to reach for this

Any "why does this look wrong" question about a UI/map screenshot is usually one of these measurements, not a fresh visual guess:

| Question | Subcommand |
| --- | --- |
| "Is this blurry?" | `blur` |
| "Why is this border/padding off?" | `border` |
| "Did this screenshot regress?" | `compare` |
| "Is there visible structure/noise here?" | `edges` |
| "Is this text/panel aligned?" | `lines` |
| "What's the dominant color?" | `histogram` |
| "What are this image's dimensions?" | `info` |

## Usage

```
uv run tools/image_inspect.py <subcommand> <image> [options]
```

- `blur <image>` — variance-of-Laplacian sharpness score. >1000 sharp, 300-1000 mildly blurred, <300 blurry.
- `border <image>` — largest outer contour's bounding box plus left/right/top/bottom padding to the image edges.
- `compare <before> <after> [--diff-out diff.png]` — SSIM score (>0.995 identical, >0.95 minor diffs, else significant) and changed-pixel count/percent. `--diff-out` writes a heatmap PNG you can then Read.
- `edges <image> [--low --high]` — Canny edge pixel count/density.
- `lines <image> [--threshold --min-length --max-report]` — Hough line segments with angles, plus horizontal/vertical counts, for checking rows/columns line up.
- `histogram <image>` — per-channel (BGR) mean + top-5 dominant colors via k-means, each with hex and area %.
- `info <image>` — width, height, channels, file size.

All output is JSON — parse it directly rather than re-deriving numbers by eye. For anything the JSON doesn't resolve (does this look intentional?), still Read the image after running the numbers.

## Example: "why does this card look misaligned?"

1. `border shots/play/hud.png` → padding numbers for the outer element.
2. `lines shots/play/hud.png` → horizontal/vertical line positions to check row/column alignment.
3. Report concrete deltas ("left padding 14px vs right padding 22px") instead of "it looks slightly off."

## Notes

- No project venv needed — `uv run` resolves the script's own inline dependencies on first use (downloads scikit-image/networkx once, cached after).
- Implementation: `tools/image_inspect.py`.
