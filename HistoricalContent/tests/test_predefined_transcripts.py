from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from historical_content.predefined_transcripts import (
    PredefinedTranscriptError,
    load_predefined_transcripts,
)


HEADER = (
    '"file_path","vo_root","file_basename","transcription","localization_key",'
    '"removed_localization_suffix","match_status"\n'
)


class PredefinedTranscriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "official.csv"

    def tearDown(self):
        self.temp.cleanup()

    def write_rows(self, *rows: str) -> None:
        self.path.write_text(HEADER + "".join(rows), encoding="utf-8")

    def test_loads_safe_statuses_and_skips_conflicts(self):
        self.write_rows(
            '"sounds/vo/astro/astro_test_01.vsnd_c","astro","astro_test_01",'
            '"First line.","key","hero_3d","single_match"\n',
            '"sounds/vo/astro/ping/astro_ping_01.vsnd_c","astro","astro_ping_01",'
            '"Second line.","key || key2","hero_3d || ping_2d",'
            '"multiple_keys_same_transcription"\n',
            '"sounds/vo/astro/ping/astro_ping_02.vsnd_c","astro","astro_ping_02",'
            '"First || Second","key || key2","hero_3d || ping_2d",'
            '"multiple_conflicting_transcriptions"\n',
        )

        catalog = load_predefined_transcripts(self.path)

        self.assertEqual(catalog.total_rows, 3)
        self.assertEqual(catalog.accepted_rows, 2)
        self.assertEqual(catalog.skipped_conflicts, 1)
        self.assertEqual(
            catalog.transcripts["astro/astro_test_01.mp3"],
            "First line.",
        )
        self.assertEqual(
            catalog.transcripts["astro/ping/astro_ping_01.mp3"],
            "Second line.",
        )
        self.assertNotIn("astro/ping/astro_ping_02.mp3", catalog.transcripts)

    def test_rejects_path_metadata_mismatch(self):
        self.write_rows(
            '"sounds/vo/astro/astro_test_01.vsnd_c","atlas","astro_test_01",'
            '"First line.","key","hero_3d","single_match"\n',
        )

        with self.assertRaisesRegex(PredefinedTranscriptError, "vo_root"):
            load_predefined_transcripts(self.path)

    def test_rejects_duplicate_normalized_paths(self):
        row = (
            '"sounds/vo/astro/astro_test_01.vsnd_c","astro","astro_test_01",'
            '"First line.","key","hero_3d","single_match"\n'
        )
        self.write_rows(row, row)

        with self.assertRaisesRegex(
            PredefinedTranscriptError, "Multiple predefined transcript rows"
        ):
            load_predefined_transcripts(self.path)

    def test_rejects_unknown_status(self):
        self.write_rows(
            '"sounds/vo/astro/astro_test_01.vsnd_c","astro","astro_test_01",'
            '"First line.","key","hero_3d","unreviewed"\n',
        )

        with self.assertRaisesRegex(
            PredefinedTranscriptError, "unsupported match_status"
        ):
            load_predefined_transcripts(self.path)


if __name__ == "__main__":
    unittest.main()
