from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


UTILITIES_ROOT = Path(__file__).resolve().parents[2]
CONVERSATIONS_ROOT = UTILITIES_ROOT / "Conversations Utilities"
VOICELINES_ROOT = UTILITIES_ROOT / "Voiceline Utilities"
for path in (CONVERSATIONS_ROOT, VOICELINES_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import convos  # noqa: E402
from modules import transcribe_voice_files as transcribe_module  # noqa: E402


class DisabledTranscriptionTests(unittest.TestCase):
    def test_conversation_export_ignores_cached_generated_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "line.mp3.json").write_text(
                json.dumps({"segments": [{"text": "Old generated text."}]}),
                encoding="utf-8",
            )

            player = convos.ConversationPlayer.__new__(convos.ConversationPlayer)
            player.transcriptions_dir = str(root)
            player.audio_dir = str(root)
            player.vdf_texts = {}
            player.file_status_map = {}
            player._generate_transcriptions_snapshot = False
            player._retranscribe_on_status_snapshot = False
            player._load_completion_overrides = lambda: set()
            player._get_speaker_from_filename = lambda filename: "abrams"
            player._read_saved_summary = lambda key: None
            player._transcribe_file = lambda path: self.fail("OpenAI transcription was called")

            result = player._export_build_conversation(
                (("abrams", "paradox"), "01", None),
                [{
                    "part_groups": {
                        1: [{"filename": "line.mp3", "variation": 1}],
                    },
                    "is_complete": True,
                }],
                transcribe_all=False,
                generate_summaries=False,
            )

            self.assertEqual(result["lines"][0]["transcription"], "")
            self.assertFalse(result["lines"][0]["has_transcription"])

    def test_voiceline_export_writes_blank_text_without_api_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "audio"
            source.mkdir()
            (source / "line.mp3").write_bytes(b"not-real-audio")
            input_path = root / "flat.json"
            output_path = root / "all_voicelines.json"
            input_path.write_text(json.dumps({
                "abrams": {"Self": {"Test": [{"filename": "line.mp3"}]}},
            }), encoding="utf-8")

            with patch.object(
                transcribe_module,
                "get_openai_client",
                side_effect=AssertionError("OpenAI client was created"),
            ):
                stats = transcribe_module.transcribe_voice_files(
                    str(input_path),
                    str(source),
                    consolidated_json_path=str(output_path),
                    generate_transcriptions=False,
                )

            payload = json.loads(output_path.read_text(encoding="utf-8"))
            line = payload["abrams"]["Self"]["Test"][0]
            self.assertEqual(line["transcription"], "")
            self.assertEqual(stats["skipped"], 1)

    def test_official_vdf_text_is_kept_when_generation_is_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "line.mp3").write_bytes(b"not-real-audio")
            args = (
                "line.mp3", str(root), None, False, None, None, 0, 1,
                None, None, {}, {"unused": "mapping"}, False, False,
            )
            with patch.object(
                transcribe_module,
                "find_vdf_match",
                return_value=("citadel_line", "Official subtitle."),
            ):
                result = transcribe_module.process_file(args)

            self.assertEqual(
                result["transcription_data"]["transcription"],
                "Official subtitle.",
            )
            self.assertTrue(result["transcription_data"]["officialtranscription"])


if __name__ == "__main__":
    unittest.main()
