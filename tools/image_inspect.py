#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["opencv-python-headless", "numpy", "scikit-image"]
# ///
"""Classical CV inspection for UI/map screenshots - no VLM needed.

Structured measurements instead of eyeballing a screenshot. Run `--help`
on any subcommand for its options.

Subcommands: blur, border, compare, edges, lines, histogram, info.
See `--help` or the image-inspect skill for details on each.

Example:
  uv run tools/image_inspect.py blur shots/play/hud.png
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np


def load_gray(path: Path) -> np.ndarray:
    img = cv2.imread(str(path))
    if img is None:
        raise FileNotFoundError(f"could not read image: {path}")
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def load_color(path: Path) -> np.ndarray:
    img = cv2.imread(str(path))
    if img is None:
        raise FileNotFoundError(f"could not read image: {path}")
    return img


def emit(result: dict) -> None:
    print(json.dumps(result, indent=2))


def cmd_blur(args: argparse.Namespace) -> int:
    gray = load_gray(args.image)
    variance = cv2.Laplacian(gray, cv2.CV_64F).var()
    if variance > 1000:
        interpretation = "sharp"
    elif variance > 300:
        interpretation = "mildly blurred"
    else:
        interpretation = "blurry"
    emit({"blur_variance": round(variance, 2), "interpretation": interpretation})
    return 0


def cmd_border(args: argparse.Namespace) -> int:
    img = load_color(args.image)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        emit({"error": "no contours found"})
        return 1

    h, w = gray.shape
    largest = max(contours, key=cv2.contourArea)
    x, y, cw, ch = cv2.boundingRect(largest)

    emit(
        {
            "image_size": {"width": w, "height": h},
            "outer_contour_bbox": {"x": x, "y": y, "width": cw, "height": ch},
            "padding": {
                "left": x,
                "top": y,
                "right": w - (x + cw),
                "bottom": h - (y + ch),
            },
            "contour_area": round(cv2.contourArea(largest), 1),
            "num_contours_detected": len(contours),
        }
    )
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    from skimage.metrics import structural_similarity as ssim

    before = load_gray(args.before)
    after = load_gray(args.after)
    if before.shape != after.shape:
        after = cv2.resize(after, (before.shape[1], before.shape[0]))

    score, diff = ssim(before, after, full=True)
    diff = (diff * 255).astype(np.uint8)
    diff_inv = 255 - diff
    _, thresh = cv2.threshold(diff_inv, 30, 255, cv2.THRESH_BINARY)
    changed_pixels = int(np.count_nonzero(thresh))
    total_pixels = thresh.size

    result = {
        "ssim_score": round(float(score), 4),
        "interpretation": "identical"
        if score > 0.995
        else ("minor differences" if score > 0.95 else "significant differences"),
        "changed_pixels": changed_pixels,
        "changed_pixel_pct": round(100 * changed_pixels / total_pixels, 2),
    }

    if args.diff_out:
        heatmap = cv2.applyColorMap(diff_inv, cv2.COLORMAP_JET)
        cv2.imwrite(str(args.diff_out), heatmap)
        result["diff_image"] = str(args.diff_out)

    emit(result)
    return 0


def cmd_edges(args: argparse.Namespace) -> int:
    gray = load_gray(args.image)
    edges = cv2.Canny(gray, args.low, args.high)
    edge_pixels = int(np.count_nonzero(edges))
    total = edges.size
    emit(
        {
            "edge_pixels": edge_pixels,
            "edge_density_pct": round(100 * edge_pixels / total, 3),
        }
    )
    return 0


def cmd_lines(args: argparse.Namespace) -> int:
    gray = load_gray(args.image)
    edges = cv2.Canny(gray, 50, 150)
    lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 180,
        threshold=args.threshold,
        minLineLength=args.min_length,
        maxLineGap=10,
    )
    if lines is None:
        emit({"num_lines": 0, "lines": []})
        return 0

    parsed = []
    for line in lines.reshape(-1, 4):
        x1, y1, x2, y2 = (int(v) for v in line)
        angle = round(float(np.degrees(np.arctan2(y2 - y1, x2 - x1))), 1)
        parsed.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2, "angle_deg": angle})

    horizontal = sum(
        1
        for l in parsed
        if abs(l["angle_deg"]) < 5 or abs(abs(l["angle_deg"]) - 180) < 5
    )
    vertical = sum(1 for l in parsed if abs(abs(l["angle_deg"]) - 90) < 5)

    emit(
        {
            "num_lines": len(parsed),
            "horizontal_lines": horizontal,
            "vertical_lines": vertical,
            "lines": parsed[: args.max_report],
        }
    )
    return 0


def cmd_histogram(args: argparse.Namespace) -> int:
    img = load_color(args.image)
    colors = ("blue", "green", "red")
    hist = {}
    for i, name in enumerate(colors):
        h = cv2.calcHist([img], [i], None, [256], [0, 256]).flatten()
        hist[name] = {"mean": round(float(np.sum(np.arange(256) * h) / np.sum(h)), 1)}

    pixels = img.reshape(-1, 3).astype(np.float32)
    k = 5
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _, labels, centers = cv2.kmeans(
        pixels, k, None, criteria, 3, cv2.KMEANS_RANDOM_CENTERS
    )
    counts = np.bincount(labels.flatten(), minlength=k)
    order = np.argsort(-counts)
    dominant = [
        {
            "bgr": [int(v) for v in centers[i]],
            "hex": "#{:02x}{:02x}{:02x}".format(*[int(v) for v in centers[i][::-1]]),
            "pct": round(100 * counts[i] / counts.sum(), 1),
        }
        for i in order
    ]

    emit({"channel_means": hist, "dominant_colors": dominant})
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    img = load_color(args.image)
    h, w, c = img.shape
    emit(
        {
            "width": w,
            "height": h,
            "channels": c,
            "file_size_bytes": args.image.stat().st_size,
        }
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("blur", help="variance-of-Laplacian sharpness score")
    p.add_argument("image", type=Path)
    p.set_defaults(func=cmd_blur)

    p = sub.add_parser("border", help="detect outer contour + padding to edges")
    p.add_argument("image", type=Path)
    p.set_defaults(func=cmd_border)

    p = sub.add_parser("compare", help="SSIM + pixel diff between two images")
    p.add_argument("before", type=Path)
    p.add_argument("after", type=Path)
    p.add_argument("--diff-out", type=Path, help="write a diff heatmap PNG here")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("edges", help="Canny edge count/density")
    p.add_argument("image", type=Path)
    p.add_argument("--low", type=int, default=50)
    p.add_argument("--high", type=int, default=150)
    p.set_defaults(func=cmd_edges)

    p = sub.add_parser("lines", help="Hough line detection (alignment checks)")
    p.add_argument("image", type=Path)
    p.add_argument("--threshold", type=int, default=80)
    p.add_argument("--min-length", type=int, default=30)
    p.add_argument("--max-report", type=int, default=50)
    p.set_defaults(func=cmd_lines)

    p = sub.add_parser("histogram", help="color histogram + dominant colors")
    p.add_argument("image", type=Path)
    p.set_defaults(func=cmd_histogram)

    p = sub.add_parser("info", help="basic image metadata")
    p.add_argument("image", type=Path)
    p.set_defaults(func=cmd_info)

    args = parser.parse_args()
    try:
        return args.func(args)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
