from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from HistoricalContent.historical_content.preview import start_preview


class _FakeProcess:
    def poll(self):
        return None


class PreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.worker = self.root / "worker"
        self.website = self.root / "VLViewer"
        (self.worker / "node_modules" / "wrangler" / "bin").mkdir(parents=True)
        (self.website / "node_modules" / "next" / "dist" / "bin").mkdir(parents=True)
        (self.website / "scripts").mkdir(parents=True)
        (self.worker / "package.json").write_text("{}", encoding="utf-8")
        (self.website / "package.json").write_text("{}", encoding="utf-8")
        (self.worker / "node_modules" / "wrangler" / "bin" / "wrangler.js").touch()
        (self.website / "node_modules" / "next" / "dist" / "bin" / "next").touch()
        (self.website / "scripts" / "prepare-game-config.mjs").touch()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_prepares_new_website_and_starts_it_with_local_content(self) -> None:
        messages: list[str] = []
        prepared = subprocess.CompletedProcess([], 0, stdout="Prepared deadlock config.\n")
        processes = [_FakeProcess(), _FakeProcess()]
        with (
            patch(
                "HistoricalContent.historical_content.preview._executable",
                side_effect=lambda name: f"resolved-{name}",
            ),
            patch(
                "HistoricalContent.historical_content.preview.subprocess.run",
                return_value=prepared,
            ) as run,
            patch(
                "HistoricalContent.historical_content.preview.subprocess.Popen",
                side_effect=processes,
            ) as popen,
        ):
            result = start_preview(self.worker, self.website, "deadlock", messages.append)

        self.assertIs(result.worker, processes[0])
        self.assertIs(result.website, processes[1])
        run.assert_called_once()
        prepare_args, prepare_kwargs = run.call_args
        self.assertEqual(
            prepare_args[0],
            ["resolved-node", str(self.website / "scripts" / "prepare-game-config.mjs")],
        )
        self.assertEqual(prepare_kwargs["cwd"], self.website)
        self.assertEqual(prepare_kwargs["env"]["VLVIEWER_GAME"], "deadlock")
        self.assertEqual(
            prepare_kwargs["env"]["NEXT_PUBLIC_VLVIEWER_CONTENT_BASE_URL"],
            "http://127.0.0.1:8787",
        )
        website_call = popen.call_args_list[1]
        self.assertEqual(
            website_call.args[0],
            [
                "resolved-node",
                str(self.website / "node_modules" / "next" / "dist" / "bin" / "next"),
                "dev",
                "--turbopack",
            ],
        )
        self.assertEqual(website_call.kwargs["cwd"], self.website)
        self.assertEqual(website_call.kwargs["env"]["NEXT_PUBLIC_VLVIEWER_GAME"], "deadlock")
        self.assertIn("Prepared deadlock config.", messages)


if __name__ == "__main__":
    unittest.main()
