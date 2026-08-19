from __future__ import annotations

import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from HistoricalContent.historical_content.image_dimensions import read_image_dimensions
from HistoricalContent.historical_content.vpk_pipeline import (
    _build_historical_icon_pack,
    _validate_mapping,
)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def _write_png(path: Path, width: int, height: int) -> None:
    row = b"\x00" + (b"\x7f\x3f\xbf\xff" * width)
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(row * height))
        + _png_chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


class ImageDimensionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_reads_png_jpeg_and_webp_headers(self) -> None:
        png = self.root / "sample.png"
        _write_png(png, 37, 53)
        self.assertEqual(read_image_dimensions(png), (37, 53))

        jpeg = self.root / "sample.jpg"
        jpeg.write_bytes(
            b"\xff\xd8"
            + b"\xff\xc0\x00\x0b\x08"
            + struct.pack(">HH", 61, 43)
            + b"\x01\x01\x11\x00"
            + b"\xff\xd9"
        )
        self.assertEqual(read_image_dimensions(jpeg), (43, 61))

        webp = self.root / "sample.webp"
        vp8x = b"\x00\x00\x00\x00" + (70 - 1).to_bytes(3, "little") + (91 - 1).to_bytes(3, "little")
        body = b"WEBP" + b"VP8X" + len(vp8x).to_bytes(4, "little") + vp8x
        webp.write_bytes(b"RIFF" + len(body).to_bytes(4, "little") + body)
        self.assertEqual(read_image_dimensions(webp), (70, 91))

    def test_historical_manifest_records_each_unique_asset_dimensions(self) -> None:
        extracted = self.root / "extracted"
        _write_png(extracted / "hero_sm_png.png", 128, 128)
        _write_png(extracted / "hero_card_psd.png", 280, 380)

        mappings_path = self.root / "character-mappings.json"
        mappings_path.write_text(
            json.dumps({"hero": ["hero", "hero_alias"]}),
            encoding="utf-8",
        )
        destination = self.root / "IconPacks" / "default"

        count = _build_historical_icon_pack(
            extracted,
            destination,
            _validate_mapping(mappings_path),
        )

        self.assertEqual(count, 2)
        manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["extractionFormatVersion"], 8)

        minimap_path = manifest["icons"]["minimap"]["hero"]
        normal_path = manifest["icons"]["normal"]["hero"]
        self.assertEqual(manifest["icons"]["normal"]["hero_alias"], normal_path)
        self.assertEqual(
            manifest["iconDimensions"],
            {
                minimap_path: {"width": 128, "height": 128},
                normal_path: {"width": 280, "height": 380},
            },
        )


if __name__ == "__main__":
    unittest.main()
