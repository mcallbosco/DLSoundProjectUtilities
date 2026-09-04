from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from historical_content.generation.storage import write_version_index


class VersionIndexTests(unittest.TestCase):
    def test_reimport_replaces_only_its_version_and_failed_write_rolls_back(self):
        with tempfile.TemporaryDirectory() as temporary:
            database_path = Path(temporary) / "index.sqlite3"

            def write(version, line_ids):
                write_version_index(
                    database_path,
                    version_id=version,
                    game="deadlock",
                    label=version,
                    imported_at="2026-09-04T00:00:00+00:00",
                    records_by_kind=[("voiceline", [
                        ("abrams", {"lineId": line_id, "filename": "line.mp3"}, None)
                        for line_id in line_ids
                    ])],
                )

            def rows():
                with closing(sqlite3.connect(database_path)) as database:
                    return database.execute(
                        "SELECT version_id, line_id FROM version_assets ORDER BY version_id, line_id"
                    ).fetchall()

            write("first", ["old-line", "obsolete-line"])
            write("second", ["unrelated-line"])
            write("first", ["new-line"])
            expected = [("first", "new-line"), ("second", "unrelated-line")]
            self.assertEqual(rows(), expected)

            with self.assertRaises(sqlite3.IntegrityError):
                write("first", ["partial-line", None])
            self.assertEqual(rows(), expected)


if __name__ == "__main__":
    unittest.main()
