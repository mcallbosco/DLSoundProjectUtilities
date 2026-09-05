from __future__ import annotations

import unittest

from historical_content.transcription import (
    DEFAULT_MODEL,
    SUPPORTED_MODELS,
)


class TranscriptionConfigurationTests(unittest.TestCase):
    def test_supported_models_include_openai_transcription_options(self):
        self.assertEqual(DEFAULT_MODEL, "gpt-transcribe")
        self.assertIn("gpt-4o-transcribe", SUPPORTED_MODELS)
        self.assertIn("gpt-transcribe", SUPPORTED_MODELS)
        self.assertIn("gpt-4o-mini-transcribe", SUPPORTED_MODELS)


if __name__ == "__main__":
    unittest.main()
