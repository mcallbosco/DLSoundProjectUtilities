"""Read raster image dimensions without decoding the full image.

Historical portrait manifests need intrinsic dimensions before the website loads
an image.  Source2Viewer output is normally PNG, but historical and manually
provided packs may contain JPEG or WebP assets, so the reader supports all three
formats without adding a Pillow dependency to the pipeline.
"""

from __future__ import annotations

import struct
from pathlib import Path


class ImageDimensionError(ValueError):
    """Raised when an image header is malformed or unsupported."""


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_START_OF_FRAME_MARKERS = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}


def _validated_dimensions(path: Path, width: int, height: int) -> tuple[int, int]:
    if width < 1 or height < 1:
        raise ImageDimensionError(f"Image has invalid dimensions {width}x{height}: {path}")
    return width, height


def _png_dimensions(path: Path, data: bytes) -> tuple[int, int]:
    if len(data) < 24 or not data.startswith(_PNG_SIGNATURE) or data[12:16] != b"IHDR":
        raise ImageDimensionError(f"Invalid PNG header: {path}")
    width, height = struct.unpack(">II", data[16:24])
    return _validated_dimensions(path, width, height)


def _jpeg_dimensions(path: Path, data: bytes) -> tuple[int, int]:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        raise ImageDimensionError(f"Invalid JPEG header: {path}")

    offset = 2
    while offset < len(data):
        while offset < len(data) and data[offset] != 0xFF:
            offset += 1
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break

        marker = data[offset]
        offset += 1
        if marker in {0x01, *range(0xD0, 0xDA)}:
            continue
        if offset + 2 > len(data):
            break

        segment_length = int.from_bytes(data[offset:offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(data):
            raise ImageDimensionError(f"Invalid JPEG segment length: {path}")
        if marker in _JPEG_START_OF_FRAME_MARKERS:
            if segment_length < 7:
                raise ImageDimensionError(f"Invalid JPEG frame header: {path}")
            height = int.from_bytes(data[offset + 3:offset + 5], "big")
            width = int.from_bytes(data[offset + 5:offset + 7], "big")
            return _validated_dimensions(path, width, height)
        offset += segment_length

    raise ImageDimensionError(f"JPEG dimensions were not found: {path}")


def _webp_dimensions(path: Path, data: bytes) -> tuple[int, int]:
    if len(data) < 20 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ImageDimensionError(f"Invalid WebP header: {path}")

    offset = 12
    while offset + 8 <= len(data):
        chunk_type = data[offset:offset + 4]
        chunk_size = int.from_bytes(data[offset + 4:offset + 8], "little")
        payload_start = offset + 8
        payload_end = payload_start + chunk_size
        if payload_end > len(data):
            raise ImageDimensionError(f"Invalid WebP chunk length: {path}")
        payload = data[payload_start:payload_end]

        if chunk_type == b"VP8X" and len(payload) >= 10:
            width = int.from_bytes(payload[4:7], "little") + 1
            height = int.from_bytes(payload[7:10], "little") + 1
            return _validated_dimensions(path, width, height)
        if chunk_type == b"VP8L" and len(payload) >= 5 and payload[0] == 0x2F:
            bits = int.from_bytes(payload[1:5], "little")
            width = (bits & 0x3FFF) + 1
            height = ((bits >> 14) & 0x3FFF) + 1
            return _validated_dimensions(path, width, height)
        if chunk_type == b"VP8 " and len(payload) >= 10 and payload[3:6] == b"\x9d\x01\x2a":
            width = int.from_bytes(payload[6:8], "little") & 0x3FFF
            height = int.from_bytes(payload[8:10], "little") & 0x3FFF
            return _validated_dimensions(path, width, height)

        offset = payload_end + (chunk_size & 1)

    raise ImageDimensionError(f"WebP dimensions were not found: {path}")


def read_image_dimensions(path: Path) -> tuple[int, int]:
    """Return ``(width, height)`` from a PNG, JPEG, or WebP header."""
    image_path = Path(path)
    try:
        data = image_path.read_bytes()
    except OSError as exc:
        raise ImageDimensionError(f"Could not read image dimensions from {image_path}: {exc}") from exc

    suffix = image_path.suffix.casefold()
    if suffix == ".png":
        return _png_dimensions(image_path, data)
    if suffix in {".jpg", ".jpeg"}:
        return _jpeg_dimensions(image_path, data)
    if suffix == ".webp":
        return _webp_dimensions(image_path, data)
    raise ImageDimensionError(f"Unsupported image format for dimensions: {image_path}")
