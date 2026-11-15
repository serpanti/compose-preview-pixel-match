#!/usr/bin/env python3
"""Compare the geometry and optionally the rendered colors of two PNG files.

Red is reference-only, cyan is candidate-only, white is overlap. The script
uses only the Python standard library and intentionally does not auto-align
images: any scale/offset must be explicit so layout errors stay visible.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import zlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class PngImage:
    width: int
    height: int
    rgba: bytes


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def read_png(path: Path) -> PngImage:
    data = path.read_bytes()
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError(f"{path}: not a PNG file")

    offset = len(PNG_SIGNATURE)
    width = height = bit_depth = color_type = interlace = None
    compressed = bytearray()
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
        elif chunk_type == b"IDAT":
            compressed.extend(payload)
        elif chunk_type == b"IEND":
            break

    if width is None or height is None:
        raise ValueError(f"{path}: missing IHDR")
    if bit_depth != 8 or color_type not in (0, 2, 4, 6) or interlace != 0:
        raise ValueError(
            f"{path}: supported PNGs are 8-bit, non-interlaced grayscale/RGB/RGBA"
        )

    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    stride = width * channels
    raw = zlib.decompress(bytes(compressed))
    expected = height * (stride + 1)
    if len(raw) != expected:
        raise ValueError(f"{path}: unexpected decompressed size")

    rows: list[bytearray] = []
    cursor = 0
    previous = bytearray(stride)
    for _ in range(height):
        filter_type = raw[cursor]
        cursor += 1
        encoded = raw[cursor : cursor + stride]
        cursor += stride
        row = bytearray(stride)
        for index, value in enumerate(encoded):
            left = row[index - channels] if index >= channels else 0
            up = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 0:
                decoded = value
            elif filter_type == 1:
                decoded = value + left
            elif filter_type == 2:
                decoded = value + up
            elif filter_type == 3:
                decoded = value + ((left + up) // 2)
            elif filter_type == 4:
                decoded = value + _paeth(left, up, upper_left)
            else:
                raise ValueError(f"{path}: unsupported PNG filter {filter_type}")
            row[index] = decoded & 0xFF
        rows.append(row)
        previous = row

    rgba = bytearray(width * height * 4)
    destination = 0
    for row in rows:
        for source in range(0, len(row), channels):
            if color_type == 0:
                r = g = b = row[source]
                a = 255
            elif color_type == 2:
                r, g, b = row[source : source + 3]
                a = 255
            elif color_type == 4:
                r = g = b = row[source]
                a = row[source + 1]
            else:
                r, g, b, a = row[source : source + 4]
            rgba[destination : destination + 4] = bytes((r, g, b, a))
            destination += 4
    return PngImage(width, height, bytes(rgba))


def _chunk(chunk_type: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + chunk_type
        + payload
        + struct.pack(">I", zlib.crc32(chunk_type + payload) & 0xFFFFFFFF)
    )


def write_png(path: Path, image: PngImage) -> None:
    scanlines = bytearray()
    stride = image.width * 4
    for row in range(image.height):
        scanlines.append(0)
        start = row * stride
        scanlines.extend(image.rgba[start : start + stride])
    ihdr = struct.pack(">IIBBBBB", image.width, image.height, 8, 6, 0, 0, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        PNG_SIGNATURE
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(bytes(scanlines), level=9))
        + _chunk(b"IEND", b"")
    )


def parse_rgb(value: str) -> tuple[int, int, int]:
    value = value.strip()
    if value.startswith("#") and len(value) == 7:
        return tuple(int(value[index : index + 2], 16) for index in (1, 3, 5))
    parts = value.split(",")
    if len(parts) == 3:
        rgb = tuple(int(part.strip()) for part in parts)
        if all(0 <= channel <= 255 for channel in rgb):
            return rgb
    raise argparse.ArgumentTypeError("background must be #RRGGBB or R,G,B")


def infer_background(image: PngImage) -> tuple[int, int, int]:
    samples: list[tuple[int, int, int]] = []
    step_x = max(1, image.width // 128)
    step_y = max(1, image.height // 128)
    positions = []
    for x in range(0, image.width, step_x):
        positions.extend(((x, 0), (x, image.height - 1)))
    for y in range(0, image.height, step_y):
        positions.extend(((0, y), (image.width - 1, y)))
    for x, y in positions:
        index = (y * image.width + x) * 4
        r, g, b, a = image.rgba[index : index + 4]
        if a < 16:
            samples.append((0, 0, 0))
        elif a > 239:
            samples.append((r, g, b))
    return Counter(samples).most_common(1)[0][0] if samples else (0, 0, 0)


def pixel_at(image: PngImage, x: float, y: float) -> bytes:
    pixel_x = math.floor(x + 0.5)
    pixel_y = math.floor(y + 0.5)
    if pixel_x < 0 or pixel_x >= image.width or pixel_y < 0 or pixel_y >= image.height:
        return bytes(4)
    index = (pixel_y * image.width + pixel_x) * 4
    return image.rgba[index : index + 4]


def coverage(pixel: bytes, background: tuple[int, int, int]) -> float:
    r, g, b, a = pixel
    alpha = a / 255.0
    composite = (
        r * alpha + background[0] * (1.0 - alpha),
        g * alpha + background[1] * (1.0 - alpha),
        b * alpha + background[2] * (1.0 - alpha),
    )
    return max(
        abs(composite[0] - background[0]),
        abs(composite[1] - background[1]),
        abs(composite[2] - background[2]),
    ) / 255.0


def color_delta(reference: bytes, candidate: bytes) -> float:
    """Maximum difference on the 8-bit scale, using premultiplied RGB and alpha."""
    reference_alpha = reference[3]
    candidate_alpha = candidate[3]
    red = abs(reference[0] * reference_alpha - candidate[0] * candidate_alpha)
    green = abs(reference[1] * reference_alpha - candidate[1] * candidate_alpha)
    blue = abs(reference[2] * reference_alpha - candidate[2] * candidate_alpha)
    return max(max(red, green, blue) / 255, abs(reference_alpha - candidate_alpha))


def include_pixel(bounds: list[int] | None, x: int, y: int) -> list[int]:
    if bounds is None:
        return [x, y, x, y]
    bounds[0] = min(bounds[0], x)
    bounds[1] = min(bounds[1], y)
    bounds[2] = max(bounds[2], x)
    bounds[3] = max(bounds[3], y)
    return bounds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--color-output", type=Path, help="write a separate color/alpha heatmap PNG")
    parser.add_argument(
        "--color-threshold",
        type=int,
        default=8,
        help="ignore color/alpha deltas up to this value on the 0..255 scale (default: 8)",
    )
    parser.add_argument("--candidate-scale", type=float, default=1.0)
    parser.add_argument("--candidate-offset-x", type=float, default=0.0)
    parser.add_argument("--candidate-offset-y", type=float, default=0.0)
    parser.add_argument("--reference-background", type=parse_rgb)
    parser.add_argument("--candidate-background", type=parse_rgb)
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.08,
        help="foreground/diff threshold in the 0..1 range (default: 0.08)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not math.isfinite(args.candidate_scale) or args.candidate_scale <= 0:
        raise ValueError("--candidate-scale must be greater than zero")
    if not all(math.isfinite(offset) for offset in (args.candidate_offset_x, args.candidate_offset_y)):
        raise ValueError("candidate offsets must be finite")
    if not 0 <= args.threshold <= 1:
        raise ValueError("--threshold must be in the 0..1 range")
    if not 0 <= args.color_threshold <= 255:
        raise ValueError("--color-threshold must be in the 0..255 range")
    if args.color_output and args.color_output.resolve() in {
        args.reference.resolve(), args.candidate.resolve(), args.output.resolve()
    }:
        raise ValueError("--color-output must be separate from the input and geometry PNGs")

    reference = read_png(args.reference)
    candidate = read_png(args.candidate)
    reference_background = args.reference_background or infer_background(reference)
    candidate_background = args.candidate_background or infer_background(candidate)

    output = bytearray(reference.width * reference.height * 4)
    color_output = bytearray(len(output)) if args.color_output else None
    color_pixel_count = 0
    color_delta_sum = max_color_delta = 0.0
    color_bbox: list[int] | None = None
    reference_weight = candidate_weight = overlap_weight = 0.0
    reference_pixels = candidate_pixels = intersection_pixels = union_pixels = 0
    diff_bbox: list[int] | None = None

    for y in range(reference.height):
        for x in range(reference.width):
            candidate_x = (x - args.candidate_offset_x) / args.candidate_scale
            candidate_y = (y - args.candidate_offset_y) / args.candidate_scale
            reference_pixel = pixel_at(reference, x, y)
            candidate_pixel = pixel_at(candidate, candidate_x, candidate_y)
            ref = coverage(reference_pixel, reference_background)
            cand = coverage(candidate_pixel, candidate_background)

            reference_weight += ref
            candidate_weight += cand
            overlap_weight += min(ref, cand)

            ref_on = ref >= args.threshold
            cand_on = cand >= args.threshold
            reference_pixels += int(ref_on)
            candidate_pixels += int(cand_on)
            intersection_pixels += int(ref_on and cand_on)
            union_pixels += int(ref_on or cand_on)

            if abs(ref - cand) >= args.threshold:
                diff_bbox = include_pixel(diff_bbox, x, y)

            index = (y * reference.width + x) * 4
            output[index : index + 4] = bytes(
                (
                    round(ref * 255),
                    round(cand * 255),
                    round(cand * 255),
                    255,
                )
            )

            if color_output is not None:
                delta = color_delta(reference_pixel, candidate_pixel)
                color_delta_sum += delta
                max_color_delta = max(max_color_delta, delta)
                intensity = 0
                if delta > args.color_threshold:
                    color_pixel_count += 1
                    color_bbox = include_pixel(color_bbox, x, y)
                    intensity = max(64, round(delta))
                color_output[index : index + 4] = bytes((intensity, intensity // 3, 0, 255))

    write_png(args.output, PngImage(reference.width, reference.height, bytes(output)))
    union_weight = reference_weight + candidate_weight - overlap_weight
    report = {
        "reference": str(args.reference),
        "candidate": str(args.candidate),
        "output": str(args.output),
        "canvas": [reference.width, reference.height],
        "reference_background": reference_background,
        "candidate_background": candidate_background,
        "candidate_transform": {
            "scale": args.candidate_scale,
            "offset_x": args.candidate_offset_x,
            "offset_y": args.candidate_offset_y,
        },
        "weighted_iou": overlap_weight / union_weight if union_weight else 1.0,
        "binary_iou": intersection_pixels / union_pixels if union_pixels else 1.0,
        "weighted_mismatch": (
            (reference_weight + candidate_weight - 2 * overlap_weight)
            / (reference_weight + candidate_weight)
            if reference_weight + candidate_weight
            else 0.0
        ),
        "reference_foreground_pixels": reference_pixels,
        "candidate_foreground_pixels": candidate_pixels,
        "diff_bbox": diff_bbox,
    }
    if color_output is not None:
        write_png(args.color_output, PngImage(reference.width, reference.height, bytes(color_output)))
        canvas_pixels = reference.width * reference.height
        report["color_diff"] = {
            "output": str(args.color_output),
            "threshold": args.color_threshold,
            "changed_pixels": color_pixel_count,
            "changed_fraction": color_pixel_count / canvas_pixels,
            "max_channel_delta": max_color_delta,
            "mean_pixel_delta": color_delta_sum / canvas_pixels,
            "diff_bbox": color_bbox,
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, zlib.error) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(2)
